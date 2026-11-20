"""Easy mode: every tool in your web browser, served only to this computer (127.0.0.1).

Every request needs a random token from the link the app opens, and requests addressed to any
other host name are refused, so other websites (or other people on your network) can't drive it.
It uses the same plans and safe-action engine as the terminal menu.
"""

from __future__ import annotations

import hmac
import json
import os
import queue
import secrets
import threading
import time
import webbrowser
from collections import deque
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from . import ai, chat, config, engine, paths, session, tools
from .cli import NOTICE
from .engine import Plan, Stopped

MAX_BODY = 1_000_000
PAGE_GONE_AFTER = 3.0  # seconds without the page checking in = the page was closed
STATIC = {"/static/geist.woff2": "font/woff2", "/static/unbounded.woff2": "font/woff2"}


def _int(value) -> int | None:
    text = str(value if value is not None else "").strip()
    return int(text) if text.isdigit() else None


def _names(value) -> list[str]:
    return str(value or "").replace(",", " ").split()


def _f(name, label, kind="number", default=""):
    return {"name": name, "label": label, "type": kind, "default": default}


LIMIT = _f("limit", "How many at most (empty = all)")
OLDER = _f("older", "Only older than how many months (empty = all)")

KEPT = " People on your keep-list (Settings) are never touched."

# id -> (section, label, fields, builder(cl, params, keep), extra); extra["help"] = what it really does
TOOLS: dict[str, tuple] = {
    "show_not_back": (
        "Followers",
        "Show who doesn't follow me back",
        [],
        lambda cl, p, k: tools.plan_unfollow(cl, "not_back"),
        {
            "readonly": True,
            "help": "Just a list of accounts you follow that don't follow you. Changes nothing.",
        },
    ),
    "unfollow_not_back": (
        "Followers",
        "Unfollow people who don't follow me back",
        [LIMIT],
        lambda cl, p, k: tools.plan_unfollow(cl, "not_back", k, limit=_int(p.limit)),
        {"help": "Unfollows accounts that don't follow you back. Re-follow anyone by hand later." + KEPT},
    ),
    "unfollow_all": (
        "Followers",
        "Unfollow everyone",
        [LIMIT],
        lambda cl, p, k: tools.plan_unfollow(cl, "all", k, limit=_int(p.limit)),
        {"help": "Unfollows every account you follow." + KEPT},
    ),
    "unfollow_these": (
        "Followers",
        "Unfollow specific usernames",
        [_f("usernames", "Usernames (spaces or commas)", "text")],
        lambda cl, p, k: tools.plan_unfollow(cl, "these", k, usernames=_names(p.usernames)),
        {"help": "Paste usernames; only the ones you actually follow are unfollowed." + KEPT},
    ),
    "remove_not_back": (
        "Followers",
        "Remove followers I don't follow back",
        [LIMIT],
        lambda cl, p, k: tools.plan_remove_followers(cl, "not_back", k, limit=_int(p.limit)),
        {
            "help": "Removes them from your followers without blocking. They aren't told, can follow again."
            + KEPT
        },
    ),
    "remove_all": (
        "Followers",
        "Remove all my followers",
        [LIMIT],
        lambda cl, p, k: tools.plan_remove_followers(cl, "all", k, limit=_int(p.limit)),
        {
            "help": "Removes every follower without blocking. Make the account private first if you don't "
            "want them to follow back." + KEPT
        },
    ),
    "decline_requests": (
        "Followers",
        "Decline all follow requests",
        [],
        lambda cl, p, k: tools.plan_decline_requests(cl),
        {"help": "Declines every pending request (private accounts). They aren't told."},
    ),
    "delete_idle": (
        "Chats",
        "Delete chats quiet for more than N months",
        [_f("months", "Months", "number", "4"), _f("groups", "Include group chats", "checkbox")],
        lambda cl, p, k: tools.plan_delete_chats(
            cl, months=_int(p.months) or 4, include_groups=bool(p.groups)
        ),
        {
            "help": "Deletes old chats from your inbox only, like the app's Delete. The other person keeps "
            "their copy. Group chats: you stay in the group."
        },
    ),
    "delete_chats": (
        "Chats",
        "Delete chats (untick the ones to keep)",
        [_f("groups", "Include group chats", "checkbox")],
        lambda cl, p, k: tools.plan_delete_chats(cl, include_groups=bool(p.groups)),
        {"help": "Deletes chats from your inbox only. The other person keeps their copy."},
    ),
    "leave_groups": (
        "Chats",
        "Leave group chats (untick the ones to keep)",
        [],
        lambda cl, p, k: tools.plan_leave_groups(cl),
        {"help": "Leaves the group (members see that you left) and deletes it from your inbox."},
    ),
    "archive_posts": (
        "Posts",
        "Archive posts & reels (you can restore them)",
        [OLDER],
        lambda cl, p, k: tools.plan_posts(cl, "archive", _int(p.older)),
        {"help": "Hides posts and reels from your profile. Likes and comments are kept; restore any time."},
    ),
    "delete_posts": (
        "Posts",
        "Delete posts & reels for good",
        [OLDER],
        lambda cl, p, k: tools.plan_posts(cl, "delete", _int(p.older)),
        {
            "help": "Permanently deletes posts and reels, with their likes and comments. Can't be undone: "
            "archive instead if you're not sure."
        },
    ),
    "unarchive": (
        "Posts",
        "Restore archived posts",
        [],
        lambda cl, p, k: tools.plan_unarchive(cl),
        {"help": "Puts archived posts back on your profile."},
    ),
    "stories": (
        "Posts",
        "Delete my current stories",
        [],
        lambda cl, p, k: tools.plan_stories(cl),
        {"help": "Deletes stories that are live right now (the last 24 hours)."},
    ),
    "highlights": (
        "Posts",
        "Delete my highlights",
        [],
        lambda cl, p, k: tools.plan_highlights(cl),
        {"help": "Deletes the highlight circles on your profile. The stories inside stay in your archive."},
    ),
    "unlike": (
        "Footprint",
        "Unlike everything I ever liked",
        [],
        lambda cl, p, k: tools.plan_unlike(cl),
        {
            "help": "Removes your likes from posts and reels. Instagram only lists your recent likes, "
            "so very old ones may stay."
        },
    ),
    "unsave": (
        "Footprint",
        "Unsave everything I saved",
        [],
        lambda cl, p, k: tools.plan_unsave(cl),
        {"help": "Empties your Saved posts. Nobody else is affected."},
    ),
    "wipe_comments": (
        "Footprint",
        "Delete all comments on my posts",
        [],
        lambda cl, p, k: tools.plan_comments_on_my_posts(cl),
        {"help": "Deletes every comment under your own posts, yours and other people's."},
    ),
    "untag": (
        "Footprint",
        "Remove me from photos I'm tagged in",
        [],
        lambda cl, p, k: tools.plan_untag(cl),
        {"help": "Removes your tag from other people's posts. The posts stay; your name comes off them."},
    ),
    "profile": (
        "Profile",
        "Profile clean-up (untick what to keep)",
        [],
        lambda cl, p, k: tools.plan_profile(cl),
        {
            "help": "Clears your bio, link, display name and profile picture, and makes the account private. "
            "Your username stays."
        },
    ),
}


class JobUI:
    def __init__(self, job: dict):
        self.job = job

    def step(self, n, total, item, ok, note=""):
        self.job["n"], self.job["total"] = n, total
        self.job["lines"].append(
            ("done  " if ok else "skip  ") + item.label + (f"  ({note})" if note else "")
        )

    def say(self, text):
        self.job["lines"].append(text)


class WebApp:
    def __init__(self, preview: bool = False):
        self.cfg = config.load()
        self._real_home = os.environ.get("IG_ULTIMATE_HOME")
        self.preview = False
        self.cl = None
        self.username = ""
        self.prompt = None
        self.answers: queue.Queue[str] = queue.Queue()
        self.login_error = ""
        self.logging_in = False
        self.plans: dict[str, Plan] = {}
        self.nuke_plans: list[Plan] = []
        self.job: dict | None = None
        self.cancel = threading.Event()
        self.start_lock = threading.Lock()  # a double-click must not start two jobs on one account
        self.last_seen = time.monotonic()  # the page checks in every second; see the nuke countdown
        self.ai_stop: threading.Event | None = None
        self.ai_log: deque[str] = deque(maxlen=200)
        if preview:
            self.use_preview()

    # ------------------------------------------------------------ status
    def busy(self) -> str:
        if self.job and self.job["running"]:
            return "A job is running. Wait for it or press Stop."
        if self.ai_stop and not self.ai_stop.is_set():
            return "Auto-reply is running. Stop it first, so only one thing uses your account at a time."
        return ""

    def state(self) -> dict:
        self.last_seen = time.monotonic()
        a = chat.settings(self.cfg)
        job = self.job and {**self.job, "lines": list(self.job["lines"])[-80:]}
        return {
            "accepted": bool(self.cfg.get("accepted_notice")),
            "logged_in": self.cl is not None,
            "preview": self.preview,
            "username": self.username,
            "has_session": session.session_file().exists(),
            "logging_in": self.logging_in,
            "prompt": self.prompt,
            "login_error": self.login_error,
            "speed": self.cfg.get("speed", "safe"),
            "keep": sorted(self.cfg.get("keep", {}).values()),
            "job": job,
            "ai": {
                "running": bool(self.ai_stop and not self.ai_stop.is_set()),
                "provider": a["provider"],
                "has_key": bool(a.get("key")),
                "model": a.get("model", ""),
                **{k: a[k] for k in ("mode", "name", "style", "tag", "groups", "instant")},
                "whitelist": sorted(a["whitelist"].values()),
                "these": a["these"],
                "log": list(self.ai_log)[-60:],
            },
        }

    def catalog(self) -> dict:
        return {
            "tools": [
                {"id": k, "section": s, "label": label, "fields": fields, **extra}
                for k, (s, label, fields, _b, extra) in TOOLS.items()
            ],
            "nuke": [{"key": k, "label": label} for k, label, _b in tools.NUKE_STEPS],
            "providers": [
                {
                    "key": k,
                    "label": p["label"],
                    "privacy": p["privacy"],
                    "steps": p["steps"],
                    "link": p.get("key_url", ""),
                    "needs_key": p.get("kind") != "cli" and not p.get("no_key"),
                }
                for k, p in ai.PRESETS.items()
            ],
            "claude_models": [{"id": m, "label": label} for m, label in ai.PRESETS["anthropic"]["models"]],
            "modes": chat.MODES,
            "notice": NOTICE,
        }

    def save(self):
        if not self.preview:
            config.save(self.cfg)

    # ------------------------------------------------------------ login
    def use_preview(self):
        from .fake import FakeClient

        if self.cl is not None and not self.preview:
            raise ValueError("Log out of your real account first.")
        os.environ["IG_ULTIMATE_HOME"] = str(paths.data_dir() / "preview")
        self.preview, self.cl, self.username = True, FakeClient(), "you"

    def _leave_preview(self):
        """Back to the real account's folder, settings and normal pacing."""
        if not self.preview:
            return
        if self._real_home is None:
            os.environ.pop("IG_ULTIMATE_HOME", None)
        else:
            os.environ["IG_ULTIMATE_HOME"] = self._real_home
        self.preview, self.cl, self.username = False, None, ""
        self.cfg = config.load()

    def start_login(self, username: str, password: str):
        if self.logging_in:
            raise ValueError("Already logging in.")
        if why := self.busy():
            raise ValueError(why)
        self._leave_preview()
        self.logging_in, self.login_error = True, ""
        while not self.answers.empty():  # a code typed for an earlier login must not be used for this one
            self.answers.get_nowait()

        def ask(question: str) -> str:
            self.prompt = question
            try:
                return self.answers.get(timeout=600)
            finally:
                self.prompt = None

        def work():
            try:
                cl = session.login(
                    username or self.cfg.get("username", ""), ask=ask, ask_secret=lambda _p: password
                )
                self.cfg["username"] = getattr(cl, "username", "") or username or self.cfg.get("username", "")
                self.save()
                self.cl, self.username = cl, self.cfg["username"]
            except session.LoginFailed as exc:
                self.login_error = str(exc)
            except queue.Empty:
                self.login_error = "Timed out waiting for the code. Try again."
            finally:
                self.logging_in = False

        threading.Thread(target=work, daemon=True).start()

    def logout(self):
        self.cancel.set()  # logging out stops whatever is still using the account
        self.ai_stop_now()
        if self.preview:
            return self._leave_preview()
        session.logout(self.cl)
        self.cl, self.username = None, ""

    # ------------------------------------------------------------ plans & jobs
    def build(self, tool: str, params: dict) -> dict:
        if why := self.busy():
            raise ValueError(why)
        _s, _label, _fields, builder, extra = TOOLS[tool]
        plan = builder(
            self.cl,
            SimpleNamespace(
                **{k: params.get(k) for k in ("limit", "older", "months", "groups", "usernames")}
            ),
            set(self.cfg.get("keep", {})),
        )
        plan_id = secrets.token_hex(8)
        self.plans = {plan_id: plan}  # only the latest preview can be run
        return {
            "plan_id": plan_id,
            "title": plan.title,
            "count": len(plan.items),
            "experimental": plan.experimental,
            "readonly": bool(extra.get("readonly")),
            "items": [[i.id, i.label] for i in plan.items[:2000]],
            "list_file": str(engine.save_list(plan)),
            "resumable": engine.unfinished(plan.job),
        }

    def _sleep(self, seconds: float) -> None:
        self.cancel.wait(min(seconds, 0.05) if self.preview else seconds)

    def start_job(self, title: str, plans: list[Plan], dry: bool = False, countdown: int = 0):
        with self.start_lock:
            if why := self.busy():
                raise ValueError(why)
            self._start_job(title, plans, dry, countdown)

    def _start_job(self, title: str, plans: list[Plan], dry: bool, countdown: int):
        job = {
            "id": secrets.token_hex(4),
            "title": title,
            "running": True,
            "n": 0,
            "total": sum(len(p.items) for p in plans),
            "lines": deque(maxlen=400),
            "result": None,
            "dry": dry,
        }
        self.job = job
        self.cancel.clear()

        def work():
            try:
                for s in range(countdown, 0, -1):
                    job["lines"].append(f"Starting in {s}... press Stop or close the page to cancel.")
                    if self.cancel.wait(1):
                        job["result"] = "Cancelled. Nothing changed."
                        return
                    if time.monotonic() - self.last_seen > PAGE_GONE_AFTER:
                        job["result"] = "Cancelled because the page was closed. Nothing changed."
                        return
                done = 0
                for plan in plans:
                    if len(plans) > 1:
                        job["lines"].append(f"--- {plan.title} ({len(plan.items)})")
                    ok, _failed = engine.execute(
                        plan,
                        JobUI(job),
                        speed=self.cfg.get("speed", "safe"),
                        dry_run=dry,
                        sleep=self._sleep,
                        cancelled=self.cancel.is_set,
                    )
                    done += ok
                job["result"] = f"Finished: {done} {'checked (dry run, nothing changed)' if dry else 'done'}."
            except Stopped as exc:
                job["result"] = str(exc)
            except Exception as exc:  # noqa: BLE001 - shown to the user instead of crashing the server
                job["result"] = engine.explain(exc) or f"Something went wrong: {str(exc)[:200]}"
            finally:
                job["running"] = False

        threading.Thread(target=work, daemon=True).start()

    def run(self, body: dict):
        plan = self.plans.get(str(body.get("plan_id")))
        if plan is None:
            raise ValueError("That list is out of date. Press 'Show me the list' again.")
        dry = bool(body.get("dry"))
        if not dry and str(body.get("confirm", "")).strip().lower() != "yes":
            raise ValueError("Type yes to confirm.")
        exclude = set(map(str, body.get("exclude") or []))
        chosen = Plan(
            plan.job,
            plan.title,
            plan.verb,
            [i for i in plan.items if i.id not in exclude],
            plan.action,
            plan.experimental,
        )
        self.start_job(chosen.title, [chosen], dry=dry)

    def nuke_plan(self, body: dict) -> dict:
        if why := self.busy():
            raise ValueError(why)
        skip = set(body.get("skip") or [])
        keep = set(self.cfg.get("keep", {}))
        self.nuke_plans, failed = tools.build_nuke(self.cl, keep, skip)
        self.username = self.cl.account_info().username
        rows = [
            {"title": p.title, "count": len(p.items), "experimental": p.experimental} for p in self.nuke_plans
        ]
        return {
            "steps": rows,
            "total": sum(r["count"] for r in rows),
            "failed": [{"title": t, "why": w} for t, w in failed],
            "username": self.username,
        }

    def nuke_run(self, body: dict):
        if not self.nuke_plans:
            raise ValueError("Count everything first.")
        if str(body.get("confirm", "")).strip().lower() != "yes":
            raise ValueError("Confirmation 1: type yes.")
        if str(body.get("username", "")).strip().lstrip("@") != self.username:
            raise ValueError("Confirmation 2: that isn't your username. Nothing changed.")
        plans, self.nuke_plans = self.nuke_plans, []
        self.start_job("NUKE", plans, countdown=5)

    # ------------------------------------------------------------ AI auto-reply
    def ai_settings(self, body: dict) -> dict:
        a = chat.settings(self.cfg)
        for key in ("mode", "name", "style", "tag", "groups", "instant"):
            if key in body:
                a[key] = body[key]
        if a["mode"] not in chat.MODES:
            raise ValueError("Unknown reply mode.")
        if "whitelist" in body:
            a["whitelist"] = (
                chat.resolve(self.cl, _names(body["whitelist"])) if _names(body["whitelist"]) else {}
            )
        said = ""
        provider = body.get("provider") or ""
        new_url = body.get("base_url", "") not in ("", a.get("base_url", ""))
        if provider and (provider != a["provider"] or body.get("key") or body.get("model") or new_url):
            said = self._setup_provider(a, provider, body)
        self.cfg["ai"] = a
        self.save()
        return {"said": said}

    def _setup_provider(self, a: dict, provider: str, body: dict) -> str:
        p = ai.PRESETS.get(provider)
        if p is None:
            raise ValueError("Unknown AI provider.")
        # Only the "custom" provider takes a URL, and a saved key is reused only for the same address.
        url = (
            ai.check_url(body.get("base_url") or a.get("base_url", ""))
            if provider == "custom"
            else p.get("base_url", "")
        )
        same = provider == a.get("provider") and url == a.get("base_url", "")
        s = {
            "provider": provider,
            "base_url": url,
            "key": body.get("key") or (a.get("key", "") if same else ""),
            "model": body.get("model") or "",
            "stt_model": "",
        }
        if p.get("kind") is None:
            models = ai.list_models(s)
            s["model"] = s["model"] or ai.pick(models, p.get("prefer", []))
            if p.get("stt_prefer"):
                s["stt_model"] = ai.pick(
                    [m for m in models if any(w in m for w in p["stt_prefer"])], p["stt_prefer"]
                )
        elif p.get("kind") == "anthropic":
            s["model"] = s["model"] or p["models"][0][0]
        said = ai.reply(
            s, "Reply with one short friendly word.", [{"role": "user", "text": "hey", "images": []}]
        )
        a.update(s)
        return said

    def ai_start(self, body: dict):
        with self.start_lock:
            if why := self.busy():
                raise ValueError(why)
            self._ai_start(body)

    def _ai_start(self, body: dict):
        a = chat.settings(self.cfg)
        if not a["provider"]:
            raise ValueError("Set up the AI first.")
        if a["mode"] == "everyone" and ai.needs_trusted_people(a):
            raise ValueError(
                "This AI can read files, so it can't answer Everyone. Use Whitelist or Only these."
            )
        allowed = None
        if a["mode"] == "whitelist":
            if not a["whitelist"]:
                raise ValueError("Your whitelist is empty.")
            allowed = set(a["whitelist"])
        elif a["mode"] == "these":
            found = chat.resolve(self.cl, _names(body.get("these")) or a["these"])
            if not found:
                raise ValueError("Couldn't find any of those usernames.")
            a["these"] = list(found.values())
            self.cfg["ai"] = a
            self.save()
            allowed = set(found)
        stop = threading.Event()
        self.ai_stop = stop
        self.ai_log.append("Auto-reply started. Only new messages get answered.")
        bot = chat.Bot(self.cl, a, allowed, log=self.ai_log.append)
        threading.Thread(target=bot.run, args=(stop,), daemon=True).start()

    def ai_stop_now(self):
        if self.ai_stop:
            self.ai_stop.set()
            self.ai_log.append("Auto-reply stopped.")

    # ------------------------------------------------------------ routing
    def post(self, path: str, body: dict):
        if path == "/api/agree":
            self.cfg["accepted_notice"] = True
            return self.save()
        if not self.cfg.get("accepted_notice"):  # same notice the terminal asks you to agree to
            raise ValueError("Read the notice and press I agree first.")
        if path == "/api/login":
            return self.start_login(
                str(body.get("username", "")).strip().lstrip("@"), str(body.get("password", ""))
            )
        if path == "/api/answer":
            if not self.prompt:
                raise ValueError("Nothing is waiting for a code right now.")
            return self.answers.put(str(body.get("text", "")).strip())
        if path == "/api/preview":
            return self.use_preview()
        if self.cl is None:
            raise ValueError("Log in first.")
        routes = {
            "/api/logout": lambda: self.logout(),
            "/api/plan": lambda: self.build(str(body.get("tool")), body.get("params") or {}),
            "/api/run": lambda: self.run(body),
            "/api/stop": lambda: self.cancel.set(),
            "/api/nuke/plan": lambda: self.nuke_plan(body),
            "/api/nuke/run": lambda: self.nuke_run(body),
            "/api/speed": lambda: self._speed(body),
            "/api/keep": lambda: self._keep(body),
            "/api/ai/settings": lambda: self.ai_settings(body),
            "/api/ai/start": lambda: self.ai_start(body),
            "/api/ai/stop": lambda: self.ai_stop_now(),
        }
        if path not in routes:
            raise KeyError(path)
        return routes[path]()

    def _speed(self, body: dict):
        self.cfg["speed"] = "fast" if body.get("speed") == "fast" else "safe"
        self.save()

    def _keep(self, body: dict):
        names = _names(body.get("usernames"))
        if body.get("remove"):
            drop = {n.lstrip("@").lower() for n in names}
            self.cfg["keep"] = {pk: n for pk, n in self.cfg.get("keep", {}).items() if n.lower() not in drop}
        else:
            self.cfg["keep"] = {**self.cfg.get("keep", {}), **chat.resolve(self.cl, names)}
        self.save()


def page() -> bytes:
    return resources.files("ig_ultimate").joinpath("web.html").read_bytes()


class Handler(BaseHTTPRequestHandler):
    server_version = "IGUltimateTool"
    timeout = 10  # a stuck or half-open connection can't hold a thread forever

    def log_message(self, *args):  # quiet, and never prints the token
        pass

    def _send(self, code: int, body, ctype: str = "application/json", cookie: str = "") -> None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        for key, value in (
            ("Content-Type", f"{ctype}; charset=utf-8"),
            ("Content-Length", str(len(data))),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "DENY"),
            ("Referrer-Policy", "no-referrer"),
        ):
            self.send_header(key, value)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(data)

    def _allowed(self) -> bool:
        return self.headers.get("Host", "") in self.server.hosts  # type: ignore[attr-defined]

    def _authed(self) -> bool:
        return hmac.compare_digest(self.headers.get("X-Token", ""), self.server.token)  # type: ignore[attr-defined]

    def do_GET(self):
        url = urlparse(self.path)
        if not self._allowed():
            return self._send(403, {"error": "forbidden"})
        if url.path == "/":
            # The link the app opens carries a one-time code (other users could see it in the browser's
            # command line). It is swapped for a browser-only cookie; the API token lives in the page.
            srv = self.server
            boot = parse_qs(url.query).get("t", [""])[0]
            fresh = bool(srv.boot) and hmac.compare_digest(boot, srv.boot)  # type: ignore[attr-defined]
            jar = SimpleCookie(self.headers.get("Cookie", ""))
            known = "igut" in jar and hmac.compare_digest(jar["igut"].value, srv.cookie)  # type: ignore[attr-defined]
            if not (fresh or known):
                return self._send(
                    403,
                    b"This link was already used. Use the tab that's already open, "
                    b"or restart IG Ultimate Tool.",
                    "text/plain",
                )
            if fresh:
                srv.boot = ""  # type: ignore[attr-defined]
            html = page().replace(b"__TOKEN__", srv.token.encode())  # type: ignore[attr-defined]
            cookie = f"igut={srv.cookie}; HttpOnly; SameSite=Strict; Path=/"  # type: ignore[attr-defined]
            return self._send(200, html, "text/html", cookie=cookie)
        if url.path in STATIC:  # the app's own fonts: exact names only, nothing else on disk is reachable
            data = resources.files("ig_ultimate").joinpath("static", url.path.rsplit("/", 1)[1]).read_bytes()
            return self._send(200, data, STATIC[url.path])
        if not self._authed():
            return self._send(403, {"error": "forbidden"})
        app: WebApp = self.server.app  # type: ignore[attr-defined]
        if url.path == "/api/state":
            return self._send(200, app.state())
        if url.path == "/api/catalog":
            return self._send(200, app.catalog())
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not (self._allowed() and self._authed()):
            return self._send(403, {"error": "forbidden"})
        if "application/json" not in self.headers.get("Content-Type", ""):
            return self._send(415, {"error": "JSON only"})
        raw_length = self.headers.get("Content-Length") or "0"
        if not raw_length.isdigit():
            return self._send(400, {"error": "bad request"})
        length = int(raw_length)
        if length > MAX_BODY:
            return self._send(413, {"error": "too large"})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                return self._send(400, {"error": "bad request"})
            result = self.server.app.post(urlparse(self.path).path, body)  # type: ignore[attr-defined]
            return self._send(200, result if isinstance(result, dict) else {"ok": True})
        except KeyError:
            return self._send(404, {"error": "not found"})
        except (ValueError, ai.AIError) as exc:
            return self._send(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - explain instead of a stack trace in the browser
            return self._send(
                500, {"error": engine.explain(exc) or f"Something went wrong: {str(exc)[:200]}"}
            )


def make_server(preview: bool = False, port: int = 0) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    real_port = server.server_address[1]
    server.token = secrets.token_urlsafe(24)  # type: ignore[attr-defined]  # API token, only inside the page
    server.boot = secrets.token_urlsafe(16)  # type: ignore[attr-defined]  # one-time code in the opened link
    server.cookie = secrets.token_urlsafe(24)  # type: ignore[attr-defined]  # lets the opened tab reload
    server.hosts = {f"127.0.0.1:{real_port}", f"localhost:{real_port}"}  # type: ignore[attr-defined]
    server.app = WebApp(preview=preview)  # type: ignore[attr-defined]
    return server


def serve(preview: bool = False, open_browser: bool = True) -> int:
    server = make_server(preview)
    url = f"http://127.0.0.1:{server.server_address[1]}/?t={server.boot}"  # type: ignore[attr-defined]
    print("\nIG Ultimate Tool is open in your web browser.")
    print(f"If it didn't open, copy this link into your browser (it only works on this computer):\n  {url}")
    print("Keep this window open while you use it. Press Ctrl+C here to quit.\n")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Bye.")
    finally:
        server.app.cancel.set()  # type: ignore[attr-defined]
        server.app.ai_stop_now()  # type: ignore[attr-defined]
        server.server_close()
    return 0
