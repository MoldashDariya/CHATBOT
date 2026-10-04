import asyncio
import io
import logging
import math
import re
import time
from collections.abc import Callable

import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect

from bot.errors import ExtractionError
from bot.extractors.base import ExtractedContent, RawSource, clean_meta, find_urls
from bot.pipeline.detector import SourceFormat

logger = logging.getLogger(__name__)

DEFAULT_TIME_BUDGET_S = 30.0
_EDGE_LINES = 2  # how many lines at the top and bottom of a page count as header/footer
_MIN_PAGES_FOR_RUNNING_LINES = 3
_RUNNING_LINE_SHARE = 0.6

_PAGE_WORDS = r"(?:page|p\.|стр\.?|страница|бет)"
_NUM = r"\d{1,4}"
_OF_TOTAL = rf"(?:\s*(?:of|из|/)\s*{_NUM})"  # " of 10", " из 9", " / 10"
# A whole line that is only a page number: "12", "Page 3", "Page 3 of 10", "3 / 10", "- 4 -"
_PAGE_NUMBER_RE = re.compile(
    rf"^(?:{_PAGE_WORDS}\s*)?{_NUM}{_OF_TOTAL}?$|^[-–—]\s*{_NUM}\s*[-–—]$", re.IGNORECASE
)
# A page counter at the start or end of a longer line ("Uni | Page 3", "3 of 10 - Rules").
# Mid-line occurrences are ordinary prose ("see page 3"), so they must not be normalised.
_COUNTER = rf"(?:{_PAGE_WORDS}\s*{_NUM}{_OF_TOTAL}?|{_NUM}{_OF_TOTAL})"
_PAGE_COUNTER_RE = re.compile(rf"^{_COUNTER}|{_COUNTER}$", re.IGNORECASE)


class PdfExtractor:
    """Text and ruled tables from PDFs with a text layer.

    Pages are read in order until `max_pages` or the time budget is reached; what was read
    is reported in `meta` so the caller can warn the user about partial coverage. Scans
    (no text layer) are refused: OCR is out of scope for the MVP.

    Parsing runs in a worker thread and cannot be interrupted mid-page, hence the page and
    time limits are checked between pages and the download size is capped upstream.
    """

    def __init__(
        self,
        max_pages: int,
        time_budget_s: float = DEFAULT_TIME_BUDGET_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_pages = max_pages
        self._time_budget_s = time_budget_s
        self._clock = clock

    async def extract(self, source: RawSource) -> ExtractedContent:
        return await asyncio.to_thread(self._extract, source)

    def _extract(self, source: RawSource) -> ExtractedContent:
        deadline = self._clock() + self._time_budget_s
        try:
            with pdfplumber.open(io.BytesIO(source.body)) as pdf:
                pages_total = len(pdf.pages)
                bodies: list[str] = []
                tables: list[str] = []
                for page in pdf.pages[: self._max_pages]:
                    if self._clock() > deadline:
                        break
                    body, table_rows = _page_parts(page)
                    bodies.append(body)
                    tables.append(table_rows)
                    page.flush_cache()
                title = _clean_title(pdf.metadata.get("Title"))
        except PDFPasswordIncorrect as exc:
            raise ExtractionError("encrypted_pdf") from exc
        except Exception as exc:
            logger.warning("PDF extraction failed: %s", type(exc).__name__, exc_info=True)
            raise ExtractionError("corrupt") from exc

        if not bodies:
            raise ExtractionError("timeout")  # budget spent before the first page finished

        # Headers/footers live in the page body, so they are stripped before tables are added
        # back (otherwise a footer would sit mid-text, away from the page edge).
        pages = [
            "\n\n".join(part for part in (body, table_rows) if part)
            for body, table_rows in zip(strip_running_lines(bodies), tables, strict=True)
        ]
        text = "\n\n".join(page for page in pages if page).strip()
        if not text:
            raise ExtractionError("scanned_pdf")

        return ExtractedContent(
            text=text,
            title=title,
            source_url=source.url,
            source_format=SourceFormat.PDF,
            links=find_urls(text),
            meta=clean_meta(pages_total=str(pages_total), pages_read=str(len(bodies))),
        )


def _page_parts(page: pdfplumber.page.Page) -> tuple[str, str]:
    """(body text, ruled tables as `| a | b |` rows so each row stays on one line)."""
    tables = page.find_tables()
    if not tables:
        return (page.extract_text() or "").strip(), ""

    boxes = [table.bbox for table in tables]

    def outside_tables(obj: dict[str, object]) -> bool:
        cx = (float(obj["x0"]) + float(obj["x1"])) / 2  # type: ignore[arg-type]
        cy = (float(obj["top"]) + float(obj["bottom"])) / 2  # type: ignore[arg-type]
        return not any(x0 <= cx <= x1 and top <= cy <= bottom for x0, top, x1, bottom in boxes)

    body = (page.filter(outside_tables).extract_text() or "").strip()
    blocks = ["\n".join(row for row in map(_table_row, table.extract()) if row) for table in tables]
    return body, "\n\n".join(block for block in blocks if block)


def _table_row(cells: list[str | None]) -> str:
    cleaned = [" ".join((cell or "").split()) for cell in cells]
    return f"| {' | '.join(cleaned)} |" if any(cleaned) else ""


def _clean_title(raw: object) -> str | None:
    title = " ".join(raw.split()) if isinstance(raw, str) else ""
    return title[:200] or None


def strip_running_lines(pages: list[str]) -> list[str]:
    """Remove page numbers and repeated headers/footers from the edges of each page.

    Only the first and last `_EDGE_LINES` lines of a page are ever touched. A repeated line
    is kept on the first page where it appears (a table header repeated on every page is
    still needed once; real running headers cost one line).
    """
    split = [page.split("\n") for page in pages]
    edges = [_edge_indexes(lines) for lines in split]

    counts: dict[str, int] = {}
    for lines, idxs in zip(split, edges, strict=True):
        for key in {_fingerprint(lines[i]) for i in idxs}:
            counts[key] = counts.get(key, 0) + 1

    threshold = max(_MIN_PAGES_FOR_RUNNING_LINES, math.ceil(_RUNNING_LINE_SHARE * len(pages)))
    repeated = (
        {key for key, n in counts.items() if n >= threshold}
        if len(pages) >= _MIN_PAGES_FOR_RUNNING_LINES
        else set()
    )

    seen_repeated: set[str] = set()
    result: list[str] = []
    for lines, idxs in zip(split, edges, strict=True):
        drop: set[int] = set()
        for i in idxs:
            line = lines[i].strip()
            key = _fingerprint(line)
            if _PAGE_NUMBER_RE.match(line):
                drop.add(i)
            elif key in repeated:
                if key in seen_repeated:
                    drop.add(i)
                seen_repeated.add(key)
        result.append("\n".join(line for i, line in enumerate(lines) if i not in drop).strip())
    return result


def _edge_indexes(lines: list[str]) -> list[int]:
    non_empty = [i for i, line in enumerate(lines) if line.strip()]
    return sorted(set(non_empty[:_EDGE_LINES] + non_empty[-_EDGE_LINES:]))


def _fingerprint(line: str) -> str:
    """Normalise a page counter at the line edge only, so "Uni | Page 1 of 9" and "Uni | Page 2
    of 9" match while numbered headings ("Article 1") and prose ("see page 3") stay distinct."""
    return _PAGE_COUNTER_RE.sub("#", " ".join(line.lower().split()))
