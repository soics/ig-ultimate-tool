"""Keep the computer from going to sleep by itself while a hunt runs, so it can hunt all night.

The screen can still turn off. Closing a laptop's lid can still put it to sleep: no app can stop
that. When the hunt ends (or the app is closed or crashes), the computer may sleep normally again.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from collections.abc import Callable, Iterator

ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001  # Windows SetThreadExecutionState flags


@contextlib.contextmanager
def keep_awake() -> Iterator[bool]:
    """True while the computer is kept awake, False if this system offers no way to do it."""
    undo = _start()
    try:
        yield undo is not None
    finally:
        if undo:
            with contextlib.suppress(Exception):
                undo()


def _start() -> Callable[[], object] | None:
    try:
        if sys.platform == "win32":
            import ctypes

            kernel = ctypes.windll.kernel32  # type: ignore[attr-defined]
            # Per thread: it ends by itself if this thread or the app goes away.
            if not kernel.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED):
                return None
            return lambda: kernel.SetThreadExecutionState(ES_CONTINUOUS)
        pid = str(os.getpid())
        if sys.platform == "darwin":
            cmd = ["caffeinate", "-i", "-w", pid]  # ends by itself when this app exits
        else:
            # `tail --pid` ends when this app exits, so a crash never leaves the computer sleepless.
            cmd = ["systemd-inhibit", "--what=sleep:idle", "--who=IG Ultimate Tool",
                   "--why=Username finder is hunting", "--mode=block",
                   "tail", f"--pid={pid}", "-f", "/dev/null"]  # fmt: skip
        proc = subprocess.Popen(  # noqa: S603 - fixed command, no user input
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(0.3)  # still running = it worked; exited at once = no sleep blocker here
        if proc.returncode is not None:
            return None
    except (OSError, AttributeError, ValueError):
        return None

    def stop():
        proc.terminate()
        proc.wait(5)

    return stop
