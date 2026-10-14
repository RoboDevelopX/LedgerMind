#!/usr/bin/env bash
# Build the Ledgermind image, push it to Nebius Container Registry and run it as a Nebius AI Cloud serverless
# endpoint on a CPU-only VM. See docs/deploy_nebius.md for prerequisites and what each step does.
#
#   REGION=eu-north1 REGISTRY_PATH=<registry path> ./scripts/deploy_nebius.sh
#
# Optional: TAG (default: git short hash), NAME (default: ledgermind), PLATFORM (cpu-d3), PRESET (2vcpu-8gb),
# AUTH=token to require a bearer token, CURRENCY=USD.
set -euo pipefail

: "${REGION:?set REGION, e.g. eu-north1}"
: "${REGISTRY_PATH:?set REGISTRY_PATH (see docs/deploy_nebius.md, step 1)}"
NAME="${NAME:-ledgermind}"
TAG="${TAG:-$(git rev-parse --short HEAD)}"
PLATFORM="${PLATFORM:-cpu-d3}"
PRESET="${PRESET:-2vcpu-8gb}"
CURRENCY="${CURRENCY:-INR}"
IMAGE="cr.${REGION}.nebius.cloud/${REGISTRY_PATH}/${NAME}:${TAG}"

if [[ -z "${NEBIUS_API_KEY:-}" && -f .env ]]; then
  NEBIUS_API_KEY="$(grep -E '^NEBIUS_API_KEY=' .env | cut -d= -f2- || true)"
fi
: "${NEBIUS_API_KEY:?set NEBIUS_API_KEY (Token Factory key) in the environment or .env}"

echo "==> Building ${IMAGE}"
docker build --platform linux/amd64 -t "${IMAGE}" .

echo "==> Pushing"
nebius registry configure-helper >/dev/null
docker push "${IMAGE}"

auth_args=()
if [[ "${AUTH:-}" == "token" ]]; then
  ENDPOINT_TOKEN="${ENDPOINT_TOKEN:-$(openssl rand -hex 32)}"
  auth_args=(--auth token --token "${ENDPOINT_TOKEN}")
fi

echo "==> Creating endpoint ${NAME} on ${PLATFORM}/${PRESET}"
nebius ai endpoint create \
  --name "${NAME}" \
  --image "${IMAGE}" \
  --container-port 8000 \
  --env "NEBIUS_API_KEY=${NEBIUS_API_KEY},LEDGERMIND_MULTIUSER=1,LEDGERMIND_CURRENCY=${CURRENCY}" \
  --platform "${PLATFORM}" \
  --preset "${PRESET}" \
  ${auth_args[@]+"${auth_args[@]}"}

ID="$(nebius ai endpoint list --format json | jq -r --arg n "${NAME}" '.items[] | select(.metadata.name == $n) | .metadata.id' | head -1)"
URL="$(nebius ai endpoint get "${ID}" --format json | jq -r '.status.public_endpoints[] | select(startswith("https://"))' | head -1)"
echo "==> Ledgermind is starting at ${URL}"
if [[ -n "${ENDPOINT_TOKEN:-}" ]]; then
  echo "    Bearer token: ${ENDPOINT_TOKEN}"
fi
