#!/usr/bin/env python3
"""Allow-list based Kubernetes drift watcher that opens GitHub PRs."""
from __future__ import annotations

import hashlib
import json
import queue
import re
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
POLICY_FILE = Path(__file__).with_name("drift-watch-policy.yaml")
REPO = "saeed0000000/project-lab"
MISSING = object()
ACTIVE_WATCHES: list[subprocess.Popen[str]] = []
ACTIVE_WATCHES_LOCK = threading.Lock()


def run(args: list[str], *, cwd: Path = ROOT, capture: bool = False,
        check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, cwd=str(cwd), text=True, check=check,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def split_documents(text: str) -> list[str]:
    documents: list[str] = []
    current: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.strip() == "---":
            if current:
                documents.append("".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        documents.append("".join(current))
    return documents


def matches_target(obj: Any, target: dict[str, Any]) -> bool:
    if not isinstance(obj, dict):
        return False
    metadata = obj.get("metadata") or {}
    return (
        obj.get("kind") == target["kind"]
        and metadata.get("name") == target["name"]
        and metadata.get("namespace", "default") == target.get("namespace", "default")
    )


def find_target_document(text: str, target: dict[str, Any]) -> dict[str, Any] | None:
    for document in split_documents(text):
        try:
            obj = yaml.safe_load(document)
        except yaml.YAMLError as exc:
            raise RuntimeError(f"Invalid YAML in {target['manifest']}: {exc}") from exc
        if matches_target(obj, target):
            return obj
    return None


def get_path(obj: Any, dotted_path: str) -> Any:
    value = obj
    for part in dotted_path.split("."):
        if not isinstance(value, dict) or part not in value:
            return MISSING
        value = value[part]
    return value


def read_git_manifest(path: str) -> str:
    return run(["git", "show", f"origin/main:{path}"], capture=True).stdout


def get_git_field(target: dict[str, Any], field: str) -> Any:
    text = read_git_manifest(target["manifest"])
    obj = find_target_document(text, target)
    if obj is None:
        return MISSING
    return get_path(obj, field)


def scalar_text(value: Any, field: str) -> str:
    if field == "spec.replicas":
        if type(value) is not int or value < 0:
            raise ValueError("spec.replicas must be a non-negative integer")
        return str(value)
    if field.startswith("data."):
        if not isinstance(value, str) or not value or "\n" in value or "\r" in value:
            raise ValueError(f"{field} must be a non-empty single-line string")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", value) and value.lower() not in {
            "null", "true", "false", "yes", "no", "on", "off"
        }:
            return value
        return json.dumps(value)
    raise ValueError(f"Unsupported field for auto-update: {field}")


def set_manifest_field(text: str, target: dict[str, Any], field: str, value: Any) -> str:
    if target["kind"] == "ConfigMap" and field.startswith("data.") and len(field.split(".")) == 2:
        parent_key, leaf_key = "data", field.split(".")[1]
    elif target["kind"] == "Deployment" and field == "spec.replicas":
        parent_key, leaf_key = "spec", "replicas"
    else:
        raise ValueError(f"Field {field!r} is not supported for {target['kind']}")

    docs = split_documents(text)
    for doc_index, document in enumerate(docs):
        try:
            obj = yaml.safe_load(document)
        except yaml.YAMLError:
            continue
        if not matches_target(obj, target):
            continue

        lines = document.splitlines(keepends=True)
        parent_pattern = re.compile(rf"^( *){re.escape(parent_key)}\s*:\s*(?:#.*)?(?:\r?\n)?$")
        parent_index = None
        parent_indent = None
        for i, line in enumerate(lines):
            match = parent_pattern.match(line)
            if match and len(match.group(1)) == 0:
                parent_index, parent_indent = i, len(match.group(1))
                break
        if parent_index is None or parent_indent is None:
            raise RuntimeError(f"Cannot locate top-level {parent_key}: in {target['manifest']}")

        block_end = len(lines)
        for i in range(parent_index + 1, len(lines)):
            stripped = lines[i].rstrip("\r\n")
            if not stripped.strip() or stripped.lstrip().startswith("#"):
                continue
            indent = len(stripped) - len(stripped.lstrip(" "))
            if indent <= parent_indent:
                block_end = i
                break

        child_indent = parent_indent + 2
        child_pattern = re.compile(rf"^ {{{child_indent}}}{re.escape(leaf_key)}\s*:")
        child_index = None
        for i in range(parent_index + 1, block_end):
            if child_pattern.match(lines[i]):
                child_index = i
                break
        if child_index is None:
            raise RuntimeError(
                f"Cannot locate existing field {field} in {target['manifest']}; refusing to insert it implicitly"
            )

        ending = "\r\n" if lines[child_index].endswith("\r\n") else "\n" if lines[child_index].endswith("\n") else ""
        lines[child_index] = f"{' ' * child_indent}{leaf_key}: {scalar_text(value, field)}{ending}"
        docs[doc_index] = "".join(lines)
        return "".join(docs)

    raise RuntimeError(f"Manifest {target['manifest']} has no {target['kind']}/{target['name']} document")


def validate_policy(policy: Any) -> list[dict[str, Any]]:
    if not isinstance(policy, dict) or not isinstance(policy.get("targets"), list):
        raise RuntimeError(f"{POLICY_FILE} must contain a targets list")
    targets = policy["targets"]
    if not targets:
        raise RuntimeError("Policy has no targets configured")

    for target in targets:
        for key in ("kind", "name", "namespace", "manifest", "fields"):
            if key not in target:
                raise RuntimeError(f"Policy target is missing {key!r}: {target}")
        if target["kind"] not in {"ConfigMap", "Deployment"}:
            raise RuntimeError(f"Unsupported target kind: {target['kind']}")
        if not isinstance(target["fields"], list) or not target["fields"]:
            raise RuntimeError(f"Target {target['name']} needs a non-empty fields list")
        for field in target["fields"]:
            allowed = (
                target["kind"] == "ConfigMap" and field.startswith("data.") and len(field.split(".")) == 2
            ) or (target["kind"] == "Deployment" and field == "spec.replicas")
            if not allowed:
                raise RuntimeError(
                    f"Field {field!r} is not allowed for {target['kind']}; supported fields are ConfigMap data.<key> and Deployment spec.replicas"
                )
        manifest = Path(target["manifest"])
        if manifest.is_absolute() or ".." in manifest.parts:
            raise RuntimeError(f"Manifest path must be repository-relative: {target['manifest']}")
        text = read_git_manifest(target["manifest"])
        obj = find_target_document(text, target)
        if obj is None:
            raise RuntimeError(f"{target['kind']}/{target['name']} not found in origin/main:{target['manifest']}")
        for field in target["fields"]:
            if get_path(obj, field) is MISSING:
                raise RuntimeError(f"Configured field {field!r} is missing in {target['manifest']}")
    return targets


def signature_for(target: dict[str, Any], changes: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"kind": target["kind"], "namespace": target["namespace"],
         "name": target["name"], "changes": changes},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:10]


def create_pr(target: dict[str, Any], changes: dict[str, Any]) -> bool:
    suffix = signature_for(target, changes)
    slug = re.sub(r"[^a-z0-9-]+", "-", f"{target['kind']}-{target['name']}".lower()).strip("-")
    branch_base = f"automation/drift-{slug}-{suffix}"

    # Suppress duplicate OPEN PRs for the same proposed field values.
    # A closed/merged historical PR must not block a future proposal.
    previous_prs = run([
        "gh", "pr", "list", "--repo", REPO, "--state", "open",
        "--json", "headRefName,url", "--limit", "100",
    ], capture=True)
    for pr in json.loads(previous_prs.stdout):
        existing_branch = pr.get("headRefName", "")
        if existing_branch == branch_base or existing_branch.startswith(branch_base + "-"):
            print(f"Open PR already exists: {pr.get('url')}", flush=True)
            return True

    # Use a unique branch for each new proposal, avoiding old closed branch refs.
    branch = f"{branch_base}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"

    remote = run(["git", "remote", "get-url", "origin"], capture=True).stdout.strip()
    with tempfile.TemporaryDirectory(prefix="drift-pr-") as tmp:
        clone = Path(tmp) / "repo"
        run(["git", "clone", "--quiet", "--branch", "main", "--single-branch", remote, str(clone)])
        run(["git", "switch", "-c", branch], cwd=clone)
        manifest_path = clone / target["manifest"]
        manifest_text = manifest_path.read_text(encoding="utf-8")

        actual_changes: dict[str, Any] = {}
        for field, desired_value in changes.items():
            current_doc = find_target_document(manifest_text, target)
            if current_doc is None:
                raise RuntimeError(f"Could not find target in cloned main: {target['name']}")
            current_value = get_path(current_doc, field)
            if current_value is MISSING:
                raise RuntimeError(f"Field {field} no longer exists in main")
            if current_value != desired_value:
                manifest_text = set_manifest_field(manifest_text, target, field, desired_value)
                actual_changes[field] = {"from": current_value, "to": desired_value}

        if not actual_changes:
            print("Ignored: latest main already contains the observed value.", flush=True)
            return False

        manifest_path.write_text(manifest_text, encoding="utf-8")
        run(["git", "diff", "--check"], cwd=clone)
        run(["git", "add", "--", target["manifest"]], cwd=clone)
        staged = run(["git", "diff", "--cached", "--quiet"], cwd=clone, check=False)
        if staged.returncode == 0:
            print("Ignored: no manifest diff after normalization.", flush=True)
            return False

        run(["git", "config", "user.name", "Kubernetes Drift Watcher"], cwd=clone)
        run(["git", "config", "user.email", "kubernetes-drift-watcher@users.noreply.github.com"], cwd=clone)
        run(["git", "commit", "-m", f"gitops: capture drift in {target['kind']}/{target['name']}"], cwd=clone)
        run(["git", "push", "-u", "origin", branch], cwd=clone)

        lines = [
            "## Observed Kubernetes drift",
            "",
            f"Observed resource: `{target['kind']}/{target['name']}` in namespace `{target['namespace']}`.",
            "",
            "Only explicitly allow-listed fields are proposed. Review the manifest diff before merging.",
            "",
            "| Field | Git before | Observed value |",
            "|---|---|---|",
        ]
        for field, values in actual_changes.items():
            lines.append(f"| `{field}` | `{values['from']}` | `{values['to']}` |")
        lines.extend(["", "Argo CD remains the reconciler. This PR does not modify `main` directly."])
        result = run([
            "gh", "pr", "create", "--repo", REPO, "--base", "main", "--head", branch,
            "--title", f"gitops: capture drift in {target['kind']}/{target['name']}",
            "--body", "\n".join(lines),
        ], cwd=clone, capture=True)
        print(f"Created PR: {result.stdout.strip()}", flush=True)
    return True


def process_event(target: dict[str, Any], event: dict[str, Any], proposed: set[str]) -> None:
    event_type = event.get("type", "OBJECT")
    if event_type not in {"ADDED", "MODIFIED"}:
        return
    obj = event.get("object", event)
    if not matches_target(obj, target):
        return

    run(["git", "fetch", "--quiet", "origin", "main"])
    differences: dict[str, Any] = {}
    for field in target["fields"]:
        live_value = get_path(obj, field)
        git_value = get_git_field(target, field)
        if live_value is MISSING or git_value is MISSING:
            print(f"Ignoring {field}: field absent in live resource or Git.", flush=True)
            continue
        if live_value != git_value:
            # Validate supported scalar types before any value can be written to Git.
            scalar_text(live_value, field)
            differences[field] = live_value

    if not differences:
        print(f"Ignored {event_type}: {target['kind']}/{target['name']} matches allow-listed Git fields.", flush=True)
        return

    signature = signature_for(target, differences)
    if signature in proposed:
        return
    print(f"Detected drift in {target['kind']}/{target['name']}: {list(differences)}", flush=True)
    if create_pr(target, differences):
        proposed.add(signature)


def watch_target(target: dict[str, Any], events: queue.Queue, stop: threading.Event) -> None:
    ref = f"{target['kind'].lower()}/{target['name']}"
    namespace = target["namespace"]
    while not stop.is_set():
        command = [
            "kubectl", "get", ref, "-n", namespace,
            "--watch", "--output-watch-events", "-o", "json",
        ]
        try:
            process = subprocess.Popen(command, cwd=str(ROOT), stdout=subprocess.PIPE,
                                       text=True, bufsize=1)
            with ACTIVE_WATCHES_LOCK:
                ACTIVE_WATCHES.append(process)
            try:
                decoder = json.JSONDecoder()
                buffer = ""
                assert process.stdout is not None
                for chunk in process.stdout:
                    if stop.is_set():
                        break
                    buffer += chunk
                    while buffer.strip():
                        stripped = buffer.lstrip()
                        try:
                            event, consumed = decoder.raw_decode(stripped)
                        except json.JSONDecodeError:
                            break
                        buffer = stripped[consumed:]
                        if isinstance(event, dict):
                            events.put((target, event))
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                with ACTIVE_WATCHES_LOCK:
                    if process in ACTIVE_WATCHES:
                        ACTIVE_WATCHES.remove(process)
        except OSError as exc:
            print(f"Watch error for {ref}: {exc}", flush=True)
        if not stop.is_set():
            print(f"Watch for {ref} stopped; reconnecting in 3 seconds.", flush=True)
            stop.wait(3)


def kubernetes_resource_ref(target: dict[str, Any]) -> str:
    resource = "configmaps" if target["kind"] == "ConfigMap" else "deployments"
    return f"{resource}/{target['name']}"


def main() -> None:
    if not (ROOT / ".git").exists():
        raise SystemExit(f"Run from the project repository; expected Git work tree at {ROOT}")
    run(["gh", "auth", "status"])
    run(["git", "fetch", "--quiet", "origin", "main"])
    policy = yaml.safe_load(POLICY_FILE.read_text(encoding="utf-8"))
    targets = validate_policy(policy)

    for target in targets:
        resource_ref = kubernetes_resource_ref(target)

        for verb in ("get", "watch"):
            allowed = run(
                [
                    "kubectl",
                    "auth",
                    "can-i",
                    verb,
                    resource_ref,
                    "-n",
                    target["namespace"],
                ],
                capture=True,
            ).stdout.strip()

            if allowed != "yes":
                raise SystemExit(
                    f"Missing permission to {verb} "
                    f"{resource_ref} in {target['namespace']}"
                )

        print(
            f"Watching {target['kind']}/{target['name']} in "
            f"{target['namespace']} for fields: "
            f"{', '.join(target['fields'])}",
            flush=True,
        )

    events: queue.Queue = queue.Queue()
    stop = threading.Event()
    threads = [threading.Thread(target=watch_target, args=(t, events, stop), daemon=True) for t in targets]
    for thread in threads:
        thread.start()

    proposed: set[str] = set()
    print("Drift watcher running. Press Ctrl+C to stop.", flush=True)
    try:
        while True:
            try:
                target, event = events.get(timeout=1)
            except queue.Empty:
                continue
            try:
                process_event(target, event, proposed)
            except Exception as exc:
                print(f"ERROR processing Kubernetes event: {exc}", flush=True)
                if isinstance(exc, subprocess.CalledProcessError):
                    if exc.stdout:
                        print(exc.stdout, flush=True)
                    if exc.stderr:
                        print(exc.stderr, flush=True)
    except KeyboardInterrupt:
        print("Stopping drift watcher...", flush=True)
        stop.set()
        with ACTIVE_WATCHES_LOCK:
            for process in list(ACTIVE_WATCHES):
                if process.poll() is None:
                    process.terminate()
        for thread in threads:
            thread.join(timeout=3)


if __name__ == "__main__":
    main()
