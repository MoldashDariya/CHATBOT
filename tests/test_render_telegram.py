import os
from pathlib import Path

import pytest

from bot.pipeline.schema import Confidence, LLMLink, SourceClass
from bot.render.telegram import (
    MAX_URL_TEXT,
    SAFE_LIMIT,
    TELEGRAM_LIMIT,
    _esc,
    _pack,
    _snippet,
    render_summary,
    visible_length,
)
from tests.summaries import make_deadline, make_summary, markup_problems, plain

GOLDEN = Path(__file__).parent / "fixtures" / "cards"


def card(**overrides: object) -> str:
    return "\n\n".join(render_summary(make_summary(**overrides)))


def lines(markup: str) -> list[str]:
    return plain(markup).split("\n")


# --- layout -------------------------------------------------------------------------------


def test_sections_come_in_the_documented_order_and_the_source_is_last() -> None:
    text = plain(card(warnings=["Check the dates"]))
    order = [
        "📌 Undergraduate Admissions 2027",
        "🏛 Example University · страница университета",
        "⏰ Дедлайны",
        "✅ Основные пункты",
        "👤 Кто может подаваться",
        "💰 Стоимость / финансирование",
        "📋 Требования",
        "🔗 Полезные ссылки",
        "⚠️ Важно",
        "🟢 Надёжность разбора",
        "🔎 Источник:",
    ]
    positions = [text.index(marker) for marker in order]
    assert positions == sorted(positions)
    assert text.rstrip().splitlines()[-1].startswith("🔎 Источник:")


def test_a_normal_card_is_a_single_valid_message() -> None:
    messages = render_summary(make_summary())

    assert len(messages) == 1
    assert markup_problems(messages[0]) == []
    assert visible_length(messages[0]) < SAFE_LIMIT


def test_deadlines_show_label_date_as_written_and_a_short_quote() -> None:
    text = plain(card())

    assert "• Early round: 1 November 2026\n  «Early round · 1 November 2026»" in text
    assert "• Обычный раунд: 15 January 2027" in text


LONG_QUOTE = (
    "Application deadline for the winter semester · Early deadline: Nov 15 (positive decision: "
    "notification at the lastest in February of the following year) Late deadline: May 15 "
    "(positive decision: notification at the lastest in July)"
)


@pytest.mark.parametrize("anchor", ["Nov 15", "May 15"])
def test_a_long_quote_is_cut_around_the_date_it_supports(anchor: str) -> None:
    shown = _snippet(LONG_QUOTE, anchor, 140)

    assert anchor in shown
    assert len(shown) <= 140
    assert shown.strip("…") in LONG_QUOTE  # nothing reworded, only cut


def test_a_cut_quote_starts_and_ends_at_words_and_marks_the_cuts() -> None:
    shown = _snippet(LONG_QUOTE, "Nov 15", 140)

    assert shown.startswith("…") and shown.endswith("…")  # cut on both sides
    assert not _snippet(LONG_QUOTE, "May 15", 140).endswith("…")  # reaches the end: no mark
    assert not shown[1].isspace() and not shown[-2].isspace()
    assert shown[1:-1].split(" ")[0] in LONG_QUOTE.split(" ")  # first piece is a whole word


def test_a_short_quote_is_left_alone_and_a_missing_date_clips_the_head() -> None:
    assert _snippet("Deadline | 1 May", "1 May", 140) == "Deadline | 1 May"
    head = _snippet(LONG_QUOTE, "not in the quote", 140)
    assert head.startswith("Application deadline") and head.endswith("…") and len(head) <= 140


def test_the_card_shows_the_late_date_in_its_quote() -> None:
    deadline = make_deadline(label="Late", date_text="May 15", quote=LONG_QUOTE)

    assert "May 15" in plain(card(deadlines=[deadline])).split("«", 1)[1]


def test_costs_show_item_and_amount_as_written() -> None:
    assert "• Вступительный взнос (невозвратный): 75 EUR" in plain(card())


# --- empty fields -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lang", "not_stated", "no_links"),
    [
        ("ru", "не указано в источнике", "в источнике нет подходящих ссылок"),
        ("en", "not stated in the source", "no suitable links in the source"),
        ("kk", "дереккөзде көрсетілмеген", "дереккөзде қолайлы сілтемелер жоқ"),
    ],
)
def test_empty_sections_say_so_instead_of_disappearing(
    lang: str, not_stated: str, no_links: str
) -> None:
    empty = make_summary(
        language=lang,
        overview="",
        deadlines=[],
        key_points=[],
        eligibility=[],
        costs=[],
        requirements=[],
        useful_links=[],
        confidence=Confidence.LOW,
    )
    text = plain("\n\n".join(render_summary(empty)))

    assert (
        text.count(f"— {not_stated}") == 5
    )  # deadlines, key points, eligibility, costs, requirements
    assert f"— {no_links}" in text
    assert text.rstrip().splitlines()[-1].startswith("🔎")


def test_missing_title_and_organization_have_fallbacks() -> None:
    text = plain(card(title=None, organization=None))

    assert text.startswith("📌 Без названия\n🗂 страница университета")


# --- notes about what the code knows ------------------------------------------------------


def test_a_clean_card_has_no_notes_block() -> None:
    assert "⚠️" not in plain(card())


def test_unverified_deadline_is_marked_and_explained() -> None:
    text = plain(card(deadlines=[make_deadline(verified=False)]))

    assert "Early round: 1 November 2026 ❓" in text
    assert "❓ Даты с этой пометкой не удалось сопоставить с текстом источника." in text


def test_a_deadline_that_may_have_passed_is_marked_and_explained() -> None:
    text = plain(card(deadlines=[make_deadline(possibly_passed=True)]))

    assert "Early round: 1 November 2026 ⏳" in text
    assert "возможно, уже прошёл" in text


def test_a_deadline_can_carry_both_marks() -> None:
    text = plain(card(deadlines=[make_deadline(verified=False, possibly_passed=True)]))
    assert "1 November 2026 ❓ ⏳" in text


def test_verified_future_deadlines_carry_no_marks() -> None:
    text = plain(card())
    assert "❓" not in text and "⏳" not in text


def test_partial_coverage_and_dropped_links_are_reported() -> None:
    text = plain(card(truncated=True, pages_read=80, pages_total=200, dropped_links=3))

    assert "Текст был слишком длинным и обрезан" in text
    assert "Прочитано страниц: 80 из 200." in text
    assert "Скрыто ссылок, которых нет в источнике: 3." in text


def test_complete_pdf_coverage_is_not_reported() -> None:
    assert "Прочитано страниц" not in plain(card(pages_read=5, pages_total=5))


def test_an_unrelated_page_says_so() -> None:
    text = plain(card(source_type=SourceClass.OTHER, confidence=Confidence.LOW))
    assert "страница не про поступление" in text


def test_model_warnings_are_listed_clipped_and_escaped() -> None:
    long_warning = "Источник противоречит сам себе. " * 40
    text = plain(card(warnings=["Check <b>this</b> & that", long_warning]))

    assert "• Check <b>this</b> & that" in text  # shown literally, not interpreted
    longest = max(len(line) for line in text.splitlines() if line.startswith("• Источник против"))
    assert longest <= 352 and "…" in text


@pytest.mark.parametrize(
    ("confidence", "emoji", "word"),
    [
        (Confidence.HIGH, "🟢", "высокая"),
        (Confidence.MEDIUM, "🟡", "средняя"),
        (Confidence.LOW, "🔴", "низкая"),
    ],
)
def test_confidence_is_shown(confidence: Confidence, emoji: str, word: str) -> None:
    assert f"{emoji} Надёжность разбора: {word}" in plain(card(confidence=confidence))


# --- links --------------------------------------------------------------------------------


def test_every_link_shows_its_domain_next_to_the_title() -> None:
    text = plain(card())

    assert "• Онлайн-портал подачи заявок — www.example.edu" in text
    assert "• FAQ по поступлению — www.example.edu" in text


def test_hrefs_are_escaped_and_complete() -> None:
    markup = card()

    assert '<a href="https://www.example.edu/faq?lang=en&amp;x=1">FAQ по поступлению</a>' in markup
    assert markup_problems(markup) == []


def test_internationalised_domains_are_shown_readably() -> None:
    link = LLMLink(title="Приём", url="https://xn--b1agajc0ayikbb.xn--p1ai/priem")
    assert "университет.рф" in plain(card(useful_links=[link]))


def test_a_link_without_a_title_shows_its_domain() -> None:
    text = plain(card(useful_links=[LLMLink(title="  ", url="https://portal.example.org/a")]))
    assert "• portal.example.org — portal.example.org" in text


@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "ftp://files.example.edu/a", "tg://resolve?x=1", ""]
)
def test_non_web_links_are_never_rendered_as_links(url: str) -> None:
    markup = card(useful_links=[LLMLink(title="Bad", url=url)])

    assert "<a href" not in markup.replace('<a href="https://www.example.edu', "")
    assert markup_problems(markup) == []
    assert "в источнике нет подходящих ссылок" in plain(markup)


def test_a_link_with_quotes_cannot_break_out_of_the_attribute() -> None:
    url = 'https://example.org/x"onmouseover="alert(1)'
    markup = card(useful_links=[LLMLink(title="T", url=url)])

    assert 'onmouseover="alert' not in markup
    assert "&quot;" in markup
    assert markup_problems(markup) == []


# --- the source line ----------------------------------------------------------------------


def test_source_line_links_to_the_exact_url() -> None:
    url = "https://www.example.edu/admissions/undergraduate?a=1&b=2"
    markup = card(source_url=url)

    assert f'<a href="{url.replace("&", "&amp;")}">' in markup.splitlines()[-1]
    assert markup.splitlines()[-1].startswith("🔎 <b>Источник:</b>")


def test_a_very_long_source_url_is_shortened_on_screen_but_not_in_the_href() -> None:
    url = "https://example.edu/" + "a" * 1500
    last = render_summary(make_summary(source_url=url))[-1].splitlines()[-1]

    assert f'href="{url}"' in last
    assert len(plain(last)) <= MAX_URL_TEXT + len("🔎 Источник: ") + 1
    assert plain(last).endswith("…")


def test_a_non_web_source_is_shown_as_code_not_as_a_link() -> None:
    last = card(source_url="file:///etc/passwd").splitlines()[-1]

    assert "<code>file:///etc/passwd</code>" in last
    assert "<a " not in last


@pytest.mark.parametrize("language", ["ru", "en", "kk"])
def test_source_is_present_in_every_language_even_for_an_empty_card(language: str) -> None:
    nothing = make_summary(
        language=language,
        title=None,
        organization=None,
        overview="",
        deadlines=[],
        key_points=[],
        eligibility=[],
        costs=[],
        requirements=[],
        useful_links=[],
    )
    messages = render_summary(nothing)

    assert "🔎" in messages[-1].splitlines()[-1]
    assert "https://www.example.edu/admissions/undergraduate" in messages[-1]


# --- escaping -----------------------------------------------------------------------------

HOSTILE = '<script>alert(1)</script> & "quotes" <a href="javascript:x">click</a> </b></i>'


def test_hostile_text_in_every_field_is_escaped_and_the_markup_stays_valid() -> None:
    from bot.pipeline.schema import LLMCost

    summary = make_summary(
        title=HOSTILE,
        organization=HOSTILE,
        overview=HOSTILE,
        deadlines=[make_deadline(label=HOSTILE, date_text=HOSTILE, quote=HOSTILE)],
        key_points=[HOSTILE],
        eligibility=[HOSTILE],
        costs=[LLMCost(item=HOSTILE, amount_text=HOSTILE)],
        requirements=[HOSTILE],
        useful_links=[LLMLink(title=HOSTILE, url="https://example.org/ok")],
        warnings=[HOSTILE],
    )

    for message in render_summary(summary):
        assert "<script" not in message
        assert 'href="javascript' not in message
        assert markup_problems(message) == []
    assert "<script>alert(1)</script>" in plain("\n".join(render_summary(summary)))  # as text


def test_unknown_language_falls_back_to_russian() -> None:
    assert "⏰ Дедлайны" in plain(card(language="xx"))


# --- splitting ----------------------------------------------------------------------------


def big_summary(**overrides: object):  # type: ignore[no-untyped-def]
    items = [f"Пункт номер {n:03d}: " + "очень длинное описание условия " * 12 for n in range(60)]
    values = {"key_points": items, "eligibility": items[:30], "requirements": items[:30]}
    values.update(overrides)
    return make_summary(**values)


def test_a_huge_card_is_split_into_messages_within_the_telegram_limit() -> None:
    messages = render_summary(big_summary())

    assert len(messages) > 1
    for message in messages:
        assert visible_length(message) <= SAFE_LIMIT < TELEGRAM_LIMIT
        assert markup_problems(message) == []


def test_splitting_loses_nothing_and_keeps_the_order() -> None:
    summary = big_summary()
    joined = plain("\n".join(render_summary(summary)))

    positions = []
    for n in range(60):
        marker = f"Пункт номер {n:03d}:"
        assert marker in joined, marker
        positions.append(joined.index(marker))
    assert positions == sorted(positions)


def test_only_the_last_message_carries_the_source_and_no_heading_is_left_dangling() -> None:
    messages = render_summary(big_summary())

    assert all("🔎" not in m for m in messages[:-1])
    assert messages[-1].splitlines()[-1].startswith("🔎")
    headings = ("⏰", "✅", "👤", "💰", "📋", "🔗", "⚠️")
    for message in messages:
        last_line = plain(message).rstrip().splitlines()[-1]
        assert not last_line.startswith(headings), f"heading left alone: {last_line!r}"


def test_emoji_count_twice_towards_the_limit() -> None:
    items = ["😀" * 390 for _ in range(30)]  # ~780 UTF-16 units each
    messages = render_summary(make_summary(key_points=items))

    assert len(messages) > 1
    for message in messages:
        assert len(plain(message).encode("utf-16-le")) // 2 <= SAFE_LIMIT


def test_very_long_fields_are_clipped_so_no_line_can_blow_the_limit() -> None:
    huge = "слово " * 5000
    summary = make_summary(
        title=huge, overview=huge, key_points=[huge], warnings=[huge], organization=huge
    )
    messages = render_summary(summary)

    assert len(messages) == 1
    assert max(len(line) for line in plain(messages[0]).splitlines()) < 1300


def test_long_hrefs_do_not_count_towards_the_message_length() -> None:
    links = [
        LLMLink(title=f"Link {n}", url=f"https://example.org/{n}/" + "x" * 2000) for n in range(8)
    ]
    messages = render_summary(make_summary(useful_links=links))

    assert len(messages) == 1  # ~16,000 characters of href, but Telegram does not count them


def test_a_single_oversize_unit_is_cut_without_breaking_markup() -> None:
    block = ["⏰ <b>Heading</b>", "<i>" + "x" * 9000 + "</i>"]

    messages = _pack([block], limit=1000)

    assert len(messages) >= 1
    for message in messages:
        assert visible_length(message) <= 1000
        assert markup_problems(message) == []


def test_an_oversize_unit_of_emoji_is_cut_to_the_real_utf16_limit() -> None:
    block = ["📋 <b>Heading</b>", "😀" * 5000]  # 10,000 UTF-16 units in one line

    messages = _pack([block], limit=1000)

    for message in messages:
        assert visible_length(message) <= 1000
        assert markup_problems(message) == []


def test_blocks_are_never_split_when_they_fit() -> None:
    blocks = [[f"Block {n}", "• a", "• b"] for n in range(10)]

    (message,) = _pack(blocks, limit=10_000)

    assert message == "\n\n".join("\n".join(b) for b in blocks)


def test_packing_starts_a_new_message_between_blocks_rather_than_inside_one() -> None:
    blocks = [["A" * 600], ["B" * 600], ["C" * 600]]

    messages = _pack(blocks, limit=1300)

    assert messages == ["A" * 600 + "\n\n" + "B" * 600, "C" * 600]


# --- visible length -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("markup", "length"),
    [
        ("plain", 5),
        ("<b>bold</b>", 4),
        ('<a href="https://example.org/a/very/long/path?x=1">link</a>', 4),
        ("a &amp; b", 5),
        ("&lt;tag&gt;", 5),
        ("😀", 2),
        ("😀<b>ab</b>", 4),
        ("Привет", 6),
        ("", 0),
    ],
)
def test_visible_length_counts_what_telegram_counts(markup: str, length: int) -> None:
    assert visible_length(markup) == length


# --- determinism and snapshots ------------------------------------------------------------


def test_rendering_is_deterministic() -> None:
    summary = make_summary(warnings=["x"])
    assert render_summary(summary) == render_summary(summary)


@pytest.mark.parametrize("language", ["ru", "en", "kk"])
def test_golden_card(language: str) -> None:
    """The card as a reader sees it. A failure means the layout changed: if that is intended,
    refresh the files with `UPDATE_GOLDEN=1 uv run pytest tests/test_render_telegram.py`."""
    summary = make_summary(
        language=language,
        warnings=["Check the dates on the official site"],
        deadlines=[
            make_deadline(),
            make_deadline(
                label="Regular round",
                date_text="15 January 2027",
                quote="Regular round | 15 January 2027",
                verified=False,
            ),
            make_deadline(
                label="Scholarship",
                date_text="1 December 2026",
                quote="Scholarship applications | 1 December 2026",
                possibly_passed=True,
            ),
        ],
        dropped_links=1,
    )
    rendered = plain("\n\n--- next message ---\n\n".join(render_summary(summary)))
    path = GOLDEN / f"{language}.txt"

    if os.environ.get("UPDATE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered + "\n", encoding="utf-8")
    assert path.read_text(encoding="utf-8") == rendered + "\n"


def test_escaping_uses_only_the_entities_telegram_defines() -> None:
    assert _esc('it\'s <b> & "x"') == "it's &lt;b&gt; &amp; &quot;x&quot;"


def test_apostrophes_stay_literal_in_the_card() -> None:
    markup = card(title="Master's programme", warnings=["Don't miss the deadline"])

    assert "Master's programme" in markup and "Don't miss" in markup
    assert "&#" not in markup  # no numeric entities anywhere


# --- pages read besides the source --------------------------------------------------------


def test_pages_read_in_addition_are_listed_before_the_source() -> None:
    extra = ["https://www.example.edu/programme/apply-now", "https://www.example.edu/fees"]
    text = plain(card(extra_sources=extra))

    assert "📎 Дополнительно прочитано (страницы, на которые ссылается источник):" in text
    assert text.index("📎") < text.index("🔎")
    assert text.index("🟢") < text.index("📎")  # after the reliability line
    assert "• https://www.example.edu/programme/apply-now" in text
    assert text.rstrip().splitlines()[-1].startswith("🔎 Источник:")  # the source stays last


def test_the_extra_pages_are_real_links() -> None:
    markup = card(extra_sources=["https://www.example.edu/apply?a=1&b=2"])

    assert '<a href="https://www.example.edu/apply?a=1&amp;b=2">' in markup
    assert markup_problems(markup) == []


def test_without_extra_pages_there_is_no_such_block() -> None:
    assert "📎" not in card()


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "ftp://x.edu/a", ""])
def test_extra_pages_must_be_web_addresses(bad: str) -> None:
    markup = card(extra_sources=[bad])

    assert "📎" not in markup and markup_problems(markup) == []


def test_the_block_is_translated() -> None:
    assert "Also read (pages the source links to):" in plain(
        card(language="en", extra_sources=["https://x.edu/a"])
    )
    assert "Қосымша оқылды" in plain(card(language="kk", extra_sources=["https://x.edu/a"]))


def test_the_domain_next_to_a_link_cannot_be_auto_linked_by_telegram() -> None:
    markup = card()

    assert "— <code>www.example.edu</code>" in markup  # plain text would become a second link
    assert markup_problems(markup) == []
