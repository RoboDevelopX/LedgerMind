# Nemotron benchmark

Measured on 2026-10-14 with `python scripts/benchmark.py --runs 3` against Nebius Token Factory (https://api.tokenfactory.nebius.com/v1/), 100 s in total. Reasoning (thinking) enabled for: Nemotron 3 Ultra; everything else as configured in `config.yaml`.

| Model | Task | Calls | Median latency | Tokens in / out | Cost per call (USD) | Score |
|---|---|---:|---:|---:|---:|---:|
| Nemotron 3 Nano | categorise | 3 | 2,005 ms | 354 / 416 | $0.000121 | 85% |
| Nemotron 3 Nano | tool choice | 18 | 524 ms | 2,943 / 41 | $0.000187 | 100% |
| Nemotron 3 Nano | narrate | 3 | 646 ms | 278 / 77 | $0.000035 | 100% |
| Nemotron 3.5 Lightning | categorise | 3 | 1,543 ms | 354 / 396 | $0.000116 | 100% |
| Nemotron 3.5 Lightning | tool choice | 18 | 465 ms | 3,170 / 39 | $0.000200 | 100% |
| Nemotron 3.5 Lightning | narrate | 3 | 686 ms | 278 / 90 | $0.000038 | 100% |
| Nemotron 3 Super | categorise | 3 | 2,366 ms | 354 / 397 | $0.000463 | 100% |
| Nemotron 3 Super | tool choice | 18 | 950 ms | 3,025 / 50 | $0.000953 | 100% |
| Nemotron 3 Super | narrate | 3 | 1,403 ms | 278 / 124 | $0.000196 | 100% |
| Nemotron 3 Ultra | categorise | 3 | 3,786 ms | 354 / 1,440 | $0.004674 | 100% |
| Nemotron 3 Ultra | tool choice | 18 | 1,064 ms | 2,978 / 104 | $0.003291 | 100% |
| Nemotron 3 Ultra | narrate | 3 | 1,881 ms | 278 / 596 | $0.002066 | 100% |

Scores: categorise = share of 20 labelled merchants categorised correctly; tool choice = share of 6 questions where the first tool call was the right one; narrate = share of explanations whose every figure passed the number guard.
