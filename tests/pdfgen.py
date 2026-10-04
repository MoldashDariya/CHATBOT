"""Builds small but valid PDF files for extractor tests (no extra dependency needed).

Latin-1 text only (standard Helvetica, no font embedding). Each page can carry a header and a
footer line at fixed positions, body lines, and a ruled table.
"""

from dataclasses import dataclass, field

PAGE_W, PAGE_H = 612, 792
BODY_TOP = 700
LINE_H = 16
TABLE_TOP = 480
ROW_H = 24
COL_W = 170
LEFT = 60


@dataclass
class PageSpec:
    body: list[str] = field(default_factory=list)
    header: str | None = None
    footer: str | None = None
    table: list[list[str]] | None = None  # drawn with ruling lines, so it is detected as a table


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _text(x: float, y: float, text: str, size: int = 11) -> str:
    return f"BT /F1 {size} Tf {x} {y} Td ({_esc(text)}) Tj ET\n"


def _page_stream(page: PageSpec) -> bytes:
    ops: list[str] = []
    if page.header:
        ops.append(_text(LEFT, 760, page.header, 9))
    for i, line in enumerate(page.body):
        ops.append(_text(LEFT, BODY_TOP - i * LINE_H, line))
    if page.table:
        rows, cols = len(page.table), max(len(r) for r in page.table)
        for r in range(rows + 1):  # horizontal rules
            y = TABLE_TOP - r * ROW_H
            ops.append(f"{LEFT} {y} m {LEFT + cols * COL_W} {y} l S\n")
        for c in range(cols + 1):  # vertical rules
            x = LEFT + c * COL_W
            ops.append(f"{x} {TABLE_TOP} m {x} {TABLE_TOP - rows * ROW_H} l S\n")
        for r, row in enumerate(page.table):
            for c, cell in enumerate(row):
                ops.append(_text(LEFT + c * COL_W + 6, TABLE_TOP - r * ROW_H - 16, cell))
    if page.footer:
        ops.append(_text(LEFT, 30, page.footer, 9))
    return "".join(ops).encode("latin-1")


def make_pdf(pages: list[PageSpec], *, title: str | None = None) -> bytes:
    # Object numbering: 1 catalog, 2 page tree, 3 font, 4 info, then (page, content) pairs.
    objects: dict[int, bytes] = {}
    page_ids = [5 + 2 * i for i in range(len(pages))]
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode()
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    objects[4] = f"<< /Title ({_esc(title)}) >>".encode("latin-1") if title else b"<< >>"
    for pid, page in zip(page_ids, pages, strict=True):
        stream = _page_stream(page)
        objects[pid] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {pid + 1} 0 R >>"
        ).encode()
        objects[pid + 1] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream"

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += f"{number} 0 obj\n".encode() + objects[number] + b"\nendobj\n"

    xref_at = len(out)
    size = max(objects) + 1
    out += f"xref\n0 {size}\n".encode() + b"0000000000 65535 f \n"
    for number in range(1, size):
        out += f"{offsets[number]:010d} 00000 n \n".encode()
    trailer = f"<< /Size {size} /Root 1 0 R /Info 4 0 R >>"
    out += f"trailer\n{trailer}\nstartxref\n{xref_at}\n%%EOF\n".encode()
    return bytes(out)
