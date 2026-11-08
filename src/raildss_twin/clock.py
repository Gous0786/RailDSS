"""Controllable simulation clock (spec FR-TWIN-12; issue #20).

Headless (``speed=None``): plain SimPy, as fast as the CPU allows. Otherwise the clock follows the
wall clock at ``speed`` simulated seconds per second (x1 to x60), can be paused and resumed, and its
speed can change while it runs (call from another thread).
"""

from __future__ import annotations

import threading
import time

import simpy
from simpy.rt import RealtimeEnvironment

POLL_S = 0.05


class _ControlledEnvironment(RealtimeEnvironment):
    def __init__(self, clock: Clock) -> None:
        super().__init__(factor=1.0 / clock.speed, strict=False)
        self._clock = clock

    def step(self) -> None:
        while self._clock.paused:
            time.sleep(POLL_S)
            self.sync()
        super().step()


class Clock:
    def __init__(self, speed: float | None, max_speed: float) -> None:
        if speed is not None and not 1.0 <= speed <= max_speed:
            raise ValueError(f"speed must be between 1 and {max_speed:g}, got {speed:g}")
        self.speed = speed or 1.0
        self.max_speed = max_speed
        self.headless = speed is None
        self.paused = False
        self._lock = threading.Lock()
        self._env: _ControlledEnvironment | None = None

    def environment(self) -> simpy.Environment:
        if self.headless:
            return simpy.Environment()
        self._env = _ControlledEnvironment(self)
        return self._env

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        with self._lock:
            self.paused = False
            if self._env is not None:
                self._env.sync()

    def set_speed(self, speed: float) -> None:
        if self.headless:
            raise ValueError("a headless clock has no speed")
        if not 1.0 <= speed <= self.max_speed:
            raise ValueError(f"speed must be between 1 and {self.max_speed:g}, got {speed:g}")
        with self._lock:
            self.speed = speed
            if self._env is not None:
                self._env._factor = 1.0 / speed  # RealtimeEnvironment.factor is read-only
                self._env.sync()
