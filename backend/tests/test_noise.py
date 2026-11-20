from app.model.types import Finding
from app.noise import apply_noise_policy


def f(rule, slide, element="e1", severity="warning", expected=None, fid=None):
    return Finding(id=fid or f"{rule}:{slide}:{element}", rule_id=rule, severity=severity, origin="code",
                   slide=slide, element_id=f"s{slide}-{element}", title=rule, message=rule, expected=expected)


def test_preserves_independent_rules_and_dedupes_identical_occurrences():
    out = apply_noise_policy([f("CONF_MOVED", 2), f("CONF_MISSING", 2, severity="error"), f("CONF_MOVED", 2)])
    assert [x.rule_id for x in out] == ["CONF_MISSING", "CONF_MOVED"]


def test_groups_repeats_on_three_or_more_slides():
    out = apply_noise_policy([f("TITLE_PUNCTUATION", s, expected="no ending punctuation") for s in (3, 5, 9)])
    assert len({x.group_id for x in out}) == 1 and out[0].group_id


def test_no_group_below_threshold_and_sorted():
    out = apply_noise_policy([f("TERM_TGT", 4), f("TERM_TGT", 2), f("CONF_MISSING", 9, severity="error")])
    assert all(x.group_id is None for x in out)
    assert [(x.rule_id, x.slide) for x in out] == [("CONF_MISSING", 9), ("TERM_TGT", 2), ("TERM_TGT", 4)]


def test_independent_table_cell_occurrences_survive_noise_policy():
    first=f("TABLE_FONT",1,element="table",fid="TABLE_FONT:1:table:cell1")
    second=f("TABLE_FONT",1,element="table",fid="TABLE_FONT:1:table:cell2")
    second.actual="8 pt in cell2"
    output=apply_noise_policy([first,second,first.model_copy()])
    assert {x.id for x in output}=={first.id,second.id}


def test_confidentiality_wording_and_style_both_remain_actionable():
    output=apply_noise_policy([f("CONF_TEXT_MODIFIED",1),f("CONF_STYLE",1)])
    assert {x.rule_id for x in output}=={"CONF_TEXT_MODIFIED","CONF_STYLE"}
