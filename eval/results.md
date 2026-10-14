# Ledgermind eval results

- Mode: live Nemotron on Nebius Token Factory
- Models: fast: Nemotron 3 Nano, agent: Nemotron 3 Super, deep: Nemotron 3 Ultra
- Ledger: seeded demo, today = 2026-10-20
- **Passed 19 of 19 (100%)**
- Tool maths matched pandas on 19 of 19 questions; answers carried the right figures on 19; number guard verified 19.
- Median time per question: 5.15 s

| # | Question | Tools called | Tools | Maths | Answer | Guard | Pass | Answered by |
|---|---|---|---|---|---|---|---|---|
| 1 | How much did I spend last month? | spending_summary | yes | yes | yes | yes | yes | Nemotron 3 Super |
| 2 | What was my income in September? | spending_summary | yes | yes | yes | yes | yes | Nemotron 3 Super |
| 3 | How much did I spend on dining last month? | spending_summary | yes | yes | yes | yes | yes | Nemotron 3 Super |
| 4 | How much did I spend at Swiggy last month? | spending_summary | yes | yes | yes | yes | yes | Nemotron 3 Super |
| 5 | Where did my money go last month? | spending_summary | yes | yes | yes | yes | yes | Nemotron 3 Super |
| 6 | How much have I spent so far this month? | spending_summary | yes | yes | yes | yes | yes | Nemotron 3 Super |
| 7 | Find my subscriptions | detect_subscriptions | yes | yes | yes | yes | yes | Nemotron 3 Super |
| 8 | Did any of my subscriptions get more expensive? | detect_subscriptions | yes | yes | yes | yes | yes | Nemotron 3 Super |
| 9 | Am I paying for a subscription I've probably forgotten? | detect_subscriptions | yes | yes | yes | yes | yes | Nemotron 3 Super |
| 10 | Anything unusual in the last 90 days? | detect_anomalies | yes | yes | yes | yes | yes | Nemotron 3 Ultra |
| 11 | I'm saving ₹3 lakh for a Japan trip by March | set_goal | yes | yes | yes | yes | yes | Nemotron 3 Super |
| 12 | Am I on track for my goals? | goal_progress | yes | yes | yes | yes | yes | Nemotron 3 Ultra |
| 13 | What if I cut dining by 30%? | what_if_simulator | yes | yes | yes | yes | yes | Nemotron 3 Ultra |
| 14 | Set a dining budget of 10000 a month | set_budget | yes | yes | yes | yes | yes | Nemotron 3 Super |
| 15 | How are my budgets looking this month? | budget_check | yes | yes | yes | yes | yes | Nemotron 3 Super |
| 16 | Give me a monthly review for September | monthly_review | yes | yes | yes | yes | yes | Nemotron 3 Super |
| 17 | Remember that I get paid on the last working day of the month | remember | yes | yes | yes | yes | yes | Nemotron 3 Super |
| 18 | What do you remember about me? | recall_memories | yes | yes | yes | yes | yes | Nemotron 3 Super |
| 19 | Toit Brewpub is dining, not entertainment | correct_category | yes | yes | yes | yes | yes | Nemotron 3 Super |

## Memories after the run

Source: user = saved by a tool call, chat = extracted by Nano after a turn.

- correction (user): Toit Brewpub transactions are Dining.
- goal (user): Saving for Japan trip: target ₹3,00,000 by 2027-03-31.
- habit (chat): Spends heavily on housing and dining, with a notable reduction in shopping expenses.
- preference (user): Monthly Dining budget is ₹10,000.
- preference (chat): Wants a monthly review for September.
- profile (user): User gets paid on the last working day of the month
- recurring (chat): Still paying for Apple Music subscription
- recurring (chat): Has had Spotify subscription since February
