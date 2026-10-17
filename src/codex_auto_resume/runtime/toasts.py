"""Notifications raised from the watcher's own thread, one at a time.

A notification is shown by the icon's thread where there is one and by Windows' own toast where
there is not, and either way it must not be raised from inside the tick that decided to raise
it. So they queue here, and a thread of their own delivers them.
"""
from __future__ import annotations

import logging
import queue
import threading

from .. import notifier


class Toasts:
    """Notifications off the tick path.

    Showing one runs PowerShell and can take many seconds. The engine only ever puts an
    event on this queue; a daemon thread shows them one at a time. A full queue drops
    the event and says so: a notification must never delay or decide a recovery.
    """

    def __init__(self, show, logger, *, mirror=None):
        self._show, self._logger = show, logger
        # The Telegram channel receives an event when it is produced, not after
        # Windows finishes presenting the toast. It has its own bounded sender queue.
        self._mirror = mirror
        self._queue = queue.Queue(maxsize=64)
        self._thread = None
        self._lock = threading.Lock()

    def __call__(self, event, detail):
        snapshot = dict(detail)
        if self._mirror is not None:
            try:
                self._mirror(event, snapshot)
            except Exception:
                self._logger.info("telegram: event dispatch failed (%s)", event)
        try:
            self._queue.put_nowait((event, snapshot))
        except queue.Full:
            self._logger.info("notification queue full; %s dropped", event)
            return False
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="toasts", daemon=True)
                self._thread.start()
        return True

    def _run(self):
        while True:
            try:
                event, detail = self._queue.get(timeout=30)
            except queue.Empty:
                return
            try:
                self._show(event, detail)
            except Exception:
                self._logger.info("notification failed (%s)", event)
