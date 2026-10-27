#!/usr/bin/env python3
"""Prepare this GitOps project for a different GitHub repository/default branch.

Run only in a clean clone. Discovers origin and its default branch, edits known
repository-specific config, installs this helper in .github/scripts, and validates
YAML, shell/Python syntax, unit tests, and git diff. It does not commit or push.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HELPER_PATH = Path(".github/scripts/prepare-repository-portability.py")


def run(args: list[str], *, cwd: Path, capture: bool = False, check: bool = True) -> str:
    result = subprocess.run(
        args, cwd=str(cwd), text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=False,
    )
    if check and result.returncode:
        details = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(args)}\n{details}")
    return result.stdout.strip() if capture and result.returncode == 0 else ""


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one occurrence of {label}; found {count}.")
    return text.replace(old, new, 1)


def detect_repo_slug(remote: str) -> str:
    match = re.search(r"(?:github\.com[:/])([^/\s]+/[^/\s]+?)(?:\.git)?$", remote.strip())
    if not match:
        raise RuntimeError(f"Cannot derive GitHub owner/repository from origin URL: {remote!r}")
    slug = match.group(1).removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", slug):
        raise RuntimeError(f"Invalid GitHub repository slug derived from origin: {slug!r}")
    return slug


def detect_default_branch(root: Path) -> str:
    output = run(["git", "ls-remote", "--symref", "origin", "HEAD"], cwd=root, capture=True)
    for line in output.splitlines():
        match = re.match(r"ref: refs/heads/(.+)\s+HEAD$", line.strip())
        if match and match.group(1) and not match.group(1).startswith("-"):
            return match.group(1)
    raise RuntimeError("Cannot discover origin's default branch; check network access and origin credentials.")


def update_app_manifest(path: Path, text: str, repo_url: str, branch: str) -> str:
    text, n_url = re.subn(
        r"^(\s*repoURL:\s*)https://github\.com/[^\s]+\.git\s*$",
        lambda m: f"{m.group(1)}{repo_url}", text, count=1, flags=re.MULTILINE,
    )
    if n_url != 1:
        raise RuntimeError(f"{path}: expected one GitHub repoURL; found {n_url}.")
    text, n_branch = re.subn(
        r"^(\s*targetRevision:\s*)[^\s#]+(\s*(?:#.*)?)$",
        lambda m: f"{m.group(1)}{branch}{m.group(2)}", text, count=1, flags=re.MULTILINE,
    )
    if n_branch != 1:
        raise RuntimeError(f"{path}: expected one targetRevision; found {n_branch}.")
    return text


def rewrite_deployment(text: str, slug: str, repo_url: str, branch: str) -> str:
    owner, name = slug.split("/", 1)
    image = f"ghcr.io/{owner.lower()}/{name.lower()}-drift-watcher"
    text, image_count = re.subn(
        r"(?m)^(\s*image:\s*)ghcr\.io/[^\s]+-drift-watcher(@sha256:[0-9a-f]{64})\s*$",
        lambda m: f"{m.group(1)}{image}{m.group(2)}", text, count=1,
    )
    if image_count != 1:
        raise RuntimeError("k8s/drift-watcher-deployment.yaml must contain exactly one SHA256-pinned Drift Watcher image.")

    def set_value(src: str, env_name: str, value: str) -> tuple[str, bool]:
        pattern = re.compile(rf"(?m)^([ \t]*- name: {re.escape(env_name)}[ \t]*\n[ \t]+value:[ \t]*).*$")
        matches = list(pattern.finditer(src))
        if len(matches) > 1:
            raise RuntimeError(f"Duplicate environment variable {env_name} in Drift Watcher Deployment.")
        if not matches:
            return src, False
        return pattern.sub(lambda m: f"{m.group(1)}{value}", src, count=1), True

    text, found_dir = set_value(text, "DRIFT_REPO_DIR", "/work/repository")
    if not found_dir:
        raise RuntimeError("Could not find DRIFT_REPO_DIR in k8s/drift-watcher-deployment.yaml.")

    desired = {
        "DRIFT_GITHUB_REPOSITORY": f'"{slug}"',
        "DRIFT_REPO_URL": f'"{repo_url}"',
        "DRIFT_DEFAULT_BRANCH": f'"{branch}"',
    }
    missing: list[tuple[str, str]] = []
    for env_name, value in desired.items():
        text, found = set_value(text, env_name, value)
        if not found:
            missing.append((env_name, value))

    if missing:
        path_entry = re.search(
            r"(?m)^(?P<item_indent>[ \t]*)- name: DRIFT_REPO_DIR[ \t]*\n(?P<value_indent>[ \t]+)value:[ \t]*[^\n]*\n",
            text,
        )
        if not path_entry:
            raise RuntimeError("Cannot insert repository metadata after DRIFT_REPO_DIR in the Deployment.")
        extra = "".join(
            f"{path_entry.group('item_indent')}- name: {env_name}\n"
            f"{path_entry.group('value_indent')}value: {value}\n"
            for env_name, value in missing
        )
        text = text[:path_entry.end()] + extra + text[path_entry.end():]
    return text


def rewrite_entrypoint(text: str) -> str:
    old_prefix = (
        ': "${GH_TOKEN:?GH_TOKEN must be supplied through the Kubernetes Secret}"\n'
        ': "${KUBERNETES_SERVICE_HOST:?This container must run inside Kubernetes}"\n\n'
        'export GH_TOKEN\n'
        'export DRIFT_REPO_DIR="${DRIFT_REPO_DIR:-/work/project-lab}"\n'
    )
    new_prefix = (
        ': "${GH_TOKEN:?GH_TOKEN must be supplied through the Kubernetes Secret}"\n'
        ': "${KUBERNETES_SERVICE_HOST:?This container must run inside Kubernetes}"\n'
        ': "${DRIFT_GITHUB_REPOSITORY:?DRIFT_GITHUB_REPOSITORY must be configured in the Deployment}"\n'
        ': "${DRIFT_REPO_URL:?DRIFT_REPO_URL must be configured in the Deployment}"\n'
        ': "${DRIFT_DEFAULT_BRANCH:?DRIFT_DEFAULT_BRANCH must be configured in the Deployment}"\n\n'
        'export GH_TOKEN DRIFT_GITHUB_REPOSITORY DRIFT_REPO_URL DRIFT_DEFAULT_BRANCH\n'
        'export DRIFT_REPO_DIR="${DRIFT_REPO_DIR:-/work/repository}"\n'
    )
    if old_prefix in text:
        text = replace_once(text, old_prefix, new_prefix, "Drift Watcher entrypoint environment block")
    elif new_prefix not in text:
        raise RuntimeError("drift-watcher/entrypoint.sh: unknown environment setup; refusing to modify it.")

    old_clone = (
        'REPO_URL="https://github.com/saeed0000000/project-lab.git"\n\n'
        'if [ -d "$DRIFT_REPO_DIR/.git" ]; then\n'
        '    git -C "$DRIFT_REPO_DIR" fetch --quiet origin main\n'
        '    git -C "$DRIFT_REPO_DIR" checkout --force --detach origin/main\n'
        'else\n'
        '    if [ -e "$DRIFT_REPO_DIR" ]; then\n'
        '        rmdir "$DRIFT_REPO_DIR" || {\n'
        '            echo "ERROR: Workspace exists but is not an empty directory or Git clone." >&2\n'
        '            exit 1\n'
        '        }\n'
        '    fi\n\n'
        '    git clone --depth 1 --branch main "$REPO_URL" "$DRIFT_REPO_DIR"\n'
        'fi\n'
    )
    new_clone = (
        'REPO_URL="$DRIFT_REPO_URL"\n'
        'DEFAULT_BRANCH="$DRIFT_DEFAULT_BRANCH"\n\n'
        'if [ -d "$DRIFT_REPO_DIR/.git" ]; then\n'
        '    git -C "$DRIFT_REPO_DIR" fetch --quiet origin \\\n'
        '      "+refs/heads/${DEFAULT_BRANCH}:refs/remotes/origin/${DEFAULT_BRANCH}"\n'
        '    git -C "$DRIFT_REPO_DIR" checkout --force --detach "origin/${DEFAULT_BRANCH}"\n'
        'else\n'
        '    if [ -e "$DRIFT_REPO_DIR" ]; then\n'
        '        rmdir "$DRIFT_REPO_DIR" || {\n'
        '            echo "ERROR: Workspace exists but is not an empty directory or Git clone." >&2\n'
        '            exit 1\n'
        '        }\n'
        '    fi\n\n'
        '    git clone --depth 1 --branch "$DEFAULT_BRANCH" "$REPO_URL" "$DRIFT_REPO_DIR"\n'
        'fi\n'
    )
    if old_clone in text:
        text = replace_once(text, old_clone, new_clone, "Drift Watcher repository clone/fetch block")
    elif 'REPO_URL="$DRIFT_REPO_URL"' not in text or 'git clone --depth 1 --branch "$DEFAULT_BRANCH"' not in text:
        raise RuntimeError("drift-watcher/entrypoint.sh: unknown clone/fetch logic; refusing to modify it.")
    return text


def rewrite_dockerfile(text: str) -> str:
    old = '    DRIFT_REPO_DIR=/work/project-lab\n\nLABEL org.opencontainers.image.source="https://github.com/saeed0000000/project-lab"\n'
    if old in text:
        return replace_once(text, old, '    DRIFT_REPO_DIR=/work/repository\n', "Drift Watcher Dockerfile repository defaults")
    if 'DRIFT_REPO_DIR=/work/repository' in text and 'saeed0000000/project-lab' not in text:
        return text
    raise RuntimeError("drift-watcher/Dockerfile: unknown repository-specific defaults; refusing to modify it.")


def rewrite_watcher_python(text: str) -> str:
    original_constant = 'REPO = "saeed0000000/project-lab"\nMISSING = object()\n'
    if original_constant not in text:
        if 'def repository_slug() -> str:' in text and 'DEFAULT_BRANCH = os.environ.get("DRIFT_DEFAULT_BRANCH", "").strip()' in text:
            return text
        raise RuntimeError(".github/scripts/drift-pr-watcher.py: unknown repository configuration; refusing to modify it.")
    text = replace_once(
        text,
        original_constant,
        'REPO = os.environ.get("DRIFT_GITHUB_REPOSITORY", "").strip()\n'
        'DEFAULT_BRANCH = os.environ.get("DRIFT_DEFAULT_BRANCH", "").strip()\n'
        'MISSING = object()\n',
        "Drift Watcher repository constants",
    )
    text = replace_once(
        text,
        'def split_documents(text: str) -> list[str]:\n',
        r'''def repository_slug() -> str:
    """Return owner/repository from the Deployment or origin remote."""
    global REPO
    if REPO:
        return REPO
    remote = run(["git", "remote", "get-url", "origin"], capture=True).stdout.strip()
    match = re.search(r"(?:github\.com[:/])([^/\s]+/[^/\s]+?)(?:\.git)?$", remote)
    if not match:
        raise RuntimeError(f"Cannot determine GitHub owner/repository from origin URL: {remote!r}")
    REPO = match.group(1).removesuffix(".git")
    return REPO


def default_branch() -> str:
    """Return the configured branch or discover origin's default branch from GitHub."""
    global DEFAULT_BRANCH
    if DEFAULT_BRANCH:
        return DEFAULT_BRANCH
    result = run(["git", "ls-remote", "--symref", "origin", "HEAD"], capture=True)
    for line in result.stdout.splitlines():
        match = re.match(r"ref: refs/heads/(.+)\s+HEAD$", line.strip())
        if match:
            DEFAULT_BRANCH = match.group(1)
            return DEFAULT_BRANCH
    raise RuntimeError("Unable to discover the default branch for origin.")


def fetch_default_branch() -> None:
    branch = default_branch()
    run(["git", "fetch", "--quiet", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}"])


def split_documents(text: str) -> list[str]:
''',
        "dynamic repository and branch helpers",
    )
    replacements = (
        (
            'return run(["git", "show", f"origin/main:{path}"], capture=True).stdout\n',
            'return run(["git", "show", f"origin/{default_branch()}:{path}"], capture=True).stdout\n',
            "Git manifest reference",
        ),
        (
            '        raise RuntimeError(f"{target[\'kind\']}/{target[\'name\']} not found in origin/main:{target[\'manifest\']}")\n',
            '        raise RuntimeError(f"{target[\'kind\']}/{target[\'name\']} not found in origin/{default_branch()}:{target[\'manifest\']}")\n',
            "policy error message",
        ),
        (
            '        "gh", "pr", "list", "--repo", REPO, "--state", "open",\n',
            '        "gh", "pr", "list", "--repo", repository_slug(), "--state", "open",\n',
            "GitHub PR lookup repository",
        ),
        (
            '        run(["git", "clone", "--quiet", "--branch", "main", "--single-branch", remote, str(clone)])\n',
            '        run(["git", "clone", "--quiet", "--branch", default_branch(), "--single-branch", remote, str(clone)])\n',
            "Git clone branch",
        ),
        (
            '            "gh", "pr", "create", "--repo", REPO, "--base", "main", "--head", branch,\n',
            '            "gh", "pr", "create", "--repo", repository_slug(), "--base", default_branch(), "--head", branch,\n',
            "GitHub PR base branch",
        ),
    )
    for old, new, label in replacements:
        text = replace_once(text, old, new, label)
    text = text.replace(
        '    run(["git", "fetch", "--quiet", "origin", "main"])\n',
        '    fetch_default_branch()\n',
    )
    leftovers = [
        line for line in text.splitlines()
        if "origin/main" in line
        or '"--branch", "main"' in line
        or '"--base", "main"' in line
        or '"gh", "pr", "list", "--repo", REPO' in line
        or '"gh", "pr", "create", "--repo", REPO' in line
    ]
    if leftovers:
        raise RuntimeError("Hard-coded repository/default-branch references remain in drift-pr-watcher.py:\n" + "\n".join(leftovers))
    text = replace_once(
        text,
        '    run(["gh", "auth", "status"])\n    fetch_default_branch()\n',
        '    run(["gh", "auth", "status"])\n'
        '    print(f"Drift watcher repository: {repository_slug()} (default branch: {default_branch()})", flush=True)\n'
        '    fetch_default_branch()\n',
        "Drift Watcher startup diagnostics",
    )
    return text


def rewrite_drift_workflow() -> str:
    return r'''name: Build Drift Watcher Image

on:
  push:
    branches:
      - "**"
    paths:
      - ".dockerignore"
      - "drift-watcher/**"
      - "argocd/**"
      - ".github/scripts/prepare-repository-portability.py"
      - ".github/scripts/drift-pr-watcher.py"
      - ".github/scripts/drift-watch-policy.yaml"
      - ".github/workflows/drift-watcher-image.yml"

  workflow_dispatch:

permissions:
  contents: read
  packages: write

jobs:
  build:
    name: Build and publish image
    runs-on: ubuntu-latest
    if: github.ref == format('refs/heads/{0}', github.event.repository.default_branch)

    steps:
      - name: Checkout repository
        uses: actions/checkout@v7
        with:
          persist-credentials: false

      - name: Validate GitHub App configuration
        shell: bash
        env:
          APP_CLIENT_ID: ${{ vars.APP_CLIENT_ID }}
          APP_PRIVATE_KEY: ${{ secrets.APP_PRIVATE_KEY }}
        run: |
          set -euo pipefail
          [[ -n "$APP_CLIENT_ID" ]] || {
            echo "::error::Missing repository/org variable APP_CLIENT_ID."
            exit 1
          }
          [[ -n "$APP_PRIVATE_KEY" ]] || {
            echo "::error::Missing repository/org secret APP_PRIVATE_KEY."
            exit 1
          }

      - name: Create GitHub App token for digest-update pull request
        id: app-token
        uses: actions/create-github-app-token@64ab729a6294000f5aa4a4bc0b73d1f32bc45510 # v3
        with:
          client-id: ${{ vars.APP_CLIENT_ID }}
          private-key: ${{ secrets.APP_PRIVATE_KEY }}
          owner: ${{ github.repository_owner }}
          repositories: ${{ github.event.repository.name }}
          permission-contents: write
          permission-pull-requests: write

      - name: Set image name
        id: image
        shell: bash
        run: |
          set -euo pipefail
          REPO_NAME="${GITHUB_REPOSITORY#*/}"
          echo "name=ghcr.io/${GITHUB_REPOSITORY_OWNER,,}/${REPO_NAME,,}-drift-watcher" >> "$GITHUB_OUTPUT"

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v4

      - name: Log in to GHCR
        uses: docker/login-action@v4
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build and push image
        id: build
        uses: docker/build-push-action@v7
        with:
          context: .
          file: drift-watcher/Dockerfile
          platforms: linux/amd64
          push: true
          tags: |
            ${{ steps.image.outputs.name }}:sha-${{ github.sha }}
            ${{ steps.image.outputs.name }}:latest
          labels: |
            org.opencontainers.image.source=https://github.com/${{ github.repository }}
            org.opencontainers.image.revision=${{ github.sha }}
          sbom: true
          provenance: mode=max

      - name: Verify and report image digest
        shell: bash
        env:
          IMAGE: ${{ steps.image.outputs.name }}
          DIGEST: ${{ steps.build.outputs.digest }}
        run: |
          set -euo pipefail
          [[ "$DIGEST" =~ ^sha256:[a-f0-9]{64}$ ]] || {
            echo "ERROR: Invalid Drift Watcher image digest: $DIGEST"
            exit 1
          }
          {
            echo "## Drift watcher image published"
            echo
            echo 'Immutable image reference:'
            echo
            printf '  `%s`\n' "${IMAGE}@${DIGEST}"
          } >> "$GITHUB_STEP_SUMMARY"

      - name: Update deployment to the published immutable digest
        shell: bash
        env:
          IMAGE: ${{ steps.image.outputs.name }}
          DIGEST: ${{ steps.build.outputs.digest }}
        run: |
          set -euo pipefail
          [[ "$DIGEST" =~ ^sha256:[a-f0-9]{64}$ ]]
          manifest=k8s/drift-watcher-deployment.yaml
          matches="$(grep -Ec '^[[:space:]]*image:[[:space:]]*ghcr\.io/[^[:space:]]+-drift-watcher@sha256:[0-9a-f]{64}[[:space:]]*$' "$manifest" || true)"
          [[ "$matches" -eq 1 ]] || {
            echo "ERROR: Expected exactly one pinned Drift Watcher image in $manifest; found $matches."
            exit 1
          }
          sed -i -E \
            "s|^([[:space:]]*image:[[:space:]]*)ghcr\.io/[^[:space:]]+-drift-watcher@sha256:[0-9a-f]{64}([[:space:]]*)$|\1${IMAGE}@${DIGEST}\2|" \
            "$manifest"
          git diff --check
          bash .github/scripts/verify-image-digests.sh

      - name: Open pull request to pin Drift Watcher image digest
        id: digest-pr
        uses: peter-evans/create-pull-request@5f6978faf089d4d20b00c7766989d076bb2fc7f1 # v8
        with:
          token: ${{ steps.app-token.outputs.token }}
          branch: automation/pin-drift-watcher-${{ github.sha }}
          base: ${{ github.event.repository.default_branch }}
          delete-branch: true
          add-paths: k8s/drift-watcher-deployment.yaml
          commit-message: "chore(gitops): pin Drift Watcher image digest"
          title: "chore(gitops): pin Drift Watcher image digest"
          body: |
            ## Immutable Drift Watcher image update

            This pull request updates `k8s/drift-watcher-deployment.yaml` to the image digest built and published by this workflow.

            - Repository: `${{ github.repository }}`
            - Source commit: `${{ github.sha }}`
            - Image: `${{ steps.image.outputs.name }}@${{ steps.build.outputs.digest }}`

            Review the manifest diff and required CI checks before merging. The deployment remains digest-pinned.

      - name: Report digest pull request
        if: steps.digest-pr.outputs.pull-request-url != ''
        env:
          PR_URL: ${{ steps.digest-pr.outputs.pull-request-url }}
        run: |
          echo "Drift Watcher digest update PR: ${PR_URL}" >> "$GITHUB_STEP_SUMMARY"
'''


def update_files(root: Path, slug: str, branch: str) -> dict[Path, bytes]:
    owner, repo_name = slug.split("/", 1)
    repo_url = f"https://github.com/{slug}.git"
    changes: dict[Path, bytes] = {}

    for rel in (Path("argocd/root-app.yaml"), Path("argocd/apps/project-app.yaml"), Path("argocd/apps/argocd-platform-app.yaml")):
        path = root / rel
        before = path.read_text(encoding="utf-8")
        after = update_app_manifest(rel, before, repo_url, branch)
        if after != before:
            changes[rel] = after.encode("utf-8")

    readme_rel = Path("argocd/README.md")
    readme = (root / readme_rel).read_text(encoding="utf-8")
    readme_new = readme.replace(
        "After the GitOps configuration has been merged into `main`,",
        "After the GitOps configuration has been merged into the repository's default branch,",
    ).replace(
        "from `argocd/apps` on the `main` branch.",
        "from `argocd/apps` on the repository's default branch.",
    )
    if "## Repository portability" not in readme_new:
        readme_new = readme_new.rstrip() + (
            "\n\n## Repository portability\n\n"
            "When copying this project to a different GitHub repository, run "
            "`python3 .github/scripts/prepare-repository-portability.py` from a clean clone. "
            "The helper discovers the `origin` URL and default branch and updates the Argo CD "
            "Applications and Drift Watcher configuration. Commit the changes through a pull request.\n\n"
            "The Drift Watcher image remains pinned by SHA256. After the source changes merge into "
            "the default branch, the image workflow builds the repository-specific image and opens a "
            "separate pull request to update its digest. Merge that digest-update PR before bootstrapping "
            "Argo CD in a new repository. Private repositories also require repository credentials "
            "to be configured in Argo CD.\n"
        )
    if readme_new != readme:
        changes[readme_rel] = readme_new.encode("utf-8")

    deployment_rel = Path("k8s/drift-watcher-deployment.yaml")
    deployment_before = (root / deployment_rel).read_text(encoding="utf-8")
    deployment_after = rewrite_deployment(deployment_before, slug, repo_url, branch)
    if deployment_after != deployment_before:
        changes[deployment_rel] = deployment_after.encode("utf-8")

    entry_rel = Path("drift-watcher/entrypoint.sh")
    entry_before = (root / entry_rel).read_text(encoding="utf-8")
    entry_after = rewrite_entrypoint(entry_before)
    if entry_after != entry_before:
        changes[entry_rel] = entry_after.encode("utf-8")

    docker_rel = Path("drift-watcher/Dockerfile")
    docker_before = (root / docker_rel).read_text(encoding="utf-8")
    docker_after = rewrite_dockerfile(docker_before)
    if docker_after != docker_before:
        changes[docker_rel] = docker_after.encode("utf-8")

    py_rel = Path(".github/scripts/drift-pr-watcher.py")
    py_before = (root / py_rel).read_text(encoding="utf-8")
    py_after = rewrite_watcher_python(py_before)
    if py_after != py_before:
        changes[py_rel] = py_after.encode("utf-8")

    workflow_rel = Path(".github/workflows/drift-watcher-image.yml")
    workflow_before = (root / workflow_rel).read_text(encoding="utf-8")
    workflow_after = rewrite_drift_workflow()
    if workflow_after != workflow_before:
        changes[workflow_rel] = workflow_after.encode("utf-8")

    return changes


def validate(root: Path) -> None:
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise RuntimeError("PyYAML is required. Install it with: python3 -m pip install PyYAML") from exc

    workflow_files = sorted((root / ".github/workflows").glob("*.yml")) + sorted((root / ".github/workflows").glob("*.yaml"))
    for path in workflow_files:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        jobs = document.get("jobs", {}) if isinstance(document, dict) else {}
        for job in jobs.values():
            for step in job.get("steps", []) if isinstance(job, dict) else []:
                if isinstance(step, dict) and isinstance(step.get("run"), str):
                    with tempfile_runfile(step["run"]) as temp_script:
                        run(["bash", "-n", str(temp_script)], cwd=root)
        print(f"PASS YAML and run-step shell syntax: {path.relative_to(root)}")

    for path in list((root / "argocd").rglob("*.yaml")) + list((root / "k8s").rglob("*.yaml")):
        list(yaml.safe_load_all(path.read_text(encoding="utf-8")))

    for rel in (Path("drift-watcher/entrypoint.sh"), Path(".github/scripts/verify-image-digests.sh"), Path(".github/scripts/smoke-test.sh")):
        run(["bash", "-n", str(rel)], cwd=root)
        print(f"PASS Bash syntax: {rel}")

    for rel in (Path(".github/scripts/drift-pr-watcher.py"), HELPER_PATH):
        compile((root / rel).read_text(encoding="utf-8"), str(rel), "exec")
        print(f"PASS Python syntax: {rel}")

    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", ".github/scripts/tests", "-p", "test_*.py", "-v"],
        cwd=str(root), env=env, check=True,
    )
    print("PASS repository unit tests")

    run(["git", "diff", "--check"], cwd=root)
    print("PASS git diff --check")
    if shutil.which("actionlint"):
        run(["actionlint"], cwd=root)
        print("PASS actionlint")
    else:
        print("NOTE: actionlint is not installed locally; GitHub Actions CI will validate workflow semantics.")


class tempfile_runfile:
    """Small context manager for shell run blocks validated with bash -n."""
    def __init__(self, contents: str):
        self.contents = contents
        self.path: Path | None = None

    def __enter__(self) -> Path:
        import tempfile
        fd, name = tempfile.mkstemp(prefix="workflow-run-", suffix=".sh")
        os.write(fd, self.contents.encode("utf-8"))
        os.close(fd)
        self.path = Path(name)
        return self.path

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.path:
            self.path.unlink(missing_ok=True)


def main() -> int:
    root = Path(run(["git", "rev-parse", "--show-toplevel"], cwd=Path.cwd(), capture=True)).resolve()
    status = run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=root, capture=True)
    if status:
        print("ERROR: Working tree must be clean. Commit/stash other work first:\n" + status, file=sys.stderr)
        return 2

    remote = run(["git", "remote", "get-url", "origin"], cwd=root, capture=True)
    slug = detect_repo_slug(remote)
    branch = detect_default_branch(root)
    repo_url = f"https://github.com/{slug}.git"
    print(f"Repository: {slug}\nDefault branch: {branch}\nArgo CD repoURL: {repo_url}")

    try:
        changes = update_files(root, slug, branch)
        helper_dst = root / HELPER_PATH
        helper_bytes = Path(__file__).resolve().read_bytes()
        if not helper_dst.exists() or helper_dst.read_bytes() != helper_bytes:
            changes[HELPER_PATH] = helper_bytes

        originals: dict[Path, bytes | None] = {}
        for rel, content in changes.items():
            target = root / rel
            originals[rel] = target.read_bytes() if target.exists() else None
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        try:
            validate(root)
        except Exception:
            for rel, old_content in originals.items():
                target = root / rel
                if old_content is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(old_content)
            raise

        if changes:
            print("\nApplied changes:")
            for rel in sorted(changes, key=str):
                print(f"  UPDATED {rel}")
        else:
            print("\nNo changes were necessary; repository config is already portable.")
        print("\nNot committed or pushed. Review with: GIT_PAGER=cat git diff")
        print("The image workflow now creates a separate PR to update the Drift Watcher image digest. Merge that PR before bootstrapping Argo CD in a newly copied repository.")
        print("Private GitHub repositories still require credentials to be configured in Argo CD.")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
