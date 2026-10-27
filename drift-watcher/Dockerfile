FROM python:3.12-slim-bookworm

ARG KUBECTL_VERSION=v1.37.0
ARG GH_VERSION=2.102.1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/watcher \
    GH_CONFIG_DIR=/home/watcher/.config/gh \
    DRIFT_REPO_DIR=/work/repository

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/*

# Install kubectl and verify its official SHA256 checksum.
RUN set -eux; \
    ARCH="$(dpkg --print-architecture)"; \
    case "$ARCH" in amd64|arm64) ;; \
      *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;; \
    esac; \
    curl -fsSLo /tmp/kubectl \
      "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${ARCH}/kubectl"; \
    curl -fsSLo /tmp/kubectl.sha256 \
      "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${ARCH}/kubectl.sha256"; \
    echo "$(cat /tmp/kubectl.sha256)  /tmp/kubectl" \
      | sha256sum --check -; \
    install -o root -g root -m 0755 /tmp/kubectl /usr/local/bin/kubectl; \
    rm -f /tmp/kubectl /tmp/kubectl.sha256

# Install GitHub CLI from its official release and verify its checksum.
RUN set -eux; \
    ARCH="$(dpkg --print-architecture)"; \
    cd /tmp; \
    curl -fsSLO \
      "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${ARCH}.tar.gz"; \
    curl -fsSLO \
      "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_checksums.txt"; \
    grep -F "  gh_${GH_VERSION}_linux_${ARCH}.tar.gz" \
      "gh_${GH_VERSION}_checksums.txt" > gh-checksum.txt; \
    sha256sum --check gh-checksum.txt; \
    tar -xzf "gh_${GH_VERSION}_linux_${ARCH}.tar.gz"; \
    install -o root -g root -m 0755 \
      "gh_${GH_VERSION}_linux_${ARCH}/bin/gh" /usr/local/bin/gh; \
    rm -rf /tmp/gh_${GH_VERSION}_linux_${ARCH} \
      /tmp/gh_${GH_VERSION}_linux_${ARCH}.tar.gz \
      /tmp/gh_${GH_VERSION}_checksums.txt /tmp/gh-checksum.txt

RUN python -m pip install \
      --no-cache-dir \
      --disable-pip-version-check \
      "PyYAML==6.0.3" \
    && useradd \
      --uid 10001 \
      --user-group \
      --create-home \
      --home-dir /home/watcher \
      --shell /usr/sbin/nologin \
      watcher \
    && install -d -o 10001 -g 10001 /opt/drift-watcher /work

COPY --chown=10001:10001 \
  .github/scripts/drift-pr-watcher.py \
  /opt/drift-watcher/drift-pr-watcher.py

COPY --chown=10001:10001 \
  .github/scripts/drift-watch-policy.yaml \
  /opt/drift-watcher/drift-watch-policy.yaml

COPY drift-watcher/entrypoint.sh /usr/local/bin/drift-watcher-entrypoint

RUN chmod 0555 /usr/local/bin/drift-watcher-entrypoint

USER 10001:10001

ENTRYPOINT ["/usr/local/bin/drift-watcher-entrypoint"]
