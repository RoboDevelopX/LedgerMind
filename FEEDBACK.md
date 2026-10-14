# Feedback: Nebius Token Factory and NVIDIA Nemotron

Notes collected while building Ledgermind. Each entry says where it came from: **docs** (reading
documentation), **build** (writing the integration), or **live** (real API calls).

The build environment could not reach the Token Factory API, so the v2 entries come from the docs and from writing
the integration. Entries from real calls are added under **Live** after running `make check`, `make eval` and
`make bench` with a key.

## Friction log

- **docs:** The three Nemotron 3 model IDs use three different casing conventions
  (`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, `nvidia/nemotron-3-super-120b-a12b`, `nvidia/Nemotron-3-Ultra-550b-a55b`).
  IDs are case-sensitive, so this is an easy way to get a 404. A lowercase alias would help.
- **docs:** On 2026-10-13 the public catalog JSON reported Nemotron 3 Super with `"status": "error"` while it is
  also the documented replacement for deprecated models. It was unclear whether that meant an outage.
- **docs:** Neither the function-calling page nor the structured-output page says which models support those
  features; the structured-output page points to a "JSON mode" tag on model cards, and the catalog's `use_cases`
  field lists `function_calling` but has no JSON-mode flag. A capability matrix would save trial and error.
- **docs:** There is no page on reasoning models. How to switch Nemotron's thinking off
  (`chat_template_kwargs.enable_thinking`) comes from NVIDIA's Hugging Face cards, and whether the trace arrives in
  `content` or `reasoning_content` is not documented. The API reference lists `reasoning_effort` without saying
  which models honour it.
- **docs:** The structured-output page has broken snippets (a Python dict missing a comma and colon, a cURL body
  with a `"response format"` key containing a space).
- **docs:** The chat-completion response schema omits `reasoning_content`, although the streaming delta schema has it.
- **docs:** The only embedding model on Token Factory is Qwen3-Embedding-8B; an NVIDIA embedding model
  (e.g. NV-Embed) would let an all-NVIDIA stack do retrieval.
- **build (v1):** With thinking on, Nemotron 3 Nano spent small `max_tokens` budgets entirely on reasoning and
  returned no answer (`finish_reason: "length"`). Ledgermind now turns thinking off for fast calls and retries with
  a larger budget when a reply is cut off mid-thought.
- **build:** There is no Token Factory Python SDK or typed client beyond OpenAI compatibility, so Nemotron
  extras are reached through `extra_body` (`chat_template_kwargs`) and untyped response attributes
  (`reasoning_content`). That works, but a short "OpenAI SDK + Nemotron" recipe page covering these would save each
  team the same digging.
- **docs (deploy):** Deploying a CPU-only app as an AI Cloud serverless endpoint needed three separate pages: the
  endpoint page does not list valid `--platform` / `--preset` values for CPU, which live on the VM types page
  (`cpu-d3`, `cpu-e2`, presets such as `2vcpu-8gb`), and the registry URL format is on the registry quickstart.
  One worked "deploy a web app" example would cover it.
- **docs (deploy):** The JSON shape of `nebius ai endpoint list --format json` is not documented, so scripting
  "find the endpoint I just created" means guessing (Ledgermind's deploy script assumes `.items[].metadata`).
- **docs (deploy):** There is no documented way to configure a health-check path for a serverless endpoint, or to
  say how readiness is decided, so it is unclear whether a slow-starting container is marked failed.
- **docs (deploy):** The endpoint docs cover create, get, stop, start and delete but no update, so shipping a new
  image tag appears to mean delete and re-create, which changes the URL a submission form already points to.
- **docs (deploy):** `--public` and the managed HTTPS URL are easy to conflate. The docs imply the HTTPS URL exists
  without `--public` (which adds a public IP), but this is stated only indirectly.
- **docs (deploy):** `--disk-size` defaults to 250 GiB and its value format (unit suffix or bytes) is not
  documented, so the deploy script leaves the default even though the app needs well under 1 GiB.

## What worked well

- **docs:** One OpenAI-compatible endpoint for every model, so tiered routing is just a different `model` string and
  the stock `openai` SDK works unchanged.
- **docs:** Clear rate-limit headers and `Retry-After` on 429s, which made the router's retry logic simple.
- **docs:** Nemotron pricing spans 0.06 to 1.00 USD per 1M input tokens across Nano, Super and Ultra, which is
  exactly what makes per-task routing worthwhile.

## Live

First live eval on 2026-10-14 (19 questions, Windows, Nemotron 3 Super as the agent, Ultra for deep answers):
13 of 19 passed, median 4.65 s per question, and the number guard verified every answer.

- **live:** Super answered follow-up questions ("What was my income in September?") from figures already in the
  conversation instead of calling a tool again. The figures were right, but it means a fresh question can quietly
  rely on stale context. The eval now starts each question in a fresh conversation.
- **live:** Asked to "remember that I get paid on the last working day of the month", Super replied "I've noted
  that" without calling the `remember` tool, although the system prompt says to call it. The memory was saved
  anyway by the Nano extraction step, but with lower confidence. The prompt now says this more firmly.
- **live:** Function calling itself was reliable: across the 19 questions every tool call Super made was to the
  right tool with valid arguments, and Ultra took over after the deep tools as configured.

Second live run on 2026-10-14, plus the benchmark and the model evals (Windows, same models): 16 of 19 passed,
median 5.42 s per question, number guard verified all 19.

- **live:** Super guessed the year. Asked "What was my income in September?", it requested `2024-09` although the
  system prompt states today's date (2026-10-20 in the eval) and the ledger's date range. Training-date priors beat
  the prompt. Fixed in code: bare month names now resolve against today, and an empty period tells the model which
  dates the ledger covers so it can retry.
- **live:** Nano took facts from the assistant's reply despite an explicit instruction not to: a dining average and
  a monthly income it had only seen in the answer, subscription prices from a reply, a home city the user never
  mentioned, and once a memory whose whole content was "₹". Negative instructions are the weakest part of Nano's
  instruction following in this app, so extracted memories now pass a code-level grounding check instead.
- **live:** Nano's paraphrase of a goal ("Target ₹3,00,000 by 2027-03-31") replaced the user's own wording
  ("Saving for Japan trip…") because it reused the goal's key. That was our merge logic, not the model, but it only
  showed up with live outputs.
- **live:** Nemotron 3.5 Lightning was the surprise. At the same price as Nano it was faster and more accurate at
  categorisation (benchmark 100% vs 85%; 72-case eval 93% vs 92% in production mode, 81% vs 75% model-only).
- **live:** Ultra's reasoning tokens dominate its cost on easy tasks: categorising 20 merchants produced about
  1,440 output tokens against about 400 for the other models, so the call cost roughly 38x Nano's for the same
  (perfect) answer. That is the concrete case for routing easy work away from it.
- **live:** All four models scored 100% on choosing the first tool for the benchmark's 6 questions, so tool calling
  through Token Factory's OpenAI-compatible API worked without any model-specific handling.
- **live:** No failed calls in the memory-extraction eval (0 errors across 84 calls), and the 96-call benchmark
  finished in 100 s.

Third live run on 2026-10-14, after the code fixes: 19 of 19 passed, median 5.15 s per question. The memory store
held 8 entries afterwards instead of 18, and the goal kept the user's wording. Nano still files some requests as
preferences ("wants a monthly review for September"), so memory precision is the next thing to work on.
