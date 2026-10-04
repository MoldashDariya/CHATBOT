"""Two things real pages do that make text wrong: neighbouring elements with no whitespace in
the markup ("15 July" + "in 9 months" read as "15 Julyin 9 months"), and letters swapped for
look-alikes of another alphabet ("Sуstеms" with Cyrillic у and е)."""

import re
from pathlib import Path

import pytest
from lxml import html as lxml_html

from bot.extractors.base import ExtractedContent, RawSource
from bot.extractors.html import HtmlExtractor, separate_glued_elements
from bot.extractors.main_area import main_area_text
from bot.pipeline.cleaner import clean_content, fold_look_alikes
from bot.pipeline.detector import SourceFormat

FIXTURES = Path(__file__).parent / "fixtures"


def separated(body: str) -> str:
    tree = lxml_html.fromstring(f"<html><body>{body}</body></html>")
    separate_glued_elements(tree)
    return "".join(tree.itertext())


# --- glued neighbours ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        # the case seen on a real admissions page
        (
            "<div><strong>15 July</strong><span>in &gt; 9 months</span></div>",
            "15 July in > 9 months",
        ),
        ("<p><b>Name</b><i>Value</i></p>", "Name Value"),
        ("<div><span>one</span></div><div><span>two</span></div>", "one two"),
        ("<td>15 July<span>in 9 months</span></td>", "15 July in 9 months"),  # text, then a widget
        ("<ul><li>a</li><li>b</li></ul>", "a b"),
        ("<p><a href='#'>Apply</a><em>now</em></p>", "Apply now"),
        ("<p><span>1</span><span>2</span></p>", "1 2"),
    ],
)
def test_neighbours_that_would_run_together_get_a_space(markup: str, expected: str) -> None:
    assert separated(markup) == expected


@pytest.mark.parametrize(
    "markup",
    [
        "<p>Sys<b>tem</b></p>",  # styling inside a word: text, then <b>
        "<p><b>Hello</b> <i>world</i></p>",  # already separated
        "<p><b>Hello</b>, <i>world</i></p>",  # separated by text
        "<p><b>(</b><i>x</i></p>",  # punctuation at the boundary
        "<p><b>a,</b><i>b</i></p>",
        "<p><b>a</b><i>-b</i></p>",
        "<p><b>one</b></p>",  # no neighbour
        "<pre><span>a</span><span>b</span></pre>",  # code keeps its exact text
        "<code><b>a</b><i>b</i></code>",
        "<pre>word<span>more</span></pre>",  # text, then a span, inside code
        "<code>word<span>more</span></code>",
        "<p>text<b>bold</b>more</p>",
    ],
)
def test_text_that_is_fine_is_left_exactly_as_it_is(markup: str) -> None:
    tree = lxml_html.fromstring(f"<html><body>{markup}</body></html>")
    before = "".join(tree.itertext())

    assert separate_glued_elements(tree) == 0
    assert "".join(tree.itertext()) == before


def test_the_number_of_opened_gaps_is_returned() -> None:
    one = lxml_html.fromstring("<html><body><p><b>a</b><i>b</i></p></body></html>")
    assert separate_glued_elements(one) == 1

    # Two paragraphs: a gap inside each, and one between the paragraphs themselves.
    two = lxml_html.fromstring(
        "<html><body><p><b>a</b><i>b</i></p><p><b>c</b><i>d</i></p></body></html>"
    )
    assert separate_glued_elements(two) == 3


def test_page_sized_siblings_are_not_examined() -> None:
    wide = "<div>" + "<i>x</i>" * 20 + "</div><div>y</div>"
    tree = lxml_html.fromstring(f"<html><body>{wide}</body></html>")
    # The wide <div> has far more children than any badge or label does: skipped, not guessed.
    assert separate_glued_elements(tree) == 19  # only the <i> neighbours inside it


def test_a_hostile_deeply_nested_page_finishes() -> None:
    nested = "<div>" * 2500 + "<b>a</b><i>b</i>" + "</div>" * 2500
    tree = lxml_html.fromstring(f"<html><body>{nested}</body></html>")

    separate_glued_elements(tree)  # must return, not hang or raise


async def test_the_real_badge_table_reads_correctly_end_to_end() -> None:
    body = (FIXTURES / "badge_table.html").read_bytes()

    result = await HtmlExtractor().extract(RawSource(url="https://uni.example/msc", body=body))

    assert (
        "| Application deadline | 15 July in > 9 months | 15 January in 103 days |" in result.text
    )
    assert "Julyin" not in result.text and "Januaryin" not in result.text


def test_the_main_area_reader_benefits_too() -> None:
    tree = lxml_html.fromstring((FIXTURES / "badge_table.html").read_bytes())
    separate_glued_elements(tree)

    assert "15 July in > 9 months" in main_area_text(tree)


# --- look-alike letters -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("garbled", "repaired"),
    [
        ("Sуstеms", "Systems"),  # Cyrillic у and е inside a Latin word
        ("signaturе", "signature"),
        ("systеm", "system"),
        ("Information Sуstеms and signaturе", "Information Systems and signature"),
        ("Мoсква", "Москва"),  # Latin o inside a Cyrillic word
        ("рoссия", "россия"),
        ("СОVID", "COVID"),  # capitals: Cyrillic С, О inside a Latin word
    ],
)
def test_words_with_look_alike_letters_are_repaired(garbled: str, repaired: str) -> None:
    assert fold_look_alikes(garbled)[0] == repaired


def test_the_number_of_repaired_words_is_counted() -> None:
    assert fold_look_alikes("Sуstеms, signaturе, ordinary words") == (
        "Systems, signature, ordinary words",
        2,
    )


@pytest.mark.parametrize(
    "text",
    [
        "Привет, мир! Приём документов до 15 июля 2027 года.",
        "Hello world, admissions close on 15 July 2027.",
        "Қазақстан Республикасының азаматтары, әдебиет, ұлттық тест",
        "Universität Münster, Straße, café, naïve, Ångström",
        "СССР and CCCP are both fine",  # each is pure in its own alphabet
        "iPhone-ом and Wi-Fi-роутер",  # Latin and Cyrillic separated by a hyphen
        "PDFфайл",  # a Latin abbreviation glued to a Russian word: not look-alike noise
        "Mиxed",  # и has no Latin twin: leave it alone
        "xх",  # a tie, in a text that is itself tied: leave it alone
        "5G и 3D-принтер",
        "",
    ],
)
def test_ordinary_text_is_never_touched(text: str) -> None:
    assert fold_look_alikes(text) == (text, 0)


def test_repair_is_idempotent() -> None:
    once, _ = fold_look_alikes("Sуstеms Мoсква")
    assert fold_look_alikes(once) == (once, 0)


def content(
    text: str, title: str | None = None, fmt: SourceFormat = SourceFormat.HTML
) -> ExtractedContent:
    return ExtractedContent(text=text, title=title, source_url="https://x.edu/a", source_format=fmt)


def test_the_cleaner_repairs_text_and_title_and_records_how_many() -> None:
    result = clean_content(
        content("M.Sc. Information Sуstеms\nsignaturе", title="Sуstеms | Uni"), 10_000
    )

    assert result.text == "M.Sc. Information Systems\nsignature"
    assert result.title == "Systems | Uni"
    assert result.meta["lookalike_words_repaired"] == "3"


def test_nothing_is_recorded_when_nothing_was_repaired() -> None:
    result = clean_content(content("Plain admissions text", title="Plain"), 10_000)
    assert "lookalike_words_repaired" not in result.meta


def test_noise_filters_see_the_repaired_words() -> None:
    # "Skір" has a Cyrillic і: without the repair this banner line would survive the noise rules.
    result = clean_content(content("Deadline is 1 May.\nSkір to content\nApply online."), 10_000)
    assert result.text == "Deadline is 1 May.\nApply online."


def test_every_format_gets_the_repair() -> None:
    for fmt in SourceFormat:
        assert clean_content(content("Sуstеms", fmt=fmt), 100).text == "Systems"


def test_no_mixed_alphabet_word_survives_on_the_real_page() -> None:
    body = (FIXTURES / "badge_table.html").read_bytes()
    extracted = HtmlExtractor()

    import asyncio

    raw = asyncio.run(extracted.extract(RawSource(url="https://uni.example/msc", body=body)))
    cleaned = clean_content(raw, 120_000)

    mixed = [
        w
        for w in re.findall(r"[^\W\d_]+", cleaned.text)
        if re.search("[\u0400-\u04ff]", w) and re.search("[A-Za-z]", w)
    ]
    assert mixed == []
    assert "Information Systems" in cleaned.text and "signature." in cleaned.text


def test_a_tied_word_is_settled_by_the_alphabet_of_the_whole_text() -> None:
    latin_text = "Skір to content and more Latin words here"  # "Skір": Latin S, k + Cyrillic і, р
    assert fold_look_alikes(latin_text) == ("Skip to content and more Latin words here", 1)


def test_a_tied_word_stays_if_the_dominant_alphabet_has_no_twin_for_its_letters() -> None:
    # In a Russian text the tie would be settled towards Cyrillic, but Latin S and k have no
    # safe Cyrillic twin, so the word is left exactly as it is.
    russian_text = "Skір и ещё много русских слов в этом тексте"
    assert fold_look_alikes(russian_text) == (russian_text, 0)
