"""Safe-action engine. Every bulk tool (and the nuke) runs through `execute`.

What it guarantees:
- human-like pacing between actions ("safe" or "fast"), plus a longer pause every batch;
- it stops immediately, in plain English, when Instagram pushes back (rate limit, action block,
  security check) instead of hammering the account;
- progress is saved after every action (keyed by Instagram's numeric ids, not usernames), so a
  stopped run continues where it left off, and a finished run starts fresh next time;
- dry runs touch nothing.
Confirmation and previews belong to the UI layer; by the time `execute` runs, the user said yes.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from instagrapi import exceptions as ig_errors

from . import paths

SPEEDS = {
    "safe": {"delay": (3.0, 15.0), "pause_every": 10, "pause": 60.0},
    "fast": {"delay": (1.0, 4.0), "pause_every": 30, "pause": 30.0},
}
MAX_FAILS_IN_A_ROW = 5


@dataclass(frozen=True)
class Item:
    id: str  # Instagram's stable numeric id (user pk, thread id, media id...)
    label: str  # what the user sees, e.g. "@alex" or "post from 2021-03-02"


@dataclass
class Plan:
    job: str  # stable key for the progress file, e.g. "unfollow"
    title: str  # "Unfollow"
    verb: str  # past tense for progress lines, e.g. "unfollowed"
    items: list[Item]
    action: Callable[[Item], object]
    experimental: bool = False  # uses an Instagram call we could not fully verify yet


class Stopped(Exception):
    """The run stopped on purpose. str(exc) is a plain-English explanation for the user."""


class UI(Protocol):
    def step(self, n: int, total: int, item: Item, ok: bool, note: str = "") -> None: ...
    def say(self, text: str) -> None: ...


def explain(exc: BaseException) -> str | None:
    """Plain-English reason when Instagram is pushing back; None for ordinary per-item errors."""
    if isinstance(exc, ig_errors.FeedbackRequired):
        return (
            "Instagram showed an 'Action Blocked' warning, so we stopped to protect your account. "
            "Wait 24-48 hours, then run it again: it continues where it left off."
        )
    if isinstance(exc, ig_errors.PleaseWaitFewMinutes | ig_errors.RateLimitError):
        return (
            "Instagram asked us to slow down, so we stopped safely. "
            "Wait a few hours, then run it again: it continues where it left off."
        )
    if isinstance(exc, ig_errors.ChallengeRequired | ig_errors.LoginRequired):
        return (
            "Instagram wants to confirm it's you. Open the Instagram app on your phone, "
            "tap 'This was me' if asked, wait 15 minutes, then log in again."
        )
    return None


def journal_path(job: str):
    return paths.data_dir() / "runs" / f"{job}.json"


def unfinished(job: str) -> int:
    """How many items an interrupted earlier run of this job already finished (0 = none)."""
    return len(paths.read_json(journal_path(job), {"done": []})["done"])


def forget(job: str) -> None:
    journal_path(job).unlink(missing_ok=True)


def save_list(plan: Plan):
    """Write the full target list to a text file so the user can read it before saying yes."""
    path = paths.data_dir() / "lists" / f"{plan.job}.txt"
    paths.write_text(path, "\n".join(i.label for i in plan.items) + "\n")
    return path


def _short(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return text[:120]


def execute(
    plan: Plan,
    ui: UI,
    speed: str = "safe",
    dry_run: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    cancelled: Callable[[], bool] = lambda: False,
) -> tuple[int, int]:
    """Run a confirmed plan. Returns (done, failed). Raises Stopped when it stops early."""
    jpath = journal_path(plan.job)
    done = set(paths.read_json(jpath, {"done": []})["done"])
    todo = [i for i in plan.items if i.id not in done]
    pace = SPEEDS.get(speed, SPEEDS["safe"])
    ok = failed = streak = 0

    def stop(reason: str) -> Stopped:
        return Stopped(f"{reason} ({ok} {plan.verb} this run.)")

    for n, item in enumerate(todo, 1):
        if cancelled():
            raise stop("Stopped by you. Run it again to continue.")
        if dry_run:
            ui.step(n, len(todo), item, True, "dry run, nothing changed")
            ok += 1
            continue
        try:
            plan.action(item)
        except KeyboardInterrupt:
            raise stop("Stopped by you. Run it again to continue.") from None
        except Exception as exc:  # noqa: BLE001 - one bad item must not end the run
            why = explain(exc)
            if why:
                raise stop(why) from exc
            failed += 1
            streak += 1
            ui.step(n, len(todo), item, False, _short(exc))
            if streak >= MAX_FAILS_IN_A_ROW:
                raise stop(f"{MAX_FAILS_IN_A_ROW} errors in a row, so we stopped to be safe.") from exc
            continue
        streak = 0
        ok += 1
        done.add(item.id)
        paths.write_json(jpath, {"done": sorted(done)})
        ui.step(n, len(todo), item, True)
        if n == len(todo):
            break
        try:
            if pace["pause_every"] and ok % pace["pause_every"] == 0:
                ui.say(f"Short break ({int(pace['pause'])}s) so Instagram sees a normal pace...")
                sleep(pace["pause"])
            else:
                sleep(random.uniform(*pace["delay"]))
        except KeyboardInterrupt:
            raise stop("Stopped by you. Run it again to continue.") from None

    if not dry_run:
        forget(plan.job)  # finished: the next run of this job starts from scratch
    return ok, failed
