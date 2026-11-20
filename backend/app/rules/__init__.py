"""Deterministic, evidence-bearing checks over the frozen Deck contract."""
from app.model.types import Deck, Finding
from app.rules.common import Reporter, load_config


def run_rules(deck: Deck, config: dict | None = None) -> list[Finding]:
    from app.rules.checks import check_slides
    from app.rules.consistency import check_consistency

    reporter = Reporter(load_config(config))
    check_slides(deck, reporter)
    check_consistency(deck, reporter)
    priority = {'error': 0, 'warning': 1, 'suggestion': 2}
    return sorted(reporter.findings.values(), key=lambda f: (priority[f.severity], f.slide, f.id))
