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


async def test_wait_remaining_and_on_wait_callback():
    t = FakeTime()
    waits = []
    rl = AsyncRateLimiter(1, 10, t.clock, t.sleep, on_wait=waits.append)
    assert rl.wait_remaining() == 0
    await rl.acquire()
    seen = []

    async def sleeper(s):                                    # mientras duerme, se puede consultar cuánto falta
        seen.append(rl.wait_remaining())
        await t.sleep(s)

    rl._sleep = sleeper
    await rl.acquire()
    assert waits == [pytest.approx(10)]                      # se avisa una vez al empezar a esperar
    assert seen == [pytest.approx(10)]
    assert rl.wait_remaining() == 0                          # al salir ya no hay espera


async def test_on_wait_is_called_once_per_waiting_episode():
    t = FakeTime()
    waits = []
    rl = AsyncRateLimiter(1, 10, t.clock, t.sleep, on_wait=waits.append)
    await rl.acquire()
    await rl.acquire()                                       # espera 1
    await rl.acquire()                                       # espera 2 (otro episodio)
    assert len(waits) == 2
