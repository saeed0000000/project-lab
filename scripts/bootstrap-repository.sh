#!/usr/bin/env bash
set -Eeuo pipefail

# Locate the repository root.
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# Required tools.
for cmd in git gh python3; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "ERROR: Required command not found: $cmd" >&2
    exit 1
  }
done

# Discover the actual GitHub repository and its default branch.
REPO="$(gh repo view --json nameWithOwner --jq '.nameWithOwner')"
DEFAULT_BRANCH="$(
  gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name'
)"

if [[ -z "$REPO" || -z "$DEFAULT_BRANCH" ]]; then
  echo "ERROR: Could not discover repository name or default branch." >&2
  exit 1
fi

REPO_URL="https://github.com/${REPO}.git"

FILES=(
  "argocd/root-app.yaml"
  "argocd/apps/project-app.yaml"
)

# Protect existing edits to the files this script will change.
for file in "${FILES[@]}"; do
  [[ -f "$file" ]] || {
    echo "ERROR: Required file not found: $file" >&2
    exit 1
  }

  git diff --quiet -- "$file" &&
  git diff --cached --quiet -- "$file" || {
    echo "ERROR: Commit or review existing edits first: $file" >&2
    exit 1
  }
done

export REPO_URL DEFAULT_BRANCH

# Validate both files before writing either one.
python3 <<'PY'
import os
import re
import sys
from pathlib import Path

repo_url = os.environ["REPO_URL"]
default_branch = os.environ["DEFAULT_BRANCH"]

files = [
    Path("argocd/root-app.yaml"),
    Path("argocd/apps/project-app.yaml"),
]

planned = {}

for path in files:
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
            lines[i] = match.group(1) + default_branch + ending

    if repo_count != 1 or branch_count != 1:
        sys.exit(
            f"ERROR: {path}: expected exactly one repoURL and one "
            f"targetRevision; found {repo_count} and {branch_count}. "
            "No files were written."
        )

    planned[path] = "".join(lines)

for path, updated in planned.items():
    if path.read_text() != updated:
        path.write_text(updated)
        print(f"Updated: {path}")
    else:
        print(f"Already correct: {path}")

print(f"Repository: {os.environ['REPO_URL']}")
print(f"Default branch: {default_branch}")
PY
