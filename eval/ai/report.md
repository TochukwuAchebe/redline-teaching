# AI writing review — seeded evaluation

Model: `claude-sonnet-5-5` · slides reviewed: 18/18 · discarded for not matching slide text: 0

- Planted problems found: **10/10**
- False flags on 8 clean slides: **0**
- Suggestions that hit a planted problem: **10/10** (100%)
- Suggested fixes that correct the planted typo or casing: **4/4**

| Rule | Found |
|---|---|
| BULLET_PARALLEL | 3/3 |
| TITLE_CASE | 1/1 |
| TITLE_TAKEAWAY | 3/3 |
| TYPO | 3/3 |

| Slide | Rule | Conf. | Quote (verified on slide) | Suggested fix | Verdict |
|---|---|---|---|---|---|
| 1 | TITLE_TAKEAWAY | 0.88 | ✓ “Peer group overview” | Sixteen companies remain in the peer group, and two were acquired this | planted ✓ |
| 2 | TITLE_TAKEAWAY | 0.88 | ✓ “Long-term incentive plan design” | Performance shares make up 60% of the award, and vesting remains three | planted ✓ |
| 3 | TITLE_TAKEAWAY | 0.90 | ✓ “Methodology” | Market data is aged to 1 April and peers are size-adjusted by revenue | planted ✓ |
| 4 | TITLE_CASE | 0.93 | ✓ “Annual Bonus Payouts Increased Across All Business Units” | Annual bonus payouts increased across all business units | planted ✓ · fix correct |
| 5 | BULLET_PARALLEL | 0.90 | ✓ “Market data is refreshed each spring” |  | planted ✓ |
| 6 | BULLET_PARALLEL | 0.88 | ✓ “The threshold was lowered to 85%” |  | planted ✓ |
| 7 | BULLET_PARALLEL | 0.90 | ✓ “Annual incentive targets were benchmarked” |  | planted ✓ |
| 8 | TYPO | 0.97 | ✓ “The committe approved” | The committee approved | planted ✓ · fix correct |
| 9 | TYPO | 0.97 | ✓ “retrun” | return | planted ✓ · fix correct |
| 10 | TYPO | 0.97 | ✓ “reveiwed” | reviewed | planted ✓ · fix correct |
