import asyncio
import logging
import sqlite3
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from bot.storage.cache import SCHEMA_VERSION, SummaryCache, button_id
from tests.summaries import make_summary

KEY = "https://www.example.edu/admissions"
FP = "fingerprint-1"


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "cache.sqlite3"


@pytest.fixture
async def make_cache(clock: Clock) -> AsyncIterator[Callable[..., SummaryCache]]:
    """Builds caches that are closed at the end of the test (an open connection keeps a thread)."""
    made: list[SummaryCache] = []

    def build(path: Path, **kwargs: Any) -> SummaryCache:
        made.append(SummaryCache(path, **{"clock": clock, **kwargs}))
        return made[-1]

    yield build
    for cache in made:
        await cache.aclose()


@pytest.fixture
def cache(db_path: Path, make_cache: Callable[..., SummaryCache]) -> SummaryCache:
    return make_cache(db_path)


async def store(cache: SummaryCache, key: str = KEY, language: str = "ru", **overrides) -> None:  # type: ignore[no-untyped-def]
    values = {
        "url": key,
        "fingerprint": FP,
        "content_hash": "hash-1",
        "summary": make_summary(language=language),
    }
    values.update(overrides)
    await cache.put(key, language, **values)


# --- storing and reading ------------------------------------------------------------------


async def test_a_stored_answer_comes_back_unchanged(cache: SummaryCache) -> None:
    summary = make_summary()
    await store(cache, summary=summary)

    entry = await cache.get(KEY, "ru", FP)

    assert entry is not None
    assert entry.summary == summary  # Russian text, dates, enums, verified flags: all intact
    assert entry.content_hash == "hash-1"
    assert entry.url == KEY
    assert entry.button_id == button_id(KEY, "ru")


async def test_nothing_is_found_for_an_unknown_page(cache: SummaryCache) -> None:
    assert await cache.get("https://other.edu/", "ru", FP) is None


async def test_each_language_has_its_own_answer(cache: SummaryCache) -> None:
    await store(cache, language="ru", summary=make_summary(language="ru", overview="по-русски"))
    await store(cache, language="en", summary=make_summary(language="en", overview="in English"))

    ru, en = await cache.get(KEY, "ru", FP), await cache.get(KEY, "en", FP)

    assert ru is not None and en is not None
    assert (ru.summary.overview, en.summary.overview) == ("по-русски", "in English")
    assert await cache.get(KEY, "kk", FP) is None


async def test_the_age_marker_is_never_stored(cache: SummaryCache) -> None:
    await store(cache, summary=make_summary(cached_minutes_ago=99))

    entry = await cache.get(KEY, "ru", FP)

    assert entry is not None and entry.summary.cached_minutes_ago is None


async def test_storing_again_replaces_the_row(cache: SummaryCache, db_path: Path) -> None:
    await store(cache, summary=make_summary(overview="first"), content_hash="h1")
    await store(
        cache, summary=make_summary(overview="second"), content_hash="h2", url="https://new"
    )

    entry = await cache.get(KEY, "ru", FP)

    assert entry is not None
    assert (entry.summary.overview, entry.content_hash, entry.url) == (
        "second",
        "h2",
        "https://new",
    )
    assert sqlite3.connect(db_path).execute("SELECT COUNT(*) FROM summaries").fetchone() == (1,)


async def test_keys_with_quotes_and_sql_are_just_text(cache: SummaryCache) -> None:
    nasty = "https://x.edu/a?q='; DROP TABLE summaries; --"
    await store(cache, key=nasty)

    assert await cache.get(nasty, "ru", FP) is not None
    assert await cache.get(KEY, "ru", FP) is None


async def test_the_cache_survives_a_restart(
    db_path: Path, clock: Clock, make_cache: Callable[..., SummaryCache]
) -> None:
    first = make_cache(db_path)
    await store(first)
    await first.aclose()

    second = make_cache(db_path)

    assert await second.get(KEY, "ru", FP) is not None
    await second.aclose()


async def test_closing_twice_and_reading_afterwards_is_fine(cache: SummaryCache) -> None:
    await store(cache)
    await cache.aclose()
    await cache.aclose()

    assert await cache.get(KEY, "ru", FP) is not None  # reopened on demand


async def test_the_folder_is_created(
    tmp_path: Path, clock: Clock, make_cache: Callable[..., SummaryCache]
) -> None:
    cache = make_cache(tmp_path / "deep" / "er" / "cache.sqlite3")
    await store(cache)
    assert await asyncio.to_thread((tmp_path / "deep" / "er" / "cache.sqlite3").exists)


# --- when an answer must not be served ----------------------------------------------------


async def test_another_fingerprint_is_a_miss_until_the_row_is_replaced(cache: SummaryCache) -> None:
    await store(cache)

    assert await cache.get(KEY, "ru", "fingerprint-2") is None

    await store(cache, fingerprint="fingerprint-2")
    assert await cache.get(KEY, "ru", "fingerprint-2") is not None
    assert await cache.get(KEY, "ru", FP) is None


async def test_rows_past_the_retention_time_are_gone(
    db_path: Path, clock: Clock, make_cache: Callable[..., SummaryCache]
) -> None:
    cache = make_cache(db_path, retention_s=100)
    await store(cache)

    clock.now += 99
    assert await cache.get(KEY, "ru", FP) is not None
    clock.now += 2

    assert await cache.get(KEY, "ru", FP) is None
    assert sqlite3.connect(db_path).execute("SELECT COUNT(*) FROM summaries").fetchone() == (0,)


async def test_storing_removes_other_expired_rows(
    db_path: Path, clock: Clock, make_cache: Callable[..., SummaryCache]
) -> None:
    cache = make_cache(db_path, retention_s=100)
    await store(cache, key="https://old.edu/")
    clock.now += 500

    await store(cache, key="https://new.edu/")

    rows = sqlite3.connect(db_path).execute("SELECT url_key FROM summaries").fetchall()
    assert rows == [("https://new.edu/",)]


async def test_the_table_never_grows_past_its_limit_and_keeps_the_newest(
    db_path: Path, clock: Clock, make_cache: Callable[..., SummaryCache]
) -> None:
    cache = make_cache(db_path, max_rows=3)
    for n in range(6):
        clock.now += 1
        await store(cache, key=f"https://x.edu/{n}")

    rows = sqlite3.connect(db_path).execute("SELECT url_key FROM summaries").fetchall()

    assert sorted(r[0] for r in rows) == [f"https://x.edu/{n}" for n in (3, 4, 5)]


async def test_a_row_that_no_longer_validates_is_dropped_not_raised(
    cache: SummaryCache, db_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    await store(cache)
    with sqlite3.connect(db_path) as raw:
        raw.execute('UPDATE summaries SET summary_json = \'{"not": "a summary"}\'')

    with caplog.at_level(logging.WARNING):
        assert await cache.get(KEY, "ru", FP) is None

    assert sqlite3.connect(db_path).execute("SELECT COUNT(*) FROM summaries").fetchone() == (0,)
    assert KEY not in caplog.text  # a link may carry a token: never logged


async def test_garbage_json_is_dropped_too(cache: SummaryCache, db_path: Path) -> None:
    await store(cache)
    with sqlite3.connect(db_path) as raw:
        raw.execute("UPDATE summaries SET summary_json = 'not json at all'")

    assert await cache.get(KEY, "ru", FP) is None


# --- the refresh button -------------------------------------------------------------------


async def test_a_button_leads_back_to_the_link_and_language(cache: SummaryCache) -> None:
    await store(cache, url="https://www.example.edu/admissions?utm_source=x")

    target = await cache.target_for(button_id(KEY, "ru"))

    assert target == ("https://www.example.edu/admissions?utm_source=x", "ru")


async def test_unknown_buttons_lead_nowhere(cache: SummaryCache) -> None:
    await store(cache)
    assert await cache.target_for("0" * 16) is None
    assert await cache.target_for("") is None
    assert await cache.target_for("' OR '1'='1") is None


async def test_a_button_expires_with_its_row(
    db_path: Path, clock: Clock, make_cache: Callable[..., SummaryCache]
) -> None:
    cache = make_cache(db_path, retention_s=100)
    await store(cache)
    clock.now += 101
    assert await cache.target_for(button_id(KEY, "ru")) is None


def test_button_ids_fit_a_telegram_callback_and_differ_per_page_and_language() -> None:
    ids = {button_id(KEY, "ru"), button_id(KEY, "en"), button_id(KEY + "x", "ru")}
    assert len(ids) == 3
    assert all(len(i) == 16 and len(f"refresh:{i}".encode()) <= 64 for i in ids)
    assert button_id(KEY, "ru") == button_id(KEY, "ru")  # stable across restarts


# --- the cache can never break a request --------------------------------------------------


async def test_an_unusable_location_behaves_like_an_empty_cache(
    tmp_path: Path,
    clock: Clock,
    caplog: pytest.LogCaptureFixture,
    make_cache: Callable[..., SummaryCache],
) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a folder")
    cache = make_cache(blocker / "cache.sqlite3")

    with caplog.at_level(logging.WARNING):
        await store(cache)  # no exception
        assert await cache.get(KEY, "ru", FP) is None
        assert await cache.target_for("x") is None
        await store(cache)

    assert caplog.text.count("Answer cache is unavailable") == 1  # reported once, not per request


async def test_a_file_that_is_not_a_database_behaves_like_an_empty_cache(
    db_path: Path, clock: Clock, make_cache: Callable[..., SummaryCache]
) -> None:
    await asyncio.to_thread(db_path.write_bytes, b"this is not sqlite" * 100)
    cache = make_cache(db_path)

    await store(cache)
    assert await cache.get(KEY, "ru", FP) is None


async def test_an_old_layout_is_replaced_not_misread(
    db_path: Path, clock: Clock, make_cache: Callable[..., SummaryCache]
) -> None:
    with sqlite3.connect(db_path) as raw:
        raw.execute("CREATE TABLE summaries (something TEXT)")
        raw.execute("INSERT INTO summaries VALUES ('old')")
        raw.execute("PRAGMA user_version = 0")
    cache = make_cache(db_path)

    await store(cache)

    assert await cache.get(KEY, "ru", FP) is not None
    version = sqlite3.connect(db_path).execute("PRAGMA user_version").fetchone()
    assert version == (SCHEMA_VERSION,)


async def test_many_requests_at_once_do_not_trip_over_each_other(cache: SummaryCache) -> None:
    keys = [f"https://x.edu/{n}" for n in range(30)]

    await asyncio.gather(*(store(cache, key=k) for k in keys))
    found = await asyncio.gather(*(cache.get(k, "ru", FP) for k in keys))

    assert all(entry is not None for entry in found)
