from pathlib import Path

import pytest

from bot.errors import TooLittleTextError
from bot.extractors.base import ExtractedContent, RawSource
from bot.extractors.html import HtmlExtractor
from bot.pipeline.cleaner import (
    MIN_TEXT_CHARS,
    TRUNCATION_MARKER,
    clean_content,
    ensure_enough_text,
)
from bot.pipeline.detector import SourceFormat

FIXTURES = Path(__file__).parent / "fixtures"
BIG = 1_000_000


def make(
    text: str,
    fmt: SourceFormat = SourceFormat.HTML,
    links: tuple[str, ...] = (),
    meta: dict[str, str] | None = None,
) -> ExtractedContent:
    return ExtractedContent(
        text=text,
        title="T",
        source_url="https://example.edu/page",
        source_format=fmt,
        links=links,
        meta=meta or {},
    )


def cleaned(text: str, fmt: SourceFormat = SourceFormat.HTML, max_chars: int = BIG) -> str:
    return clean_content(make(text, fmt), max_chars).text


# --- characters ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a\xa0b", "a b"),  # no-break space
        ("a b c", "a b c"),  # thin and hair spaces (seen on Wikipedia)
        ("a　b", "a b"),  # ideographic space
        ("a b", "a b"),  # narrow no-break space
        ("zero\u200bwidth", "zerowidth"),
        ("\ufeffbom at start", "bom at start"),
        ("soft\u00adhyphen", "softhyphen"),
        ("a‎b‏c", "abc"),  # direction marks
        ("a‪b‬c", "abc"),  # bidi embedding controls
        ("tab\tseparated", "tab separated"),
        ("nul\x00and\x1bescape", "nulandescape"),
        ("école", "école"),  # NFC composition
        ("Приём документов", "Приём документов"),
        ("family 👨‍👩‍👧", "family 👨‍👩‍👧"),  # ZWJ kept for emoji
    ],
)
def test_characters(raw: str, expected: str) -> None:
    for fmt in SourceFormat:
        assert cleaned(raw, fmt) == expected


def test_all_line_break_styles_become_newlines() -> None:
    assert cleaned("a\r\nb\rc\x0cd\u2028e\u2029f") == "a\nb\nc\nd\ne\nf"


# --- whitespace ---------------------------------------------------------------------------


def test_whitespace_runs_and_line_ends_are_normalised() -> None:
    text = "  Hello    world  \n\n\n\n   Next \t paragraph   \n\n\n"
    assert cleaned(text, SourceFormat.PDF) == "Hello world\n\nNext paragraph"


def test_table_cells_are_tidied_but_rows_stay_intact() -> None:
    text = "|  Stage   |  Deadline  |  \n|  Early  round |  1 November  2026 |"
    assert cleaned(text) == "| Stage | Deadline |\n| Early round | 1 November 2026 |"


def test_empty_input() -> None:
    assert cleaned("") == ""
    assert cleaned(" \n\t\n ") == ""


# --- wrapped lines (HTML) -----------------------------------------------------------------


def test_indented_continuation_lines_join_their_paragraph() -> None:
    text = (
        "Tuition is 14,500 EUR per year. The application fee\n"
        "         is 75 EUR and is non-refundable. Merit\n"
        "         scholarships are available."
    )
    assert cleaned(text) == (
        "Tuition is 14,500 EUR per year. The application fee is 75 EUR and is non-refundable. "
        "Merit scholarships are available."
    )


def test_unindented_lines_are_never_guessed_to_be_continuations() -> None:
    # A deadline row split over lines must not be glued together by a lowercase heuristic.
    text = (
        "Scholarship applications for undergraduate students 1 December 2026\n"
        "regular round 15 January 2027"
    )
    assert cleaned(text) == text


@pytest.mark.parametrize(
    "structural",
    [
        "- list item",
        "* star item",
        "• bullet",
        "– dash item",
        "1. first",
        "2) second",
        "| a | b |",
        "# Heading",
        "> quote",
    ],
)
def test_structure_is_never_joined_to_the_line_before_or_after(structural: str) -> None:
    after = f"Intro line of prose\n     {structural}"
    before = f"{structural}\n     continuation of prose"
    assert cleaned(after).count("\n") == 1
    assert cleaned(before).count("\n") == 1


def test_indented_line_after_a_blank_line_starts_a_new_paragraph() -> None:
    assert (
        cleaned("First paragraph.\n\n    Second paragraph.")
        == "First paragraph.\n\nSecond paragraph."
    )


def test_other_formats_are_never_joined() -> None:
    text = "Line one of a PDF\n     indented line two"
    for fmt in (SourceFormat.PDF, SourceFormat.DOCX, SourceFormat.YOUTUBE):
        assert cleaned(text, fmt) == "Line one of a PDF\nindented line two"


# --- web noise (HTML only) ----------------------------------------------------------------

NOISE_LINES = [
    "Skip to content",
    "Skip contents",
    "Skip to main content",
    "Back to top",
    "Share",
    "Share this page",
    "Print this page",
    "Read more »",
    "Learn more",
    "Continue reading",
    "Menu",
    "Close",
    "Subscribe",
    "Accept all cookies",
    "Читать далее",
    "Поделиться",
    "Вернуться наверх",
    "We use cookies to improve your experience. Accept all cookies",
    "This website uses cookies to ensure you get the best experience.",
    "Мы используем файлы cookie для улучшения работы сайта.",
    "© 2026 Example University. All rights reserved.",
    "© 2026 [Example University](https://example.edu). All rights reserved.",
    "Copyright 2026 Example University",
    "Все права защищены.",
    "Subscribe to our newsletter for updates",
    "[",
    "]",
    "[edit](https://en.wikipedia.org/w/index.php?title=Rhodes_Scholarship&action=edit&section=1)]",
]

CONTENT_LINES = [
    "Applicants can read more about the cookie policy at the end of the application form.",
    "Search for scholarships by country of study",
    "Menu of undergraduate programmes",
    "Print this page and bring it to the embassy interview",
    "Closing date: 1 May 2027",
    "The deadline to share your results with the committee is 1 May.",
    "[Read more](https://example.edu/programme-details)",  # may be the only pointer to details
    "Share of international students: 24%",
    "Copyright holders must submit the form by 2027-03-01 at the latest.",
    "Subscribers to the scholarship list receive updates by email.",
]


@pytest.mark.parametrize("noise", NOISE_LINES)
def test_web_noise_is_removed_from_html(noise: str) -> None:
    text = f"Deadline is 1 May 2027.\n{noise}\nApply online."
    assert cleaned(text) == "Deadline is 1 May 2027.\nApply online."


@pytest.mark.parametrize("noise", NOISE_LINES)
def test_the_same_lines_are_kept_for_other_formats(noise: str) -> None:
    for fmt in (SourceFormat.PDF, SourceFormat.DOCX, SourceFormat.YOUTUBE):
        assert " ".join(noise.split()) in cleaned(f"before\n{noise}\nafter", fmt)


@pytest.mark.parametrize("line", CONTENT_LINES)
def test_real_content_that_resembles_noise_is_kept(line: str) -> None:
    assert cleaned(f"Intro.\n{line}\nOutro.") == f"Intro.\n{line}\nOutro."


def test_links_that_only_lived_in_removed_noise_are_dropped() -> None:
    kept, gone = "https://example.edu/apply", "https://example.edu"
    content = make(
        "Apply at [the portal](https://example.edu/apply).\n"
        "© 2026 [Example University](https://example.edu). All rights reserved.",
        links=(kept, gone),
    )
    assert clean_content(content, BIG).links == (kept,)


# --- footnote markers (HTML only) ---------------------------------------------------------


def test_citation_markers_glued_to_text_are_removed_from_html() -> None:
    assert cleaned("Established in 1902.[1][2] Its founder[34] wanted unity.") == (
        "Established in 1902. Its founder wanted unity."
    )


@pytest.mark.parametrize(
    "text",
    [
        "Option [1] is cheaper than option [2].",  # spaced: a label, not a citation
        "See [1](https://example.edu/ref) for details.",  # a link whose text is a number
        "Table[1](https://example.edu/ref) lists the dates.",  # same, glued to the word before
    ],
)
def test_bracketed_numbers_that_are_content_are_kept(text: str) -> None:
    assert cleaned(text) == text


def test_citation_markers_are_kept_outside_html() -> None:
    assert cleaned("Result.[1] More.", SourceFormat.PDF) == "Result.[1] More."


def test_wikipedia_style_sample() -> None:
    text = (
        "Rhodes Scholarship\n"
        "The scholarship is awarded for postgraduate study at Oxford.[1][2]\n"
        "History\n"
        "[\n"
        "[edit](https://en.wikipedia.org/w/index.php?title=Rhodes_Scholarship&action=edit&section=1)]\n"
        "Established in 1902, it is one of the oldest awards.[3]"
    )
    assert cleaned(text) == (
        "Rhodes Scholarship\n"
        "The scholarship is awarded for postgraduate study at Oxford.\n"
        "History\n"
        "Established in 1902, it is one of the oldest awards."
    )


# --- duplicates ---------------------------------------------------------------------------


def test_adjacent_duplicate_lines_are_removed_in_every_format() -> None:
    line = "Applications close on 1 May 2027."
    for fmt in SourceFormat:
        assert cleaned(f"{line}\n{line}\nNext.", fmt) == f"{line}\nNext."


def test_duplicates_separated_only_by_blank_lines_count_as_adjacent() -> None:
    line = "Applications close on 1 May 2027."
    assert cleaned(f"{line}\n\n\n{line.upper()}\n\nNext.") == f"{line}\n\nNext."


def test_short_repeated_lines_and_table_rows_are_kept() -> None:
    assert cleaned("Yes\nYes\nNo") == "Yes\nYes\nNo"
    rows = "| Law | 1 May |\n| Law | 1 May |"
    assert cleaned(rows) == rows


def test_long_paragraph_repeated_later_is_dropped_for_html_only() -> None:
    paragraph = (
        "Eligible applicants must provide proof of admission from a partner university. " * 3
    )
    text = f"{paragraph.strip()}\nSomething else in between.\n{paragraph.strip()}"

    assert cleaned(text, SourceFormat.HTML).count("Eligible applicants") == 3  # one copy kept
    assert cleaned(text, SourceFormat.PDF).count("Eligible applicants") == 6  # parallel clauses


def test_medium_length_repeats_are_kept_even_for_html() -> None:
    clause = "Applicants must hold an IELTS certificate with at least 6.5 overall."  # ~70 chars
    text = f"Programme A\n{clause}\nProgramme B\n{clause}"
    assert cleaned(text) == text


# --- truncation ---------------------------------------------------------------------------


def numbered_lines(count: int) -> str:
    return "\n".join(f"Line number {n:04d} with some padding text." for n in range(count))


def test_text_within_the_limit_is_untouched() -> None:
    text = numbered_lines(10)
    result = clean_content(make(text, SourceFormat.PDF), len(text))

    assert result.text == text
    assert "truncated" not in result.meta
    assert TRUNCATION_MARKER not in result.text


def test_long_text_is_cut_at_a_line_break_with_a_marker_and_meta() -> None:
    text = numbered_lines(500)
    # 2011 is chosen so the budget falls in the middle of a line, not on a line boundary.
    result = clean_content(make(text, SourceFormat.PDF), 2011)

    assert len(result.text) <= 2011
    assert result.text.endswith(TRUNCATION_MARKER)
    body = result.text.removesuffix(TRUNCATION_MARKER).rstrip().split("\n")
    assert all(line.startswith("Line number") and line.endswith("padding text.") for line in body)
    assert result.meta["truncated"] == "true"
    assert result.meta["original_chars"] == str(len(text))


def test_a_single_huge_line_is_cut_at_the_budget() -> None:
    result = clean_content(make("word " * 5000, SourceFormat.YOUTUBE), 1000)

    assert len(result.text) <= 1000
    assert result.text.endswith(TRUNCATION_MARKER)


def test_a_tiny_limit_degrades_to_a_plain_cut() -> None:
    result = clean_content(make("x" * 500, SourceFormat.PDF), 50)

    assert result.text == "x" * 50
    assert result.meta["truncated"] == "true"


def test_links_after_the_cut_are_dropped() -> None:
    early, late = "https://example.edu/early", "https://example.edu/late"
    text = f"Start {early}\n" + numbered_lines(300) + f"\nEnd {late}"
    result = clean_content(make(text, SourceFormat.DOCX, links=(early, late)), 3000)

    assert result.links == (early,)


def test_the_limit_applies_to_the_cleaned_text_not_the_raw_text() -> None:
    padded = "word   " * 100  # 700 chars raw, 499 once whitespace is collapsed
    result = clean_content(make(padded, SourceFormat.PDF), 500)

    assert "truncated" not in result.meta


# --- contract -----------------------------------------------------------------------------


def test_the_input_is_never_modified() -> None:
    content = make("Hello\xa0world\n\n\n" + "x" * 50, SourceFormat.PDF, meta={"pages_total": "3"})
    before = (content.text, dict(content.meta), content.links)

    result = clean_content(content, 20)

    assert (content.text, dict(content.meta), content.links) == before
    assert result is not content
    assert result.meta["pages_total"] == "3"  # existing meta is carried over
    assert result.title == content.title and result.source_url == content.source_url


def test_links_stay_a_subset_of_the_text_and_keep_their_order() -> None:
    a, b, c = (f"https://example.edu/{name}" for name in "abc")
    content = make(f"See {c} and {a} and [x]({b}).", links=(a, b, c, "https://example.edu/gone"))

    result = clean_content(content, BIG)

    assert result.links == (a, b, c)
    assert all(link in result.text for link in result.links)


MESSY_INPUTS = [
    "",
    "plain text",
    "  a   b  \r\n\r\n\r\nc\u200b\xa0d\t\te",
    "Line\n     wrapped\n- item\n     | not joined |\n| row |\n| row |\n[\n[edit](https://x.org/?a=1)]",
    "Text.[1][2] again.[3]\nSkip to content\nShare\n" + "dup line over ten chars\n" * 3,
    ("Paragraph long enough to count as page builder duplication, " * 5 + "\n") * 3,
    "x" * 5000,
    numbered_lines(200),
]


@pytest.mark.parametrize("fmt", list(SourceFormat))
@pytest.mark.parametrize("text", MESSY_INPUTS)
@pytest.mark.parametrize("limit", [BIG, 1500, 60])
def test_cleaning_is_idempotent(text: str, fmt: SourceFormat, limit: int) -> None:
    once = clean_content(make(text, fmt), limit)
    twice = clean_content(once, limit)

    assert twice.text == once.text
    assert twice.links == once.links


# --- ensure_enough_text -------------------------------------------------------------------


def test_threshold_is_exactly_the_documented_one() -> None:
    assert MIN_TEXT_CHARS == 300
    ensure_enough_text(make("x" * 300))  # passes
    with pytest.raises(TooLittleTextError) as exc_info:
        ensure_enough_text(make("x" * 299))
    assert (exc_info.value.chars, exc_info.value.minimum) == (299, 300)


@pytest.mark.parametrize("fmt", list(SourceFormat))
def test_error_carries_the_format_for_the_user_message(fmt: SourceFormat) -> None:
    with pytest.raises(TooLittleTextError) as exc_info:
        ensure_enough_text(make("short", fmt))
    assert exc_info.value.source_format == fmt


def test_custom_threshold() -> None:
    ensure_enough_text(make("x" * 50), min_chars=50)
    with pytest.raises(TooLittleTextError):
        ensure_enough_text(make("x" * 49), min_chars=50)


async def extract_fixture(name: str, url: str) -> ExtractedContent:
    body = (FIXTURES / name).read_bytes()
    return await HtmlExtractor().extract(RawSource(url=url, body=body, content_type="text/html"))


async def test_javascript_shell_is_refused_after_cleaning() -> None:
    content = clean_content(await extract_fixture("js_shell.html", "https://portal.example/"), BIG)

    with pytest.raises(TooLittleTextError) as exc_info:
        ensure_enough_text(content)
    assert exc_info.value.source_format == SourceFormat.HTML


async def test_whitespace_padding_does_not_count_as_text() -> None:
    padded = make("short" + " \n \u200b" * 400)
    with pytest.raises(TooLittleTextError):
        ensure_enough_text(clean_content(padded, BIG))


async def test_real_page_passes_through_the_whole_clean_up() -> None:
    url = "https://www.example.edu/admissions/undergraduate"
    extracted = await extract_fixture("university_admissions.html", url)

    result = clean_content(extracted, BIG)
    ensure_enough_text(result)

    # Hard-wrapped source lines are one sentence again, and nothing is left to tidy.
    assert (
        "Tuition for international students is 14,500 EUR per academic year. The application fee "
        "is 75 EUR and is non-refundable."
    ) in result.text
    assert "  " not in result.text
    assert "| Early round | 1 November 2026 |" in result.text
    assert result.links == extracted.links  # three real links, all still in the text
    assert len(result.links) == 3


@pytest.mark.parametrize(
    ("text", "present"),
    [
        ("see https://example.edu/apply.", True),  # trailing full stop is punctuation
        ("see (https://example.edu/apply)", True),
        ("see [x](https://example.edu/apply) now", True),
        ("https://example.edu/apply, then", True),
        ("https://example.edu/apply\nnext line", True),
        ("https://example.edu/apply/form", False),  # a longer URL, not this one
        ("https://example.edu/apply?x=1", False),
        ("https://example.edu/apply.html", False),
        ("https://example.edu/application", False),
        ("no link here", False),
    ],
)
def test_link_presence_is_judged_on_whole_urls(text: str, present: bool) -> None:
    url = "https://example.edu/apply"
    result = clean_content(make(text, SourceFormat.PDF, links=(url,)), BIG)
    assert (url in result.links) is present


def test_a_url_that_is_only_a_prefix_of_another_is_not_kept() -> None:
    root, deep = "https://example.edu", "https://example.edu/apply"
    result = clean_content(
        make(f"Only {deep} is mentioned.", SourceFormat.PDF, links=(root, deep)), BIG
    )
    assert result.links == (deep,)


def test_indentation_made_of_odd_spaces_still_counts() -> None:
    assert cleaned("Tuition is paid per year and\n\u00a0\u00a0\u00a0the fee is 75 EUR.") == (
        "Tuition is paid per year and the fee is 75 EUR."
    )


def test_source_files_hold_no_invisible_or_line_separator_characters() -> None:
    """Escapes like \\u2028 must stay escapes in the source: raw separators are invisible in an
    editor and make line-based tools see phantom line breaks."""
    forbidden = {"\u2028", "\u2029", "\u200b", "\u2060", "\ufeff", "\u00ad", "\x85"}
    root = Path(__file__).parent.parent
    for path in [*(root / "bot").rglob("*.py"), *(root / "tests").glob("*.py")]:
        text = path.read_text(encoding="utf-8")
        found = {hex(ord(ch)) for ch in text if ch in forbidden}
        assert not found, f"{path.relative_to(root)} contains raw {sorted(found)}"
