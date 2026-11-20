"""Build a deck with planted writing problems and clean controls, plus its answer key.

Seeded slides each carry one known defect; control slides are correct and must
stay silent, including traps (unusual but correct company names, acronyms,
parallel sentences with different subjects). Usage:
    python eval/ai/generate_seeded_deck.py eval/ai
"""
import sys
from pathlib import Path

import yaml
from pptx import Presentation

SEEDED = [
    # (rule, title, bullets, what a correct suggestion must contain)
    ("TITLE_TAKEAWAY", "Peer group overview",
     ["Sixteen companies remain in the peer group", "Two companies were acquired this year"], None),
    ("TITLE_TAKEAWAY", "Long-term incentive plan design",
     ["Performance shares make up 60% of the award", "Vesting remains three years"], None),
    ("TITLE_TAKEAWAY", "Methodology",
     ["Market data is aged to 1 April", "Peers are size-adjusted by revenue"], None),
    ("TITLE_CASE", "Annual Bonus Payouts Increased Across All Business Units",
     ["Payouts averaged 112% of target", "All units exceeded threshold"], "Annual bonus payouts increased"),
    ("BULLET_PARALLEL", "The committee refreshed its approach to pay benchmarking",
     ["Reviewed the peer group annually", "Market data is refreshed each spring", "To align pay with performance"], None),
    ("BULLET_PARALLEL", "Three changes to the annual incentive are recommended",
     ["Increase the payout cap to 200%", "The threshold was lowered to 85%", "Stronger retention"], None),
    ("BULLET_PARALLEL", "Benchmarking now covers every element of pay",
     ["Benchmark base salary", "Annual incentive targets were benchmarked", "Benchmarking of long-term incentives"], None),
    ("TYPO", "The committee approved revised targets for FY26",
     ["The committe approved the revised targets in March", "Targets apply from the next fiscal year"], "committee"),
    ("TYPO", "Relative TSR outperformed the peer median",
     ["Total shareholder retrun was 18% over three years", "The peer median was 11%"], "return"),
    ("TYPO", "Salary increases will be decided in the spring",
     ["Salaries will be reveiwed in March", "Increases take effect in April"], "reviewed"),
]

CONTROLS = [
    ("CEO pay remains at the 50th percentile of peers",
     ["Benchmarked base salary against sixteen peers", "Reviewed annual incentive targets", "Approved the long-term award mix"]),
    ("Three peers were removed after acquisitions",
     ["Hologic, acquired by Blackstone and TPG", "Masimo, acquired by a strategic buyer", "Stericycle, acquired by WM"]),
    ("Bonus pool funding rose 12% on stronger margins",
     ["Operating margin improved by two points", "Revenue grew 8% year on year", "Cash conversion stayed above 90%"]),
    ("Mettler-Toledo, DexCom and ResMed remain in the peer group",
     ["All three meet the revenue screen", "All three sell majority direct", "All three invest over 5% of revenue in R&D"]),
    ("ISS and Glass Lewis supported the say-on-pay vote",
     ["ISS recommended a vote for the LTIP", "Glass Lewis raised no concerns", "Shareholder support was 94%"]),
    ("Retention awards are concentrated on critical roles",
     ["Engineering leaders received 60% of the pool", "Sales leaders received 25% of the pool", "Finance leaders received 15% of the pool"]),
    ("The board approved the FY26 LTIP design in February",
     ["The CEO receives 70% in performance shares", "Other executives receive 50% in performance shares", "All awards vest over three years"]),
    ("Each committee member has a distinct role in the review",
     ["The chair sets the agenda", "The adviser presents market data", "Management answers questions on strategy"]),
]


def main(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    prs = Presentation()
    expected, controls = [], []
    for rule, title, bullets, fix in SEEDED:
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = title
        s.placeholders[1].text = "\n".join(bullets)
        expected.append({"slide": len(prs.slides), "rule": rule, **({"fix_contains": fix} if fix else {})})
    for title, bullets in CONTROLS:
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = title
        s.placeholders[1].text = "\n".join(bullets)
        controls.append(len(prs.slides))
    prs.save(out / "seeded-writing.pptx")
    (out / "seeded_expected.yaml").write_text(yaml.safe_dump(
        {"deck": "eval/ai/seeded-writing.pptx", "expected": expected, "clean_slides": controls}, sort_keys=False))
    print(f"{len(expected)} seeded slides, {len(controls)} clean controls -> {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
