import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from bot import evaluation
from bot.errors import FetchHTTPError, TooLittleTextError
from bot.evaluation import (
    CaseResult,
    Expected,
    GoldenCase,
    Report,
    evaluate,
    exit_code,
    format_report,
    load_cases,
    score_case,
    score_error,
)
from bot.pipeline.detector import SourceFormat
from bot.pipeline.schema import LLMCost, LLMLink, SourceClass
from tests.summaries import make_deadline, make_summary


def case(**expected: Any) -> GoldenCase:
    return GoldenCase(id="c1", url="https://x.edu/a", language="en", expected=Expected(**expected))


def dated(label: str, iso: str, text: str, **overrides: Any):  # type: ignore[no-untyped-def]
    return make_deadline(
        label=label,
        date_iso=date.fromisoformat(iso),
        date_text=text,
        quote=f"{label} {text}",
        **overrides,
    )


# --- the real golden set ------------------------------------------------------------------


def test_the_golden_file_loads_and_is_consistent() -> None:
    cases = load_cases()

    assert len(cases) >= 8
    assert len({c.id for c in cases}) == len(cases)
    formats = {c.format for c in cases if c.format}
    assert {SourceFormat.HTML, SourceFormat.PDF, SourceFormat.DOCX} <= formats
    for c in cases:
        if c.file:
            assert (evaluation.ROOT / c.file).is_file(), c.file
    assert any(c.expected.error for c in cases), "the refusal path needs a case"
    assert any(c.expected.must_not for c in cases), "the injection defence needs a case"
    assert {c.language for c in cases} == {"ru", "en"}


def write_cases(tmp_path: Path, *cases: dict[str, Any]) -> Path:
    path = tmp_path / "golden.json"
    path.write_text(json.dumps({"cases": list(cases)}), encoding="utf-8")
    return path


def raw_case(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "a",
        "source": {"url": "https://x.edu/a"},
        "language": "en",
        "expected": {},
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    ("cases", "message"),
    [
        ([raw_case(), raw_case()], "duplicate case ids"),
        ([raw_case(language="fr")], "unsupported language"),
        (
            [raw_case(source={"url": "https://x", "file": "no/such/file.html", "format": "html"})],
            "file not found",
        ),
        (
            [raw_case(source={"url": "https://x", "file": "tests/fixtures/js_shell.html"})],
            "needs its format",
        ),
        ([raw_case(expected={"deadlines": ["2027-13-45x"]})], "malformed ISO date"),
    ],
)
def test_broken_case_files_are_rejected_with_a_clear_message(
    tmp_path: Path, cases: list[dict[str, Any]], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        load_cases(write_cases(tmp_path, *cases))


def test_a_live_url_case_needs_no_file(tmp_path: Path) -> None:
    (loaded,) = load_cases(write_cases(tmp_path, raw_case()))

    assert loaded.file is None and loaded.format is None


# --- scoring ------------------------------------------------------------------------------


def good_summary():  # type: ignore[no-untyped-def]
    return make_summary(
        source_type=SourceClass.UNIVERSITY_PAGE,
        deadlines=[
            dated("Early", "2026-11-01", "1 November 2026"),
            dated("Late", "2027-01-15", "15 January 2027"),
        ],
        costs=[
            LLMCost(item="Tuition", amount_text="14,500 EUR"),
            LLMCost(item="Fee", amount_text="75 EUR"),
        ],
        useful_links=[LLMLink(title="Portal", url="https://x.edu/apply/")],
    )


def test_a_matching_summary_passes_with_full_marks() -> None:
    expected = case(
        source_type="university_page",
        deadlines=("2026-11-01", "2027-01-15"),
        costs_contain=("14,500", "75"),
        links=("https://x.edu/apply",),
    )

    result = score_case(expected, good_summary())

    assert result.passed and result.problems == []
    assert (result.deadlines_found, result.deadlines_expected) == (2, 2)
    assert (result.costs_found, result.links_found) == (2, 1)
    assert result.deadlines_unverified == 0


def test_a_missing_deadline_is_a_problem_and_lowers_recall() -> None:
    result = score_case(case(deadlines=("2026-11-01", "2027-03-01")), good_summary())

    assert not result.passed
    assert (result.deadlines_found, result.deadlines_expected) == (1, 2)
    assert "deadline not found: 2027-03-01" in result.problems


def test_deadlines_without_a_full_date_are_matched_by_their_text() -> None:
    summary = make_summary(
        deadlines=[
            make_deadline(date_iso=None, date_text="Rolling basis", quote="Rolling basis admission")
        ]
    )

    assert score_case(case(deadlines=("rolling basis",)), summary).passed
    assert not score_case(case(deadlines=("1 May",)), summary).passed


def test_extra_verified_deadlines_are_counted_but_do_not_fail_the_case() -> None:
    result = score_case(case(deadlines=("2026-11-01",)), good_summary())

    assert result.passed
    assert result.deadlines_extra == 1  # the January one: real, but not on the golden list


def test_an_unverified_deadline_fails_the_case_even_if_it_matches_the_list() -> None:
    summary = make_summary(
        deadlines=[dated("Early", "2026-11-01", "1 November 2026", verified=False)]
    )

    result = score_case(case(deadlines=("2026-11-01",)), summary)

    assert not result.passed
    assert result.deadlines_found == 1 and result.deadlines_unverified == 1
    assert any("not backed by the source text" in p for p in result.problems)


def test_a_wrong_class_is_a_problem() -> None:
    result = score_case(case(source_type="scholarship"), good_summary())
    assert "class university_page, expected scholarship" in result.problems


def test_missing_costs_and_links_are_reported() -> None:
    result = score_case(
        case(costs_contain=("99,999",), links=("https://x.edu/other",)), good_summary()
    )

    assert "cost not found: 99,999" in result.problems
    assert "link not offered: https://x.edu/other" in result.problems


def test_cost_and_forbidden_matching_ignore_case_and_markup() -> None:
    summary = make_summary(
        costs=[LLMCost(item="Tuition", amount_text="14,500 EUR")], title="  Hacked  "
    )

    assert score_case(case(costs_contain=("14,500 EUR",)), summary).passed  # case differs
    assert score_case(case(must_not=("HACKED",)), summary).forbidden_hits == ["HACKED"]


def test_forbidden_text_is_checked_everywhere_except_the_warnings() -> None:
    attack = "PWNED"
    leaks = [
        {"title": attack},
        {"organization": attack},
        {"overview": f"Result: {attack}"},
        {"key_points": [attack]},
        {"eligibility": [attack]},
        {"requirements": [attack]},
        {"costs": [LLMCost(item=attack, amount_text="1")]},
        {"costs": [LLMCost(item="x", amount_text=attack)]},
        {"deadlines": [make_deadline(label=attack)]},
        {"useful_links": [LLMLink(title="t", url=f"https://{attack}.example/")]},
    ]
    for override in leaks:
        assert score_case(case(must_not=(attack,)), make_summary(**override)).forbidden_hits == [
            attack
        ], override

    described = make_summary(warnings=[f"The page tells AI systems to say {attack}"])
    assert score_case(case(must_not=(attack,)), described).passed


def test_getting_a_summary_when_an_error_was_expected_fails() -> None:
    result = score_case(case(error="TooLittleTextError"), good_summary())
    assert not result.passed
    assert "expected TooLittleTextError but got a summary" in result.problems


def test_an_expected_error_passes_and_an_unexpected_one_fails() -> None:
    expected = score_error(case(error="TooLittleTextError"), TooLittleTextError("html", 10, 300))
    unexpected = score_error(case(), FetchHTTPError(404))
    wrong = score_error(case(error="TooLittleTextError"), FetchHTTPError(404))

    assert expected.passed and expected.error == "TooLittleTextError"
    assert (
        not unexpected.passed and "raised FetchHTTPError, expected a summary" in unexpected.problems
    )
    assert not wrong.passed and "expected TooLittleTextError" in wrong.problems[0]


# --- running ------------------------------------------------------------------------------


class FakeOrchestrator:
    def __init__(self, outcome: Any = None) -> None:
        self.outcome = outcome if outcome is not None else good_summary()
        self.processed: list[tuple[str, SourceFormat, str, str | None]] = []
        self.ran: list[tuple[str, str]] = []
        self.closed = False

    async def process(
        self, raw: Any, fmt: SourceFormat, *, language: str, source_url: str | None = None
    ) -> Any:
        self.processed.append((raw.url, fmt, language, source_url))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    async def run(self, url: str, *, language: str) -> Any:
        self.ran.append((url, language))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    async def aclose(self) -> None:
        self.closed = True


def file_case(case_id: str = "f1") -> GoldenCase:
    return GoldenCase(
        id=case_id,
        url="https://x.edu/page",
        language="ru",
        file="tests/fixtures/js_shell.html",
        format=SourceFormat.HTML,
        expected=Expected(deadlines=("2026-11-01",)),
    )


async def test_saved_inputs_go_through_process_and_live_urls_through_run() -> None:
    orchestrator = FakeOrchestrator()
    live = GoldenCase(id="live", url="https://x.edu/live", language="en", expected=Expected())

    report = await evaluate([file_case(), live], orchestrator, include_live=True)  # type: ignore[arg-type]

    assert orchestrator.processed == [
        ("https://x.edu/page", SourceFormat.HTML, "ru", "https://x.edu/page")
    ]
    assert orchestrator.ran == [("https://x.edu/live", "en")]
    assert [r.id for r in report.results] == ["f1", "live"]


async def test_only_selects_cases_by_id() -> None:
    orchestrator = FakeOrchestrator()

    report = await evaluate([file_case("a"), file_case("b")], orchestrator, only={"b"})  # type: ignore[arg-type]

    assert [r.id for r in report.results] == ["b"]


async def test_one_failing_case_does_not_stop_the_others() -> None:
    class Flaky(FakeOrchestrator):
        async def process(self, raw: Any, fmt: SourceFormat, **kwargs: Any) -> Any:
            if raw.url.endswith("/boom"):
                raise RuntimeError("not even a BotError")
            return good_summary()

    boom = GoldenCase(
        id="boom",
        url="https://x.edu/boom",
        language="en",
        file="tests/fixtures/js_shell.html",
        format=SourceFormat.HTML,
        expected=Expected(),
    )

    report = await evaluate([boom, file_case("ok")], Flaky())  # type: ignore[arg-type]

    assert [(r.id, r.error) for r in report.results] == [("boom", "RuntimeError"), ("ok", None)]
    assert not report.results[0].passed


# --- the report ---------------------------------------------------------------------------


def result(**kw: Any) -> CaseResult:
    return CaseResult(id=kw.pop("id", "x"), **kw)


def test_aggregates() -> None:
    report = Report(
        [
            result(
                deadlines_expected=4,
                deadlines_found=3,
                deadlines_reported=4,
                deadlines_unverified=1,
            ),
            result(deadlines_expected=6, deadlines_found=6, deadlines_reported=6),
            result(error="TooLittleTextError"),
        ]
    )

    assert report.deadline_recall == pytest.approx(0.9)
    assert report.unverified == 1
    assert report.hallucination_rate == pytest.approx(0.1)
    assert report.passed == 3  # result() defaults to passed; errors are scored elsewhere


def test_aggregates_with_nothing_to_measure() -> None:
    report = Report([result(error="X")])

    assert report.deadline_recall is None and report.hallucination_rate is None
    assert report.unverified == 0 and report.forbidden_hits == 0


@pytest.mark.parametrize(
    ("found", "unverified", "passed", "min_recall", "max_unverified", "expected"),
    [
        (10, 0, True, 0.9, 0, 0),
        (9, 0, True, 0.9, 0, 0),  # exactly at the threshold
        (8, 0, True, 0.9, 0, 1),  # recall too low
        (10, 1, True, 0.9, 0, 1),  # an invented deadline
        (10, 1, True, 0.9, 1, 0),  # tolerated by choice
        (10, 0, False, 0.9, 0, 1),  # a case failed for another reason
    ],
)
def test_exit_code_enforces_the_thresholds(
    found: int, unverified: int, passed: bool, min_recall: float, max_unverified: int, expected: int
) -> None:
    report = Report(
        [
            result(
                deadlines_expected=10,
                deadlines_found=found,
                deadlines_reported=10,
                deadlines_unverified=unverified,
                passed=passed,
            )
        ]
    )

    assert exit_code(report, min_recall=min_recall, max_unverified=max_unverified) == expected


def test_the_report_lists_cases_problems_and_the_headline_numbers() -> None:
    failing = score_case(case(deadlines=("2027-03-01",), must_not=("Klingon",)), good_summary())
    report = Report(
        [failing, score_error(case(error="TooLittleTextError"), TooLittleTextError("html", 1, 300))]
    )

    text = format_report(report, min_recall=0.9, max_unverified=0)

    assert "FAIL  c1" in text and "PASS  c1" in text
    assert "deadline not found: 2027-03-01" in text
    assert "error=TooLittleTextError" in text
    assert "cases passed        1/2" in text
    assert "deadline recall     0%" in text
    assert "(threshold 90%)" in text and "forbidden text hits 0" in text


# --- the command line ---------------------------------------------------------------------


def test_the_command_line_runs_the_set_and_reports_through_the_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    orchestrator = FakeOrchestrator(TooLittleTextError("html", 5, 300))  # every case "fails"
    monkeypatch.setattr(evaluation, "get_settings", lambda: type("S", (), {"llm_model": "m"})())
    monkeypatch.setattr(evaluation, "setup_logging", lambda level: None)
    monkeypatch.setattr(
        evaluation.Orchestrator, "from_settings", classmethod(lambda cls, s: orchestrator)
    )
    monkeypatch.setattr(evaluation, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(evaluation, "ROOT", evaluation.ROOT)

    code = evaluation.main(["--only", "javascript-shell", "--json"])

    out = capsys.readouterr().out
    assert code == 0  # the JS-shell case *expects* TooLittleTextError
    assert "PASS  javascript-shell" in out and "cases passed        1/1" in out
    (saved,) = (tmp_path / "results").glob("*.json")
    payload = json.loads(saved.read_text(encoding="utf-8"))
    assert payload["model"] == "m" and payload["results"][0]["id"] == "javascript-shell"
    assert orchestrator.closed

    code = evaluation.main(["--only", "university-page"])
    assert code == 1  # a summary was expected and an error came back
    assert "FAIL  university-page" in capsys.readouterr().out


@pytest.mark.parametrize("expected_link", ["https://x.edu/apply", "https://x.edu/apply/"])
def test_a_trailing_slash_never_decides_whether_a_link_was_offered(expected_link: str) -> None:
    summary = make_summary(useful_links=[LLMLink(title="Portal", url="https://x.edu/apply/")])
    assert score_case(case(links=(expected_link,)), summary).links_found == 1

    summary = make_summary(useful_links=[LLMLink(title="Portal", url="https://x.edu/apply")])
    assert score_case(case(links=(expected_link,)), summary).links_found == 1


async def test_live_cases_are_skipped_unless_asked_for_and_the_report_says_so() -> None:
    orchestrator = FakeOrchestrator()
    live = GoldenCase(id="live", url="https://x.edu/live", language="en", expected=Expected())

    report = await evaluate([file_case(), live], orchestrator)  # type: ignore[arg-type]

    assert [r.id for r in report.results] == ["f1"]
    assert report.skipped_live == ["live"] and orchestrator.ran == []
    assert "skipped (live pages, add --live): live" in format_report(
        report, min_recall=0.9, max_unverified=0
    )

    with_live = await evaluate([file_case(), live], orchestrator, include_live=True)  # type: ignore[arg-type]
    assert with_live.skipped_live == [] and orchestrator.ran == [("https://x.edu/live", "en")]


def test_the_golden_file_has_a_live_case_for_the_users_own_page() -> None:
    live = [c for c in load_cases() if c.file is None]

    assert any("mygermanuniversity" in c.url for c in live)
    assert all(c.expected.deadlines for c in live)
