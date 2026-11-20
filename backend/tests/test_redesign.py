"""Slide redesigns: units are read from the table and never guessed."""
import pytest

from app import redesign


# Two adversarial counterexamples (an unsupported headline, a negated bullet), replayed against the contract.


def _table(headers, rows):
    numeric = [i for i in range(len(headers)) if i and all(redesign.parse_number(r[i]) for r in rows if r[i])]
    return {"headers": headers, "rows": rows, "numeric": numeric, "labels": [0], "element_id": "t"}


ROWS = [[f"Peer {i}", f"${1000 + 100 * i:,}", f"{10 + i}%", f"{1.5 + i / 10:.1f}x", str(i)] for i in range(8)] + [
    ["Client Co", "$1,450", "12%", "2.4x", "3"]]


@pytest.mark.parametrize("headers,caption,expected", [
    (["($MM) Company", "Revenue", "Growth", "Multiple", "Count"], "", {1: "$MM"}),        # shared table marker
    (["Company", "Revenue", "Growth", "Multiple", "Count"], "Peer pay ($000s)", {1: "$000s"}),  # caption marker
    (["Company", "Revenue ($MMs)", "Growth", "Multiple", "Count"], "", {1: "$MM"}),        # column header marker
    (["Company", "Revenue", "Growth", "Multiple", "Count"], "", {1: ""}),                  # no marker: as the slide shows
])
def test_monetary_scale_is_carried_and_never_lent_to_ratios_or_counts(headers, caption, expected):
    units = redesign.column_units(_table(headers, ROWS), caption)
    assert units[1]["kind"] == "money" and units[1]["label"] == expected[1]
    assert (units[2]["kind"], units[2]["label"]) == ("percent", "")
    assert (units[3]["kind"], units[3]["label"]) == ("multiple", "")
    assert (units[4]["kind"], units[4]["label"]) == ("number", "")


def test_conflicting_scales_are_refused_not_guessed():
    table = _table(["($000s) Company", "Revenue ($MM)", "Growth", "Multiple", "Count"], ROWS)
    assert redesign.column_units(table)[1]["ambiguous"]
    texts = [t["text"] for t in redesign.templates(table, "Client")]
    assert texts and not any("Revenue" in t for t in texts)  # no comparison for the ambiguous column
    assert any("Multiple" in t for t in texts)  # other columns still get theirs


