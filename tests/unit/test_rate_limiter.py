import pytest

from scanner_opciones.broker.rate_limiter import AsyncRateLimiter


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    async def sleep(self, s):
        self.sleeps.append(s)
        self.now += s


async def test_allows_burst_then_waits():
    t = FakeTime()
    rl = AsyncRateLimiter(2, 10, t.clock, t.sleep)
    await rl.acquire()
    await rl.acquire()
    assert t.sleeps == []
    await rl.acquire()  # tercera: debe esperar hasta que salga la primera de la ventana
    assert t.sleeps == [pytest.approx(10)]


async def test_window_slides():
    t = FakeTime()
    rl = AsyncRateLimiter(1, 5, t.clock, t.sleep)
    await rl.acquire()
    t.now = 6
    await rl.acquire()
    assert t.sleeps == []


def test_invalid_args():
    with pytest.raises(ValueError):
        AsyncRateLimiter(0, 10)
