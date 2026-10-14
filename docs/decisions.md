# Decisions log

Short entries, newest last. "Ribhu" marks the decisions that were his call.

1. **Build on v1, restructure into packages.** v1's CSV parser, Nano thinking handling and mock-first tests are kept
   and moved; the single-call router is replaced by native tool calling so the agent can chain skills.
2. **Model roles.** `fast` = Nemotron 3 Nano (categorise, extract memory, consolidate), `agent` = Nemotron 3
   Super (tool-calling conversation), `deep` = Nemotron 3 Ultra (monthly review, what-if narration, goal plans,
   anomaly explanations). Each role has a fallback chain in `config.yaml`; Nemotron 3.5 Lightning is the shared
   fallback because it supports function calling at Nano's price.
3. **Super may be unavailable.** The catalog showed Super with status `error` on 2026-10-13, so the agent chain is
   Super → Lightning → Nano, and the router logs every fallback.
4. **No embeddings.** Token Factory's only embedding model is Qwen3-Embedding-8B (not NVIDIA). Memory volumes are
   small, and FTS5 with recency and importance scoring is enough; embeddings were first on the brief's cut list.
5. **Numbers only from code.** Skills compute everything; the agent prompt forbids arithmetic; a number guard
   checks that every figure in an answer appears in a tool result or memory and regenerates once if not.
6. **Privacy by construction.** Tools return aggregates and at most a few minimised rows (normalised merchant, date,
   amount). Free text passes through redaction before any call. Strict mode drops row-level data entirely. Every
   outgoing payload is logged to SQLite for the Privacy Inspector.
7. **Default currency INR** with lakh/crore grouping (₹1,20,000); USD via `config.yaml` or `LEDGERMIND_CURRENCY`.
8. **Monthly review export.** Markdown download plus a print stylesheet for "Save as PDF" in the browser, instead of
   a PDF library (PDF export is second on the cut list; this costs nothing).
9. **UI stack (confirmed by Ribhu, 2026-10-14).** No-build vanilla JS + hand-written CSS: one HTML file, one
   stylesheet, one script, served by FastAPI. Nothing to install, nothing to compile, and the Docker image stays
   Python-only. Charts are hand-drawn SVG rather than a charting library. React + Vite + Tailwind was the
   alternative.
10. **Live calls run on a developer machine.** The cloud build environment cannot reach the Token Factory API, so
    `check_models.py`, `benchmark.py` and the live test are run locally with a real key.
11. **Strict mode is about rows, not names.** Strict mode removes every list that holds transaction-level rows
    (anything with a date) and sends only counts in their place. Merchant names in aggregates (top merchants,
    subscriptions) still go out, because without them the assistant cannot answer "which subscription went up?".
    The UI shows both what the model saw and the full result, which never leaves the machine.
12. **The deterministic path is always there.** When models are unreachable, categorisation falls back to rules and
    "Other", the monthly review falls back to a template built from the same figures, and the agent returns a
    friendly error instead of failing. The app is fully usable offline in mock mode.
13. **Enum arguments are case-insensitive.** Models often send "dining" for "Dining". Tool argument validation
    accepts a case-only mismatch and nothing looser.
14. **No separate intent classifier.** The brief lists intent classification under Nano. A Nano pre-pass would add a
    round trip to every turn, and Super's own tool selection already is the intent decision (the benchmark scores it
    as "tool choice"). Nano's share of the work is categorisation, memory extraction and consolidation instead.
15. **Model-quality evals beyond the Q&A eval.** `eval/memory_eval.py` scores memory extraction (precision, recall,
    per type, false positives on messages that hold nothing to remember) and `eval/categorisation_eval.py` scores
    categorisation on labelled bank descriptions as an ablation: rules only, model only, rules + model. Both run the
    production code paths with the model pinned (no fallback), so each number belongs to one model.
16. **Fix live failures in code, not only in prompts.** The second live eval (16/19) showed Super guessing a year for
    "September" despite today's date in the prompt, and Nano extracting facts from the assistant's reply despite an
    explicit rule. Prompt rules alone had already failed, so: bare month names resolve against today and empty
    periods report the ledger's date range; extracted memories must share a word with the user's message and contain
    no amount the user didn't say; and a model paraphrase can't overwrite a user-confirmed memory unless it states a
    new amount or keeps every word. The grounding check is lexical, so it can drop a correct paraphrase that shares
    no word with the message; that trade favours precision, which matters more for long-term memory.
17. **Nano stays the fast tier for now; Lightning is the candidate.** Nemotron 3.5 Lightning beat Nano on
    categorisation accuracy and latency at the same price, but had more false positives in memory extraction.
    Switching categorisation alone is a one-line config change once the role chains are split per task.
