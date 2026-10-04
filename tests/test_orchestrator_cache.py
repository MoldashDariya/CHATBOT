"""The answer cache inside the pipeline: when a stored answer is shown, when it is not, and
that it can never cost a request. Same fakes as the pipeline tests (HTTP and model replaced)."""

from collections.abc import AsyncIterator, Callable
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import respx

from bot.errors import LLMError, TooLittleTextError
from bot.pipeline.orchestrator import Orchestrator
from bot.pipeline.schema import Summary
from bot.storage.cache import SummaryCache
from tests.fakes import FakeClient, api_error, llm_answer, reply
from tests.pdfgen import PageSpec, make_pdf
from tests.test_orchestrator import (
    FIXTURES,
    SITE_IP,
    TODAY,
    UNIVERSITY_URL,
    build,
    serve_university,
)

import anthropic  # isort: skip

TTL = 3600.0


class Clock:
    def __init__(self) -> None:
        self.now = 2_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
async def make_cache(tmp_path: Path, clock: Clock) -> AsyncIterator[Callable[..., SummaryCache]]:
    made: list[SummaryCache] = []

    def build_cache(path: Path | None = None) -> SummaryCache:
        made.append(SummaryCache(path or tmp_path / "cache.sqlite3", clock=clock))
        return made[-1]

    yield build_cache
    for cache in made:
        await cache.aclose()


def pipeline_with(
    cache: SummaryCache | None, clock: Clock, *outcomes: Any, **options: Any
) -> tuple[Orchestrator, FakeClient]:
    return build(*outcomes, cache=cache, cache_ttl_s=TTL, clock=clock, **options)


def model_calls(client: FakeClient) -> int:
    return len(client.messages.calls)


def serve_page(router: respx.MockRouter, path: str = "/admissions/undergraduate") -> respx.Route:
    return router.get(f"https://{SITE_IP}{path}").respond(
        200,
        content=(FIXTURES / "university_admissions.html").read_bytes(),
        headers={"Content-Type": "text/html; charset=utf-8"},
    )


def serve_changed_page(router: respx.MockRouter) -> respx.Route:
    html = (FIXTURES / "university_admissions.html").read_text(encoding="utf-8")
    changed = html.replace("1 November 2026", "8 November 2026")
    assert changed != html
    return router.get(f"https://{SITE_IP}/admissions/undergraduate").respond(
        200, content=changed.encode(), headers={"Content-Type": "text/html; charset=utf-8"}
    )


# --- a repeated link ----------------------------------------------------------------------


async def test_the_second_request_costs_no_download_and_no_model_call(
    make_cache: Callable[..., SummaryCache], clock: Clock
) -> None:
    pipeline, client = pipeline_with(make_cache(), clock, reply())

    with respx.mock() as router:
        route = serve_page(router)
        first = await pipeline.run(UNIVERSITY_URL, language="ru")
        clock.now += 600
        second = await pipeline.run(UNIVERSITY_URL, language="ru")

    assert route.call_count == 1 and model_calls(client) == 1
    assert first.cached_minutes_ago is None
    assert second.cached_minutes_ago == 10
    assert second.model_copy(update={"cached_minutes_ago": None}) == first


async def test_cached_summary_answers_without_running_anything(
    make_cache: Callable[..., SummaryCache], clock: Clock
) -> None:
    pipeline, client = pipeline_with(make_cache(), clock, reply())
    assert await pipeline.cached_summary(UNIVERSITY_URL, "ru") is None  # nothing stored yet

    with respx.mock() as router:
        serve_page(router)
        await pipeline.run(UNIVERSITY_URL, language="ru")

    hit = await pipeline.cached_summary(UNIVERSITY_URL, "ru")  # no network mock: would fail
    assert hit is not None and hit.cached_minutes_ago == 0
    assert model_calls(client) == 1


@pytest.mark.parametrize(
    "variant",
    [
        UNIVERSITY_URL + "#deadlines",
        UNIVERSITY_URL + "?utm_source=telegram&utm_campaign=x",
        UNIVERSITY_URL.replace("https://www.", "HTTPS://WWW."),
    ],
)
async def test_the_same_page_under_another_spelling_is_a_hit_and_shows_the_link_just_sent(
    variant: str, make_cache: Callable[..., SummaryCache], clock: Clock
) -> None:
    pipeline, client = pipeline_with(make_cache(), clock, reply())
    with respx.mock() as router:
        serve_page(router)
        await pipeline.run(UNIVERSITY_URL, language="ru")

    hit = await pipeline.run(variant, language="ru")

    assert model_calls(client) == 1
    assert hit.source_url == variant  # the source is what this user sent, not the first user's


async def test_another_page_or_another_language_is_a_miss(
    make_cache: Callable[..., SummaryCache], clock: Clock
) -> None:
    pipeline, client = pipeline_with(make_cache(), clock, reply(), reply(), reply())

    with respx.mock() as router:
        serve_page(router)
        serve_page(router, "/admissions/graduate")
        await pipeline.run(UNIVERSITY_URL, language="ru")
        await pipeline.run(UNIVERSITY_URL, language="en")
        await pipeline.run(UNIVERSITY_URL.replace("undergraduate", "graduate"), language="ru")

    assert model_calls(client) == 3


async def test_a_page_with_a_different_query_is_not_confused_with_the_first(
    make_cache: Callable[..., SummaryCache], clock: Clock
) -> None:
    pipeline, client = pipeline_with(make_cache(), clock, reply(), reply())

    with respx.mock() as router:
        router.get(f"https://{SITE_IP}/admissions/undergraduate").respond(
            200,
            content=(FIXTURES / "university_admissions.html").read_bytes(),
            headers={"Content-Type": "text/html; charset=utf-8"},
        )
        await pipeline.run(UNIVERSITY_URL + "?programme=msc", language="ru")
        await pipeline.run(UNIVERSITY_URL + "?programme=bsc", language="ru")

    assert model_calls(client) == 2


async def test_a_hit_does_not_wait_for_a_free_slot(
    make_cache: Callable[..., SummaryCache], clock: Clock
) -> None:
    pipeline, client = pipeline_with(make_cache(), clock, reply(), max_concurrent=1)
    with respx.mock() as router:
        serve_page(router)
        await pipeline.run(UNIVERSITY_URL, language="ru")

    await pipeline._slots.acquire()  # every slot is busy with someone else's request
    try:
        hit = await pipeline.run(UNIVERSITY_URL, language="ru")
    finally:
        pipeline._slots.release()

    assert hit.cached_minutes_ago == 0 and model_calls(client) == 1


# --- freshness ----------------------------------------------------------------------------


async def test_an_answer_is_fresh_for_exactly_the_ttl(
    make_cache: Callable[..., SummaryCache], clock: Clock
) -> None:
    pipeline, _ = pipeline_with(make_cache(), clock, reply())
    with respx.mock() as router:
        serve_page(router)
        await pipeline.run(UNIVERSITY_URL, language="ru")

    clock.now += TTL - 1
    assert await pipeline.cached_summary(UNIVERSITY_URL, "ru") is not None
    clock.now += 1
    assert await pipeline.cached_summary(UNIVERSITY_URL, "ru") is None


async def test_after_the_ttl_an_unchanged_page_reuses_the_answer_without_the_model(
    make_cache: Callable[..., SummaryCache], clock: Clock
) -> None:
    pipeline, client = pipeline_with(make_cache(), clock, reply())

    with respx.mock() as router:
        route = serve_page(router)
        await pipeline.run(UNIVERSITY_URL, language="ru")
        clock.now += TTL * 5  # long past fresh, well inside the retention time
        again = await pipeline.run(UNIVERSITY_URL, language="ru")

    assert route.call_count == 2  # the page is read again to compare
    assert model_calls(client) == 1  # but the model is not asked the same question twice
    assert again.cached_minutes_ago == int(TTL * 5 // 60)  # the age stays the stored one


async def test_after_the_ttl_a_changed_page_gets_a_new_answer(
    make_cache: Callable[..., SummaryCache], clock: Clock
) -> None:
    new = llm_answer(overview="Updated overview")
    pipeline, client = pipeline_with(make_cache(), clock, reply(), reply(new))

    with respx.mock() as router:
        serve_page(router)
        await pipeline.run(UNIVERSITY_URL, language="ru")
    clock.now += TTL * 2
    with respx.mock() as router:
        serve_changed_page(router)
        again = await pipeline.run(UNIVERSITY_URL, language="ru")

    assert model_calls(client) == 2
    assert again.overview == "Updated overview" and again.cached_minutes_ago is None
    fresh = await pipeline.cached_summary(UNIVERSITY_URL, "ru")  # and it is stored now
    assert fresh is not None and fresh.overview == "Updated overview"


async def test_a_deadline_that_has_passed_since_is_flagged_when_the_answer_is_shown_again(
    make_cache: Callable[..., SummaryCache], clock: Clock
) -> None:
    today = {"value": TODAY}
    pipeline, _ = pipeline_with(make_cache(), clock, reply(), today=lambda: today["value"])
    with respx.mock() as router:
        serve_page(router)
        first = await pipeline.run(UNIVERSITY_URL, language="ru")
    assert [d.possibly_passed for d in first.deadlines] == [False, False]

    today["value"] = date(2026, 12, 1)  # the 1 November 2026 deadline is now behind us
    later = await pipeline.cached_summary(UNIVERSITY_URL, "ru")

    assert later is not None
    assert [d.possibly_passed for d in later.deadlines] == [True, False]


# --- the refresh -------------------------------------------------------------------------


async def test_refresh_ignores_a_fresh_answer_and_replaces_it(
    make_cache: Callable[..., SummaryCache], clock: Clock
) -> None:
    pipeline, client = pipeline_with(
        make_cache(), clock, reply(), reply(llm_answer(overview="Second reading"))
    )

    with respx.mock() as router:
        route = serve_page(router)
        await pipeline.run(UNIVERSITY_URL, language="ru")
        refreshed = await pipeline.run(UNIVERSITY_URL, language="ru", refresh=True)

    assert route.call_count == 2 and model_calls(client) == 2
    assert refreshed.overview == "Second reading" and refreshed.cached_minutes_ago is None
    stored = await pipeline.cached_summary(UNIVERSITY_URL, "ru")
    assert stored is not None and stored.overview == "Second reading"


async def test_refresh_does_not_reuse_the_old_answer_even_if_the_page_is_unchanged(
    make_cache: Callable[..., SummaryCache], clock: Clock
) -> None:
    pipeline, client = pipeline_with(make_cache(), clock, reply(), reply())
    with respx.mock() as router:
        serve_page(router)
        await pipeline.run(UNIVERSITY_URL, language="ru")
        clock.now += TTL * 5  # stale, and the page reads the same: normally reused
        await pipeline.run(UNIVERSITY_URL, language="ru", refresh=True)

    assert model_calls(client) == 2


async def test_a_failed_refresh_keeps_the_stored_answer(
    make_cache: Callable[..., SummaryCache], clock: Clock
) -> None:
    pipeline, _ = pipeline_with(
        make_cache(), clock, reply(), api_error(anthropic.InternalServerError, 500)
    )
    with respx.mock() as router:
        serve_page(router)
        first = await pipeline.run(UNIVERSITY_URL, language="ru")
        with pytest.raises(LLMError):
            await pipeline.run(UNIVERSITY_URL, language="ru", refresh=True)

    kept = await pipeline.cached_summary(UNIVERSITY_URL, "ru")
    assert kept is not None and kept.overview == first.overview


async def test_the_button_leads_back_to_the_link_as_sent_and_its_language(
    make_cache: Callable[..., SummaryCache], clock: Clock
) -> None:
    pipeline, _ = pipeline_with(make_cache(), clock, reply())
    sent = UNIVERSITY_URL + "?utm_source=telegram"
    assert await pipeline.refresh_button_id(sent, "ru") is None  # nothing stored yet

    with respx.mock() as router:
        serve_page(router)
        await pipeline.run(sent, language="ru")

    button = await pipeline.refresh_button_id(UNIVERSITY_URL, "ru")
    assert button is not None and len(button) == 16
    assert await pipeline.refresh_target(button) == (sent, "ru")
    assert await pipeline.refresh_button_id(UNIVERSITY_URL, "en") is None
    assert await pipeline.refresh_target("0" * 16) is None


# --- what is never reused -----------------------------------------------------------------


async def test_an_answer_made_under_another_model_is_not_served(
    make_cache: Callable[..., SummaryCache], clock: Clock
) -> None:
    shared = make_cache()
    old, _ = pipeline_with(shared, clock, reply(), model="model-a")
    with respx.mock() as router:
        serve_page(router)
        await old.run(UNIVERSITY_URL, language="ru")

    new, client = pipeline_with(shared, clock, reply(), model="model-b")
    assert await new.cached_summary(UNIVERSITY_URL, "ru") is None
    with respx.mock() as router:
        serve_page(router)
        await new.run(UNIVERSITY_URL, language="ru")

    assert model_calls(client) == 1


async def test_answers_made_with_another_linked_page_setting_are_not_served(
    make_cache: Callable[..., SummaryCache], clock: Clock
) -> None:
    shared = make_cache()
    first, _ = pipeline_with(shared, clock, reply(), follow_pages=0)
    with respx.mock() as router:
        serve_page(router)
        await first.run(UNIVERSITY_URL, language="ru")

    second, _ = pipeline_with(shared, clock, reply(), follow_pages=2)

    assert await second.cached_summary(UNIVERSITY_URL, "ru") is None


async def test_failures_are_never_stored(
    make_cache: Callable[..., SummaryCache], clock: Clock
) -> None:
    pipeline, client = pipeline_with(
        make_cache(), clock, api_error(anthropic.InternalServerError, 500), reply()
    )
    with respx.mock() as router:
        serve_page(router)
        with pytest.raises(LLMError):
            await pipeline.run(UNIVERSITY_URL, language="ru")
        assert await pipeline.cached_summary(UNIVERSITY_URL, "ru") is None
        await pipeline.run(UNIVERSITY_URL, language="ru")  # tried again, not served from a failure

    assert model_calls(client) == 2


async def test_a_page_without_text_is_never_stored(
    make_cache: Callable[..., SummaryCache], clock: Clock
) -> None:
    pipeline, client = pipeline_with(make_cache(), clock)
    with respx.mock() as router:
        router.get(f"https://{SITE_IP}/admissions/undergraduate").respond(
            200, content=(FIXTURES / "js_shell.html").read_bytes(),
            headers={"Content-Type": "text/html"},
        )  # fmt: skip
        for _ in range(2):
            with pytest.raises(TooLittleTextError):
                await pipeline.run(UNIVERSITY_URL, language="ru")

    assert model_calls(client) == 0
    assert await pipeline.cached_summary(UNIVERSITY_URL, "ru") is None


async def test_files_sent_into_the_chat_are_never_cached(
    make_cache: Callable[..., SummaryCache], clock: Clock
) -> None:
    pipeline, client = pipeline_with(make_cache(), clock, reply(), reply())
    pdf = make_pdf(
        [
            PageSpec(
                body=["Admission rules 2027."]
                + [f"Deadline {n}: 15 March 2027." for n in range(1, 21)]
            )
        ]
    )

    for _ in range(2):
        await pipeline.run_file(pdf, filename="rules.pdf", language="ru")

    assert model_calls(client) == 2
    assert await pipeline.cached_summary("telegram://uploaded-file", "ru") is None


async def test_links_the_cache_cannot_key_just_go_through_the_pipeline(
    make_cache: Callable[..., SummaryCache], clock: Clock
) -> None:
    pipeline, client = pipeline_with(make_cache(), clock)
    assert await pipeline.cached_summary("https://user:pw@www.example.edu/x", "ru") is None

    with pytest.raises(Exception) as caught:  # refused by the URL guard, as without a cache
        await pipeline.run("https://user:pw@www.example.edu/x", language="ru")

    assert type(caught.value).__name__ == "InvalidURLError"
    assert model_calls(client) == 0


# --- switched off, broken ------------------------------------------------------------------


@pytest.mark.parametrize("ttl", [0.0, -1.0])
async def test_without_a_ttl_nothing_is_cached(
    ttl: float, make_cache: Callable[..., SummaryCache], clock: Clock
) -> None:
    pipeline, client = build(reply(), reply(), cache=make_cache(), cache_ttl_s=ttl, clock=clock)

    with respx.mock() as router:
        serve_page(router)
        await pipeline.run(UNIVERSITY_URL, language="ru")
        again = await pipeline.run(UNIVERSITY_URL, language="ru")

    assert model_calls(client) == 2 and again.cached_minutes_ago is None
    assert await pipeline.cached_summary(UNIVERSITY_URL, "ru") is None
    assert await pipeline.refresh_button_id(UNIVERSITY_URL, "ru") is None
    assert await pipeline.refresh_target("0" * 16) is None


async def test_without_a_store_the_pipeline_works_as_before() -> None:
    pipeline, client = build(reply(), reply())
    with respx.mock() as router:
        serve_university(router)
        await pipeline.run(UNIVERSITY_URL, language="ru")
        await pipeline.run(UNIVERSITY_URL, language="ru")

    assert model_calls(client) == 2


async def test_a_broken_store_never_fails_a_request(
    tmp_path: Path, make_cache: Callable[..., SummaryCache], clock: Clock
) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a folder")
    pipeline, client = pipeline_with(make_cache(blocker / "x.sqlite3"), clock, reply(), reply())

    with respx.mock() as router:
        serve_page(router)
        first = await pipeline.run(UNIVERSITY_URL, language="ru")
        second = await pipeline.run(UNIVERSITY_URL, language="ru")

    assert first.cached_minutes_ago is None and second.cached_minutes_ago is None
    assert model_calls(client) == 2
    assert await pipeline.refresh_button_id(UNIVERSITY_URL, "ru") is None


async def test_closing_the_pipeline_closes_the_store(
    make_cache: Callable[..., SummaryCache], clock: Clock
) -> None:
    cache = make_cache()
    pipeline, client = pipeline_with(cache, clock, reply())
    with respx.mock() as router:
        serve_page(router)
        await pipeline.run(UNIVERSITY_URL, language="ru")

    await pipeline.aclose()

    assert client.closed and cache._db is None


def test_the_summary_type_carries_the_age() -> None:
    assert "cached_minutes_ago" in Summary.model_fields
