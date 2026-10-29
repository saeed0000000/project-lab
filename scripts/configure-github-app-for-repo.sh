#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'USAGE'
Usage:
  ./scripts/configure-github-app-for-repo.sh \
    --repo OWNER/NEW-REPOSITORY \
    --key-file /absolute/path/to/github-app-private-key.pem \
    [--source-repo OWNER/EXISTING-REPOSITORY] [--apply]

Safe by default: validates GitHub App credentials and target installation,
then reports what it would configure. It does not write variables/secrets
unless --apply is supplied.

Prerequisites:
  - gh authenticated with permission to manage Actions variables/secrets
    on the target repository and read APP_CLIENT_ID on the source repository.
  - The target repository already exists on GitHub.
  - The GitHub App is installed on the target repository.
  - The PEM key belongs to that GitHub App and is stored outside Git.

Configured on target:
  - Actions variable APP_CLIENT_ID (copied from source repository)
  - Actions secret APP_PRIVATE_KEY (read from the PEM file)
USAGE
}

TARGET_REPO=""
SOURCE_REPO=""
KEY_FILE=""
APPLY=false

while (($#)); do
  case "$1" in
    --repo)
      (($# >= 2)) || { echo "ERROR: --repo requires OWNER/REPOSITORY" >&2; exit 2; }
      TARGET_REPO="$2"; shift 2 ;;
    --source-repo)
      (($# >= 2)) || { echo "ERROR: --source-repo requires OWNER/REPOSITORY" >&2; exit 2; }
      SOURCE_REPO="$2"; shift 2 ;;
    --key-file)
      (($# >= 2)) || { echo "ERROR: --key-file requires a file path" >&2; exit 2; }
      KEY_FILE="$2"; shift 2 ;;
    --apply)
      APPLY=true; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$TARGET_REPO" ]] || { echo "ERROR: --repo is required." >&2; usage >&2; exit 2; }
[[ -n "$KEY_FILE" ]] || { echo "ERROR: --key-file is required." >&2; usage >&2; exit 2; }
[[ "$TARGET_REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || {
  echo "ERROR: Invalid target repository '$TARGET_REPO'; expected OWNER/REPOSITORY." >&2
  exit 2
}

for cmd in gh openssl curl python3 git; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "ERROR: Required command not found: $cmd" >&2; exit 1; }
done

KEY_FILE="$(realpath -- "$KEY_FILE")"
[[ -f "$KEY_FILE" && -r "$KEY_FILE" ]] || { echo "ERROR: Key file is missing or unreadable." >&2; exit 1; }

# Never permit a private key inside the repository working tree.
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "ERROR: Run this helper from inside the source repository clone." >&2
  exit 1
}
REPO_ROOT="$(realpath -- "$REPO_ROOT")"
case "$KEY_FILE" in
  "$REPO_ROOT"/*)
    echo "ERROR: Keep the private key outside the Git working tree." >&2
    exit 1 ;;
esac

# Discover source repository only if not explicitly provided.
if [[ -z "$SOURCE_REPO" ]]; then
  SOURCE_REPO="$(gh repo view --json nameWithOwner --jq '.nameWithOwner')"
fi
[[ "$SOURCE_REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || {
  echo "ERROR: Could not determine a valid source repository." >&2
  exit 1
}

normalize_repo() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }
if [[ "$(normalize_repo "$SOURCE_REPO")" == "$(normalize_repo "$TARGET_REPO")" ]]; then
  echo "ERROR: Source and target repositories are identical; refusing to overwrite the current setup." >&2
  exit 1
fi

# Confirm current gh authentication and both repositories are accessible.
gh auth status >/dev/null 2>&1 || { echo "ERROR: gh is not authenticated. Run gh auth login." >&2; exit 1; }
SOURCE_CLIENT_ID="$(gh variable get APP_CLIENT_ID --repo "$SOURCE_REPO" 2>/dev/null)" || {
  echo "ERROR: Could not read APP_CLIENT_ID from source repository $SOURCE_REPO." >&2
  exit 1
}
[[ -n "$SOURCE_CLIENT_ID" ]] || { echo "ERROR: APP_CLIENT_ID in source repository is empty." >&2; exit 1; }

TARGET_CANONICAL="$(gh repo view "$TARGET_REPO" --json nameWithOwner --jq '.nameWithOwner' 2>/dev/null)" || {
  echo "ERROR: Target repository $TARGET_REPO does not exist or is not accessible with gh." >&2
  exit 1
}

# Validate PEM structure without printing the key.
openssl pkey -in "$KEY_FILE" -check -noout >/dev/null 2>&1 || {
  echo "ERROR: The PEM file is not a readable, valid private key." >&2
  exit 1
}

TMP_DIR="$(mktemp -d)"
chmod 700 "$TMP_DIR"
trap 'unset APP_JWT SOURCE_CLIENT_ID signature payload unsigned; rm -rf "$TMP_DIR"' EXIT

# Create a short-lived RS256 JWT using the GitHub App client ID.
# Keep it out of shell command arguments and never write it to the repository.
now="$(date +%s)"
iat=$((now - 60))
exp=$((now + 540))
base64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }
header="$(printf '%s' '{"alg":"RS256","typ":"JWT"}' | base64url)"
payload="$(printf '{"iat":%s,"exp":%s,"iss":"%s"}' "$iat" "$exp" "$SOURCE_CLIENT_ID" | base64url)"
unsigned="${header}.${payload}"
signature="$(printf '%s' "$unsigned" | openssl dgst -sha256 -sign "$KEY_FILE" -binary | base64url)"
APP_JWT="${unsigned}.${signature}"
printf 'Authorization: Bearer %s\n' "$APP_JWT" > "$TMP_DIR/auth-header"
chmod 600 "$TMP_DIR/auth-header"
unset APP_JWT signature payload unsigned

api_get() {
  local endpoint="$1" output_file="$2" code
  code="$(curl --silent --show-error \
    --output "$output_file" --write-out '%{http_code}' \
    --header "Accept: application/vnd.github+json" \
    --header "@${TMP_DIR}/auth-header" \
    --header "X-GitHub-Api-Version: 2022-11-28" \
    "https://api.github.com${endpoint}")" || {
      echo "ERROR: Failed to connect to GitHub API." >&2; return 1;
    }
  if [[ "$code" != "200" ]]; then
    local message
    message="$(python3 - "$output_file" <<'PY'
import json, sys
try:
    obj=json.load(open(sys.argv[1], encoding="utf-8"))
    print(obj.get("message", "No error details returned"))
except Exception:
    print("Could not parse API error response")
PY
)"
    echo "ERROR: GitHub App API request failed (HTTP ${code}): ${message}" >&2
    return 1
  fi
}

api_get "/app" "$TMP_DIR/app.json" || {
  echo "ERROR: The client ID and PEM key did not authenticate as the same GitHub App." >&2
  exit 1
}

APP_SLUG="$(python3 - "$TMP_DIR/app.json" "$SOURCE_CLIENT_ID" <<'PY'
import json, sys
obj=json.load(open(sys.argv[1], encoding="utf-8"))
expected=sys.argv[2]
actual=obj.get("client_id")
if actual != expected:
    print("ERROR: GitHub returned a different client ID for this key.", file=sys.stderr)
    sys.exit(1)
print(obj.get("slug", ""))
PY
)" || exit 1

api_get "/repos/${TARGET_CANONICAL}/installation" "$TMP_DIR/installation.json" || {
  echo "ERROR: GitHub App is not installed on the target repository, or lacks access." >&2
  echo "Install project-lab-gitops-bot on ${TARGET_CANONICAL} via GitHub Settings, then rerun." >&2
  exit 1
}

python3 - "$TMP_DIR/installation.json" "$APP_SLUG" <<'PY'
import json, sys
obj=json.load(open(sys.argv[1], encoding="utf-8"))
expected_slug=sys.argv[2]
slug=obj.get("app_slug")
permissions=obj.get("permissions") or {}
problems=[]
if slug != expected_slug:
    problems.append(f"target installation belongs to app '{slug}', expected '{expected_slug}'")
if permissions.get("contents") != "write":
    problems.append("Contents permission must be Read and write")
if permissions.get("pull_requests") != "write":
    problems.append("Pull requests permission must be Read and write")
if problems:
    for problem in problems: print("ERROR: " + problem, file=sys.stderr)
    sys.exit(1)
print(f"Verified installation: app={slug}; Contents=write; Pull requests=write")
PY

if [[ "$APPLY" != true ]]; then
  cat <<EOF2

DRY RUN: no repository settings were changed.
Source repository: ${SOURCE_REPO}
Target repository: ${TARGET_CANONICAL}
GitHub App: ${APP_SLUG}
Would set Actions variable: APP_CLIENT_ID
Would set Actions secret: APP_PRIVATE_KEY

After reviewing this output, rerun the same command with --apply to write the settings.
EOF2
  exit 0
fi

# Write the app ID as a repository variable and pass the private key only via stdin.
gh variable set APP_CLIENT_ID --repo "$TARGET_CANONICAL" --body "$SOURCE_CLIENT_ID"
gh secret set APP_PRIVATE_KEY --repo "$TARGET_CANONICAL" --app actions < "$KEY_FILE"

# Verify names only. Never display secret contents.
variable_found="$(gh variable list --repo "$TARGET_CANONICAL" --json name --jq '[.[].name] | index("APP_CLIENT_ID") != null')"
secret_found="$(gh secret list --repo "$TARGET_CANONICAL" --app actions --json name --jq '[.[].name] | index("APP_PRIVATE_KEY") != null')"
[[ "$variable_found" == "true" && "$secret_found" == "true" ]] || {
  echo "ERROR: GitHub CLI did not confirm both target settings by name." >&2
  exit 1
}

echo "SUCCESS: APP_CLIENT_ID and APP_PRIVATE_KEY are configured on ${TARGET_CANONICAL}."
echo "Only names were verified; secret contents were not read back or printed."
