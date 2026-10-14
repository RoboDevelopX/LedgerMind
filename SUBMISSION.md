# Ledgermind: hackathon submission kit

Everything needed for the NVIDIA x Nebius Global AI Hackathon form, in the order the form asks for it.

## Track

Personal AI

## Project name and tagline

**Ledgermind**: a private AI finance assistant that remembers your money.

## Project description (paste into the form)

**What it is.** Ledgermind is a personal finance assistant you talk to. Import a bank statement as CSV and ask
"Where did my money go last month?", "I'm saving ₹3 lakh for a Japan trip by March" or "What if I cut dining by
30%?". It remembers your goals, budgets, habits and corrections between conversations, and it notices things
before you ask: a duplicate charge, a subscription price rise, a budget about to break.

**Why.** Budgeting apps make you do the analysis. Chatbots forget you, make up numbers, and want your whole bank
history. Ledgermind remembers you, computes every figure itself, and sends the model only what it needs.

**How it uses NVIDIA Nemotron on Nebius Token Factory.** A config-driven router picks the smallest Nemotron model
that does each job, all through one OpenAI-compatible Token Factory endpoint. Nemotron 3 Nano categorises
merchants and extracts typed memories after every turn. Nemotron 3 Super runs the tool-calling agent over 15
deterministic finance skills. Nemotron 3 Ultra writes the answers that need reasoning: what-if scenarios, goal
plans, anomaly explanations and monthly reviews. Nemotron 3.5 Lightning is the fallback in every chain, with
retries, timeouts and a per-call cost and latency log.

**Why you can trust it.** Every number is computed in Python from your own SQLite ledger; a number guard checks
that each figure in an answer appears in a tool result and regenerates the answer if not. Transaction rows, account
numbers and raw bank descriptions never leave the machine: the model sees only aggregates with personal details
redacted, the Privacy Inspector shows exactly what was sent, and strict mode sends only totals and counts. Import
is CSV only by design (no bank logins), and every memory is visible, editable and deletable. Ledgermind gives
informational insights, not financial advice.

**Built with:** NVIDIA Nemotron 3 (Nano, Super, Ultra) and Nemotron 3.5 Lightning, Nebius Token Factory, Nebius AI
Cloud serverless endpoints, Python, FastAPI, SQLite (FTS5), pandas, APScheduler. MIT licensed.

## Links

| Field | Value |
|---|---|
| Repository | https://github.com/RoboDevelopX/LedgerMind |
| Live demo | _add after `make deploy` (see [docs/deploy_nebius.md](docs/deploy_nebius.md))_ |
| Video | _add the YouTube link after uploading_ |

## Video script (3 minutes or less)

The narration below is [`demo_video/narration.json`](demo_video/narration.json), which
[`demo_video/make_video.py`](demo_video/make_video.py) turns into a finished video: it drives the app in headless
Chromium scene by scene, voices each scene with an offline TTS model and cuts them together (see
[demo_video/README.md](demo_video/README.md)). Timestamps come from a dry run against the mock models and come to
about 2:20; live models add a few seconds per chat scene, which still leaves room under 3:00. To record by hand,
follow the "On screen" column and read the narration.

| Time | Scene | On screen | Narration |
|---|---|---|---|
| 0:00 | Title | Title slide: name, tagline, Nemotron and Token Factory | This is Ledgermind: a private AI finance assistant that remembers your money. It runs on NVIDIA Nemotron models through Nebius Token Factory, and it's built for the Personal AI track. |
| 0:11 | Problem | Slide: budgeting apps vs chatbots vs Ledgermind | Budgeting apps make you do the analysis. Chatbots forget you, make up numbers, and want your whole bank history. Ledgermind remembers you, computes every figure itself, and sends the model only what it needs. |
| 0:24 | Import | Data menu, Load demo data; dashboard fills in; "Ledgermind noticed" cards appear | I'll load twelve months of fictional bank data. Import is CSV only, by design: no bank logins. Nemotron Nano categorises the merchants that local rules don't know, and Ledgermind has already noticed a few things: a duplicate Amazon charge, a Netflix price rise, and a new subscription. |
| 0:42 | Spending | Ask "Where did my money go last month?"; open the tool step; point at the model and latency badges | Where did my money go last month? Nemotron Super calls the spending tool. The figures come from plain Python over my ledger, and a number guard checks that every amount in the answer appears in the tool result. Each answer shows its model and latency. |
| 0:59 | Subscriptions | Ask "Find my subscriptions" | Find my subscriptions. It spots the Netflix price rise, and flags Apple Music as probably forgotten, because I've also been paying for Spotify since February. |
| 1:08 | Goal | Type "I'm saving ₹3 lakh for a Japan trip by March"; the "learned" chip appears; goal card updates | Now I'll mention that I'm saving three lakh rupees for a Japan trip by March. Ledgermind sets the goal, works out the monthly saving it needs, and stores it as a typed memory. |
| 1:20 | What-if | Ask "What if I cut dining by 30%?"; badge switches to Nemotron 3 Ultra | What if I cut dining by thirty percent? The simulator runs locally, then the answer moves up to Nemotron Ultra, which explains how much sooner the Japan goal arrives. Heavy reasoning is only spent where it matters. |
| 1:34 | Memory | Memory tab: goal, preference and correction groups; edit or pin one | Everything it remembers is here, typed as goals, preferences, recurring bills and corrections. I can edit, pin or delete any memory. After each turn, Nano extracts new memories and merges duplicates. |
| 1:46 | Privacy | Privacy tab: last payload with redaction tokens; flip strict mode | The Privacy Inspector shows exactly what left my machine on the last turn. No transaction rows, no account numbers, no raw bank descriptions. Personal details are redacted, and strict mode sends only totals and counts. |
| 1:59 | Architecture | Architecture slide: four boxes and the router tiers | Under the hood, a config-driven router sends bulk work to Nano, the tool-calling agent to Super, and deep reasoning to Ultra, with retries and fallback, all through one OpenAI-compatible Token Factory endpoint. |
| 2:11 | Outro | Outro slide: repo URL, MIT, disclaimer | Ledgermind is open source under the MIT license. It gives informational insights, not financial advice. Thanks for watching. |

## Before you submit

Live Nemotron calls could not be made from the build environment, so the first three steps need a machine with
`NEBIUS_API_KEY` in `.env`.

- [ ] `make check`: lists the Nemotron models your key can reach and pings each configured one.
- [ ] `LEDGERMIND_LIVE_TESTS=1 python -m pytest tests/test_live.py`: two live tests (model IDs exist; the agent
      answers a spending question with ledger figures).
- [x] `make eval`: live runs on 2026-10-14 scored 13/19, 16/19 and, after the fixes, 19/19 (in the README).
- [x] `make bench`, `make eval-memory`, `make eval-cat`: live results are in the README.
- [ ] Record the video: `python demo_video/make_video.py` with the app running on live models (or record by hand
      from the script above). Keep it under 3:00.
- [ ] Upload to YouTube as **Public** (or Unlisted if the form allows) and put the link in the README and the form.
- [ ] Deploy: `REGION=... REGISTRY_PATH=... make deploy` (see [docs/deploy_nebius.md](docs/deploy_nebius.md)).
      Open the URL, load the demo data, ask one question, then add the URL to the README and the form.
- [ ] Check the repo page shows the MIT licence at the top (GitHub reads `LICENSE` at the root).
- [x] Live observations are in [FEEDBACK.md](FEEDBACK.md#live). Add anything you noticed yourself.
- [ ] Fill in the form: track **Personal AI**, description above, repo, demo URL, video.
- [ ] City: pick the Builders & Brews city if you attended one; otherwise leave it blank.
- [ ] After judging: `nebius ai endpoint stop --id <endpoint id>` so the public demo stops spending credits.
