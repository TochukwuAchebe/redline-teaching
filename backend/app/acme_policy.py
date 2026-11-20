"""ACME interpretation: every written requirement is checked; disputed ones are suggestions.

A requirement is disputed when the guideline's own examples contradict it, or when
ACME's annotated reviewer evidently doesn't apply it. The conflict is stated in the
finding so the reviewer can decide.
"""

from app.model.types import Finding

CONFLICTS = {
    "TITLE_VERTICAL_ALIGNMENT": "The detailed headline rule specifies vertical centring (line 36); the later example uses bottom alignment (line 230).",
    "TABLE_HEADER_STYLE": "Detailed header rule specifies blue fill; the later example discourages shaded headers.",
    "TABLE_ZEBRA": "Detailed body rule specifies alternating shading; the later example discourages shading.",
    "TABLE_STATS_ROW": "Detailed statistics rule specifies yellow fill; the later example discourages shading.",
    "TABLE_CLIENT_ROW": "Detailed client rule specifies green fill; the later example discourages shading.",
    "MARGIN_EDGE": "The written rule asks for 1 inch from the left edge; ACME's annotated sample decks don't apply it to full-width tables and charts.",
}


def apply_interpretation(findings: list[Finding]) -> list[Finding]:
    for finding in findings:
        conflict = CONFLICTS.get(finding.rule_id)
        if conflict:
            finding.severity = "suggestion"
            finding.rule_ref += (
                " · disputed: written rule checked as a suggestion"
            )
            finding.message += " Review this suggestion: " + conflict
    return findings
