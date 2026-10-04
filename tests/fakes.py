"""Stand-ins for the Anthropic client, shared by the summarizer and pipeline tests."""

import json
from types import SimpleNamespace
from typing import Any

import anthropic


def llm_answer(**overrides: Any) -> dict[str, Any]:
    answer: dict[str, Any] = {
        "source_type": "university_page",
        "title": "Undergraduate Admissions 2027",
        "organization": "Example University",
        "overview": "Admission to the 2027 undergraduate intake.",
        "deadlines": [
            {
                "label": "Early round",
                "date_iso": "2026-11-01",
                "date_text": "1 November 2026",
                "quote": "Early round | 1 November 2026",
            },
            {
                "label": "Regular round",
                "date_iso": "2027-01-15",
                "date_text": "15 January 2027",
                "quote": "Regular round | 15 January 2027",
            },
        ],
        "key_points": ["Apply through the online portal"],
        "eligibility": ["International students with IELTS 6.5 or TOEFL 90"],
        "costs": [{"item": "Tuition", "amount_text": "14,500 EUR per academic year"}],
        "requirements": ["IELTS 6.5 or TOEFL 90"],
        "useful_links": [{"title": "Online portal", "url": "https://www.example.edu/apply/online"}],
        "warnings": [],
        "confidence": "high",
    }
    answer.update(overrides)
    return answer


def reply(
    body: dict[str, Any] | str | None = None,
    *,
    stop_reason: str = "end_turn",
    content: list | None = None,
) -> SimpleNamespace:
    text = body if isinstance(body, str) else json.dumps(body if body is not None else llm_answer())
    blocks = content if content is not None else [SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(
        content=blocks,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=1200, output_tokens=300),
    )


class FakeMessages:
    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeClient:
    def __init__(self, *outcomes: Any) -> None:
        self.messages = FakeMessages(*outcomes)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def fake_http(status: int) -> SimpleNamespace:
    """What the SDK's exceptions read from a response; no HTTP library needed."""
    return SimpleNamespace(status_code=status, headers={}, request=SimpleNamespace())


def api_error(cls: type[anthropic.APIStatusError], status: int, message: str = "boom"):  # type: ignore[no-untyped-def]
    return cls(message, response=fake_http(status), body=None)
