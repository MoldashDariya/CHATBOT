import itertools

import pdfplumber
import pytest
from pdfminer.pdfdocument import PDFPasswordIncorrect

from bot.errors import ExtractionError
from bot.extractors import pdf as pdf_module
from bot.extractors.base import RawSource
from bot.extractors.pdf import PdfExtractor, strip_running_lines
from bot.pipeline.detector import SourceFormat
from tests.pdfgen import PageSpec, make_pdf

URL = "https://example.edu/rules.pdf"
HEADER = "Example University - Admissions Rules 2027"


def source(pdf: bytes) -> RawSource:
    return RawSource(url=URL, body=pdf, content_type="application/pdf")


def rules_pdf() -> bytes:
    return make_pdf(
        [
            PageSpec(
                header=HEADER,
                footer="Page 1 of 3",
                body=[
                    "Article 1. General provisions",
                    "Applications are accepted from 1 October 2026.",
                    "See https://example.edu/apply for the online form.",
                ],
            ),
            PageSpec(
                header=HEADER,
                footer="Page 2 of 3",
                body=["Article 2. Key dates", "The deadlines are listed below."],
                table=[
                    ["Stage", "Deadline"],
                    ["Early round", "1 November 2026"],
                    ["Regular round", "15 January 2027"],
                ],
            ),
            PageSpec(
                header=HEADER,
                footer="Page 3 of 3",
                body=["Article 3. Fees", "The application fee is 75 EUR."],
            ),
        ],
        title="Admissions Rules 2027",
    )


# --- extraction ---------------------------------------------------------------------------


async def test_extracts_text_title_and_page_counts() -> None:
    result = await PdfExtractor(max_pages=80).extract(source(rules_pdf()))

    assert result.source_format is SourceFormat.PDF
    assert result.source_url == URL
    assert result.title == "Admissions Rules 2027"
    assert result.meta == {"pages_total": "3", "pages_read": "3"}
    for fact in [
        "Applications are accepted from 1 October 2026.",
        "Article 2. Key dates",
        "The application fee is 75 EUR.",
    ]:
        assert fact in result.text


async def test_ruled_tables_keep_each_row_on_one_line_without_duplication() -> None:
    text = (await PdfExtractor(max_pages=80).extract(source(rules_pdf()))).text

    assert "| Early round | 1 November 2026 |" in text
    assert "| Regular round | 15 January 2027 |" in text
    # The table text must not also be present as loose body text.
    assert text.count("1 November 2026") == 1
    assert text.count("15 January 2027") == 1


async def test_page_numbers_and_repeated_header_are_removed() -> None:
    text = (await PdfExtractor(max_pages=80).extract(source(rules_pdf()))).text

    assert "Page 1 of 3" not in text
    assert "Page 2 of 3" not in text  # sits above the table on page 2: must still go
    assert "Page 3 of 3" not in text
    assert text.count(HEADER) == 1  # the first occurrence is kept, repeats are dropped


async def test_links_found_in_the_text_are_reported() -> None:
    result = await PdfExtractor(max_pages=80).extract(source(rules_pdf()))
    assert result.links == ("https://example.edu/apply",)


async def test_page_limit_reads_the_first_pages_and_says_so() -> None:
    pages = [PageSpec(body=[f"Content of page {n} is here."]) for n in range(1, 6)]

    result = await PdfExtractor(max_pages=3).extract(source(make_pdf(pages)))

    assert result.meta["pages_total"] == "5"
    assert result.meta["pages_read"] == "3"
    assert "Content of page 3" in result.text
    assert "Content of page 4" not in result.text


async def test_time_budget_returns_what_was_read_so_far() -> None:
    pages = [PageSpec(body=[f"Content of page {n} is here."]) for n in range(1, 5)]
    # clock: deadline computed at t=0 (budget 10); page 1 starts at t=1, page 2 at t=99.
    ticks = itertools.chain([0.0, 1.0], itertools.repeat(99.0))

    result = await PdfExtractor(max_pages=80, time_budget_s=10, clock=lambda: next(ticks)).extract(
        source(make_pdf(pages))
    )

    assert result.meta["pages_read"] == "1"
    assert result.meta["pages_total"] == "4"
    assert "Content of page 1" in result.text
    assert "Content of page 2" not in result.text


async def test_time_budget_spent_before_the_first_page_is_an_error() -> None:
    ticks = itertools.chain([0.0], itertools.repeat(99.0))
    extractor = PdfExtractor(max_pages=80, time_budget_s=10, clock=lambda: next(ticks))

    with pytest.raises(ExtractionError) as exc_info:
        await extractor.extract(source(make_pdf([PageSpec(body=["text"])])))
    assert exc_info.value.reason == "timeout"


# --- refusals -----------------------------------------------------------------------------


async def test_pdf_without_text_layer_is_refused_as_a_scan() -> None:
    blank_pages = make_pdf([PageSpec(), PageSpec()])

    with pytest.raises(ExtractionError) as exc_info:
        await PdfExtractor(max_pages=80).extract(source(blank_pages))
    assert exc_info.value.reason == "scanned_pdf"


async def test_password_protected_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    def locked(*args: object, **kwargs: object) -> None:
        raise PDFPasswordIncorrect

    monkeypatch.setattr(pdfplumber, "open", locked)

    with pytest.raises(ExtractionError) as exc_info:
        await PdfExtractor(max_pages=80).extract(source(rules_pdf()))
    assert exc_info.value.reason == "encrypted_pdf"


@pytest.mark.parametrize(
    "body",
    [b"%PDF-1.4 this is not really a pdf", b"%PDF-1.4\n", bytes(range(256)) * 8, rules_pdf()[:300]],
    ids=["text-after-magic", "magic-only", "binary-noise", "truncated"],
)
async def test_broken_pdf_is_a_clean_error(body: bytes) -> None:
    with pytest.raises(ExtractionError) as exc_info:
        await PdfExtractor(max_pages=80).extract(source(body))
    assert exc_info.value.reason == "corrupt"


async def test_unexpected_parser_failure_is_contained(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(page: object) -> tuple[str, str]:
        raise RecursionError

    monkeypatch.setattr(pdf_module, "_page_parts", boom)

    with pytest.raises(ExtractionError) as exc_info:
        await PdfExtractor(max_pages=80).extract(source(rules_pdf()))
    assert exc_info.value.reason == "corrupt"


# --- strip_running_lines ------------------------------------------------------------------


def numbered_pages(count: int) -> list[str]:
    return [
        f"Example University | Rules\nAlpha text {n} first\nBeta text {n} second\n"
        f"Page {n} of {count}"
        for n in range(1, count + 1)
    ]


def test_repeated_header_dropped_after_first_page_and_page_numbers_dropped_everywhere() -> None:
    result = strip_running_lines(numbered_pages(4))

    assert result[0].startswith("Example University | Rules")
    for later in result[1:]:
        assert "Example University" not in later
    for page in result:
        assert "Page " not in page  # no counter left anywhere
    assert "Alpha text 3 first" in result[2]
    assert "Beta text 3 second" in result[2]


@pytest.mark.parametrize(
    "line",
    [
        "12",
        "Page 3",
        "page 3 of 10",
        "3 / 10",
        "3 of 10",
        "- 4 -",
        "– 4 –",
        "стр. 5",
        "Страница 5 из 9",
        "Бет 2",
    ],
)
def test_page_number_formats_are_recognised_at_page_edges(line: str) -> None:
    pages = [f"Body of page {n}.\nMore body text.\n{line}" for n in range(2)]
    result = strip_running_lines(pages)  # two pages: only page numbers are ever removed
    assert all(line not in page.split("\n") for page in result)
    assert all("More body text." in page for page in result)


def test_page_numbers_removed_even_on_a_single_page() -> None:
    assert strip_running_lines(["Hello world\nSecond line\n7"]) == ["Hello world\nSecond line"]


def test_numbered_headings_are_not_mistaken_for_running_lines() -> None:
    pages = [
        f"Article {n}\nText of article {n} goes here.\nMore text for article {n}."
        for n in range(1, 6)
    ]

    assert strip_running_lines(pages) == pages


def test_repeated_lines_in_the_middle_of_a_page_are_never_removed() -> None:
    middle = "This sentence is identical on every page"
    pages = [
        f"Heading {n}\nline 1 of p{n}\nline 2 of p{n}\n{middle}\n"
        f"line 4 of p{n}\nline 5 of p{n}\nTail {n}"
        for n in range(5)
    ]

    assert all(middle in page for page in strip_running_lines(pages))


def test_few_pages_do_not_trigger_header_detection() -> None:
    pages = ["Example University\nBody one", "Example University\nBody two"]
    assert strip_running_lines(pages) == pages


def test_line_repeated_on_only_some_pages_is_kept() -> None:
    pages = [
        "Confidential\nBody 1\nBody 2\nBody 3\nBody 4\nEnd 1",
        "Body 5\nBody 6\nBody 7\nBody 8\nBody 9\nEnd 2",
        "Body 10\nBody 11\nBody 12\nBody 13\nBody 14\nEnd 3",
        "Confidential\nBody 15\nBody 16\nBody 17\nBody 18\nEnd 4",
        "Body 19\nBody 20\nBody 21\nBody 22\nBody 23\nEnd 5",
    ]
    assert strip_running_lines(pages) == pages


def test_repeated_table_header_survives_on_the_first_page() -> None:
    pages = [
        "Programme | Deadline\nMedicine | 1 May\nLaw | 2 May\nEconomics | 2 May\nArt | 3 May",
        "Programme | Deadline\nPhysics | 3 May\nMath | 4 May\nBiology | 5 May\nChemistry | 6 May",
        "Programme | Deadline\nHistory | 7 May\nArt | 8 May\nMusic | 9 May\nDrama | 10 May",
    ]
    result = strip_running_lines(pages)

    assert "Programme | Deadline" in result[0]
    assert "Programme | Deadline" not in result[1] and "Programme | Deadline" not in result[2]
    assert "Physics | 3 May" in result[1]


def test_empty_input() -> None:
    assert strip_running_lines([]) == []
    assert strip_running_lines(["", ""]) == ["", ""]


def test_prose_mentioning_a_page_number_is_not_a_running_line() -> None:
    pages = [f"Content of page {n} is here." for n in range(1, 6)]
    assert strip_running_lines(pages) == pages


def test_counter_inside_a_header_line_still_matches() -> None:
    pages = [
        f"Rules 2027 | Page {n}\nUnique body number {n} text\nMore unique content {n}"
        for n in range(1, 5)
    ]
    result = strip_running_lines(pages)

    assert result[0].startswith("Rules 2027 | Page 1")
    assert all("Rules 2027" not in page for page in result[1:])
