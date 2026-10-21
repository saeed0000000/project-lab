# Finance DevOps Lab
A small full-stack financial dashboard designed specifically for DevOps/DevSecOps practice.
## Stack
- Frontend: React + Vite
- Backend: Node.js + Express
- Database: PostgreSQL
- Auth: JWT
- Containerization: Docker / Docker Compose
## Features
- User login
- Accounts and balances
- Transactions
- Dashboard totals
- Health and readiness endpoints
- PostgreSQL migrations and seed data
## Run locally
```bash
cp .env.example .env
docker compose up --build
```
Frontend: http://localhost:5173
API: http://localhost:3000
API health: http://localhost:3000/health
Demo user:
email: demo@finance.local
password: Demo123!
## Suggested DevOps exercises
1. Build separate production Docker images.
2. Add GitHub Actions CI.
3. Add image scanning with Trivy.
4. Add dependency scanning and secret scanning.
5. Deploy PostgreSQL and the two applications to Kubernetes.
6. Store secrets in Vault instead of .env.
7. Add Prometheus metrics and Grafana dashboards.
8. Add OpenTelemetry tracing.
9. Add Nginx/Ingress and TLS.
10. Add NetworkPolicies.
11. Add resource requests/limits and HPA.
12. Add PostgreSQL backup + PITR.
13. Add Velero for Kubernetes backups.
14. Add SAST/DAST stages to CI/CD.
15. Create staging and production overlays with Kustomize.
# project-lab
# project-lab


## GitOps Drift Watcher

This repository also contains a Kubernetes GitOps drift-detection lab.

The watcher monitors explicitly allow-listed Kubernetes resource fields and opens GitHub Pull Requests when live values differ from the manifests in Git. Argo CD remains responsible for reconciliation.

- [Architecture, validation results, and reproduction guide](docs/drift-watcher-architecture.md)
- [Drift watcher implementation](.github/scripts/drift-pr-watcher.py)
- [Monitoring policy](.github/scripts/drift-watch-policy.yaml)
- [Kubernetes Deployment](k8s/drift-watcher-deployment.yaml)
