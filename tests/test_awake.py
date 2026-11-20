"""Keeping the computer awake: the right system call per platform, always undone, never fatal."""

import subprocess
import sys
import types

from ig_ultimate import awake


class FakeProc:
    def __init__(self, cmd, exits_at_once=False):
        self.cmd, self.returncode, self.stopped = cmd, (1 if exits_at_once else None), False

    def wait(self, timeout=None):
        if self.returncode is None and not self.stopped:
            raise subprocess.TimeoutExpired(self.cmd, timeout)
        return self.returncode

    def terminate(self):
        self.stopped, self.returncode = True, -15


def fake_popen(made, exits_at_once=False):
    def popen(cmd, **kw):
        made.append(FakeProc(cmd, exits_at_once))
        return made[-1]

    return popen


def test_linux_blocks_sleep_until_the_hunt_ends_and_ties_it_to_this_app(monkeypatch):
    made = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(subprocess, "Popen", fake_popen(made))
    with awake.keep_awake() as ok:
        assert ok and made[0].cmd[0] == "systemd-inhibit" and "--what=sleep:idle" in made[0].cmd
        assert any(c.startswith("--pid=") for c in made[0].cmd)  # ends if the app crashes
        assert not made[0].stopped
    assert made[0].stopped


def test_mac_uses_caffeinate_tied_to_this_app(monkeypatch):
    made = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "Popen", fake_popen(made))
    with awake.keep_awake() as ok:
        assert ok and made[0].cmd[:3] == ["caffeinate", "-i", "-w"]
    assert made[0].stopped


def test_windows_sets_and_clears_the_execution_state(monkeypatch):
    calls = []
    kernel = types.SimpleNamespace(SetThreadExecutionState=lambda f: calls.append(f) or 1)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(
        sys.modules, "ctypes", types.SimpleNamespace(windll=types.SimpleNamespace(kernel32=kernel))
    )
    with awake.keep_awake() as ok:
        assert ok and calls == [awake.ES_CONTINUOUS | awake.ES_SYSTEM_REQUIRED]
    assert calls[-1] == awake.ES_CONTINUOUS  # back to normal


def test_no_sleep_blocker_is_reported_honestly_and_never_crashes(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    def missing(cmd, **kw):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(subprocess, "Popen", missing)
    with awake.keep_awake() as ok:
        assert ok is False
    made = []
    monkeypatch.setattr(subprocess, "Popen", fake_popen(made, exits_at_once=True))  # e.g. no system bus
    with awake.keep_awake() as ok:
        assert ok is False
