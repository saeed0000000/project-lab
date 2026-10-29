#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'USAGE'
Usage:
  ./scripts/bootstrap-repository.sh [--repo OWNER/REPOSITORY] [--branch BRANCH] [--apply]

Safe by default: prints the planned changes without modifying files.

Examples:
  # Preview values for a repository that does not exist yet:
  ./scripts/bootstrap-repository.sh --repo OWNER/NEW-REPOSITORY --branch main

  # After creating and cloning that repository, apply the changes there:
  ./scripts/bootstrap-repository.sh --repo OWNER/NEW-REPOSITORY --branch main --apply

Rules:
  - --apply is allowed only inside a local clone whose origin is the target repository.
  - --apply requires the target to exist on GitHub and --branch to match its default branch.
  - This script updates only Argo CD repoURL/targetRevision fields. It does not generate
    GitHub App credentials, rewrite image digests, or regenerate Sealed Secrets.
USAGE
}

TARGET_REPO=""
TARGET_BRANCH=""
APPLY=false

while (($#)); do
  case "$1" in
    --repo)
      (($# >= 2)) || { echo "ERROR: --repo requires OWNER/REPOSITORY" >&2; exit 2; }
      TARGET_REPO="$2"
      shift 2
      ;;
    --branch)
      (($# >= 2)) || { echo "ERROR: --branch requires a branch name" >&2; exit 2; }
      TARGET_BRANCH="$2"
      shift 2
      ;;
    --apply)
      APPLY=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "ERROR: Run this script from inside a Git repository." >&2
  exit 1
}
cd "$REPO_ROOT"

for cmd in git python3; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "ERROR: Required command not found: $cmd" >&2
    exit 1
  }
done

# GitHub CLI is only needed to discover repository metadata or apply changes.
if [[ -z "$TARGET_REPO" || -z "$TARGET_BRANCH" || "$APPLY" == true ]]; then
  command -v gh >/dev/null 2>&1 || {
    echo "ERROR: GitHub CLI (gh) is required for repository discovery or --apply." >&2
    exit 1
  }
fi

if [[ -z "$TARGET_REPO" ]]; then
  TARGET_REPO="$(gh repo view --json nameWithOwner --jq '.nameWithOwner')"
  [[ -n "$TARGET_REPO" ]] || {
    echo "ERROR: Could not determine repository. Supply --repo OWNER/REPOSITORY." >&2
    exit 1
  }
fi

if [[ ! "$TARGET_REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  echo "ERROR: Invalid repository format: $TARGET_REPO (expected OWNER/REPOSITORY)." >&2
  exit 2
fi

# Look up target metadata unless a caller explicitly supplied a branch for a preview
# of a repository that might not exist yet.
TARGET_EXISTS=false
CANONICAL_REPO="$TARGET_REPO"
REMOTE_DEFAULT_BRANCH=""
if [[ -z "$TARGET_BRANCH" || "$APPLY" == true ]]; then
  if REPO_METADATA="$(gh repo view "$TARGET_REPO" --json nameWithOwner,defaultBranchRef 2>/dev/null)"; then
    TARGET_EXISTS=true
    CANONICAL_REPO="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nameWithOwner"])' <<<"$REPO_METADATA")"
    REMOTE_DEFAULT_BRANCH="$(python3 -c 'import json,sys; d=json.load(sys.stdin)["defaultBranchRef"]; print(d["name"] if d else "")' <<<"$REPO_METADATA")"
    [[ -n "$REMOTE_DEFAULT_BRANCH" ]] || {
      echo "ERROR: Could not determine default branch for $CANONICAL_REPO." >&2
      exit 1
    }
  else
    if [[ "$APPLY" == true || -z "$TARGET_BRANCH" ]]; then
      echo "ERROR: Cannot read $TARGET_REPO on GitHub." >&2
      echo "For a preview of a not-yet-created repository, pass both --repo and --branch without --apply." >&2
      exit 1
    fi
  fi
fi

if [[ -z "$TARGET_BRANCH" ]]; then
  TARGET_BRANCH="$REMOTE_DEFAULT_BRANCH"
fi

if [[ "$TARGET_BRANCH" == *$'\n'* || -z "$TARGET_BRANCH" ]]; then
  echo "ERROR: Invalid or empty branch name." >&2
  exit 2
fi

if [[ "$APPLY" == true ]]; then
  [[ "$TARGET_EXISTS" == true ]] || {
    echo "ERROR: --apply requires the target repository to exist on GitHub." >&2
    exit 1
  }

  if [[ "$TARGET_BRANCH" != "$REMOTE_DEFAULT_BRANCH" ]]; then
    echo "ERROR: --branch '$TARGET_BRANCH' differs from target default branch '$REMOTE_DEFAULT_BRANCH'." >&2
    exit 1
  fi

  ORIGIN_URL="$(git remote get-url origin 2>/dev/null)" || {
    echo "ERROR: This clone has no origin remote." >&2
    exit 1
  }

  export ORIGIN_URL CANONICAL_REPO
  python3 <<'PY'
import os
import re
import sys


def normalize(value: str) -> str:
    value = value.strip()
    patterns = [
        r"^https?://github\.com/([^/]+/[^/]+?)(?:\.git)?/?$",
        r"^git@github\.com:([^/]+/[^/]+?)(?:\.git)?$",
        r"^ssh://git@github\.com/([^/]+/[^/]+?)(?:\.git)?/?$",
    ]
    for pattern in patterns:
        match = re.match(pattern, value, flags=re.IGNORECASE)
        if match:
            return match.group(1).lower()
    return ""

origin = normalize(os.environ["ORIGIN_URL"])
target = normalize("https://github.com/" + os.environ["CANONICAL_REPO"] + ".git")
if not origin or origin != target:
    print("ERROR: --apply is permitted only inside a clone whose origin matches the target repo.", file=sys.stderr)
    print("Origin URL does not match target repository; no files were changed.", file=sys.stderr)
    sys.exit(1)
PY

  for file in "argocd/root-app.yaml" "argocd/apps/project-app.yaml"; do
    [[ -f "$file" ]] || { echo "ERROR: Required file not found: $file" >&2; exit 1; }
    git diff --quiet -- "$file" && git diff --cached --quiet -- "$file" || {
      echo "ERROR: Commit or review existing changes first: $file" >&2
      exit 1
    }
  done
fi

export REPO_URL="https://github.com/${CANONICAL_REPO}.git"
export TARGET_BRANCH
export APPLY

python3 <<'PY'
import difflib
import os
import re
import sys
from pathlib import Path

repo_url = os.environ["REPO_URL"]
branch = os.environ["TARGET_BRANCH"]
apply = os.environ["APPLY"] == "true"
files = [Path("argocd/root-app.yaml"), Path("argocd/apps/project-app.yaml")]
planned = {}

for path in files:
    if not path.is_file():
        sys.exit(f"ERROR: Required file not found: {path}; no files were written.")
    original = path.read_text()
    lines = original.splitlines(keepends=True)
    repo_count = 0
    branch_count = 0

    for i, line in enumerate(lines):
        content = line.rstrip("\r\n")
        ending = line[len(content):]
        match = re.match(r"^(\s*repoURL:\s*).*$", content)
        if match:
            repo_count += 1
            lines[i] = match.group(1) + repo_url + ending
            continue
        match = re.match(r"^(\s*targetRevision:\s*).*$", content)
        if match:
            branch_count += 1
            lines[i] = match.group(1) + branch + ending

    if repo_count != 1 or branch_count != 1:
        sys.exit(
            f"ERROR: {path}: expected exactly one repoURL and one targetRevision; "
            f"found {repo_count} and {branch_count}. No files were written."
        )
    planned[path] = (original, "".join(lines))

changed = [(p, old, new) for p, (old, new) in planned.items() if old != new]
for path, old, new in changed:
    diff = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
    )
    sys.stdout.writelines(diff)

if not changed:
    print("No changes needed; both Argo CD files already match the target.")
elif not apply:
    print("DRY RUN ONLY: no files were modified. Re-run with --apply from the target repository clone to write changes.")
else:
    for path, old, new in changed:
        path.write_text(new)
        print(f"Updated: {path}")

print(f"Target repository: {repo_url}")
print(f"Target default branch: {branch}")
PY

if [[ "$APPLY" != true ]]; then
  cat <<'NOTE'

Next steps / important limitations:
- Preview mode never writes files. --apply must be run inside a clone whose origin is the target repository.
- This helper updates only Argo CD repoURL/targetRevision values. It intentionally does not rewrite image references;
  create a release in the target repository and merge its image-digest PR before bootstrapping Argo CD.
- Existing SealedSecret files must be regenerated with the target cluster's Sealed Secrets certificate.
- GitHub App variables/secrets and installation permissions must be configured separately.
NOTE
fi
