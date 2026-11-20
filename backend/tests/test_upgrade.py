"""Design upgrades: figures are parsed with their units before anything is charted."""
from app import upgrade


def test_numbers_parse_with_their_units():
    assert upgrade.parse_number("$10,065") == (10065, "$")
    assert upgrade.parse_number("17.8X") == (17.8, "x")
    assert upgrade.parse_number("5.6%") == (5.6, "%")
    assert upgrade.parse_number("12/31") is None
