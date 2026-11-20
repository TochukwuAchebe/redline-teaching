"""Generate new review content with a hidden middle slide for integration tests.

An optional supplied template retains only its master/layout/theme, removing all
original slides and their notes. The output is a test artifact, not a client deck.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from pptx import Presentation


def generate(output: Path, template: Path | None = None) -> None:
    deck = Presentation(str(template)) if template else Presentation()
    for slide_id in list(deck.slides._sldIdLst):
        deck.part.drop_rel(slide_id.rId)
        deck.slides._sldIdLst.remove(slide_id)
    layout = next((item for item in deck.slide_layouts
                   if any(p.placeholder_format.type in (2, 7) for p in item.placeholders)),
                  deck.slide_layouts[0])
    for number in range(1, 4):
        slide = deck.slides.add_slide(layout)
        if slide.shapes.title is not None:
            slide.shapes.title.text = f'Operating results improved in region {number}'
        body = next((p for p in slide.placeholders if p.placeholder_format.type in (2, 7)), None)
        if body is not None:
            body.text = 'Revenue increased during the quarter\nDelivery costs decreased during the quarter'
        if number == 2:
            slide._element.set('show', '0')
    deck.save(str(output))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--template', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    generate(arguments.output, arguments.template)
