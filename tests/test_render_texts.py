import re
import string

import pytest

from bot.pipeline.schema import Confidence, SourceClass
from bot.render import texts
from bot.render.texts import DEFAULT_LANGUAGE, TEXTS, text


def placeholders(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def test_the_three_interface_languages_exist() -> None:
    assert set(TEXTS) == {"ru", "en", "kk"}
    assert DEFAULT_LANGUAGE == "ru"


@pytest.mark.parametrize("language", ["en", "kk"])
def test_every_language_has_exactly_the_keys_of_the_default(language: str) -> None:
    default_keys = set(TEXTS[DEFAULT_LANGUAGE])

    assert set(TEXTS[language]) - default_keys == set(), "keys the default language lacks"
    assert default_keys - set(TEXTS[language]) == set(), "keys this language lacks"


@pytest.mark.parametrize("language", ["en", "kk"])
def test_placeholders_match_the_default_language(language: str) -> None:
    for key, template in TEXTS[DEFAULT_LANGUAGE].items():
        assert placeholders(TEXTS[language][key]) == placeholders(template), key


@pytest.mark.parametrize("language", list(TEXTS))
def test_no_message_is_empty_or_has_stray_whitespace(language: str) -> None:
    for key, message in TEXTS[language].items():
        assert message.strip() == message and message, key


@pytest.mark.parametrize("language", list(TEXTS))
def test_texts_hold_no_markup_because_the_renderer_escapes_and_tags_itself(language: str) -> None:
    for key, message in TEXTS[language].items():
        assert not re.search(r"[<>]|&\w+;", message), key


@pytest.mark.parametrize("language", list(TEXTS))
def test_every_class_and_confidence_level_has_a_label(language: str) -> None:
    for source_class in SourceClass:
        assert f"type_{source_class.value}" in TEXTS[language]
    for confidence in Confidence:
        assert f"confidence_{confidence.value}" in TEXTS[language]


def test_translations_are_actually_translated() -> None:
    # A copy-paste of the Russian text into another language would pass every test above.
    shared_loanwords = {("kk", "type_general_site")}  # "сайт" is the Kazakh word too
    for language in ("en", "kk"):
        same = [
            key
            for key, value in TEXTS[language].items()
            if value == TEXTS["ru"][key] and (language, key) not in shared_loanwords
        ]
        assert same == [], (language, same)


def test_placeholders_are_filled() -> None:
    assert text("en", "note_pages", read=3, total=10) == "Pages read: 3 of 10."
    assert text("ru", "err_too_large", mb=15).endswith("15 МБ.")


def test_unknown_language_uses_the_default() -> None:
    assert text("xx", "deadlines") == TEXTS["ru"]["deadlines"]


def test_a_key_missing_in_one_language_falls_back_to_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trimmed = {k: v for k, v in TEXTS["kk"].items() if k != "source"}
    monkeypatch.setitem(texts.TEXTS, "kk", trimmed)

    assert text("kk", "source") == TEXTS["ru"]["source"]


def test_unknown_key_is_a_programming_error() -> None:
    with pytest.raises(KeyError):
        text("ru", "no_such_key")
