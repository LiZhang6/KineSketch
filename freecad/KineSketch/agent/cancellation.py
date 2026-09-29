# SPDX-License-Identifier: LGPL-2.1-or-later

"""Thread-safe cooperative cancellation with nonblocking transport interrupts."""

from contextlib import contextmanager
from threading import Event, Lock


class RequestCancelled(RuntimeError):
    pass


class RequestCancellation:
    def __init__(self):
        self._event = Event()
        self._lock = Lock()
        self._callbacks = set()

    @property
    def cancelled(self):
        return self._event.is_set()

    def check(self):
        if self.cancelled:
            raise RequestCancelled("Conversation stopped")

    def cancel(self):
        with self._lock:
            self._event.set()
            callbacks = tuple(self._callbacks)
        for callback in callbacks:
            self._interrupt(callback)

    @staticmethod
    def _interrupt(callback):
        try:
            callback()
        except OSError:
            pass

    @contextmanager
    def bind(self, callback):
        with self._lock:
            self._callbacks.add(callback)
            cancelled = self.cancelled
        try:
            if cancelled:
                self._interrupt(callback)
            self.check()
            yield
        finally:
            with self._lock:
                self._callbacks.discard(callback)
