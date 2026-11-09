"""One configured instrument, and the set of them acquired from together."""
from __future__ import annotations

import math
import time

from ..util import opt
from .driver import empty_status

MAX_BACKOFF_S = 30


class DeviceSession:
    """Owns a driver, tracks link state and reconnects with exponential
    backoff. A failure in one device is contained here."""

    def __init__(self, config: dict, driver):
        self.config = dict(config)
        self.config["autoReconnect"] = bool(config.get("autoReconnect", True))
        self.config["silenceTimeoutS"] = config.get("silenceTimeoutS", 10)
        self.driver = driver
        self.state = "offline"          # offline, online, reconnecting, failed
        self.identity: dict = {}
        self.last_error = ""
        self.last_data_t = math.nan
        self.last_status = empty_status()
        self.reconnect_count = 0
        self.last_read_s = 0.0
        self.max_read_s = 0.0
        self._next_retry_t = 0.0
        self._backoff_s = 1.0
        driver.configure(opt(config, "settings", {}))

    @property
    def id(self) -> str:
        return self.config["id"]

    def open(self, t: float) -> None:
        self._backoff_s = 1.0
        self._attempt_connect(t, first_attempt=True)

    def close(self) -> None:
        if self.state == "online":
            self._safe_disconnect()
        self.state = "offline"
        self.last_status = empty_status()

    def poll(self, t: float) -> list:
        """Advance the link state machine and return any new samples."""
        if self.state == "reconnecting" and t >= self._next_retry_t:
            self._attempt_connect(t, first_attempt=False)
        if self.state != "online":
            return []
        started = time.perf_counter()
        try:
            block = self.driver.read(t)
            self.last_status = self.driver.status()
        except Exception as error:
            self._link_lost(t, str(error))
            return []
        self.last_read_s = time.perf_counter() - started
        self.max_read_s = max(self.max_read_s, self.last_read_s)
        if not self.last_status["connected"]:
            self._link_lost(t, "Driver reports the connection is closed.")
            return []
        if any(b.t.size for b in block):
            self.last_data_t = t
        elif self.config["silenceTimeoutS"] > 0 and t - self.last_data_t > self.config["silenceTimeoutS"]:
            self._link_lost(t, f"No data for {t - self.last_data_t:.1f} s.")
            return []
        return block

    def _attempt_connect(self, t, first_attempt) -> None:
        try:
            self.driver.note_time(t)
            self.driver.connect()
            self.identity = self.driver.identify()
            self.last_status = self.driver.status()
            self.state = "online"
            self.last_error = ""
            self.last_data_t = t
            self._backoff_s = 1.0
            if not first_attempt:
                self.reconnect_count += 1
        except Exception as error:
            self.last_error = str(error)
            self._safe_disconnect()
            self._schedule_retry(t)

    def _link_lost(self, t, reason) -> None:
        self.last_error = reason
        self._safe_disconnect()
        self.last_status = empty_status()
        self._schedule_retry(t)

    def _schedule_retry(self, t) -> None:
        if self.config["autoReconnect"]:
            self.state = "reconnecting"
            self._next_retry_t = t + self._backoff_s
            self._backoff_s = min(self._backoff_s * 2, MAX_BACKOFF_S)
        else:
            self.state = "failed"

    def _safe_disconnect(self) -> None:
        try:
            self.driver.disconnect()
        except Exception as error:
            self.last_error += f" (disconnect: {error})"


class DeviceManager:
    def __init__(self, registry):
        self.registry = registry
        self.sessions: list[DeviceSession] = []

    def add(self, config: dict) -> DeviceSession:
        if self.has(config["id"]):
            raise ValueError(f'Device id "{config["id"]}" already exists.')
        session = DeviceSession(config, self.registry.create(config["driver"]))
        self.sessions.append(session)
        return session

    def remove(self, device_id) -> None:
        session = self.get(device_id)
        session.close()
        self.sessions.remove(session)

    def get(self, device_id) -> DeviceSession:
        for session in self.sessions:
            if session.id == device_id:
                return session
        raise KeyError(f'No device with id "{device_id}".')

    def has(self, device_id) -> bool:
        return any(session.id == device_id for session in self.sessions)

    def open_all(self, t) -> None:
        for session in self.sessions:
            session.open(t)

    def close_all(self) -> None:
        for session in self.sessions:
            session.close()
