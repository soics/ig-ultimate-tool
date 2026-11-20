from types import SimpleNamespace as NS

import pytest

from ig_ultimate import ai


class FakeResponse:
    def __init__(self, status=200, data=None):
        self.status_code, self._data, self.text = status, data or {}, str(data)

    def json(self):
        return self._data


def test_media_downloads_only_from_instagram_servers():
    for bad in (
        "http://scontent.cdninstagram.com/x.jpg",
        "https://evil.com/x.jpg",
        "https://cdninstagram.com.evil.com/x",
        "file:///etc/passwd",
        "https://169.254.169.254/latest",
        "https://evil.example\\@scontent.cdninstagram.com/x",  # urlparse and requests disagree on the host
        "https://user@scontent.cdninstagram.com/x",
    ):
        assert ai.fetch_media(bad) is None


def test_openai_style_request_includes_system_history_and_photos(monkeypatch):
    sent = {}

    def fake_post(url, json=None, headers=None, timeout=None, **kw):
        sent.update(url=url, body=json, headers=headers)
        return FakeResponse(data={"choices": [{"message": {"content": "  haha nice  "}}]})

    monkeypatch.setattr(ai.requests, "post", fake_post)
    s = {
        "provider": "groq",
        "base_url": "https://api.groq.com/openai/v1",
        "key": "gsk_test",
        "model": "llama-4-scout",
    }
    turns = [
        {"role": "user", "text": "look", "images": [b"\xff\xd8jpeg"]},
        {"role": "assistant", "text": "omg", "images": []},
    ]
    assert ai.reply(s, "be chill", turns) == "haha nice"
    assert sent["url"].endswith("/chat/completions") and sent["headers"]["Authorization"] == "Bearer gsk_test"
    msgs = sent["body"]["messages"]
    assert msgs[0] == {"role": "system", "content": "be chill"}
    assert msgs[1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert msgs[2] == {"role": "assistant", "content": "omg"}


def test_bad_key_and_free_limit_give_plain_messages(monkeypatch):
    s = {"provider": "groq", "base_url": "https://x/v1", "key": "k", "model": "m"}
    for status, words in ((401, "rejected the key"), (429, "free limit")):
        monkeypatch.setattr(ai.requests, "post", lambda *a, _s=status, **k: FakeResponse(_s, {}))
        with pytest.raises(ai.AIError, match=words):
            ai.reply(s, "x", [{"role": "user", "text": "hi", "images": []}])


def test_model_picker_prefers_the_best_available():
    models = ["gemma2-9b", "meta-llama/llama-4-scout-17b-16e-instruct", "whisper-large-v3"]
    assert ai.pick(models, ai.PRESETS["groq"]["prefer"]) == "meta-llama/llama-4-scout-17b-16e-instruct"
    assert ai.pick(["x", "y"], ["nothing"]) == "x"


def test_claude_uses_official_sdk_with_fallbacks(monkeypatch):
    import anthropic

    calls = {}

    class Messages:
        def create(self, **kw):
            calls["plain"] = kw
            return NS(stop_reason="end_turn", content=[NS(type="text", text="yo")])

    class Beta:
        messages = NS(
            create=lambda **kw: (
                calls.setdefault("beta", kw)
                and NS(stop_reason="end_turn", content=[NS(type="text", text="hey there")])
            )
        )

    class FakeAnthropic:
        def __init__(self, api_key=None, timeout=None):
            self.messages, self.beta = Messages(), Beta()

    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    turns = [
        {"role": "assistant", "text": "earlier", "images": []},
        {"role": "user", "text": "hi", "images": []},
    ]
    assert (
        ai.reply({"provider": "anthropic", "key": "k", "model": "claude-opus-5-5"}, "sys", turns)
        == "hey there"
    )
    assert calls["beta"]["fallbacks"] == "default" and calls["beta"]["messages"][0]["role"] == "user"
    assert ai.reply({"provider": "anthropic", "key": "k", "model": "claude-haiku-5-5"}, "sys", turns) == "yo"


def test_keys_only_go_over_https_or_to_this_computer():
    for ok in (
        "https://x.example/v1",
        "http://localhost:11434/v1",
        "http://127.0.0.1:8080/v1",
        "http://[::1]:1/v1",
    ):
        assert ai.check_url(ok) == ok
    for bad in ("http://x.example/v1", "http://localhost.evil.example/v1", "ftp://x", "", "x.example"):
        with pytest.raises(ai.AIError):
            ai.check_url(bad)


def test_codex_gets_the_dm_on_stdin_never_on_the_command_line(monkeypatch):
    seen = {}
    monkeypatch.setattr(ai.shutil, "which", lambda exe: f"/opt/{exe}.cmd")

    def fake_run(cmd, **kw):
        seen["cmd"], seen["input"] = cmd, kw.get("input")
        return NS(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ai.subprocess, "run", fake_run)
    dm = 'hey" & calc & "'  # would break out of cmd.exe quoting if it were an argument
    ai.reply({"provider": "codex"}, "rules", [{"role": "user", "text": dm, "images": []}])
    assert seen["cmd"][0] == "/opt/codex.cmd" and seen["cmd"][-2:] == ["--", "-"]
    assert not any(dm in arg for arg in seen["cmd"]) and dm in seen["input"]
