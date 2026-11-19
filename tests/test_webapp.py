"""The browser version: locked to this computer + token, and the same safety rules as the terminal."""

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from ig_ultimate import session, webapp
from ig_ultimate.fake import FakeClient


@pytest.fixture
def srv(monkeypatch):
    monkeypatch.setattr(session, "new_client", FakeClient)  # browser logins use the pretend account
    server = webapp.make_server()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.app.cancel.set()
    server.shutdown()
    server.server_close()


def call(srv, path, body=None, token=True, host=None, ctype="application/json", cookie=None):
    port = srv.server_address[1]
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, method="GET" if body is None else "POST"
    )
    if token:
        req.add_header("X-Token", srv.token)
    if body is not None:
        req.add_header("Content-Type", ctype)
    if host:
        req.add_header("Host", host)
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            return resp.status, (
                json.loads(raw) if resp.headers.get_content_type() == "application/json" else raw
            )
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, (json.loads(raw) if raw.startswith(b"{") else raw)


def wait(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    raise AssertionError("timed out")


def test_locked_to_this_computer_and_the_secret_link(srv):
    port = srv.server_address[1]
    assert call(srv, "/api/state", token=False)[0] == 403
    assert call(srv, "/api/state", host=f"evil.example:{port}")[0] == 403  # DNS-rebinding style request
    assert call(srv, "/", token=False)[0] == 403
    assert call(srv, f"/?t={srv.token}", token=False)[0] == 403  # the API token never works as a link
    boot = srv.boot
    status, html = call(srv, f"/?t={boot}", token=False)
    assert status == 200 and b"IG Ultimate Tool" in html and srv.token.encode() in html
    assert (
        call(srv, f"/?t={boot}", token=False)[0] == 403
    )  # one-time: someone who saw the link can't reuse it
    assert call(srv, "/", token=False, cookie=f"igut={srv.cookie}")[0] == 200  # the opened tab can reload
    assert call(srv, "/", token=False, cookie="igut=guess")[0] == 403
    assert call(srv, "/api/preview", b"{}", ctype="text/plain")[0] == 415  # plain HTML forms can't post here


def test_pretend_account_never_leaks_into_the_real_one(srv):
    app = srv.app
    call(srv, "/api/preview", {})
    assert app.preview and app._sleep is not None
    call(srv, "/api/logout", {})
    assert not app.preview and app.cl is None
    call(srv, "/api/login", {"username": "you", "password": "pw"})
    wait(lambda: app.cl is not None)
    assert not app.preview  # normal human pacing and saving again
    status, body = call(srv, "/api/preview", {})
    assert status == 400 and "Log out" in body["error"]


def test_ai_key_only_goes_to_its_own_provider(srv, monkeypatch):
    seen = {}
    monkeypatch.setattr(webapp.ai, "list_models", lambda s: seen.update(s) or ["llama-4-scout"])
    monkeypatch.setattr(webapp.ai, "reply", lambda s, system, turns: "hi")
    call(srv, "/api/preview", {})
    call(srv, "/api/ai/settings", {"provider": "groq", "key": "gsk_secret", "mode": "whitelist"})
    call(
        srv,
        "/api/ai/settings",
        {"provider": "groq", "base_url": "https://evil.example/v1", "model": "x", "mode": "whitelist"},
    )
    assert seen["base_url"] == "https://api.groq.com/openai/v1"
    assert srv.app.cfg["ai"]["base_url"] == "https://api.groq.com/openai/v1"


def test_preview_tool_run_needs_yes_and_respects_unticked_items(srv):
    assert call(srv, "/api/preview", {})[0] == 200
    status, plan = call(srv, "/api/plan", {"tool": "unfollow_not_back", "params": {}})
    assert status == 200 and plan["count"] == 10
    assert call(srv, "/api/run", {"plan_id": plan["plan_id"], "confirm": ""})[0] == 400
    keep = [plan["items"][0][0], plan["items"][1][0]]
    assert call(srv, "/api/run", {"plan_id": plan["plan_id"], "confirm": "yes", "exclude": keep})[0] == 200
    app = srv.app
    wait(lambda: not app.job["running"])
    unfollowed = [c[1] for c in app.cl.calls if c[0] == "user_unfollow"]
    assert len(unfollowed) == 8 and not set(keep) & set(unfollowed)


def test_two_factor_code_typed_in_the_browser(srv):
    assert call(srv, "/api/login", {"username": "you", "password": "needs-2fa"})[0] == 200
    wait(lambda: call(srv, "/api/state")[1]["prompt"])
    call(srv, "/api/answer", {"text": "123456"})
    wait(lambda: call(srv, "/api/state")[1]["logged_in"])
    assert not session.session_file().read_text().count("needs-2fa")


def test_nuke_needs_both_confirmations_and_stop_cancels_the_countdown(srv):
    call(srv, "/api/preview", {})
    status, counted = call(srv, "/api/nuke/plan", {"skip": []})
    assert status == 200 and counted["total"] > 0 and counted["username"] == "you"
    assert call(srv, "/api/nuke/run", {"confirm": "yes", "username": "someone"})[0] == 400
    assert call(srv, "/api/nuke/run", {"confirm": "yes", "username": "@you"})[0] == 200
    call(srv, "/api/stop", {})
    wait(lambda: not srv.app.job["running"])
    assert srv.app.job["result"] == "Cancelled. Nothing changed." and srv.app.cl.calls == []


def test_closing_the_page_during_the_countdown_cancels_the_nuke(srv, monkeypatch):
    monkeypatch.setattr(webapp, "PAGE_GONE_AFTER", 0.5)
    call(srv, "/api/preview", {})
    call(srv, "/api/nuke/plan", {"skip": []})
    assert call(srv, "/api/nuke/run", {"confirm": "yes", "username": "you"})[0] == 200
    # no more /api/state calls from here on: the page is "closed"
    wait(lambda: not srv.app.job["running"])
    assert "page was closed" in srv.app.job["result"] and srv.app.cl.calls == []


def test_file_reading_ai_refuses_everyone_mode(srv):
    call(srv, "/api/preview", {})
    srv.app.cfg["ai"] = {"provider": "codex", "mode": "everyone"}
    status, body = call(srv, "/api/ai/start", {})
    assert status == 400 and "can't answer Everyone" in body["error"]


def test_only_one_thing_uses_the_account_at_a_time(srv, monkeypatch):
    call(srv, "/api/preview", {})
    srv.app.cfg["ai"] = {"provider": "groq", "mode": "whitelist", "whitelist": {"2000": "alex.nova"}}
    monkeypatch.setattr(webapp.chat.Bot, "run", lambda self, stop: stop.wait(5))
    assert call(srv, "/api/ai/start", {})[0] == 200
    status, body = call(srv, "/api/plan", {"tool": "unlike", "params": {}})
    assert status == 400 and "Auto-reply is running" in body["error"]
    call(srv, "/api/ai/stop", {})
    assert call(srv, "/api/plan", {"tool": "unlike", "params": {}})[0] == 200
