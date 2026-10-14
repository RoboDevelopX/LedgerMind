# Categorisation eval

Live run on Nebius Token Factory, 72 labelled bank descriptions.

Local rules recognise 76% of descriptions, and are right on 100% of those. Rules alone (unknown merchants become Other) score **76%**.

| Model | Model only | Rules + model (production) | Model on merchants rules don't know | ms, all merchants | ms, unknown only |
|---|---|---|---|---|---|
| Nemotron 3 Nano | 75% | 92% | 65% | 6557 | 1994 |
| Nemotron 3.5 Lightning | 81% | 93% | 71% | 5132 | 1469 |
| Nemotron 3 Super | 86% | 96% | 82% | 7176 | 2005 |

## Nemotron 3 Nano: errors (rules + model)

- POS 4821 MTR 1924 BASAVANAGUDI: got Transport, want Dining
- UPI/P2M/510/FERNS N PETALS: got Other, want Gifts
- UPI/P2M/4401/NAMMA YATRI/ICIC: got Entertainment, want Transport
- UPI/P2M/1180/SONYLIV/ICIC: got Entertainment, want Subscriptions
- UPI/P2M/119/KINDLE UNLIMITED: got Entertainment, want Subscriptions
- TATA AIG HEALTH PREMIUM: got Health, want Insurance

## Nemotron 3.5 Lightning: errors (rules + model)

- POS 4821 MTR 1924 BASAVANAGUDI: got Transport, want Dining
- UPI/P2M/510/FERNS N PETALS: got Groceries, want Gifts
- UPI/P2M/1180/SONYLIV/ICIC: got Entertainment, want Subscriptions
- UPI/P2M/119/KINDLE UNLIMITED: got Entertainment, want Subscriptions
- TATA AIG HEALTH PREMIUM: got Health, want Insurance

## Nemotron 3 Super: errors (rules + model)

- UPI/P2M/510/FERNS N PETALS: got Subscriptions, want Gifts
- UPI/P2M/1180/SONYLIV/ICIC: got Entertainment, want Subscriptions
- TATA AIG HEALTH PREMIUM: got Health, want Insurance
