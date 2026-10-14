# Design notes

The short version of why Ledgermind is built the way it is, what it costs, and what comes next. The full log of
choices is in [decisions.md](decisions.md); verified API facts are in [nebius_notes.md](nebius_notes.md).

## Key decisions and trade-offs

**1. The model never does arithmetic on money.** Skills compute every figure with pandas/SQL; tool results reach the
model as pre-formatted strings ("₹18,684"), so it copies rather than calculates; a number guard
(`agent/numguard.py`) checks every figure in the answer against the tool results and memory, and regenerates once if
one is unsupported. *Trade-off:* the guard can reject a correct paraphrase ("about 19k"), so it accepts rounding to
fewer significant digits; it cannot catch a right number attached to the wrong label.

**2. Route by task, not by conversation.** Nano does high-volume structured work (categorisation, memory extraction,
consolidation), Super runs the tool-calling conversation, Ultra writes only the answers that need reasoning (monthly
review, what-if, goal plans). Each role is a fallback chain in `config.yaml`. *Trade-off:* a hand-off to Ultra adds
latency on deep questions; the benchmark measures how much.

**3. Native tool calling over a single-shot router.** v1 had Nano pick one skill per message. v2 lets Super chain
tools (import → categorise → summarise) with a max-iterations guard. *Trade-off:* more calls per turn; the guard and
the per-turn call log keep that visible and bounded.

**4. Privacy by construction, not by policy.** The ledger never leaves SQLite. Tool results pass through
`privacy/minimise.py` (drop ids and raw descriptions, cap row lists, strict mode = aggregates only) and every
outgoing payload through redaction, and is logged for the Privacy Inspector. *Trade-off:* the model sees normalised
merchant names in aggregates, because "which subscription went up?" is unanswerable without them; strict mode is a
row-level guarantee, not a names guarantee (decision 11).

**5. Typed memory with lexical retrieval.** Seven memory types with keys, confidence, importance and source; Nano
extracts candidates after each turn; dedupe by key and Jaccard similarity; retrieval = 0.55·FTS5 relevance +
0.30·importance + 0.15·recency. *Trade-off:* no embeddings, so paraphrases with no shared words can miss. For a few
hundred short memories this is cheap, explainable and testable; Token Factory also has no NVIDIA embedding model.

**6. Rules first, model second, corrections above both.** Categorisation applies user corrections, then local
rules, then Nano in batches for the rest. *Trade-off:* rules need upkeep; in exchange they cut tokens and data sent,
and `eval/categorisation_eval.py` measures exactly how much the model adds on top.

**7. Mock-first.** A deterministic mock honours every task's contract, so 95 tests, the Q&A eval and CI run
offline. *Trade-off:* mock passes say nothing about model quality; every report labels mock runs as harness checks.

**8. Integer-exact money, configurable currency.** INR with lakh grouping by default, USD via config.

## Known limitations

- Live Q&A eval: 13/19, then 16/19, then 19/19 after fixing a guessed year, a review headline without income and a goal
  memory overwritten by a Nano paraphrase. That is one run of 19 questions: it shows the known failures are fixed,
  not that the agent is always right. Some weak memories still get through extraction (a request filed as a
  preference, subscription facts inferred from a question).
- Nano follows negative instructions ("never take facts from the assistant's reply") unreliably, so extracted
  memories now pass a code-level grounding check. It is lexical: a paraphrase sharing no word with the user's message
  is dropped even if it is right.
- The number guard checks presence, not attribution.
- Single-user SQLite with a per-browser ledger in multi-user mode; no auth, so the hosted demo is for synthetic data.
- Merchant normalisation is regex-based and leaks short reference numbers into a few names.

## What I'd do next

1. Re-run the live evals, then use the failures as few-shot examples or a small LoRA on Nano for categorisation.
2. Attribute numbers in the guard: require each figure to sit next to the label it came with in the tool result.
3. Add embeddings behind the same retrieval interface and A/B them against FTS5 on a labelled recall set.
4. Encrypt the SQLite file at rest (SQLCipher) and add an auth layer before any real-data deployment.
5. Learn the routing policy: log per-task quality vs cost and pick the cheapest model that meets a quality bar.

## Likely interview questions

**Why three model sizes instead of one good model?** Cost and latency differ by about 17× on input price ($0.06 vs $1.00 per 1M tokens) between Nano and
Ultra, and most calls are simple structured tasks. Routing sends each task to the cheapest model that does it well;
the benchmark and evals report accuracy, latency and cost per model so that claim is measured, not assumed.

**How do you stop the LLM from making up numbers?** Three layers: skills compute everything, results arrive
pre-formatted so the model copies strings, and the number guard verifies every figure post-hoc with one regeneration.
The eval checks tool maths against independent pandas and checks the answer carries those figures.

**What exactly leaves the user's machine?** The system prompt, recent conversation, top-k memories and minimised,
redacted tool results: aggregates plus at most a few rows with merchant, date and amount, never raw bank
descriptions, ids or account numbers. The Privacy Inspector shows the exact last payload and what was withheld.

**How does memory work, and how do you evaluate it?** Nano extracts typed candidates as JSON after each turn; they
are validated, filtered by confidence, and merged by key/similarity. Retrieval blends FTS5, importance and recency.
`eval/memory_eval.py` reports precision, recall per type and the false-positive rate on messages with nothing to
remember; dedupe and retrieval have unit tests.

**Why FTS5 and not a vector database?** Small corpus, short texts, and keyword overlap is strong for finance facts
("rent", "Netflix"). It's explainable, has no extra service, and Token Factory had no NVIDIA embedding model. I'd
add embeddings behind the same interface if a recall eval showed paraphrase misses.

**What happens when a model is down or rate-limited?** The router retries retriable errors, honouring `Retry-After`,
then falls back along the role's chain (e.g. Super → Lightning → Nano), logging each attempt. If every model fails,
deterministic paths keep working: rules for categorisation, a template monthly review, a friendly chat error.

**How did you evaluate categorisation?** 72 labelled bank descriptions, scored as an ablation: rules only, model
only, rules + model. That separates what the rules contribute from what Nano adds on unknown merchants.

**What went wrong in the live runs?** The first run scored 13/19: Super reused figures from earlier turns instead of
calling tools, and once said "noted" without calling `remember`. I isolated the questions and tightened the prompt,
and the second run scored 16/19. Its failures were more interesting: Super asked for September *2024* even though
the prompt gives today's date, and Nano overwrote a user-confirmed goal with its own paraphrase, losing the goal's
name. Both are fixed in code rather than prompts: bare month names resolve against today, empty periods tell the
agent what the ledger covers, and model-extracted memories must be grounded in the user's words and can't overwrite
what the user said. The third run passed 19/19. In every run the number guard verified every answer, so no failure
was a wrong number.

**Why CSV import instead of bank APIs?** Deliberate privacy choice: no credentials, no third-party aggregator, and
the user decides exactly what data exists. It also keeps the demo reproducible.

**What would you change for production?** Encryption at rest, auth, a migration story for the SQLite schema, the
attributed number guard, and per-task quality monitoring feeding the routing policy.
