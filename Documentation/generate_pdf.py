from __future__ import annotations

from datetime import datetime
from pathlib import Path
import textwrap


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "demandiq_project_report_matched.md"
OUTPUT = ROOT / "DemandIQ_Project_Documentation_Report_Matched_Format.pdf"

PAGE_WIDTH = 595
PAGE_HEIGHT = 842
LEFT = 56
RIGHT = 56
TOP = 72
BOTTOM = 64
LINE_GAP = 6
FONT_REG = "F1"
FONT_BOLD = "F2"


def escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def wrap_text(text: str, width: int) -> list[str]:
    if not text.strip():
        return [""]
    return textwrap.wrap(
        text,
        width=width,
        break_long_words=False,
        break_on_hyphens=False,
        replace_whitespace=False,
    ) or [text]


def parse_source(path: Path) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if line.strip() == "---":
            blocks.append(("spacer", ""))
            continue
        if line.startswith("# "):
            blocks.append(("h1", line[2:].strip()))
        elif line.startswith("## "):
            blocks.append(("h2", line[3:].strip()))
        elif line.startswith("### "):
            blocks.append(("h3", line[4:].strip()))
        elif line.startswith("- "):
            blocks.append(("bullet", line[2:].strip()))
        elif line[:2].isdigit() and line[1] == ".":
            blocks.append(("para", line))
        else:
            blocks.append(("para", line))
    return blocks


def build_lines(blocks: list[tuple[str, str]]) -> list[dict[str, object]]:
    lines: list[dict[str, object]] = []
    for kind, text in blocks:
        if kind == "spacer":
            lines.append({"text": "", "size": 10, "font": FONT_REG, "gap_after": 8})
            continue
        if kind == "h1":
            for part in wrap_text(text, 36):
                lines.append({"text": part, "size": 20, "font": FONT_BOLD, "gap_after": 4})
            lines.append({"text": "", "size": 10, "font": FONT_REG, "gap_after": 8})
            continue
        if kind == "h2":
            for part in wrap_text(text, 52):
                lines.append({"text": part, "size": 15, "font": FONT_BOLD, "gap_after": 3})
            lines.append({"text": "", "size": 10, "font": FONT_REG, "gap_after": 5})
            continue
        if kind == "h3":
            for part in wrap_text(text, 60):
                lines.append({"text": part, "size": 12, "font": FONT_BOLD, "gap_after": 2})
            continue
        if kind == "bullet":
            wrapped = wrap_text(text, 78)
            first = True
            for part in wrapped:
                prefix = "- " if first else "  "
                lines.append({"text": f"{prefix}{part}", "size": 11, "font": FONT_REG, "gap_after": 1})
                first = False
            continue
        wrapped = wrap_text(text, 88)
        for part in wrapped:
            lines.append({"text": part, "size": 11, "font": FONT_REG, "gap_after": 1})
        if text.strip():
            lines.append({"text": "", "size": 10, "font": FONT_REG, "gap_after": 4})
    return lines


def paginate(lines: list[dict[str, object]]) -> list[list[dict[str, object]]]:
    pages: list[list[dict[str, object]]] = []
    current: list[dict[str, object]] = []
    y = PAGE_HEIGHT - TOP
    for line in lines:
        size = int(line["size"])
        step = size + LINE_GAP + int(line.get("gap_after", 0))
        if y - step < BOTTOM:
            pages.append(current)
            current = []
            y = PAGE_HEIGHT - TOP
        current.append(line)
        y -= step
    if current:
        pages.append(current)
    return pages


def page_stream(page_lines: list[dict[str, object]], page_no: int, total_pages: int) -> str:
    parts = ["BT"]
    y = PAGE_HEIGHT - TOP
    for line in page_lines:
        text = str(line["text"])
        size = int(line["size"])
        font = str(line["font"])
        parts.append(f"/{font} {size} Tf")
        parts.append(f"1 0 0 1 {LEFT} {y} Tm")
        parts.append(f"({escape_pdf_text(text)}) Tj")
        y -= size + LINE_GAP + int(line.get("gap_after", 0))
    footer = f"DemandIQ Project Documentation Report | Page {page_no} of {total_pages} | Generated {datetime.now().strftime('%d-%m-%Y')}"
    parts.extend(
        [
            f"/{FONT_REG} 9 Tf",
            f"1 0 0 1 {LEFT} 28 Tm",
            f"({escape_pdf_text(footer)}) Tj",
            "ET",
        ]
    )
    return "\n".join(parts)


def build_pdf(pages: list[list[dict[str, object]]], output: Path) -> None:
    objects: list[bytes] = []

    def add_object(data: str | bytes) -> int:
        payload = data.encode("latin-1", errors="replace") if isinstance(data, str) else data
        objects.append(payload)
        return len(objects)

    catalog_id = add_object("<< /Type /Catalog /Pages 2 0 R >>")
    pages_id = add_object("<< /Type /Pages /Count 0 /Kids [] >>")
    font1_id = add_object("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    font2_id = add_object("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")

    page_ids: list[int] = []
    content_ids: list[int] = []
    total_pages = len(pages)
    for idx, page in enumerate(pages, start=1):
        stream = page_stream(page, idx, total_pages)
        content = f"<< /Length {len(stream.encode('latin-1', errors='replace'))} >>\nstream\n{stream}\nendstream"
        content_ids.append(add_object(content))
        page_obj = (
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            f"/Resources << /Font << /{FONT_REG} {font1_id} 0 R /{FONT_BOLD} {font2_id} 0 R >> >> "
            f"/Contents {content_ids[-1]} 0 R >>"
        )
        page_ids.append(add_object(page_obj))

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[pages_id - 1] = f"<< /Type /Pages /Count {len(page_ids)} /Kids [{kids}] >>".encode("latin-1")
    objects[catalog_id - 1] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("latin-1")

    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{idx} 0 obj\n".encode("latin-1"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")
    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    pdf.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        pdf.extend(f"{off:010d} 00000 n \n".encode("latin-1"))
    pdf.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF"
        ).encode("latin-1")
    )
    output.write_bytes(pdf)


def main() -> None:
    blocks = parse_source(SOURCE)
    lines = build_lines(blocks)
    pages = paginate(lines)
    build_pdf(pages, OUTPUT)
    print(f"Created: {OUTPUT}")


if __name__ == "__main__":
    main()
