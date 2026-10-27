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

## Repository portability

When copying this project to a different GitHub repository, run `python3 .github/scripts/prepare-repository-portability.py` from a clean clone. The helper discovers the `origin` URL and default branch and updates the Argo CD Applications and Drift Watcher configuration. Commit the changes through a pull request.

The Drift Watcher image remains pinned by SHA256. After the source changes merge into the default branch, the image workflow builds the repository-specific image and opens a separate pull request to update its digest. Merge that digest-update PR before bootstrapping Argo CD in a new repository. Private repositories also require repository credentials to be configured in Argo CD.
