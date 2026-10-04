import pytest

from bot.urls import cache_key

PAGE = "https://www.example.edu/admissions/apply"


@pytest.mark.parametrize(
    "variant",
    [
        PAGE,
        PAGE + "#deadlines",
        PAGE + "?utm_source=telegram&utm_medium=social",
        PAGE + "?UTM_Campaign=x&fbclid=abc#top",
        "HTTPS://WWW.EXAMPLE.EDU/admissions/apply",
        "https://www.example.edu:443/admissions/apply",
        "https://www.example.edu./admissions/apply",
        "  https://www.example.edu/admissions/apply  ",
        PAGE + "?gclid=1&msclkid=2&yclid=3&_ga=4&mc_cid=5&mc_eid=6&igshid=7&dclid=8&_gl=9",
    ],
)
def test_links_that_differ_only_in_noise_are_the_same_page(variant: str) -> None:
    assert cache_key(variant) == cache_key(PAGE) == PAGE


@pytest.mark.parametrize(
    "other",
    [
        "https://www.example.edu/admissions/apply/",  # a trailing slash can be another page
        "https://www.example.edu/admissions/Apply",  # paths are case sensitive
        "https://example.edu/admissions/apply",  # the www. host may be another site
        "http://www.example.edu/admissions/apply",
        "https://www.example.edu:8443/admissions/apply",
        "https://www.example.edu/admissions/apply?programme=msc",
        "https://www.example.edu/admissions/apply?page=2",
    ],
)
def test_pages_that_may_differ_are_never_merged(other: str) -> None:
    assert cache_key(other) != cache_key(PAGE)


def test_a_real_query_parameter_survives_next_to_tracking_ones() -> None:
    key = cache_key("https://x.edu/list?utm_source=a&programme=msc&fbclid=z&lang=en")
    assert key == "https://x.edu/list?programme=msc&lang=en"


def test_the_order_of_real_parameters_is_kept() -> None:
    assert cache_key("https://x.edu/a?b=1&a=2") != cache_key("https://x.edu/a?a=2&b=1")


def test_an_empty_path_is_the_root() -> None:
    assert cache_key("https://x.edu") == cache_key("https://x.edu/") == "https://x.edu/"


@pytest.mark.parametrize(
    "link",
    [
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30s&list=PL1",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
    ],
)
def test_a_video_is_its_id_however_it_was_linked(link: str) -> None:
    assert cache_key(link) == "youtube:dQw4w9WgXcQ"


def test_a_different_video_is_a_different_key() -> None:
    assert cache_key("https://youtu.be/AAAAAAAAAAA") != cache_key("https://youtu.be/BBBBBBBBBBB")


@pytest.mark.parametrize(
    "link",
    [
        "",
        "not a url",
        "ftp://x.edu/a",
        "javascript:alert(1)",
        "https:///path",
        "https://user:pass@x.edu/a",
        "https://user@x.edu/a",
        "https://x.edu:99999/a",
        "https://x.edu:abc/a",
    ],
)
def test_links_the_pipeline_would_refuse_get_no_key(link: str) -> None:
    assert cache_key(link) is None


def test_ipv6_hosts_keep_their_brackets() -> None:
    assert cache_key("http://[2001:db8::1]/a") == "http://[2001:db8::1]/a"
    assert cache_key("http://[2001:db8::1]:8080/a") == "http://[2001:db8::1]:8080/a"
