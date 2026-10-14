# Memory-extraction eval

Live run on Nebius Token Factory, 28 labelled messages.

| Model | Precision | Recall | F1 | TP | FP | FN | Negatives with no memory | Median ms | Errors |
|---|---|---|---|---|---|---|---|---|---|
| Nemotron 3 Nano | 83% | 86% | 0.84 | 19 | 4 | 3 | 7/8 | 597 | 0 |
| Nemotron 3.5 Lightning | 77% | 91% | 0.83 | 20 | 6 | 2 | 6/8 | 524 | 0 |
| Nemotron 3 Super | 88% | 95% | 0.91 | 21 | 3 | 1 | 8/8 | 888 | 0 |

## Nemotron 3 Nano: recall by type

| Type | Gold | Found | Predicted |
|---|---|---|---|
| correction | 2 | 2 | 2 |
| decision | 3 | 2 | 2 |
| goal | 3 | 3 | 3 |
| habit | 3 | 3 | 3 |
| preference | 3 | 2 | 3 |
| profile | 5 | 5 | 8 |
| recurring | 3 | 2 | 2 |

### Nemotron 3 Nano: misses and extras

- "I live in Pune with my wife and our two kids.": extra profile: Has a wife and two kids
- "My rent is 28,000 and I pay it on the 1st. Also I'm saving 50k for a new laptop by December.": extra profile: Pays 28,000 rent on the 1st of each month.; missed recurring /rent/
- "I don't want any investment tips, just help me track spending. I earn about 85,000.": missed preference /investment/
- "Netflix went up to 649, but I'm keeping it for now.": extra preference: Keeps Netflix despite price increase to 649.; missed decision /netflix/
- "My friend Arjun spends way too much on gadgets.": extra profile: Arjun is a friend.

## Nemotron 3.5 Lightning: recall by type

| Type | Gold | Found | Predicted |
|---|---|---|---|
| correction | 2 | 2 | 2 |
| decision | 3 | 2 | 2 |
| goal | 3 | 3 | 3 |
| habit | 3 | 3 | 5 |
| preference | 3 | 3 | 3 |
| profile | 5 | 5 | 8 |
| recurring | 3 | 2 | 3 |

### Nemotron 3.5 Lightning: misses and extras

- "I live in Pune with my wife and our two kids.": extra profile: Lives with wife and two kids
- "I'm a freelance designer, so my income swings a lot month to month.": extra profile: Uses USD (assumed from context).
- "My rent is 28,000 and I pay it on the 1st. Also I'm saving 50k for a new laptop by December.": extra profile: Rent is 28,000, paid on the 1st.; missed recurring /rent/
- "Netflix went up to 649, but I'm keeping it for now.": extra recurring: Netflix subscription costs 649 (currency units); missed decision /netflix/
- "What if I cut shopping by 20%?": extra habit: Cuts shopping spending by 20%.
- "My friend Arjun spends way too much on gadgets.": extra habit: Friend Arjun spends way too much on gadgets.

## Nemotron 3 Super: recall by type

| Type | Gold | Found | Predicted |
|---|---|---|---|
| correction | 2 | 2 | 2 |
| decision | 3 | 2 | 2 |
| goal | 3 | 3 | 3 |
| habit | 3 | 3 | 4 |
| preference | 3 | 3 | 3 |
| profile | 5 | 5 | 6 |
| recurring | 3 | 3 | 4 |

### Nemotron 3 Super: misses and extras

- "I live in Pune with my wife and our two kids.": extra profile: Lives with wife and two kids.
- "I'm a freelance designer, so my income swings a lot month to month.": extra habit: Income varies significantly from month to month due to freelance work.
- "Netflix went up to 649, but I'm keeping it for now.": extra recurring: Pays 649 for Netflix.; missed decision /netflix/
