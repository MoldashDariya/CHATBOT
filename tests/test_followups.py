import pytest

from bot.extractors.base import ExtractedContent
from bot.pipeline.detector import SourceFormat
from bot.pipeline.followups import (
    candidate_links,
    combine,
    registrable_domain,
    same_site,
)

PAGE = "https://sc.informatik.rwth-aachen.de/en/studium/master/data-science/"


def content(links: tuple[str, ...], text: str = "", url: str = PAGE) -> ExtractedContent:
    return ExtractedContent(
        text=text, title="T", source_url=url, source_format=SourceFormat.HTML, links=links
    )


# --- which site a page belongs to ---------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("sc.informatik.rwth-aachen.de", "rwth-aachen.de"),
        ("www.rwth-aachen.de", "rwth-aachen.de"),
        ("rwth-aachen.de", "rwth-aachen.de"),
        ("www.ox.ac.uk", "ox.ac.uk"),
        ("admissions.ox.ac.uk", "ox.ac.uk"),
        ("a.b.example.co.uk", "example.co.uk"),
        ("www.mit.edu", "mit.edu"),
        ("localhost", "localhost"),
        ("93.184.216.34", "93.184.216.34"),
        ("WWW.Example.COM.", "example.com"),
    ],
)
def test_registrable_domain(host: str, expected: str) -> None:
    assert registrable_domain(host) == expected


@pytest.mark.parametrize(
    ("other", "result"),
    [
        ("https://www.rwth-aachen.de/cms/x", True),
        ("http://cs.rwth-aachen.de/", True),
        ("https://rwth-aachen.de.evil.example/x", False),
        ("https://evil-rwth-aachen.de/x", False),
        ("https://www.tu-berlin.de/", False),
        ("not a url", False),
    ],
)
def test_same_site(other: str, result: bool) -> None:
    assert same_site(PAGE, other) is result


# --- which links are worth reading --------------------------------------------------------


def test_admission_pages_of_the_same_site_are_chosen_best_first() -> None:
    links = (
        "https://sc.informatik.rwth-aachen.de/en/studium/master/data-science/prerequisites",
        "https://sc.informatik.rwth-aachen.de/en/studium/master/data-science/application-for-admission/",
        "https://www.rwth-aachen.de/cms/root/Studium/Bewerbung-Master/",
    )
    chosen = candidate_links(content(links), limit=5)

    assert chosen[0].endswith("application-for-admission/")  # two admission words in the path
    assert set(chosen) == {links[1], links[2]}  # "prerequisites" is not an admission word


def test_link_text_counts_more_than_the_address() -> None:
    plain = "https://x.edu/page-one"
    labelled = "https://x.edu/page-two"
    text = f"See [How to apply](https://x.edu/page-two) and [Gallery]({plain})."

    assert candidate_links(content((plain, labelled), text, url="https://x.edu/"), limit=2) == [
        labelled
    ]


def test_words_are_matched_by_their_start_not_anywhere() -> None:
    links = (
        "https://x.edu/news/updates",  # contains "dates", does not start with it
        "https://x.edu/candidates",
        "https://x.edu/reentry",  # contains "entry"
        "https://x.edu/gallery",
        "https://x.edu/admissions",
    )
    assert candidate_links(content(links, url="https://x.edu/"), limit=10) == [
        "https://x.edu/admissions"
    ]


@pytest.mark.parametrize(
    "word", ["apply", "applying", "application", "applications", "admission", "deadlines"]
)
def test_inflected_forms_count(word: str) -> None:
    link = f"https://x.edu/{word}"
    assert candidate_links(content((link,), url="https://x.edu/"), limit=3) == [link]


@pytest.mark.parametrize(
    "path",
    [
        "bewerbung",
        "zulassungsvoraussetzungen",
        "fristen",
        "kak-podat-zayavku",
        "поступление",
        "приём-документов",
        "срок-подачи",
    ],
)
def test_other_languages(path: str) -> None:
    link = f"https://x.edu/{path}"
    # Transliterated Russian is not recognised; Cyrillic, German and English words are.
    expected = [] if path == "kak-podat-zayavku" else [link]
    assert candidate_links(content((link,), url="https://x.edu/"), limit=3) == expected


def test_the_page_itself_and_its_aliases_are_never_candidates() -> None:
    page = "https://www.x.edu/admissions/"
    links = (
        "https://www.x.edu/admissions",
        "http://x.edu/admissions/",
        "https://www.x.edu/admissions/#deadlines",
        "https://www.x.edu/admissions/apply",
    )
    assert candidate_links(content(links, url=page), limit=5) == [
        "https://www.x.edu/admissions/apply"
    ]


def test_other_sites_are_never_followed() -> None:
    links = (
        "https://other.org/admission",
        "https://x.edu.evil.example/apply",
        "https://x.edu/apply",
    )
    assert candidate_links(content(links, url="https://x.edu/"), limit=5) == ["https://x.edu/apply"]


@pytest.mark.parametrize(
    "name", ["apply.jpg", "apply.png", "application.zip", "apply.mp4", "apply.xlsx", "apply.doc"]
)
def test_files_that_cannot_be_read_are_skipped(name: str) -> None:
    assert candidate_links(content((f"https://x.edu/{name}",), url="https://x.edu/"), limit=3) == []


@pytest.mark.parametrize("name", ["apply.pdf", "apply.docx", "apply.html", "apply"])
def test_pages_and_documents_that_can_be_read_are_allowed(name: str) -> None:
    link = f"https://x.edu/{name}"
    assert candidate_links(content((link,), url="https://x.edu/"), limit=3) == [link]


def test_the_limit_is_respected_and_zero_disables() -> None:
    links = tuple(f"https://x.edu/apply-{n}" for n in range(6))
    page = content(links, url="https://x.edu/")

    assert len(candidate_links(page, limit=2)) == 2
    assert candidate_links(page, limit=0) == []
    assert candidate_links(page, limit=-1) == []


def test_equal_scores_keep_the_order_of_the_page() -> None:
    links = ("https://x.edu/apply-c", "https://x.edu/apply-a", "https://x.edu/apply-b")
    assert candidate_links(content(links, url="https://x.edu/"), limit=3) == list(links)


def test_no_links_no_candidates() -> None:
    assert candidate_links(content(()), limit=3) == []


# --- putting the texts together -----------------------------------------------------------


def test_linked_pages_follow_the_main_page_under_marker_lines() -> None:
    main = content(("https://x.edu/a",), "Main page text.")
    extra = content(("https://x.edu/b",), "Deadline: 1 May 2027.", url="https://x.edu/apply")

    combined = combine(main, [extra])

    assert (
        combined.text
        == "Main page text.\n\n=== Linked page: https://x.edu/apply ===\nDeadline: 1 May 2027."
    )
    assert combined.links == ("https://x.edu/a", "https://x.edu/b")
    assert combined.meta["followed_pages"] == "1"
    assert combined.source_url == main.source_url and combined.title == main.title


def test_combining_nothing_changes_only_the_count() -> None:
    main = content((), "Text")
    assert combine(main, []).text == "Text"
    assert combine(main, []).meta["followed_pages"] == "0"


def test_duplicate_links_across_pages_are_merged() -> None:
    main = content(("https://x.edu/a", "https://x.edu/b"), "m")
    extra = content(("https://x.edu/b", "https://x.edu/c"), "e", url="https://x.edu/apply")
    assert combine(main, [extra]).links == ("https://x.edu/a", "https://x.edu/b", "https://x.edu/c")


def test_a_better_match_wins_even_if_it_comes_later_on_the_page() -> None:
    weak = "https://x.edu/apply"  # one admission word
    strong = "https://x.edu/admission-application-deadlines"  # three
    middling = "https://x.edu/admission-dates"  # two

    chosen = candidate_links(content((weak, strong, middling), url="https://x.edu/"), limit=3)

    assert chosen == [strong, middling, weak]
