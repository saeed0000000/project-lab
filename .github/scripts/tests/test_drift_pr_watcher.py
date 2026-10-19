import importlib.util
import unittest
from pathlib import Path
from unittest import mock

import yaml

SCRIPT = Path(__file__).resolve().parents[1] / "drift-pr-watcher.py"

spec = importlib.util.spec_from_file_location("drift_pr_watcher", SCRIPT)
watcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watcher)


class DriftWatcherTests(unittest.TestCase):
    def setUp(self):
        self.configmap_target = {
            "kind": "ConfigMap",
            "namespace": "default",
            "name": "drift-lab",
            "manifest": "k8s/drift-lab.yaml",
            "fields": ["data.mode"],
        }

    def test_get_path_returns_nested_value(self):
        obj = {"data": {"mode": "baseline"}}
        self.assertEqual(watcher.get_path(obj, "data.mode"), "baseline")

    def test_get_path_marks_missing_field(self):
        self.assertIs(
            watcher.get_path({"data": {}}, "data.mode"),
            watcher.MISSING,
        )

    def test_matches_target_checks_kind_name_and_namespace(self):
        obj = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "drift-lab", "namespace": "default"},
        }
        self.assertTrue(watcher.matches_target(obj, self.configmap_target))

        obj["metadata"]["namespace"] = "other"
        self.assertFalse(watcher.matches_target(obj, self.configmap_target))

    def test_scalar_text_validates_configmap_values(self):
        self.assertEqual(watcher.scalar_text("manual-test", "data.mode"), "manual-test")
        self.assertEqual(
            yaml.safe_load(watcher.scalar_text("two words", "data.mode")),
            "two words",
        )

        with self.assertRaises(ValueError):
            watcher.scalar_text("", "data.mode")

        with self.assertRaises(ValueError):
            watcher.scalar_text("line1\nline2", "data.mode")

    def test_scalar_text_validates_replica_count(self):
        self.assertEqual(watcher.scalar_text(3, "spec.replicas"), "3")

        for value in (-1, True, 2.5, "3"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    watcher.scalar_text(value, "spec.replicas")

    def test_configmap_update_changes_only_allowlisted_field(self):
        manifest = """apiVersion: v1
kind: ConfigMap
metadata:
  name: unrelated
  namespace: default
data:
  mode: keep-me
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: drift-lab
  namespace: default
data:
  mode: baseline
  owner: git
"""
        updated = watcher.set_manifest_field(
            manifest, self.configmap_target, "data.mode", "v2-test"
        )
        docs = list(yaml.safe_load_all(updated))

        self.assertEqual(docs[0]["data"]["mode"], "keep-me")
        self.assertEqual(docs[1]["data"]["mode"], "v2-test")
        self.assertEqual(docs[1]["data"]["owner"], "git")

    def test_deployment_update_changes_only_replicas(self):
        manifest = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: replicas-lab
  namespace: default
spec:
  replicas: 2
  selector:
    matchLabels:
      app: replicas-lab
  template:
    metadata:
      labels:
        app: replicas-lab
    spec:
      containers:
        - name: nginx
          image: nginx
"""
        target = {
            "kind": "Deployment",
            "namespace": "default",
            "name": "replicas-lab",
            "manifest": "k8s/replicas-lab.yaml",
            "fields": ["spec.replicas"],
        }

        updated = watcher.set_manifest_field(
            manifest, target, "spec.replicas", 3
        )
        obj = yaml.safe_load(updated)

        self.assertEqual(obj["spec"]["replicas"], 3)
        self.assertEqual(
            obj["spec"]["template"]["spec"]["containers"][0]["image"],
            "nginx",
        )

    def test_refuses_to_insert_missing_field(self):
        manifest = """apiVersion: v1
kind: ConfigMap
metadata:
  name: drift-lab
  namespace: default
data:
  owner: git
"""
        with self.assertRaises(RuntimeError):
            watcher.set_manifest_field(
                manifest, self.configmap_target, "data.mode", "new-value"
            )

    def test_policy_rejects_non_allowlisted_field(self):
        policy = {
            "version": 1,
            "targets": [{
                **self.configmap_target,
                "fields": ["metadata.name"],
            }],
        }
        with self.assertRaises(RuntimeError):
            watcher.validate_policy(policy)

    def test_policy_accepts_existing_configmap_field(self):
        manifest = """apiVersion: v1
kind: ConfigMap
metadata:
  name: drift-lab
  namespace: default
data:
  mode: baseline
"""
        with mock.patch.object(
            watcher, "read_git_manifest", return_value=manifest
        ):
            targets = watcher.validate_policy({
                "version": 1,
                "targets": [self.configmap_target],
            })

        self.assertEqual(targets, [self.configmap_target])


if __name__ == "__main__":
    unittest.main()
