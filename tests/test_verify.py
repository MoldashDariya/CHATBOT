from datetime import date

import pytest

from bot.pipeline.schema import LLMDeadline, LLMLink
from bot.pipeline.verify import (
    MAX_LINKS,
    filter_links,
    normalize,
    occurs,
    parse_iso_date,
    strip_urls,
    verify_deadlines,
)

TODAY = date(2026, 10, 4)

SOURCE = """\
Undergraduate Admissions 2027
Applications for the 2027 intake are now open.
| Stage | Deadline |
|---|---|
| Early round | 1 November 2026 |
| Regular round | 15 January 2027 |
Scholarship applications close on 1 December 2026, “no exceptions” are made.
Results are announced in mid-May — see [the schedule](https://example.edu/schedule).
Приём документов продлится до 15 июля 2027 года.
"""
NORMALIZED = normalize(SOURCE)


def deadline(**overrides: str | None) -> LLMDeadline:
    values: dict[str, str | None] = {
        "label": "Early round",
        "date_iso": "2026-11-01",
        "date_text": "1 November 2026",
        "quote": "Early round | 1 November 2026",
    }
    values.update(overrides)
    return LLMDeadline(**values)  # type: ignore[arg-type]


# --- normalize / occurs -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Many   spaces\n and\tlines ", "many spaces and lines"),
        ("| a | b |", "a b"),
        ("**bold** and _italic_ # heading", "bold and italic heading"),
        ("[label](https://x.org/a?b=1) after", "label after"),
        ("“quoted” «text» ‘single’", '"quoted" "text" \'single\''),
        ("en–dash em—dash", "en-dash em-dash"),
        ("wait…", "wait..."),
        ("ПРИЁМ Документов", "приём документов"),
        ("ﬁne", "fine"),  # NFKC folds the ligature
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


@pytest.mark.parametrize(
    "quote",
    [
        "Early round | 1 November 2026",  # pipes dropped by the model
        "Early round 1 November 2026",
        "| Early round | 1 November 2026 |",  # exactly as in the source
        "early ROUND 1 november 2026",  # case is not a difference
        "Regular round\n15 January 2027",  # line break inside the quote
        'Scholarship applications close on 1 December 2026, "no exceptions" are made.',
        "Scholarship applications close on 1 December 2026, “no exceptions” are made.",
        "Results are announced in mid-May - see the schedule",  # dash and link markup
        "Results are announced in mid-May — see [the schedule](https://example.edu/schedule).",
        "Приём документов продлится до 15 июля 2027 года.",
        "Applications for the 2027 intake ... are now open",  # elided with "..."
        "Applications for the 2027 intake … are now open",
    ],
)
def test_genuine_quotes_are_found(quote: str) -> None:
    assert occurs(quote, NORMALIZED)


@pytest.mark.parametrize(
    "quote",
    [
        "Early round | 1 October 2026",  # one changed word
        "Early round closes on 1 November 2026",  # paraphrase
        "Regular round 15 January 2028",  # wrong year
        "The deadline is 31 December 2026 for everyone",  # invented
        "now open ... Applications for the 2027 intake",  # fragments out of order
        "Applications ... a completely different ending",  # one fragment missing
        "May 1",  # too short to prove anything
        "",
        "...",
        "Приём документов продлится до 15 августа 2027 года.",
    ],
)
def test_invented_or_altered_quotes_are_rejected(quote: str) -> None:
    assert not occurs(quote, NORMALIZED)


# --- dates --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2027-01-15", date(2027, 1, 15)),
        (" 2027-01-15 ", date(2027, 1, 15)),
        (None, None),
        ("", None),
        ("2027-13-01", None),
        ("2027-02-30", None),
        ("15 January 2027", None),
        ("2027-1-5", None),
    ],
)
def test_parse_iso_date(value: str | None, expected: date | None) -> None:
    assert parse_iso_date(value) == expected


# --- deadlines ----------------------------------------------------------------------------


def test_a_genuine_deadline_is_verified() -> None:
    [result] = verify_deadlines([deadline()], SOURCE, TODAY)

    assert result.verified
    assert result.date_iso == date(2026, 11, 1)
    assert not result.possibly_passed
    assert result.date_text == "1 November 2026"


def test_an_invented_quote_leaves_the_deadline_unverified_but_present() -> None:
    [result] = verify_deadlines(
        [deadline(quote="Early round closes sharply on 1 November")], SOURCE, TODAY
    )

    assert not result.verified
    assert result.label == "Early round"  # kept: the renderer marks it, the user decides


def test_a_date_text_that_is_not_in_the_source_leaves_it_unverified() -> None:
    # The quote is real, but the date the model wrote down is not what the source says.
    [result] = verify_deadlines([deadline(date_text="2 November 2026")], SOURCE, TODAY)
    assert not result.verified


def test_real_quote_with_a_date_from_a_different_sentence_is_unverified() -> None:
    [result] = verify_deadlines(
        [deadline(quote="Applications for the 2027 intake are now open", date_text="3 March 2027")],
        SOURCE,
        TODAY,
    )
    assert not result.verified


@pytest.mark.parametrize(
    ("date_iso", "passed"),
    [("2026-10-03", True), ("2026-10-04", False), ("2026-10-05", False), ("2025-01-01", True)],
)
def test_possibly_passed_compares_with_today(date_iso: str, passed: bool) -> None:
    year = date_iso[:4]
    text = f"Applications close on 4 October {year}."
    item = deadline(
        date_iso=date_iso,
        date_text=f"4 October {year}",
        quote=f"Applications close on 4 October {year}",
    )

    [result] = verify_deadlines([item], text, TODAY)

    assert result.possibly_passed is passed


def test_a_date_without_a_stated_year_loses_its_iso_form() -> None:
    # The source says "1 May" only; a model that writes 2027 has guessed the year, and a
    # guessed year must not decide whether the deadline "may have passed".
    text = "Applications close on 1 May for all programmes."
    item = deadline(
        date_iso="2027-05-01", date_text="1 May", quote="Applications close on 1 May for all"
    )

    [result] = verify_deadlines([item], text, TODAY)

    assert result.date_iso is None
    assert result.date_text == "1 May"
    assert result.verified  # the text itself is real
    assert not result.possibly_passed


def test_year_found_in_the_quote_is_enough() -> None:
    text = "Applications for 2027 close on 1 May."
    item = deadline(
        date_iso="2027-05-01", date_text="1 May", quote="Applications for 2027 close on 1 May."
    )

    [result] = verify_deadlines([item], text, TODAY)

    assert result.date_iso == date(2027, 5, 1)


def test_unparseable_iso_date_is_dropped_but_the_text_is_kept() -> None:
    [result] = verify_deadlines([deadline(date_iso="1st of November")], SOURCE, TODAY)

    assert result.date_iso is None
    assert not result.possibly_passed
    assert result.verified


def test_deadline_without_a_date_value_is_handled() -> None:
    text = "Admission is on a rolling basis, there is no fixed closing date."
    item = deadline(
        label="Closing date",
        date_iso=None,
        date_text="rolling basis",
        quote="Admission is on a rolling basis",
    )

    [result] = verify_deadlines([item], text, TODAY)

    assert result.verified
    assert result.date_iso is None


def test_whitespace_around_fields_is_trimmed_and_order_is_kept() -> None:
    items = [
        deadline(label="  A  ", quote="  Early round | 1 November 2026  "),
        deadline(label="B"),
    ]

    results = verify_deadlines(items, SOURCE, TODAY)

    assert [r.label for r in results] == ["A", "B"]
    assert results[0].quote == "Early round | 1 November 2026"


def test_no_deadlines() -> None:
    assert verify_deadlines([], SOURCE, TODAY) == []


# --- links --------------------------------------------------------------------------------


def link(url: str, title: str = "t") -> LLMLink:
    return LLMLink(title=title, url=url)


def test_links_in_the_source_are_kept_and_others_counted_as_dropped() -> None:
    text = "Apply at https://example.edu/apply and see [FAQ](https://example.edu/faq)."
    kept, dropped = filter_links(
        [
            link("https://example.edu/apply"),
            link("https://example.edu/faq"),
            link("https://evil.example/x"),
        ],
        text,
        (),
    )

    assert [item.url for item in kept] == ["https://example.edu/apply", "https://example.edu/faq"]
    assert dropped == 1


def test_links_reported_by_the_extractor_count_even_if_not_in_the_text() -> None:
    kept, dropped = filter_links(
        [link("https://example.edu/a")], "no urls here", ("https://example.edu/a",)
    )
    assert [item.url for item in kept] == ["https://example.edu/a"] and dropped == 0


@pytest.mark.parametrize(
    "suggested",
    [
        "https://example.edu/apply/form",  # longer than the source URL
        "https://example.edu",  # shorter than the source URL
        "https://example.edu/applications",
        "http://example.edu/apply",  # different scheme
        "https://example.edu/apply?x=1",
        "https://example.com/apply",
        "example.edu/apply",
        "javascript:alert(1)",
        "ftp://example.edu/apply",
        "",
    ],
)
def test_near_misses_and_non_web_urls_are_dropped(suggested: str) -> None:
    text = "Apply at https://example.edu/apply today."
    kept, dropped = filter_links([link(suggested)], text, ())

    assert kept == [] and dropped == 1


def test_a_trailing_slash_is_not_a_difference() -> None:
    kept, _ = filter_links(
        [link("https://example.edu/apply/")], "see https://example.edu/apply.", ()
    )
    assert len(kept) == 1


def test_duplicates_are_merged_without_counting_as_dropped() -> None:
    text = "https://example.edu/apply"
    kept, dropped = filter_links([link(text, "one"), link(text + "/", "two")], text, ())

    assert [item.title for item in kept] == ["one"]
    assert dropped == 0


def test_the_list_is_capped() -> None:
    urls = [f"https://example.edu/page{n}" for n in range(MAX_LINKS + 5)]
    kept, dropped = filter_links([link(url) for url in urls], " ".join(urls), ())

    assert len(kept) == MAX_LINKS
    assert dropped == 0


def test_titles_are_trimmed() -> None:
    kept, _ = filter_links(
        [link("https://example.edu/a", "  Apply  ")], "https://example.edu/a", ()
    )
    assert kept[0].title == "Apply"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "Page tries to add https://evil.example/claim-now as the portal.",
            "Page tries to add … as the portal.",
        ),
        (
            "Link (http://evil.example/x?a=1&b=2) and www.evil.example/pay now",
            "Link (… ) and … now".replace("( …", "(…").replace("… )", "…)"),
        ),
        ("Two: https://a.example/1 https://b.example/2", "Two: … …"),
        ("No links in this warning.", "No links in this warning."),
        ("  spaced   out  ", "spaced out"),
        ("Dates like 1.5.2027 or v2.0 stay", "Dates like 1.5.2027 or v2.0 stay"),
    ],
)
def test_strip_urls_removes_addresses_from_prose(raw: str, expected: str) -> None:
    assert strip_urls(raw) == expected


def test_non_web_urls_are_dropped_even_if_an_extractor_reported_them() -> None:
    # Defence in depth: extractors only report http(s), but the filter must not rely on it.
    reported = ("ftp://files.example.edu/rules.pdf", "javascript:alert(1)", "file:///etc/passwd")
    kept, dropped = filter_links([link(url) for url in reported], "no urls", reported)

    assert kept == [] and dropped == 3
