"""Username hunter: checks the best-ranked names one at a time, records an honest result for each,
and (only if you switched it on for this session) claims a free one for your account, then stops.

Pipeline: names.py generates, validates and scores -> huntdb.Store dedupes, queues and remembers ->
a Verifier asks Instagram -> the result is written down before anything else happens.
When the lookup finds no account, Instagram's own username check (the one behind the Edit profile
screen) is asked straight away: held names are skipped for good without spending a claim, and a
name it calls free is claimed that same moment. No clear answer from that check = no claim: the
name is asked about once more after 30 minutes, and skipped for good (as held) if still unclear.

What a result means:
- "taken": an account has the name. Never looked at again.
- "possibly available": no account was found and Instagram's username check gave no clear answer.
  That is NOT proof: Instagram keeps the names of banned, deactivated and recently renamed accounts.
- "verified available": Instagram's username check says it is free. Still only a change that shows
  on your account proves it.
- "held": no account, but Instagram's check or the change itself said no. Never tried again.
- "unknown" / "rate limited": no usable answer. The name is tried again later; it never counts as checked.

Platform honesty: Meta's official Instagram API can read a username but has no way to check or change
one. The only verifier here uses instagrapi, an unofficial client of Instagram's private app API (the
same one every tool in this app uses). Automatic claiming therefore runs through that unofficial call,
is OFF unless you authorize it for one account and one session, and the default is to find names and
let you claim them yourself in the Instagram app.

Pace: one account, ~240 lookups an hour, at most one claim every 3 minutes, and it backs off when
Instagram says slow down. It never switches accounts or hides what it is to get around limits.
"""

from __future__ import annotations

import itertools
import random
import re
import threading
import time
import weakref
from collections.abc import Callable
from dataclasses import dataclass

from instagrapi import exceptions as ig_errors

from . import awake, huntdb, names
from .engine import explain

EXPLAIN = (
    "Hunts for a free, good-looking username: the best-ranked names first, one at a time.\n"
    "Pick a style (short words, rare words, other languages, invented names...). Every name gets a "
    "quality score you can open, and you can star or hide names to teach it your taste.\n"
    "Be realistic: almost every short name is taken or held by Instagram, so a hunt can run for days. "
    "It checks about 240 names an hour (480 on Fast, in Settings) and pauses by itself when Instagram "
    "says slow down. It keeps going as long as this page is open, all night if you like, and keeps the "
    "computer from sleeping by itself. Close the page and it stops within seconds, keeping its progress.\n"
    "When no account has a name, it asks Instagram's own username check: held names (kept from banned "
    "or deactivated accounts) are skipped for good, and a name Instagram calls free is claimed that same "
    "second if you switched automatic claiming on. If that check doesn't answer, nothing is claimed: "
    "the name is asked about once more after 30 minutes, then skipped for good. Taken names are skipped "
    "for good too. Otherwise you claim it yourself in the Instagram "
    "app. Automatic claiming is unofficial (Instagram has no official way to do it), must be switched "
    "on each time, and RENAMES your account straight away: your old username becomes free for anyone."
)
LOOKUP_GAP = (10.0, 20.0)  # seconds between lookups: ~240 an hour; a "slow down" still backs off
FAST_GAP = (5.0, 10.0)  # Settings > Speed: Fast. ~480 an hour: more "slow down" pauses are likely
# Live 2026-11-20: ~20 attempts 3 min apart went through; 1 min apart hit "Please wait a few minutes" after 4.
CLAIM_GAP = 180.0  # at most one username change attempt every 3 minutes (only after a refused one)
CHECK_OFF = 30 * 60.0  # the username check said "slow down": skip it 30 min; unsure names wait that long
CLAIM_REFUSED = 7 * 86400.0  # a change refused for a reason we don't recognise: not tried again for a week
BACKOFF = (15 * 60.0, 2 * 3600.0)  # "too many requests": 15 min, doubling, max 2 h
OFFLINE = (60.0, 15 * 60.0)  # no connection: 1 min, doubling, max 15 min, then the same name again
IDLE = 300.0  # every name has a fresh result: look again in 5 minutes
SLOW_DOWN = (ig_errors.PleaseWaitFewMinutes, ig_errors.RateLimitError, ig_errors.ClientThrottledError)
NO_ANSWER = (
    ig_errors.ClientConnectionError,
    ig_errors.ClientRequestTimeout,
    ig_errors.ClientIncompleteReadError,
    ig_errors.ClientJSONDecodeError,
)
STOP_NOW = (
    ig_errors.ChallengeError,
    ig_errors.LoginRequired,
    ig_errors.ClientLoginRequired,
    ig_errors.ReloginAttemptExceeded,
    ig_errors.FeedbackRequired,
    ig_errors.AccountSuspended,
    ig_errors.ClientUnauthorizedError,
    ig_errors.ClientForbiddenError,
)
HELD_WORDS = (
    "isn't available",
    "is not available",
    "not available",
    "already taken",
    "is taken",
    "another account",
    "already exists",  # "A user with that username already exists." while the lookup finds nobody
)
INVALID_WORDS = ("can only use", "not allowed", "invalid", "can't contain", "cannot contain", "only numbers")
LIMIT_WORDS = ("too many", "try again later", "14 days", "change your username", "limit")
SECRET = re.compile(
    r"(sessionid|csrftoken|ds_user_id|authorization|bearer|password|token|ig-u-[\w-]+)(\W{1,4})[^\s,;\"'}&]+",
    re.I,
)

TOKEN = re.compile(r"(bearer\s+|igt:\d+:)[^\s,;\"'}&]+", re.I)


def redact(text) -> str:
    """No session cookies, tokens or passwords in logs, the dashboard or exports."""
    return SECRET.sub(r"\1\2[hidden]", TOKEN.sub(r"\1[hidden]", str(text)))


def clean(text) -> str:
    """Error text for logs: redacted, one line, short."""
    return redact(text).replace("\n", " ")[:160]


@dataclass
class Answer:
    status: str  # a huntdb.STATUSES key, or "accepted" (claim went through; must still be confirmed)
    detail: str = ""
    retry: bool = False  # no answer at all (offline): don't record anything, try the same name again
    stop: str = ""  # the account needs attention: end the hunt with this message


_LOCKS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_LOCKS_GUARD = threading.Lock()


def client_lock(cl) -> threading.RLock:
    """instagrapi keeps the last reply on the client (last_json), so two threads using one client at
    once can read each other's answers. Every hunter call on a client goes through its one lock."""
    with _LOCKS_GUARD:
        if cl not in _LOCKS:
            _LOCKS[cl] = threading.RLock()
        return _LOCKS[cl]


def _stop_answer(exc) -> Answer:
    return Answer("unknown", clean(exc), stop=explain(exc) or f"Instagram stopped us: {clean(exc)}")


class InstagrapiVerifier:
    """The only verifier today. Replace it with one built on a documented, authorized integration if
    Meta ever offers username checks; the Hunter only needs these four methods and flags."""

    kind = "instagrapi"
    official = False
    can_lookup = True
    can_claim = True  # through the same unofficial profile-edit call the app's other tools use
    summary = (
        "instagrapi: an unofficial client of Instagram's private app API, signed in as you. "
        "Not a Meta-authorized integration. Meta's official API has no way to check or change a username."
    )

    def __init__(self, cl):
        self.cl = cl
        self.lock = client_lock(cl)
        self.check_off_until = 0.0  # the username check said "slow down": skipped until then

    def account(self) -> tuple[str, str]:
        """(numeric id, username) of the account this session is signed in to, read from Instagram."""
        with self.lock:
            a = self.cl.account_info()
        return str(a.pk), str(a.username).lower()

    def session_id(self) -> str:
        """The signed-in account id from the session itself (no request)."""
        return str(getattr(self.cl, "user_id", "") or "")

    def lookup(self, name: str) -> Answer:
        with self.lock:
            return self._lookup(name)

    def claim(self, name: str) -> Answer:
        with self.lock:
            return self._claim(name)

    def precheck(self, name: str) -> Answer | None:
        """Instagram's own username check, for a name the lookup found no account for. It tells a
        held name from a free one without spending a username change. None = no clear answer: the
        Hunter claims nothing and asks again later."""
        if time.monotonic() < self.check_off_until:
            return None
        with self.lock:
            try:
                r = self.cl.check_username(name)
            except SLOW_DOWN:
                # Live 2026-11-20: this check got a 429 while lookups were fine. Don't stall the hunt:
                # leave the check alone for a while; names found meanwhile wait for it.
                self.check_off_until = time.monotonic() + CHECK_OFF
                return None
            except STOP_NOW as exc:
                return _stop_answer(exc)
            except Exception:  # noqa: BLE001 - unclear: no answer, never guess "held"
                return None
        if not isinstance(r, dict) or str(r.get("username", name)).lower() != name:
            return None
        if r.get("available") is True:
            return Answer("verified_available", "Instagram's username check says it's free.")
        if r.get("available") is False:
            why = clean(r.get("error") or r.get("error_type") or "not available")
            if any(w in why.lower() for w in INVALID_WORDS):
                return Answer("invalid", why)
            return Answer("held", f"Instagram's username check says no: {why}")
        return None

    def _lookup(self, name: str) -> Answer:
        try:
            user = self.cl.user_info_by_username_v1(name)
        except ig_errors.UserNotFound:
            return Answer("possibly_available", "No account has this name (not proof it's free).")
        except SLOW_DOWN as exc:
            return Answer("rate_limited", clean(exc))
        except NO_ANSWER as exc:
            return Answer("unknown", f"No answer from Instagram: {clean(exc)}", retry=True)
        except STOP_NOW as exc:
            return _stop_answer(exc)
        except Exception as exc:  # noqa: BLE001 - an odd reply is "unknown", never "checked"
            return Answer("unknown", f"Unexpected reply: {clean(exc)}")
        got = str(getattr(user, "username", "") or "").lower()
        if got and got != name:
            return Answer("unknown", f"Instagram answered about a different account (@{got}).")
        return Answer("taken")

    def _claim(self, name: str) -> Answer:
        try:
            account = self.cl.account_edit(username=name)
        except SLOW_DOWN as exc:
            return Answer("rate_limited", clean(exc))
        except NO_ANSWER as exc:
            return Answer("unknown", f"No answer to the change: {clean(exc)}", retry=True)
        except STOP_NOW as exc:
            return _stop_answer(exc)
        except ig_errors.ClientError as exc:
            msg = f"{exc} {getattr(exc, 'message', '')}".lower()
            if any(w in msg for w in HELD_WORDS):
                return Answer(
                    "held", "Instagram refused it: usually kept from a banned or deactivated account."
                )
            if any(w in msg for w in INVALID_WORDS):
                return Answer("invalid", clean(exc))
            if any(w in msg for w in LIMIT_WORDS):
                return Answer(
                    "unknown",
                    clean(exc),
                    stop="Instagram won't let this account change its username right now (it limits "
                    "changes, often to a few in 14 days). Try again in a few days.",
                )
            return Answer("unknown", f"Instagram refused the change without saying why: {clean(exc)}")
        except Exception as exc:  # noqa: BLE001 - outcome unclear: the Hunter checks the account itself
            return Answer("unknown", f"Unexpected reply: {clean(exc)}")
        got = str(getattr(account, "username", "") or "").lower()
        return Answer("accepted", "" if got == name else f"Instagram replied with @{got}.")


class Presence:
    """Is the dashboard still open? The page's heartbeat keeps this alive. After `timeout` seconds of
    silence it is over for good: a heartbeat arriving later (say, after the laptop slept) does not bring
    the hunt back; you press Start again. Wall-clock time, so a sleeping laptop counts as silence."""

    GRACE = 5.0  # after a tab says goodbye: long enough for a page reload to check back in

    def __init__(self, timeout: float = 30.0, clock: Callable[[], float] = time.time):
        self.timeout, self.clock = max(5.0, float(timeout)), clock
        self.lock = threading.Lock()
        self.last = clock()
        self.tabs: dict[str, float] = {}
        self.expired = False

    def beat(self, tab: str = "") -> bool:
        with self.lock:
            if self._check():
                self.last = self.tabs[str(tab)[:40]] = self.clock()
            return not self.expired

    def bye(self, tab: str = "") -> None:
        with self.lock:
            self.tabs.pop(str(tab)[:40], None)
            if not self._open_tabs():
                self.last = min(self.last, self.clock() - self.timeout + self.GRACE)

    def alive(self) -> bool:
        with self.lock:
            return self._check()

    def _check(self) -> bool:
        if not self.expired and self.clock() - self.last > self.timeout:
            self.expired = True
        return not self.expired

    def _open_tabs(self) -> int:
        now = self.clock()
        return sum(now - t <= self.timeout for t in self.tabs.values())

    def info(self) -> dict:
        with self.lock:
            alive = self._check()
            return {"alive": alive, "tabs": self._open_tabs() if alive else 0, "timeout": self.timeout,
                    "quiet_for": round(max(0.0, self.clock() - self.last), 1)}  # fmt: skip


class Hunter:
    """One hunt on one account. run() blocks (start it in a thread); pause/resume/stop are safe from any
    thread. It never starts a lookup or a claim once stopped, paused or the dashboard is gone; an
    operation already sent finishes and its outcome is written down."""

    def __init__(
        self,
        verifier,
        store,
        account_id: str,
        me: str,
        *,
        log: Callable[[str], None],
        preset: str = "og",
        min_len: int | None = None,
        max_len: int | None = None,
        langs=(),
        auto_claim: dict | None = None,  # {"account_id", "username"} authorized for THIS session only
        alive: Callable[[], bool] = lambda: True,
        sleep: Callable[[float], object] | None = None,
        clock: Callable[[], float] = time.monotonic,
        speed: str = "safe",
    ):
        label, cats, _profile, lo, hi = names.PRESETS.get(preset, names.PRESETS["og"])
        self.v, self.store, self.account_id, self.me = verifier, store, str(account_id), me.lower()
        self.preset, self.cats, self.langs = (
            preset if preset in names.PRESETS else "og",
            cats,
            tuple(langs),
        )
        self.min_len, self.max_len = int(min_len or lo), int(max_len or hi)
        self.auto = auto_claim
        if self.auto and (str(self.auto.get("account_id")) != self.account_id or not verifier.can_claim):
            raise ValueError("Automatic claiming was authorized for a different account.")
        self._log, self.alive, self.now = log, alive, clock
        self.stop_event, self.paused = threading.Event(), threading.Event()
        self.sleep = sleep or self.stop_event.wait
        self.state, self.message, self.won = "ready", "", ""
        self.checks, self.current, self.last = 0, "", {}
        self.wait_until = 0.0
        self.last_claim = -CLAIM_GAP
        self.speed = "fast" if speed == "fast" else "safe"
        self.gap = FAST_GAP if self.speed == "fast" else LOOKUP_GAP

    # -------------------------------------------------------------- controls
    def pause(self):
        self.paused.set()

    def resume(self):
        self.paused.clear()

    def stop(self):
        self.stop_event.set()

    def log(self, text: str) -> None:
        self._log(redact(text))

    def info(self) -> dict:
        return {
            "state": "paused" if self.state == "running" and self.paused.is_set() else self.state,
            "message": self.message,
            "preset": self.preset,
            "label": names.PRESETS[self.preset][0],
            "min_len": self.min_len,
            "max_len": self.max_len,
            "langs": list(self.langs),
            "auto_claim": bool(self.auto),
            "checks": self.checks,
            "current": self.current,
            "last": self.last,
            "waiting_for": round(max(0.0, self.wait_until - self.now())),
            "won": self.won,
            "speed": self.speed,
            "per_hour": round(3600 / (sum(self.gap) / 2)),
        }

    # -------------------------------------------------------------- waiting
    def _go(self) -> bool:
        """True when the next network action may start. Paused = idle here, no requests at all."""
        while True:
            if self.stop_event.is_set():
                return False
            if not self.alive():
                self.state = "expired"
                return False
            if not self.paused.is_set():
                return True
            self.sleep(1.0)

    def _pause(self, seconds: float) -> bool:
        """Wait in short steps so Stop and a closed dashboard end it within about a second."""
        self.wait_until = self.now() + seconds
        while (left := self.wait_until - self.now()) > 0:
            if self.stop_event.is_set() or not self.alive():
                return False
            self.sleep(min(1.0, left))
        return True

    # -------------------------------------------------------------- the hunt
    def run(self) -> str:
        """Hunts until a claim is confirmed (returns the name), or it is stopped, the dashboard goes
        away, or Instagram needs attention (returns "")."""
        self.state = "running"
        try:
            with self.store.hunting(), awake.keep_awake() as awake_ok:
                self.log(
                    "This computer won't go to sleep by itself while it hunts (the screen can still turn "
                    "off). Keep a laptop's lid open and plugged in."
                    if awake_ok
                    else "This computer may go to sleep by itself, and that stops the hunt. To hunt all "
                    "night, set it to never sleep."
                )
                return self._run()
        except huntdb.HuntBusy as exc:
            self.state, self.message = "blocked", str(exc)
            return ""
        except Exception as exc:  # noqa: BLE001 - shown in the dashboard; progress is already saved
            self.state, self.message = "error", f"The hunt stopped on an error: {clean(exc)}"
            self.log(self.message)
            return ""
        finally:
            if self.state == "running":
                self.state = "expired" if not self.alive() else "stopped"
            self.current = ""
            self.log(
                {
                    "won": f"Done: your account is now @{self.won}.",
                    "expired": "The dashboard closed, so the hunt stopped. "
                    "Progress is saved: press Start to go on.",
                    "stopped": "Stopped. Progress is saved: press Start to go on.",
                }.get(self.state, self.message or "Stopped.")
            )

    def _run(self) -> str:
        if won := self._recover():
            return won
        if any(
            self.store.get(f"ingested:{c}") != huntdb.GEN_VERSION
            for c in self.cats
            if c not in huntdb.OPEN and c != "custom"
        ):
            self.log("Getting the name list ready. Only the first time: it can take up to a minute.")
        self.store.ensure(self.cats)
        mode = "claims automatically (unofficial)" if self.auto else "finds names for you to claim"
        self.log(
            f"Hunting: {names.PRESETS[self.preset][0]}, {self.min_len}-{self.max_len} characters. It {mode}."
        )
        return self._loop()

    def _loop(self) -> str:
        lanes = itertools.cycle(self.cats)  # balanced = take turns between the styles
        backoff, offline = BACKOFF[0], OFFLINE[0]
        while self._go():
            name = self._next(lanes)
            if name is None:
                self.message = "Every name has a fresh result. Looking again in 5 minutes."
                self._pause(IDLE)
                continue
            self.current, self.message = name, ""
            ans = self.v.lookup(name)
            if ans.status == "possibly_available" and self._go() and (pre := self.v.precheck(name)):
                ans = pre  # Instagram's own check: free, held, or "slow down"
            self.checks += 1
            if ans.stop:
                return self._blocked(ans.stop)
            if ans.retry:  # offline: nothing learned about this name, so nothing is recorded
                self.log(f"No connection to Instagram. Trying @{name} again in {int(offline)} s.")
                self._pause(offline)
                offline = min(offline * 2, OFFLINE[1])
                continue
            offline = OFFLINE[0]
            if ans.status == "rate_limited":
                self.store.mark(name, "rate_limited", ans.detail)
                self.log(
                    f"Instagram said slow down. Pausing {int(backoff // 60)} minutes, then @{name} again."
                )
                self._pause(backoff)
                backoff = min(backoff * 2, BACKOFF[1])
                continue
            backoff = BACKOFF[0]
            # Only a name Instagram's own check calls free is claimed. Live 2026-11-20: with the check
            # throttled, 13 of 16 blind claims hit held names, each a refused change on the account.
            claim = ans.status == "verified_available" and self.auto and not names.blocked(name)
            # No clear answer from the check: ask it once more in 30 min; still unclear = treat as held,
            # skipped for good. Never spend a claim on a maybe.
            unsure = ans.status == "possibly_available" and bool(self.auto)
            again = unsure and (self.store.row(name) or {}).get("status") != "possibly_available"
            if unsure and not again:
                ans = Answer("held", "Instagram's username check gave no answer twice: skipped for good.")
                unsure = False
            # Recorded before anything else; a claim we then don't get to make is retried first next time.
            self.store.mark(name, ans.status, ans.detail, soon=claim, wait=CHECK_OFF if again else None)
            self.last = {"name": name, "status": ans.status}
            if claim:
                if self._claim(name):
                    return name
                if self.state != "running":
                    return ""
            elif unsure:
                self.log(
                    f"@{name}: no account has it, but Instagram's username check gave no answer, so it "
                    "may be held. No claim spent; asking once more in 30 minutes. You can try it in the app."
                )
            elif ans.status == "verified_available":
                self.log(f"@{name}: FREE, Instagram's own check says so. Claim it now in the Instagram app.")
            elif ans.status == "possibly_available":
                self.log(f"@{name}: no account has it. Claim it in the Instagram app (see Results).")
            elif ans.status == "held":
                self.log(f"@{name}: held by Instagram (or its check never answered). Skipped for good.")
            self._pause(random.uniform(*self.gap))
        return ""

    def _next(self, lanes) -> str | None:
        staged = self.preset in names.STAGED  # in order: a later style only once the earlier ones are done
        for i in range(len(self.cats)):
            cat = self.cats[i] if staged else next(lanes)
            if cat in huntdb.OPEN:
                self.store.top_up(cat)
            name = self.store.pick([cat], self.min_len, self.max_len, self.langs, skip=[self.me])
            if name:
                return name
        return None

    def _blocked(self, why: str) -> str:
        self.state, self.message = "blocked", why
        return ""

    def _confirm(self) -> tuple[str, str] | None:
        try:
            return self.v.account()
        except Exception:  # noqa: BLE001 - "couldn't confirm" is handled by the caller
            return None

    def _claim(self, name: str) -> bool:
        """Claim one name for the authorized account. Written down before it is sent, confirmed by
        reading the account back afterwards: a 200 reply on its own is never taken as success."""
        gap = self.last_claim + CLAIM_GAP - self.now()
        if gap > 0 and not self._pause(gap):
            return False  # stopped while waiting: the name keeps its old status and comes up first again
        if not self._go():
            return False
        if (
            self.v.session_id() not in ("", self.account_id)
            or str(self.auto["account_id"]) != self.account_id
        ):
            self._blocked(
                "You are signed in to a different account than the one you authorized. Nothing changed."
            )
            return False
        self.store.mark(name, "claiming", f"claim sent for account {self.account_id}")
        if self.stop_event.is_set() or not self.alive():  # Stop/close arrived while that was written
            self.store.mark(name, "possibly_available", "claim not sent: the hunt stopped", soon=True)
            return False
        self.last_claim = self.now()
        self.log(f"@{name}: looks free. Claiming it now...")
        ans = self.v.claim(name)
        seen = self._confirm()
        if seen and seen[0] == self.account_id and seen[1] == name:
            return self._won(name)
        if seen is None:
            self.store.mark(name, "unknown", "claim sent, but your account couldn't be read back")
            self._blocked(
                f"Couldn't confirm your username after trying @{name}. Check your profile, then start again."
            )
            return False
        if ans.status == "accepted":
            self.store.mark(
                name, "failed_verification", f"Instagram accepted it but your account still shows @{seen[1]}"
            )
            self.log(f"@{name}: Instagram said yes, but your account still shows @{seen[1]}. Moving on.")
        else:
            # A refusal is an answer: retrying it every 10 minutes only burns username-change attempts.
            refused = ans.status == "unknown" and not ans.retry and not ans.stop
            self.store.mark(
                name,
                "unknown" if ans.retry else ans.status,
                ans.detail,
                wait=CLAIM_REFUSED if refused else None,
            )
            self.log(f"@{name}: {huntdb.STATUSES.get(ans.status, ans.status)}. {ans.detail}".strip())
        self.last = {"name": name, "status": ans.status}
        if ans.stop:
            self._blocked(ans.stop)
        elif ans.status == "rate_limited":
            self._pause(BACKOFF[0])
        return False

    def _won(self, name: str) -> bool:
        self.store.mark(name, "claimed", f"confirmed on account {self.account_id}")
        self.won, self.state, self.last = name, "won", {"name": name, "status": "claimed"}
        self.log(f"GOT IT: your account is now @{name}. The hunt is over.")
        return True

    def _recover(self) -> str:
        """A claim that was sent but never recorded (crash, power cut, closed terminal): look at the
        account to learn what really happened before doing anything else."""
        pending = self.store.interrupted_claims()
        if not pending:
            return ""
        seen = self._confirm()
        if seen is None:
            raise RuntimeError("an earlier claim was interrupted and your account couldn't be read")
        for name in pending:
            if seen[0] == self.account_id and seen[1] == name:
                self.log(f"Your earlier claim of @{name} went through.")
                self._won(name)
                return name
            self.store.mark(name, "unknown", f"claim was interrupted; your account is still @{seen[1]}")
        self.log(f"An interrupted claim didn't go through; your account is still @{seen[1]}.")
        return ""
