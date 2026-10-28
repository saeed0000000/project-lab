# Argo CD Bootstrap

Install Argo CD on the target Kubernetes cluster first.

After the GitOps configuration has been merged into the repository's default branch,
select the intended Kubernetes context and run `./argocd/bootstrap.sh`
from the repository root.

The script creates or updates the `root-app` Application.
The Root Application then discovers and manages child Application
definitions from `argocd/apps` on the repository's default branch.

The bootstrap script does not install Argo CD itself.
If `argocd/root-app.yaml` changes later, rerun the script to apply
the updated bootstrap definition.
