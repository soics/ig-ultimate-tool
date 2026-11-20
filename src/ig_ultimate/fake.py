"""A pretend Instagram account for preview mode (`--preview`) and tests. No network, no login.

Every method here exists on the real `instagrapi.Client` with the same parameter names, and every
attribute on the fake objects exists on the real instagrapi types: tests/test_contract.py enforces
it, so preview mode can never hide a call that would fail on a real account.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

from instagrapi import exceptions as ig_errors

NOW = datetime.now(UTC)
NAMES = (
    "alex.nova mia_rose jayden.k luna.sky noah_b zoe.art liam.fit ava_eats ethan.codes isla.m "
    "mason.j chloe.v leo_runs ella.b kai.waves nina.blue otto.b sara_k theo.cam yara.l "
    "drew.p ivy_g jonah.t lara.mx milo.r nova.z omar.h piper.q quinn.d rhea.s "
    "sami.f tara.w uma.k vic.n wren.o xena.y yuri.c zane.e abby.l ben.t"
).split()


def _user(i: int) -> NS:
    return NS(pk=str(2000 + i), username=NAMES[i], full_name=NAMES[i].split(".")[0].split("_")[0].title())


def _media(
    pk: int, days: int, kind: int = 1, product: str = "feed", caption: str = "", comments: int = 0, by=None
):
    return NS(
        id=f"{pk}_1000",
        pk=str(pk),
        taken_at=NOW - timedelta(days=days),
        media_type=kind,
        product_type=product,
        caption_text=caption,
        comment_count=comments,
        user=by or NS(pk="1000", username="you"),
    )


class FakeClient:
    is_fake = True

    def __init__(self):
        self.user_id = "1000"
        self.uuid = "00000000-fake-uuid"
        self.delay_range = [0, 0]
        self.challenge_code_handler = None
        self.calls: list[tuple] = []  # every change, for tests
        self._settings: dict = {}
        users = [_user(i) for i in range(len(NAMES))]
        self._following = {u.pk: u for u in users[:30]}  # you follow 0-29
        self._followers = {u.pk: u for u in users[10:]}  # 10-39 follow you: 0-9 don't follow back
        self._requests = [
            NS(pk="3001", username="new.fan1", full_name=""),
            NS(pk="3002", username="new.fan2", full_name=""),
        ]
        self._pending: list = []  # message requests (chats from people you don't follow)
        months = [0, 1, 2, 4, 5, 7, 9, 13, 20, 30]
        self._threads = [
            NS(
                id=str(5000 + i),
                users=[users[i]],
                is_group=False,
                thread_title="",
                last_activity_at=NOW - timedelta(days=30 * m),
                messages=[],
            )
            for i, m in enumerate(months)
        ] + [
            NS(
                id=str(5100 + i),
                users=users[20 + 3 * i : 23 + 3 * i],
                is_group=True,
                thread_title=title,
                last_activity_at=NOW - timedelta(days=d),
                messages=[],
            )
            for i, (title, d) in enumerate([("weekend plans", 3), ("old class chat", 400), ("memes", 60)])
        ]
        self._medias = [
            _media(7001, 900, caption="first post!", comments=4),
            _media(7002, 600, 8, caption="trip dump", comments=0),
            _media(7003, 300, 2, "clips", "dance trend", comments=12),
            _media(7004, 120, caption="new hair", comments=2),
            _media(7005, 40, 2, "clips", "gym day", comments=0),
            _media(7006, 5, caption="sunset", comments=1),
        ]
        self._archived = [_media(7100, 1000, caption="cringe 2019 post")]
        self._stories = [
            NS(pk="8001", taken_at=NOW - timedelta(hours=3)),
            NS(pk="8002", taken_at=NOW - timedelta(hours=1)),
        ]
        self._highlights = [NS(pk="highlight:9001", title="Summer"), NS(pk="highlight:9002", title="Food")]
        self._liked = [_media(7200 + i, 10 * i, by=users[i]) for i in range(12)]
        self._saved = [_media(7300 + i, 20 * i, by=users[i + 5]) for i in range(5)]
        self._tagged = [_media(7400 + i, 50 * i, by=users[i + 12]) for i in range(3)]
        self._account = NS(
            pk="1000",
            username="you",
            full_name="Preview Person",
            biography="just vibes",
            external_url="https://example.com",
            phone_number="",
            email="you@example.com",
            is_private=False,
        )

    def _did(self, *call) -> bool:
        self.calls.append(call)
        return True

    # login & session
    def login(
        self,
        username: str | None = None,
        password: str | None = None,
        relogin: bool = False,
        verification_code: str = "",
    ):
        if password == "wrong":
            raise ig_errors.BadPassword("bad password")
        if password == "needs-2fa" and not verification_code:
            raise ig_errors.TwoFactorRequired("2fa")
        self._settings.setdefault("uuids", {"phone_id": "fake-phone"})
        return self._did("login", username)

    def set_settings(self, settings: dict) -> bool:
        self._settings = dict(settings)
        return True

    def get_settings(self) -> dict:
        return {"uuids": {"phone_id": "fake-phone"}, **self._settings}

    def set_uuids(self, uuids: dict | None = None) -> bool:
        self._settings["uuids"] = uuids or {}
        return True

    def logout(self) -> bool:
        return self._did("logout")

    def account_info(self):
        if self._settings.get("expired"):
            raise ig_errors.LoginRequired("expired")
        return self._account

    def with_default_data(self, data: dict) -> dict:
        return dict(data)

    def with_action_data(self, data: dict) -> dict:
        return dict(data)

    def private_request(
        self,
        endpoint,
        data=None,
        params=None,
        login=False,
        with_signature=True,
        headers=None,
        extra_sig=None,
        domain: str | None = None,
    ):
        self._did("private_request", endpoint)
        if endpoint.endswith("/leave/"):
            tid = endpoint.split("/")[2]
            self._threads = [t for t in self._threads if t.id != tid]
        elif endpoint.startswith("usertags/"):
            mid = endpoint.split("/")[1]
            self._tagged = [m for m in self._tagged if m.id != mid]
        elif endpoint == "accounts/edit_profile/":
            self._account.full_name = (data or {}).get("first_name", self._account.full_name)
        return {"status": "ok"}

    # followers & following
    def user_following(self, user_id: str, use_cache: bool = True, amount: int = 0):
        return dict(self._following)

    def user_followers(self, user_id: str, use_cache: bool = True, amount: int = 0, order=None):
        return dict(self._followers)

    def user_unfollow(self, user_id: str) -> bool:
        self._following.pop(str(user_id), None)
        return self._did("user_unfollow", user_id)

    def user_remove_follower(self, user_id: str) -> bool:
        self._followers.pop(str(user_id), None)
        return self._did("user_remove_follower", user_id)

    def user_follow_requests(self, amount: int = 0):
        return list(self._requests)

    def user_follow_request_decline(self, user_id: str) -> bool:
        self._requests = [u for u in self._requests if u.pk != str(user_id)]
        return self._did("user_follow_request_decline", user_id)

    # chats
    def direct_threads(self, amount: int = 20, selected_filter=None, box=None, thread_message_limit=None):
        return list(self._threads[:amount] if amount else self._threads)

    def direct_send(
        self, text: str, user_ids=(), thread_ids=(), send_attribute="message_button", reply_to_message=None
    ):
        for t in self._threads:
            if int(t.id) in [int(x) for x in thread_ids]:
                t.messages.append(
                    NS(
                        id=f"m{len(self.calls)}",
                        user_id="1000",
                        timestamp=max((m.timestamp for m in t.messages), default=NOW) + timedelta(seconds=1),
                        item_type="text",
                        text=text,
                        is_sent_by_viewer=True,
                        media=None,
                        clip=None,
                    )
                )
        self._did("direct_send", text, tuple(thread_ids))
        return NS(id="sent")

    def user_id_from_username(self, username: str) -> str:
        for u in list(self._following.values()) + list(self._followers.values()):
            if u.username == username:
                return u.pk
        raise ig_errors.UserNotFound(username)

    def direct_pending_inbox(self, amount: int = 20):
        return list(self._pending[:amount])

    def direct_pending_approve(self, thread_id: int) -> bool:
        self._threads += [t for t in self._pending if t.id == str(thread_id)]
        self._pending = [t for t in self._pending if t.id != str(thread_id)]
        return self._did("direct_pending_approve", thread_id)

    def direct_thread_hide(self, thread_id: int, move_to_spam: bool = False) -> bool:
        self._threads = [t for t in self._threads if t.id != str(thread_id)]
        return self._did("direct_thread_hide", thread_id)

    # posts & stories
    def user_medias(self, user_id: str, amount: int = 0, sleep: int = 0):
        return list(self._medias)

    def media_delete(self, media_id: str) -> bool:
        self._medias = [m for m in self._medias if m.id != media_id]
        return self._did("media_delete", media_id)

    def media_archive(self, media_id: str, revert: bool = False) -> bool:
        moved = [m for m in self._medias if m.id == media_id]
        self._medias = [m for m in self._medias if m.id != media_id]
        self._archived += moved
        return self._did("media_archive", media_id)

    def media_unarchive(self, media_id: str) -> bool:
        moved = [m for m in self._archived if m.id == media_id]
        self._archived = [m for m in self._archived if m.id != media_id]
        self._medias += moved
        return self._did("media_unarchive", media_id)

    def archive_medias(self, amount: int = 0):
        return list(self._archived)

    def user_stories(self, user_id: str, amount: int | None = None):
        return list(self._stories)

    def story_delete(self, story_pk: str) -> bool:
        self._stories = [s for s in self._stories if s.pk != story_pk]
        return self._did("story_delete", story_pk)

    def user_highlights(self, user_id: str, amount: int = 0):
        return list(self._highlights)

    def highlight_delete(self, highlight_pk: str) -> bool:
        self._highlights = [h for h in self._highlights if h.pk != highlight_pk]
        return self._did("highlight_delete", highlight_pk)

    # likes, saves, comments, tags
    def liked_medias(self, amount: int = 21, last_media_pk: int = 0):
        return list(self._liked)

    def media_unlike(self, media_id: str) -> bool:
        self._liked = [m for m in self._liked if m.id != media_id]
        return self._did("media_unlike", media_id)

    def collection_medias(self, collection_pk: str, amount: int = 21, last_media_pk: int = 0):
        return list(self._saved)

    def media_unsave(self, media_id: str, collection_pk: int | None = None) -> bool:
        self._saved = [m for m in self._saved if m.id != media_id]
        return self._did("media_unsave", media_id)

    def media_comments(self, media_id: str, amount: int = 20):
        media = next((m for m in self._medias if m.id == media_id), None)
        count = media.comment_count if media else 0
        return [
            NS(pk=str(int(media_id.split("_")[0]) * 100 + i), user=_user(i), text="nice!")
            for i in range(count)
        ]

    def comment_bulk_delete(self, media_id: str, comment_pks: list[int]) -> bool:
        for m in self._medias:
            if m.id == media_id:
                m.comment_count = max(0, m.comment_count - len(comment_pks))
        return self._did("comment_bulk_delete", media_id, len(comment_pks))

    def usertag_medias(self, user_id: str, amount: int = 0):
        return list(self._tagged)

    # profile
    def account_set_biography(self, biography: str) -> bool:
        self._account.biography = biography
        return self._did("account_set_biography", biography)

    def account_edit(self, **data):
        if "external_url" in data:
            self._account.external_url = data["external_url"]
        self._did("account_edit", tuple(sorted(data)))
        return self._account

    def account_set_private(self) -> bool:
        self._account.is_private = True
        return self._did("account_set_private")
