"""Quality evaluation of the whole pipeline against a golden set (`evals/golden.json`).

Each case is a saved input (or a live URL) plus the facts a correct summary must contain. The
metrics that matter most for this product:

- deadline recall: how many of the expected deadlines were found (matched by ISO date, or by
  the date text for undated ones);
- unverified deadlines: reported deadlines whose quote or date is not in the source text. This
  is the measure of invented facts and does not depend on what the golden file lists;
- must_not: text an attacker or a bad answer must not produce (checked outside `warnings`,
  where describing an attack is welcome).

Run (spends real API tokens):   uv run python -m bot.evaluation [--only ID] [--json]
The exit code is non-zero if a threshold is missed, so it can gate a release.
"""

import argparse
import asyncio
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from bot.config import get_settings
from bot.extractors.base import RawSource
from bot.log import setup_logging
from bot.pipeline.detector import SourceFormat
from bot.pipeline.orchestrator import Orchestrator
from bot.pipeline.schema import Summary
from bot.pipeline.summarizer import LANGUAGE_NAMES
from bot.pipeline.verify import normalize

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = ROOT / "evals" / "golden.json"
RESULTS_DIR = ROOT / "evals" / "results"

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class Expected:
    source_type: str | None = None
    deadlines: tuple[str, ...] = ()  # ISO dates, or text for deadlines without a full date
    costs_contain: tuple[str, ...] = ()
    links: tuple[str, ...] = ()
    must_not: tuple[str, ...] = ()
    error: str | None = None  # the exception class name the pipeline should raise


@dataclass(frozen=True)
class GoldenCase:
    id: str
    url: str
    language: str
    expected: Expected
    file: str | None = None  # a saved input; without it the URL is fetched live
    format: SourceFormat | None = None


@dataclass
class CaseResult:
    id: str
    passed: bool = True
    error: str | None = None
    confidence: str | None = None
    class_actual: str | None = None
    deadlines_expected: int = 0
    deadlines_found: int = 0
    deadlines_reported: int = 0
    deadlines_unverified: int = 0
    deadlines_extra: int = 0  # dated, verified, but not listed as expected (informational)
    costs_expected: int = 0
    costs_found: int = 0
    links_expected: int = 0
    links_found: int = 0
    forbidden_hits: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    seconds: float = 0.0


@dataclass
class Report:
    results: list[CaseResult]
    skipped_live: list[str] = field(default_factory=list)  # ids of live cases not run

    @property
    def passed(self) -> int:
        return sum(r.passed for r in self.results)

    @property
    def deadline_recall(self) -> float | None:
        expected = sum(r.deadlines_expected for r in self.results)
        return sum(r.deadlines_found for r in self.results) / expected if expected else None

    @property
    def unverified(self) -> int:
        return sum(r.deadlines_unverified for r in self.results)

    @property
    def hallucination_rate(self) -> float | None:
        reported = sum(r.deadlines_reported for r in self.results)
        return self.unverified / reported if reported else None

    @property
    def forbidden_hits(self) -> int:
        return sum(len(r.forbidden_hits) for r in self.results)


# --- loading ------------------------------------------------------------------------------


def load_cases(path: Path = DEFAULT_CASES) -> list[GoldenCase]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases: list[GoldenCase] = []
    for raw in data["cases"]:
        source = raw["source"]
        expected = raw.get("expected", {})
        cases.append(
            GoldenCase(
                id=raw["id"],
                url=source["url"],
                language=raw["language"],
                file=source.get("file"),
                format=SourceFormat(source["format"]) if source.get("format") else None,
                expected=Expected(
                    source_type=expected.get("source_type"),
                    deadlines=tuple(expected.get("deadlines", ())),
                    costs_contain=tuple(expected.get("costs_contain", ())),
                    links=tuple(expected.get("links", ())),
                    must_not=tuple(expected.get("must_not", ())),
                    error=expected.get("error"),
                ),
            )
        )
    _validate(cases)
    return cases


def _validate(cases: list[GoldenCase]) -> None:
    ids = [case.id for case in cases]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate case ids: {sorted({i for i in ids if ids.count(i) > 1})}")
    for case in cases:
        if case.language not in LANGUAGE_NAMES:
            raise ValueError(f"{case.id}: unsupported language {case.language!r}")
        if case.file and case.format is None:
            raise ValueError(f"{case.id}: a saved file needs its format")
        if case.file and not (ROOT / case.file).is_file():
            raise ValueError(f"{case.id}: file not found: {case.file}")
        for item in case.expected.deadlines:
            if re.match(r"^\d{4}-", item) and not _ISO_DATE_RE.match(item):
                raise ValueError(f"{case.id}: malformed ISO date {item!r}")


# --- scoring (pure) -----------------------------------------------------------------------


def score_case(case: GoldenCase, summary: Summary) -> CaseResult:
    """Compare one summary with what the case expects."""
    expected = case.expected
    result = CaseResult(
        id=case.id, confidence=summary.confidence.value, class_actual=summary.source_type.value
    )
    problems = result.problems

    if expected.error:
        problems.append(f"expected {expected.error} but got a summary")

    if expected.source_type and summary.source_type.value != expected.source_type:
        problems.append(f"class {summary.source_type.value}, expected {expected.source_type}")

    result.deadlines_expected = len(expected.deadlines)
    result.deadlines_reported = len(summary.deadlines)
    result.deadlines_unverified = sum(not d.verified for d in summary.deadlines)
    found_dates = {d.date_iso for d in summary.deadlines if d.date_iso}
    texts = [normalize(d.date_text) for d in summary.deadlines]
    expected_isos = {date.fromisoformat(i) for i in expected.deadlines if _ISO_DATE_RE.match(i)}
    for item in expected.deadlines:
        if _ISO_DATE_RE.match(item):
            hit = date.fromisoformat(item) in found_dates
        else:
            hit = any(normalize(item) in text for text in texts)
        result.deadlines_found += hit
        if not hit:
            problems.append(f"deadline not found: {item}")
    result.deadlines_extra = sum(
        1
        for d in summary.deadlines
        if d.date_iso and d.date_iso not in expected_isos and d.verified
    )
    if result.deadlines_unverified:
        problems.append(f"{result.deadlines_unverified} deadline(s) not backed by the source text")

    costs = [normalize(f"{c.item} {c.amount_text}") for c in summary.costs]
    result.costs_expected = len(expected.costs_contain)
    for needle in expected.costs_contain:
        hit = any(normalize(needle) in cost for cost in costs)
        result.costs_found += hit
        if not hit:
            problems.append(f"cost not found: {needle}")

    urls = {link.url.rstrip("/") for link in summary.useful_links}
    result.links_expected = len(expected.links)
    for link in expected.links:
        hit = link.rstrip("/") in urls
        result.links_found += hit
        if not hit:
            problems.append(f"link not offered: {link}")

    body = normalize(" ".join(_non_warning_text(summary)))
    result.forbidden_hits = [bad for bad in expected.must_not if normalize(bad) in body]
    problems.extend(f"forbidden text present: {bad}" for bad in result.forbidden_hits)

    result.passed = not problems
    return result


def score_error(case: GoldenCase, error: BaseException) -> CaseResult:
    """A case that ended in an exception passes only if that exception was expected."""
    name = type(error).__name__
    result = CaseResult(id=case.id, error=name)
    if case.expected.error != name:
        wanted = f"expected {case.expected.error}" if case.expected.error else "expected a summary"
        result.problems.append(f"raised {name}, {wanted}")
    result.passed = not result.problems
    return result


def _non_warning_text(summary: Summary) -> list[str]:
    """Every field except `warnings`: describing an attack there is wanted, repeating it in
    the facts is not."""
    parts = [summary.title or "", summary.organization or "", summary.overview]
    parts += [*summary.key_points, *summary.eligibility, *summary.requirements]
    parts += [f"{c.item} {c.amount_text}" for c in summary.costs]
    parts += [f"{d.label} {d.date_text} {d.quote} {d.date_iso or ''}" for d in summary.deadlines]
    parts += [f"{link.title} {link.url}" for link in summary.useful_links]
    return parts


# --- running ------------------------------------------------------------------------------


async def evaluate(
    cases: list[GoldenCase],
    orchestrator: Orchestrator,
    *,
    only: set[str] | None = None,
    include_live: bool = False,
) -> Report:
    """Run the selected cases one after another (cheap on rate limits, readable in a log).

    Cases without a saved file fetch a live page, so they depend on someone else's site being
    up and unchanged; they run only with `include_live`."""
    results: list[CaseResult] = []
    skipped_live: list[str] = []
    for case in cases:
        if only and case.id not in only:
            continue
        if case.file is None and not include_live:
            skipped_live.append(case.id)
            continue
        started = time.monotonic()
        try:
            summary = await _run_case(case, orchestrator)
            result = score_case(case, summary)
        except Exception as exc:
            result = score_error(case, exc)
        result.seconds = round(time.monotonic() - started, 1)
        results.append(result)
    return Report(results, skipped_live)


async def _run_case(case: GoldenCase, orchestrator: Orchestrator) -> Summary:
    if case.file and case.format:
        raw = RawSource(url=case.url, body=(ROOT / case.file).read_bytes())
        return await orchestrator.process(
            raw, case.format, language=case.language, source_url=case.url
        )
    return await orchestrator.run(case.url, language=case.language)


def exit_code(report: Report, *, min_recall: float, max_unverified: int) -> int:
    recall = report.deadline_recall
    failed = (
        any(not r.passed for r in report.results)
        or (recall is not None and recall < min_recall)
        or report.unverified > max_unverified
    )
    return 1 if failed else 0


def format_report(report: Report, *, min_recall: float, max_unverified: int) -> str:
    lines: list[str] = []
    for r in report.results:
        mark = "PASS" if r.passed else "FAIL"
        detail = (
            f"error={r.error}"
            if r.error
            else (
                f"class={r.class_actual} conf={r.confidence} "
                f"deadlines {r.deadlines_found}/{r.deadlines_expected} "
                f"unverified {r.deadlines_unverified}/{r.deadlines_reported} "
                f"costs {r.costs_found}/{r.costs_expected} links {r.links_found}/{r.links_expected}"
            )
        )
        lines.append(f"{mark}  {r.id:<28} {detail}  ({r.seconds}s)")
        lines.extend(f"        - {problem}" for problem in r.problems)

    recall = report.deadline_recall
    rate = report.hallucination_rate
    lines += [
        "",
        f"cases passed        {report.passed}/{len(report.results)}",
        f"deadline recall     {_pct(recall)}   (threshold {min_recall:.0%})",
        f"unverified deadlines {report.unverified}   (threshold {max_unverified}; "
        f"rate {_pct(rate)} of reported)",
        f"forbidden text hits {report.forbidden_hits}",
    ]
    if report.skipped_live:
        lines.append(f"skipped (live pages, add --live): {', '.join(report.skipped_live)}")
    return "\n".join(lines)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


# --- command line -------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the pipeline on the golden set.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--only", action="append", help="run only this case id (repeatable)")
    parser.add_argument("--min-recall", type=float, default=0.9)
    parser.add_argument("--max-unverified", type=int, default=0)
    parser.add_argument("--live", action="store_true", help="also run cases that fetch live pages")
    parser.add_argument("--json", action="store_true", help="also save the results as JSON")
    args = parser.parse_args(argv)

    settings = get_settings()
    setup_logging("WARNING")

    async def run() -> Report:
        orchestrator = Orchestrator.from_settings(settings)
        try:
            return await evaluate(
                load_cases(args.cases),
                orchestrator,
                only=set(args.only) if args.only else None,
                include_live=args.live,
            )
        finally:
            await orchestrator.aclose()

    report = asyncio.run(run())
    print(format_report(report, min_recall=args.min_recall, max_unverified=args.max_unverified))
    if args.json:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        out = RESULTS_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
        payload: dict[str, Any] = {
            "model": settings.llm_model,
            "results": [asdict(r) for r in report.results],
        }
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"saved {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")
    return exit_code(report, min_recall=args.min_recall, max_unverified=args.max_unverified)


if __name__ == "__main__":
    sys.exit(main())
