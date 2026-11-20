# Stress decks

Decks that test how Redline copes with unusual files rather than with the house style: charts and pictures, nested groups with no title, merged and empty table cells, right-to-left, CJK and emoji text, long and hidden slides, a 300-slide deck, and A4-portrait and tiny-square slide sizes.

They are generated from python-pptx's blank template, so they contain no third-party content:

```bash
python scripts/generate_stress_decks.py docs/testing/decks/stress
```

Product edge cases (empty, corrupt, password-protected and oversized files, zip bombs, too many slides) are created inside the back-end tests rather than committed as files.
