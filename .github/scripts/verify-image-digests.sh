#!/usr/bin/env bash

set -euo pipefail

bad=0
found=0

while IFS= read -r -d '' file; do
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
  done < <(
    grep -nE '^[[:space:]]*image:[[:space:]]*' "$file" || true
  )
done < <(
  find k8s -type f \( -name '*.yaml' -o -name '*.yml' \) -print0
)

if [[ "$found" -eq 0 ]]; then
  echo "ERROR: no Kubernetes image references were found."
  exit 1
fi

if [[ "$bad" -ne 0 ]]; then
  echo "Image digest policy: FAILED"
  exit 1
fi

echo "Image digest policy: PASSED"
echo "All Kubernetes image references are pinned by SHA256 digest."
