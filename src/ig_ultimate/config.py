"""User settings (settings.json in the app-data folder). The password is never stored."""

from __future__ import annotations

import copy

from . import paths

DEFAULTS: dict = {
    "username": "",
    "speed": "safe",  # "safe" or "fast" (fast = shorter pauses, higher chance of Instagram limits)
    "keep": {},  # {user_pk: username} never unfollowed / removed by any tool, including the nuke
    "ai": {},  # filled in by the AI auto-reply setup
}


def _file():
    return paths.data_dir() / "settings.json"


def load() -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    cfg.update(paths.read_json(_file(), {}))
    return cfg


def save(cfg: dict) -> None:
    paths.write_json(_file(), cfg)
