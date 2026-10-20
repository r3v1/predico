from predico50hz.orchestration import network

STEP_S = 10.0


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def sleep(self, s: float) -> None:
        self.now += s

    def __call__(self) -> float:
        return self.now


def test_wait_online_returns_when_reachable_after_retries() -> None:
    answers = iter([False, False, True])
    c = Clock()
    ok = network.wait_online(
        ["h"], 100, interval_s=STEP_S, check=lambda h: next(answers), sleep=c.sleep, clock=c
    )
    assert ok
    assert c.now == 2 * STEP_S


def test_wait_online_gives_up_at_timeout() -> None:
    c = Clock()
    ok = network.wait_online(
        ["h"], 30, interval_s=STEP_S, check=lambda h: False, sleep=c.sleep, clock=c
    )
    assert not ok
    assert c.now <= 30  # noqa: PLR2004
