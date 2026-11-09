"""Time source for the acquisition loop.

All subsystems take time from here, so tests and the simulator can run
deterministically on manual time. System time is anchored once and advanced
with a monotonic counter, so it holds steady if the OS clock is stepped.
"""
import time


class Clock:
    def __init__(self, manual_start=None):
        self.manual = manual_start is not None
        self._value = manual_start
        self._anchor = time.time()
        self._counter = time.perf_counter()

    def now(self) -> float:
        if self.manual:
            return self._value
        return self._anchor + time.perf_counter() - self._counter

    def advance(self, dt: float) -> None:
        if not self.manual:
            raise RuntimeError("Only a manual clock can be advanced.")
        self._value += dt
