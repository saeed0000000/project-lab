# Kubernetes and GitOps Incident Runbook

## 1. Purpose

This runbook provides repeatable procedures for diagnosing application
health issues, failed Kubernetes rollouts, and Argo CD health problems.

Git is the source of truth for application manifests. Production changes
must be proposed through Pull Requests and reconciled by Argo CD.

## 2. Healthy Baseline

The expected state after the v1.1.1 release:

| Component | Expected state |
|---|---|
| Argo CD application `project-app` | Synced / Healthy |
| Backend Deployment | 3/3 available |
| Frontend Deployment | 2/2 available |
| PostgreSQL StatefulSet | 1/1 ready |
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
```


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

## 5. Recovery and Change Control

- Keep Git as the source of truth.
- Correct manifests in a branch and open a Pull Request.
- Review the diff and required CI checks before merging.
- Allow Argo CD to reconcile the merged desired state.
- Verify rollout status, application endpoints, and Argo CD health.
- If a rollback is required, use a reviewed Git revert or a corrective
  Pull Request, then verify the resulting cluster state.

Do not use `kubectl set image` or manual Deployment patches as the
normal release process.

## 6. Security Guardrails

- Never disable TLS verification to work around certificate failures.
- Never print `GH_TOKEN` or other secret values in logs.
- Do not trust a certificate until its origin and trust chain are known.
- Keep application images pinned by digest.
- Remove temporary diagnostic Pods after the investigation is complete.

## 7. Incident Closure Checklist

- [ ] Root cause identified from evidence.
- [ ] Remediation committed through a reviewed Pull Request.
- [ ] Required CI checks passed.
- [ ] Argo CD is Synced and Healthy.
- [ ] Application smoke tests passed.
- [ ] Workloads are ready and stable.
- [ ] Temporary diagnostic resources removed.
- [ ] Incident findings documented.
