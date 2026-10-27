# Kubernetes and GitOps Incident Runbook

## 1. Purpose

This runbook provides repeatable procedures for diagnosing application
health issues, failed Kubernetes rollouts, Argo CD health problems, and
Drift Watcher startup failures.

Git is the source of truth for application manifests. Production changes
must be proposed through Pull Requests and reconciled by Argo CD.

## 2. Healthy Baseline

The expected state after the v1.1.1 release and Drift Watcher DNS fix:

| Component | Expected state |
|---|---|
| Argo CD application `project-app` | Synced / Healthy |
| Backend Deployment | 3/3 available |
| Frontend Deployment | 2/2 available |
| PostgreSQL StatefulSet | 1/1 ready |
| Drift Watcher Deployment | 1/1 available |
| Backend `/health` | HTTP success and status `ok` |
| Backend `/ready` | Status `ready`, database `ok` |
| Frontend `/` | HTTP 200 with a non-empty response |

Replica counts may change if the project's scaling configuration changes.

## 3. Initial Triage

Check the GitOps application:

```bash
kubectl -n argocd get application project-app
kubectl -n argocd describe application project-app


```

Check application workloads:

```bash
kubectl -n default get deployments,statefulset,pods
kubectl -n drift-watcher get deployments,pods
```

Inspect a failing Pod:

```bash
kubectl -n drift-watcher describe pod POD_NAME
kubectl -n drift-watcher logs POD_NAME --tail=100
kubectl -n drift-watcher logs POD_NAME --previous --tail=100
```

Replace `POD_NAME` with the actual Pod name. Previous-container logs may
be unavailable, so inspect current logs and events as well.

Do not infer application health solely from `kubectl rollout status`.
Also verify application endpoints and Argo CD health.

## 4. Post-Deployment Verification

Run the repository's smoke-test script:

```bash
bash .github/scripts/smoke-test.sh
```

Verify the deployed image references:

```bash
kubectl -n default get deployment backend \
  -o jsonpath='{.spec.template.spec.containers[?(@.name=="backend")].image}{"\n"}'

kubectl -n default get deployment frontend \
  -o jsonpath='{.spec.template.spec.containers[?(@.name=="frontend")].image}{"\n"}'
```

For the v1.1.1 release, the expected image names are
`project-lab-api:v1.1.1` and `project-lab-web:v1.1.1`, each pinned
to its corresponding immutable SHA-256 digest.

## 5. Drift Watcher CrashLoopBackOff

1. Inspect the Pod's last termination reason and exit code.
2. Read current and previous container logs when available.
3. Inspect Pod events and the current Deployment manifest.
4. Check DNS resolution and TLS verification from inside the cluster.
5. Verify that the GitHub Secret exists without printing its token.
6. Correct the root cause through Git and a reviewed Pull Request.

Do not assume every exit code 128 indicates the same Git failure. Use
the actual error message to identify the cause.

## 6. DNS and TLS Incident: Observed Root Cause

During the October 27, 2026 incident, the Drift Watcher Pod had a DNS
search configuration with `ndots:5` and the search suffix `domain.name`.

The Pod resolved GitHub to addresses presenting a self-signed certificate
with subject `CN=localhost` and issuer `comhostnew-proxy`. TLS verification
failed, causing the Git operation to exit with code 128.

A diagnostic Pod using `ndots:1` successfully resolved GitHub and
completed HTTPS certificate verification and `git ls-remote`.

The committed remediation is in:

`k8s/drift-watcher-deployment.yaml`

The Pod uses:

```yaml
dnsPolicy: ClusterFirst
dnsConfig:
  options:
    - name: ndots
      value: "1"
```

This preserves cluster DNS while changing the name-search behavior.
Treat this as the fix for the observed environment, not a universal
solution for every TLS or DNS failure.

## 7. Verification After the DNS Fix

```bash
kubectl -n drift-watcher rollout status deployment/drift-watcher

kubectl -n drift-watcher get deployment drift-watcher
kubectl -n drift-watcher logs deployment/drift-watcher --since=15m --tail=100

kubectl -n argocd get application project-app
```

The expected result is a ready Drift Watcher Pod without repeated
restarts and an Argo CD application in `Synced / Healthy` state.

## 8. Recovery and Change Control

- Keep Git as the source of truth.
- Correct manifests in a branch and open a Pull Request.
- Review the diff and required CI checks before merging.
- Allow Argo CD to reconcile the merged desired state.
- Verify rollout status, application endpoints, and Argo CD health.
- If a rollback is required, use a reviewed Git revert or a corrective
  Pull Request, then verify the resulting cluster state.

Do not use `kubectl set image` or manual Deployment patches as the
normal release process.

## 9. Security Guardrails

- Never disable TLS verification to work around certificate failures.
- Never print `GH_TOKEN` or other secret values in logs.
- Do not trust a certificate until its origin and trust chain are known.
- Keep application images pinned by digest.
- Keep Drift Watcher Kubernetes RBAC and GitHub permissions least-privileged.
- Remove temporary diagnostic Pods after the investigation is complete.

## 10. Incident Closure Checklist

- [ ] Root cause identified from evidence.
- [ ] Remediation committed through a reviewed Pull Request.
- [ ] Required CI checks passed.
- [ ] Argo CD is Synced and Healthy.
- [ ] Application smoke tests passed.
- [ ] Workloads are ready and stable.
- [ ] Temporary diagnostic resources removed.
- [ ] Incident findings documented.
