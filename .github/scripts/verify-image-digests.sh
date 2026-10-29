#!/usr/bin/env bash

set -euo pipefail

bad=0
found=0
files=()

if (($# > 0)); then
  # Release workflows can pass the exact manifests they just updated.
  for file in "$@"; do
    if [[ ! -f "$file" ]]; then
      echo "ERROR: manifest file not found: $file"
      exit 1
    fi
    files+=("$file")
  done
else
  # Default behavior for CI: inspect every YAML manifest under k8s/.
  if [[ ! -d k8s ]]; then
    echo "ERROR: no manifest arguments supplied and k8s/ directory is missing."
    exit 1
  fi
  while IFS= read -r -d '' file; do
    files+=("$file")
  done < <(find k8s -type f \( -name '*.yaml' -o -name '*.yml' \) -print0)
fi

for file in "${files[@]}"; do
  while IFS= read -r line; do
    found=1
    line_number="${line%%:*}"
    content="${line#*:}"
    image="${content#*image:}"
    image="$(printf '%s' "$image" | sed 's/^[[:space:]]*//')"

    if [[ -z "$image" || "$image" == \#* ]]; then
      echo "ERROR: empty Kubernetes image reference"
      echo "  File: $file"
      echo "  Line: $line_number"
      echo
      bad=1
      continue
    fi

    if [[ ! "$image" =~ @sha256:[0-9a-fA-F]{64}([[:space:]]|#|$) ]]; then
      echo "ERROR: unpinned image reference"
      echo "  File:  $file"
      echo "  Line:  $line_number"
      echo "  Image: $image"
      echo
      bad=1
    fi
  done < <(grep -nE '^[[:space:]]*image:[[:space:]]*' "$file" || true)
done

if [[ "$found" -eq 0 ]]; then
  echo "ERROR: no Kubernetes image references were found in the selected manifest files."
  exit 1
fi

if [[ "$bad" -ne 0 ]]; then
  echo "Image digest policy: FAILED"
  exit 1
fi

echo "Image digest policy: PASSED"
echo "All selected Kubernetes image references are pinned by SHA256 digest."
