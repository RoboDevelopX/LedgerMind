# Nebius Token Factory: verified notes

Checked on **2026-10-13** against the live docs and the public model catalog. Every value Ledgermind relies on is
listed here with its source. Anything not confirmed by a source is marked **unverified**.

## Endpoint and auth

| Item | Value | Source |
|------|-------|--------|
| Base URL | `https://api.tokenfactory.nebius.com/v1/` | [docs home](https://docs.tokenfactory.nebius.com/) |
| Auth | `Authorization: Bearer $NEBIUS_API_KEY` (key from tokenfactory.nebius.com) | [API intro](https://docs.tokenfactory.nebius.com/api-reference/introduction#authentication) |
| Protocol | OpenAI-compatible; the official `openai` SDK works with `base_url` set | docs home, function-calling and JSON pages (Python examples use `OpenAI(base_url=...)`) |
| List models | `GET /v1/models` | [List models](https://docs.tokenfactory.nebius.com/api-reference/models/list-models.md) |

## NVIDIA Nemotron models on Token Factory

From the public catalog ([model-catalog.md](https://tokenfactory.nebius.com/model-catalog.md)) and its JSON source
(`https://tokenfactory.nebius.com/api/public/models_info`). Prices are USD per 1M tokens.

| Ledgermind tier | Model ID (exact, case-sensitive) | Context | In / Out price | Catalog `use_cases` | Catalog status |
|---|---|---|---|---|---|
| Nano (fast) | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | 262K | 0.06 / 0.24 | text, reasoning, function_calling, code, context_and_rag, responses_api | active |
| Super (agent) | `nvidia/nemotron-3-super-120b-a12b` | 256K | 0.30 / 0.90 | dedicated-endpoint, text, summarization, reasoning, function_calling, complex_writing, responses_api | **error** (see below) |
| Ultra (deep) | `nvidia/Nemotron-3-Ultra-550b-a55b` | 1M | 1.00 / 3.00 | dedicated-endpoint, text, summarization, reasoning, function_calling, complex_writing, responses_api | active |
| (fallback) | `nvidia/Nemotron-3_5-Lightning` | 1M | 0.06 / 0.24 | text, reasoning, function_calling, code, context_and_rag, responses_api | active |

Notes:

- The three IDs use **different casing conventions**. They must be copied exactly; `scripts/check_models.py`
  compares them with `GET /v1/models` and suggests the right casing if one is off.
- On 2026-10-13 the catalog listed Super with `"status": "error"`. It is also the documented replacement for
  deprecated models (see below), so it is expected to be served, but Ledgermind's router treats it as possibly
  unavailable and falls back to the next model in the chain (config-driven).
- `nvidia/Nemotron-3_5-Lightning` (30B total / 3B active, 1M context, function calling, same price as Nano) is the
  newest Nemotron on the catalog. It is used as a fallback model, not a primary tier.
- [August 2026 deprecation notice](https://docs.tokenfactory.nebius.com/august-2026-deprecation-notice.md): on
  2026-08-31 `nvidia/Llama-3_1-Nemotron-Ultra-253B-v1` and `nvidia/Nemotron-3-Nano-Omni` left Serverless.
  Replacements: Super and Lightning respectively. None of the four models above is deprecated.
- Licences: Nano and Super are under the NVIDIA Open Model License; Ultra and Lightning under OpenMDW 1.1
  (catalog `license` field). All four are NVIDIA open models, which satisfies the hackathon requirement.

## Capabilities

| Capability | Status | Source / note |
|---|---|---|
| Chat completions | Yes | [Create chat completion](https://docs.tokenfactory.nebius.com/api-reference/inference/create-chat-completion.md) |
| Tool / function calling | Yes. `tools` (OpenAI format), `tool_choice`: `none`, `auto`, `required`, or named function. Results go back as `role: "tool"` with `tool_call_id`. | [Function calling](https://docs.tokenfactory.nebius.com/ai-models-inference/function-calling.md) + API reference. All four Nemotron models list `function_calling` in the catalog. `parallel_tool_calls` is **not** in the spec, so Ledgermind does not rely on it. |
| JSON output | `response_format`: `{"type": "json_object"}` or `{"type": "json_schema", "json_schema": {"name", "schema", "strict"}}`. Docs say output "is not guaranteed" valid; check for parse errors. | [Structured output](https://docs.tokenfactory.nebius.com/ai-models-inference/json.md). Per-model "JSON mode" support is shown as a tag on the model card; **not confirmed for Nemotron** from the docs, so Ledgermind sends `json_object`, also instructs JSON in the prompt, parses defensively and repairs/retries once. |
| Streaming | Yes. `stream: true` returns SSE `chat.completion.chunk` events ending with `data: [DONE]`; `stream_options.include_usage` puts usage in the last chunk. Deltas may carry `reasoning_content` / `reasoning`. Mid-stream errors arrive as `data: {"error": {...}}`. | API reference (Create chat completion) |
| Usage accounting | `usage.prompt_tokens`, `completion_tokens`, `total_tokens`, `completion_tokens_details.reasoning_tokens` | API reference |
| `max_tokens` default | 8192 when omitted | API reference |
| `reasoning_effort` | Accepted request field: `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` | API reference. Effect on Nemotron **unverified**. |
| Extra engine params | Request schema allows additional properties and an `extra_body` object | API reference |
| Embeddings | Yes. `POST /v1/embeddings` (`model`, `input`, optional `dimensions`, `encoding_format`). The only embedding model in the catalog is `Qwen/Qwen3-Embedding-8B` (4096 dims, 41K context, $0.01/1M). **No NVIDIA embedding model is listed.** | [Create embeddings](https://docs.tokenfactory.nebius.com/api-reference/inference/create-embeddings.md), catalog |

### Nemotron reasoning ("thinking") switch

From the NVIDIA model cards on Hugging Face
([Nano](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-FP8),
[Super](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-FP8)):

- Reasoning is **on by default**. Turn it off per request with
  `extra_body={"chat_template_kwargs": {"enable_thinking": False}}`.
- Super also supports `{"enable_thinking": True, "low_effort": True}` for fewer reasoning tokens.
- Recommended sampling: Nano tool calling `temperature=0.6, top_p=0.95`; Super `temperature=1.0, top_p=0.95` for
  all tasks.
- Tool-call parser on the serving side is `qwen3_coder` (NVIDIA's reference serving configs).
- Whether Token Factory returns the trace in `content` (inside `<think>…</think>`) or in a separate
  `reasoning_content` field is **not stated** by Nebius docs. The previous Ledgermind commit (`29e523e`) handled
  Nano's thinking live and found replies cut off mid-thought when `max_tokens` was too small, so Ledgermind
  strips `<think>` blocks, ignores `reasoning_content`, and retries with a larger budget if a reply is only thinking.

## Rate limits and errors

From [Rate Limits & Scaling](https://docs.tokenfactory.nebius.com/ai-models-inference/rate-limits.md):

- Over-limit requests return **HTTP 429** with a `Retry-After` header (seconds). Ledgermind honours it, capped.
- Limits are dynamic per 15-minute window (example baseline 60 RPM / 400K TPM; up to 20x the base).
- `x-ratelimit-remaining-requests` / `-tokens` headers report headroom.

## Deployment options (Nebius)

- **Token Factory Public Serverless** endpoints host *models*, not apps
  ([page](https://docs.tokenfactory.nebius.com/public-serverless.md)).
- **Nebius AI Cloud Serverless endpoints** run a custom container:
  `nebius ai endpoint create --image <image> --container-port <port> --env "<k=v>" --platform <id> --preset <preset> --auth token --token <token> --public`,
  URL via `nebius ai endpoint get <id> --format json`
  ([manage endpoints](https://docs.nebius.com/serverless/endpoints/manage.md)). VMs without GPUs are supported;
  the CPU platforms are `cpu-d3` (AMD EPYC Genoa, most regions) and `cpu-e2` (eu-north1), with presets from
  `2vcpu-8gb` upwards ([types](https://docs.nebius.com/compute/virtual-machines/types.md)).
  Images are pushed to `cr.<region>.nebius.cloud/<registry path>/<image>:<tag>` after
  `nebius registry configure-helper`. Details in `docs/deploy_nebius.md`.

## What was not reachable from the build environment

The cloud container this was built in cannot reach `api.tokenfactory.nebius.com` (egress policy), so every value
above comes from the docs and catalog, and no live call was made from it. Live checks run on a developer machine with
`python scripts/check_models.py` and `python scripts/benchmark.py`.
