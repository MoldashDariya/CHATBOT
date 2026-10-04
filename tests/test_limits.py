from bot.security.limits import RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_allows_up_to_limit_then_blocks_with_retry_hint() -> None:
    clock = FakeClock()
    limiter = RateLimiter(limit=3, window_s=3600, clock=clock)

    assert all(limiter.acquire(1).allowed for _ in range(3))

    blocked = limiter.acquire(1)
    assert not blocked.allowed
    assert blocked.retry_after_s == 3600


def test_window_slides() -> None:
    clock = FakeClock()
    limiter = RateLimiter(limit=2, window_s=100, clock=clock)

    limiter.acquire(1)  # t=1000
    clock.now += 60
    limiter.acquire(1)  # t=1060
    clock.now += 30  # t=1090: first hit still inside the window
    blocked = limiter.acquire(1)
    assert not blocked.allowed
    assert blocked.retry_after_s == 10  # first hit expires at t=1100

    clock.now += 10  # t=1100
    assert limiter.acquire(1).allowed


def test_users_are_independent() -> None:
    limiter = RateLimiter(limit=1, clock=FakeClock())

    assert limiter.acquire(1).allowed
    assert not limiter.acquire(1).allowed
    assert limiter.acquire(2).allowed


def test_blocked_attempts_do_not_extend_the_block() -> None:
    clock = FakeClock()
    limiter = RateLimiter(limit=1, window_s=100, clock=clock)

    limiter.acquire(1)
    for _ in range(5):
        clock.now += 10
        assert not limiter.acquire(1).allowed

    clock.now += 50  # t=1100, exactly one window after the only recorded hit
    assert limiter.acquire(1).allowed


def test_idle_users_are_evicted_when_many_are_tracked() -> None:
    clock = FakeClock()
    limiter = RateLimiter(limit=1, window_s=100, clock=clock)
    for user_id in range(1100):
        limiter.acquire(user_id)

    clock.now += 101
    limiter.acquire(5000)

    assert len(limiter._hits) == 1
