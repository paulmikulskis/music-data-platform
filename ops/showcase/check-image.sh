#!/usr/bin/env bash
# Read the final image filesystem, including artifacts unused by the current UI.
set -euo pipefail
image=${1:?Pass a showcase image, then its archived context directory.}
context=${2:?Pass the archived context directory.}
revision=$(docker image inspect "$image" --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}')
scratch=$(mktemp -d /tmp/sc-artifact-image.XXXXXX)
container=$(docker create "$image")
trap 'docker rm -v "$container" >/dev/null; rm -rf "$scratch"' EXIT
docker cp "$container:/app/apps/showcase/artifacts/." "$scratch/"
if [[ ! -f "$scratch/links.generated.json" ]]; then
  printf 'links links_entries
' >&2
  printf 'Rebuild the image with its link overlay. Read ops/showcase/README.md#recover.
' >&2
  exit 1
fi
uv run --project functions python ops/showcase/artifacts.py --context "$context" --image-payload "$scratch" --revision "$revision"
printf 'Image artifacts match their inputs. Open ops/showcase/README.md.\n'
