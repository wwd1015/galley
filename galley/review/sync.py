"""Near-real-time sync: poll GitHub while the window is in use, back off when idle."""

from __future__ import annotations

import threading
from collections.abc import Callable

ACTIVE_SECONDS = 10.0
IDLE_SECONDS = 60.0


class Poller:
    """Calls ``refresh`` every 10 seconds, or every 60 while the window is idle.

    Conditional requests (ETags) are the caller's job; a 304 does not count
    against GitHub's rate limit, so polling this often is cheap.
    """

    def __init__(
        self,
        refresh: Callable[[], object],
        *,
        active: float = ACTIVE_SECONDS,
        idle: float = IDLE_SECONDS,
    ) -> None:
        self.refresh = refresh
        self.active = active
        self.idle_interval = idle
        self.idle = False
        self.polls = 0
        self._wake = threading.Event()
        self._stopped = False
        self._thread = threading.Thread(target=self._loop, name="galley-sync", daemon=True)

    @property
    def interval(self) -> float:
        return self.idle_interval if self.idle else self.active

    def set_idle(self, idle: bool) -> None:
        """Called when the window gains or loses the user's attention."""
        if idle == self.idle:
            return
        self.idle = idle
        if not idle:
            self._wake.set()  # coming back: poll now rather than in up to a minute

    def poke(self) -> None:
        """Poll as soon as possible."""
        self._wake.set()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stopped = True
        self._wake.set()

    def _loop(self) -> None:
        while not self._stopped:
            self._wake.wait(self.interval)
            self._wake.clear()
            if self._stopped:
                return
            try:
                self.refresh()
            finally:
                self.polls += 1
