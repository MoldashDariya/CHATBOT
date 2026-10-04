import pytest

from bot.urls import extract_urls


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://example.edu/admissions", ["https://example.edu/admissions"]),
        ("посмотри https://example.edu/a, пожалуйста", ["https://example.edu/a"]),
        ("(https://example.edu/a)", ["https://example.edu/a"]),
        ("«https://example.edu/a».", ["https://example.edu/a"]),
        ("HTTP://EXAMPLE.EDU/A", ["HTTP://EXAMPLE.EDU/A"]),
        ("https://a.edu/x https://b.edu/y", ["https://a.edu/x", "https://b.edu/y"]),
        ("https://a.edu/x и снова https://a.edu/x", ["https://a.edu/x"]),
        ("https://youtu.be/abc?t=10&x=1", ["https://youtu.be/abc?t=10&x=1"]),
        ("без ссылок", []),
        ("ftp://example.edu/file", []),
        ("https://", []),
    ],
)
def test_extract_urls(text: str, expected: list[str]) -> None:
    assert extract_urls(text) == expected
