from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from ig_ultimate import ai, chat
from ig_ultimate.fake import FakeClient

START = datetime(2026, 11, 1, 12, tzinfo=UTC)


def msg(user_id, minutes, text="hey", kind="text", **extra):
    return NS(
        id=f"m{user_id}-{minutes}",
        user_id=str(user_id),
        timestamp=START + timedelta(minutes=minutes),
        item_type=kind,
        text=text,
        is_sent_by_viewer=str(user_id) == "1000",
        media=extra.get("media"),
        clip=extra.get("clip"),
    )


@pytest.fixture
def replies(monkeypatch):
    seen = []

    def fake_reply(s, system, turns):
        seen.append(turns)
        return "lol same"

    monkeypatch.setattr(ai, "reply", fake_reply)
    return seen


def bot(cl, mode_allowed=None, **a):
    settings = {**chat.DEFAULT_AI, "provider": "groq", **a}
    return chat.Bot(cl, settings, mode_allowed, log=lambda s: None, sleep=lambda s: None, now=lambda: START)


def sends(cl):
    return [c for c in cl.calls if c[0] == "direct_send"]


def test_messages_from_before_start_are_never_answered(replies):
    cl = FakeClient()
    cl._threads[0].messages = [msg(2000, -30), msg(2000, -5)]
    assert bot(cl).poll_once() == 0 and not sends(cl)


def test_new_message_gets_exactly_one_reply_once(replies):
    cl = FakeClient()
    b = bot(cl)
    cl._threads[0].messages = [msg(2000, -5), msg(2000, 1, "u up?"), msg(2000, 2, "??")]
    assert b.poll_once() == 1
    assert sends(cl) == [("direct_send", "lol same", (5000,))]
    assert replies[0][-1]["text"] == "??" and len(replies[0]) == 3
    assert b.poll_once() == 0  # same messages: not answered twice


def test_whitelist_only_answers_listed_people(replies):
    cl = FakeClient()
    cl._threads[0].messages = [msg(2000, 1)]
    cl._threads[1].messages = [msg(2001, 1)]
    assert bot(cl, mode_allowed={"2001"}).poll_once() == 1
    assert sends(cl)[0][2] == (5001,)


def test_everyone_mode_answers_all_dms_but_not_groups_by_default(replies):
    cl = FakeClient()
    cl._threads[0].messages = [msg(2000, 1)]
    group = cl._threads[-1]
    group.messages = [msg(2025, 1)]
    assert bot(cl).poll_once() == 1
    cl2 = FakeClient()
    cl2._threads[-1].messages = [msg(2025, 1)]
    assert bot(cl2, groups=True).poll_once() == 1


def test_backs_off_when_you_already_replied_yourself(replies):
    cl = FakeClient()
    cl._threads[0].messages = [msg(2000, 1), msg(1000, 2, "omw")]
    assert bot(cl).poll_once() == 0


def test_hourly_cap_stops_bot_to_bot_loops(replies, monkeypatch):
    monkeypatch.setattr(chat, "PER_CHAT_PER_HOUR", 2)
    cl = FakeClient()
    b = bot(cl)
    for n in range(1, 5):
        cl._threads[0].messages.append(msg(2000, n * 10))
        b.poll_once()
    assert len(sends(cl)) == 2


def test_robot_tag_is_optional(replies):
    cl = FakeClient()
    cl._threads[0].messages = [msg(2000, 1)]
    bot(cl, tag=True).poll_once()
    assert sends(cl)[0][1] == "lol same 🤖"


def test_voice_note_is_transcribed_and_reel_described(replies, monkeypatch):
    monkeypatch.setattr(ai, "fetch_media", lambda url: b"bytes")
    monkeypatch.setattr(ai, "transcribe", lambda s, audio: "where are you")
    cl = FakeClient()
    reel = NS(
        thumbnail_url="https://scontent.cdninstagram.com/r.jpg",
        caption_text="cat vs cucumber",
        user=NS(username="pets"),
    )
    cl._threads[0].messages = [
        msg(
            2000,
            1,
            None,
            "voice_media",
            media=NS(audio_url="https://x.cdninstagram.com/a.m4a", media_type=11),
        ),
        msg(2000, 2, None, "clip", clip=reel),
    ]
    bot(cl).poll_once()
    texts = [t["text"] for t in replies[0]]
    assert texts[0] == "[voice note] where are you"
    assert "cat vs cucumber" in texts[1] and replies[0][1]["images"] == [b"bytes"]


def test_ai_that_cant_see_photos_retries_text_only(monkeypatch):
    calls = []

    def picky(s, system, turns):
        calls.append(any(t["images"] for t in turns))
        if calls[-1]:
            raise ai.AIError("model does not support images")
        return "ok"

    monkeypatch.setattr(ai, "reply", picky)
    monkeypatch.setattr(ai, "fetch_media", lambda url: b"img")
    cl = FakeClient()
    photo = NS(thumbnail_url="https://scontent.cdninstagram.com/p.jpg", media_type=1)
    cl._threads[0].messages = [msg(2000, 1, None, "media", media=photo)]
    assert bot(cl).poll_once() == 1 and calls == [True, False]


def test_file_reading_ais_are_never_allowed_for_everyone():
    assert ai.needs_trusted_people({"provider": "codex"})
    assert ai.needs_trusted_people({"provider": "claude_code", "agent_folder": "bot-files"})
    assert not ai.needs_trusted_people({"provider": "claude_code"})
    assert not ai.needs_trusted_people({"provider": "groq"})
