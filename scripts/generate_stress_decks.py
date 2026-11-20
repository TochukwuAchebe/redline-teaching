"""Generate varied, non-ACME PowerPoint decks for robustness testing.

Each deck exercises structures real presentations contain that the two ACME
samples do not: native charts, pictures, grouped shapes, merged cells, odd slide
sizes, hidden slides, right-to-left and CJK text, empty placeholders and very
long text. Usage: python scripts/generate_stress_decks.py OUT_DIR
"""
import io
import sys
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Emu, Inches, Pt


def _png(color=(30, 90, 200), size=(320, 200)) -> io.BytesIO:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    buf.seek(0)
    return buf


def charts_and_pictures(out: Path):
    p = Presentation()
    p.slide_width, p.slide_height = Inches(13.333), Inches(7.5)
    s = p.slides.add_slide(p.slide_layouts[5])
    s.shapes.title.text = "Revenue grew 18% as recurring contracts doubled"
    data = CategoryChartData()
    data.categories = ["2022", "2023", "2024", "2025"]
    data.add_series("Revenue", (4.1, 4.6, 5.2, 6.1))
    data.add_series("Recurring", (1.2, 1.6, 2.2, 3.0))
    s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.8), Inches(1.6), Inches(7), Inches(5), data)
    s.shapes.add_picture(_png(), Inches(8.3), Inches(1.6), Inches(4.2))
    tb = s.shapes.add_textbox(Inches(0.8), Inches(6.7), Inches(7), Inches(0.4)).text_frame
    tb.text = "Source: company filings (1)"
    s2 = p.slides.add_slide(p.slide_layouts[5])
    s2.shapes.title.text = "Pie and line charts"
    pie = CategoryChartData()
    pie.categories = ["UK", "US", "EU"]
    pie.add_series("Mix", (0.4, 0.35, 0.25))
    s2.shapes.add_chart(XL_CHART_TYPE.PIE, Inches(0.8), Inches(1.6), Inches(5.5), Inches(5), pie)
    s2.shapes.add_chart(XL_CHART_TYPE.LINE, Inches(6.8), Inches(1.6), Inches(5.5), Inches(5), data)
    p.save(out / "charts-and-pictures-16x9.pptx")


def groups_and_shapes(out: Path):
    p = Presentation()
    s = p.slides.add_slide(p.slide_layouts[6])  # blank: no title at all
    grp = s.shapes.add_group_shape()
    for i in range(6):
        box = grp.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.5 + i * 1.5), Inches(2), Inches(1.3), Inches(1))
        box.text_frame.text = f"Step {i + 1}"
        box.fill.solid()
        box.fill.fore_color.rgb = RGBColor(0x8F, 0x2D, 0x56)
    inner = grp.shapes.add_group_shape()
    inner.shapes.add_shape(MSO_SHAPE.OVAL, Inches(4), Inches(4), Inches(2), Inches(2)).text_frame.text = "Nested"
    s.shapes.add_connector(1, Inches(1), Inches(3.5), Inches(9), Inches(3.5))
    p.save(out / "groups-nested-no-title.pptx")


def tricky_tables(out: Path):
    p = Presentation()
    s = p.slides.add_slide(p.slide_layouts[5])
    s.shapes.title.text = "Merged, empty and numeric table cells"
    t = s.shapes.add_table(6, 5, Inches(0.3), Inches(1.5), Inches(9.4), Inches(4)).table
    t.cell(0, 0).merge(t.cell(0, 1))
    t.cell(2, 2).merge(t.cell(4, 3))
    for r in range(6):
        for c in range(5):
            cell = t.cell(r, c)
            if cell.is_spanned:
                continue
            cell.text = "" if (r + c) % 4 == 0 else (f"{r * 1.2345:.3f}%" if c > 2 else f"Row {r} Percentile")
    p.save(out / "tables-merged-empty.pptx")


def international_text(out: Path):
    p = Presentation()
    for title, body in [
        ("نمو الإيرادات بنسبة ١٨٪", "النص من اليمين إلى اليسار\nسطر ثان"),
        ("收入增长百分之十八", "中文要点一\n中文要点二。"),
        ("Ünïcödé — émojis 🚀 and symbols ✓", "• Bullet with “smart quotes”\n– En dash item"),
    ]:
        s = p.slides.add_slide(p.slide_layouts[1])
        s.shapes.title.text = title
        s.placeholders[1].text = body
    p.save(out / "international-rtl-cjk-emoji.pptx")


def long_and_empty(out: Path):
    p = Presentation()
    s = p.slides.add_slide(p.slide_layouts[1])
    s.shapes.title.text = "A very long headline " * 12
    s.placeholders[1].text = "\n".join(f"Point {i}: " + "detail " * 40 for i in range(25))
    p.slides.add_slide(p.slide_layouts[1])  # untouched placeholders
    hidden = p.slides.add_slide(p.slide_layouts[5])
    hidden.shapes.title.text = "Hidden slide"
    hidden._element.set("show", "0")
    p.save(out / "long-text-empty-hidden.pptx")


def odd_sizes(out: Path):
    for name, (w, h) in {"a4-portrait": (Inches(8.27), Inches(11.69)), "tiny-square": (Inches(3), Inches(3))}.items():
        p = Presentation()
        p.slide_width, p.slide_height = Emu(int(w)), Emu(int(h))
        s = p.slides.add_slide(p.slide_layouts[1])
        s.shapes.title.text = f"{name} slide"
        s.placeholders[1].text = "Content"
        p.save(out / f"size-{name}.pptx")


def many_slides(out: Path, n=300):
    p = Presentation()
    for i in range(n):
        s = p.slides.add_slide(p.slide_layouts[1])
        s.shapes.title.text = f"Section {i // 10 + 1}: finding {i}"
        s.placeholders[1].text = "First point\nSecond point\nThird point"
        run = s.placeholders[1].text_frame.paragraphs[0].runs[0]
        run.font.size = Pt(12 + i % 5)
        run.font.color.rgb = RGBColor(i % 256, 40, 120)
    p.save(out / f"many-{n}-slides.pptx")


if __name__ == "__main__":
    target = Path(sys.argv[1])
    target.mkdir(parents=True, exist_ok=True)
    for make in (charts_and_pictures, groups_and_shapes, tricky_tables, international_text, long_and_empty, odd_sizes, many_slides):
        make(target)
    print("\n".join(sorted(str(f) for f in target.glob("*.pptx"))))
