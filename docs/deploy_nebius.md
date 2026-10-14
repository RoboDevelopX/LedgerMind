# Deploying Ledgermind on Nebius AI Cloud

Ledgermind is a small CPU-only web app; the Nemotron models it calls are served by Nebius Token Factory. The
simplest public deployment is a **Nebius AI Cloud serverless endpoint** running the Docker image on a CPU VM.

Everything below follows the Nebius docs as read on 2026-10-13
([serverless endpoints](https://docs.nebius.com/serverless/endpoints/manage.md),
[container registry](https://docs.nebius.com/container-registry/quickstart.md),
[VM platforms and presets](https://docs.nebius.com/compute/virtual-machines/types.md)). It has **not** been run end
to end from the build environment, which had no Docker daemon and no Nebius credentials.

## What the deployed app looks like

- `LEDGERMIND_MULTIUSER=1`: every browser gets its own SQLite ledger (keyed by an HTTP-only cookie), so visitors
  never see each other's data. Visitors load the demo data or upload their own CSV.
- `NEBIUS_API_KEY` is passed to the container as an environment variable (or a SecretStash secret, below). Without
  it the app runs in offline mock mode, which is fine for a smoke test.
- Data lives in `/data` inside the container. A serverless endpoint's disk is not a backup; treat the public demo as
  disposable.

## Prerequisites

- The [Nebius CLI](https://docs.nebius.com/cli/install), logged in to a project, plus `docker` and `jq`.
- A Token Factory API key in `.env` or the environment as `NEBIUS_API_KEY`.

## Step 1: create a registry (once)

```bash
export REGION=eu-north1
export REGISTRY_PATH=$(nebius registry create --name ledgermind --format json | jq -r ".metadata.id" | cut -d- -f 2)
```

## Step 2: build, push and create the endpoint

```bash
./scripts/deploy_nebius.sh
```

The script:

1. builds the image for `linux/amd64` and tags it `cr.$REGION.nebius.cloud/$REGISTRY_PATH/ledgermind:<git hash>`;
2. authenticates Docker with `nebius registry configure-helper` and pushes;
3. runs `nebius ai endpoint create` with `--container-port 8000`, the environment above, platform `cpu-d3`
   (AMD EPYC Genoa, available in eu-north1, eu-west1, us-central1 and others) and preset `2vcpu-8gb`;
4. prints the managed HTTPS URL from `nebius ai endpoint get <id> --format json`.

Override `PLATFORM`, `PRESET`, `NAME`, `TAG` or `CURRENCY=USD` as environment variables.

## Keeping the key out of the command line

`--env` puts the key in the endpoint spec. To keep it in Nebius SecretStash instead, create a secret whose payload
has a key named `NEBIUS_API_KEY` and replace the `NEBIUS_API_KEY=...` part of `--env` with:

```bash
--env-secret "NEBIUS_API_KEY=<secret selector>"
```

## Access control

The script creates an endpoint without authentication by default, because judges open it in a browser and a browser
cannot attach the `Authorization: Bearer` header that `--auth token` requires. Set `AUTH=token` for an API-only or
private deployment. Anyone with the URL can spend Token Factory credits through the app, so stop the endpoint after
judging:

```bash
nebius ai endpoint stop --id <endpoint id>
nebius ai endpoint delete --id <endpoint id>   # also deletes the VM and its disk
```

## Running the same image anywhere else

```bash
docker build -t ledgermind .
docker run -p 8000:8000 -e NEBIUS_API_KEY=... -v ledgermind-data:/data ledgermind
```

## Unverified details

- The JSON shape used to find the endpoint id (`.items[].metadata`) is inferred from the other Nebius CLI outputs.
  If it differs, run `nebius ai endpoint list` and copy the id by hand.
- The docs list a default 250 GiB boot disk (`--disk-size`); Ledgermind needs far less, but the flag's value format
  was not documented, so the script leaves the default.
