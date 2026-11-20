"""Every clean-up tool, as a Plan (a list of targets + one action) for the safe-action engine.

Building a plan only *reads* the account. Nothing changes until the engine executes it, after
the user has seen the list and said yes. The nuke (`NUKE_STEPS`) is just these plans in order.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta

from .engine import Item, Plan


def me(cl) -> str:
    return str(cl.user_id)


def _ok(result) -> None:
    """Raw Instagram calls answer {"status": "ok"}; anything else is a failure for that item."""
    if isinstance(result, dict) and result.get("status", "ok") != "ok":
        raise RuntimeError(result.get("message") or "Instagram refused this action")


def _utc(dt: datetime | None) -> datetime:
    if dt is None:
        return datetime.min.replace(tzinfo=UTC)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _media_label(m) -> str:
    kind = {"clips": "reel"}.get(
        getattr(m, "product_type", ""), {8: "album", 2: "video"}.get(m.media_type, "photo")
    )
    when = _utc(m.taken_at).strftime("%Y-%m-%d") if m.taken_at else "unknown date"
    caption = (getattr(m, "caption_text", "") or "").replace("\n", " ")[:40]
    return f"{kind} from {when}" + (f': "{caption}"' if caption else "")


def _thread_label(t) -> str:
    who = t.thread_title or ", ".join(f"@{u.username}" for u in (t.users or [])[:3]) or "chat"
    return f"{who} (last message {_utc(t.last_activity_at):%Y-%m-%d})"


def _users_plan(
    job, title, verb, users: Iterable, action: Callable, keep: set[str], limit: int | None
) -> Plan:
    items = [Item(str(u.pk), f"@{u.username}") for u in users if str(u.pk) not in keep]
    return Plan(job, title, verb, items[:limit] if limit else items, action)


# ---------------------------------------------------------------- followers & following
def plan_unfollow(cl, mode: str = "not_back", keep: set[str] = frozenset(), usernames=(), limit=None) -> Plan:
    """mode: "not_back" (they don't follow you back), "all", or "these" (pasted usernames)."""
    following = cl.user_following(me(cl), amount=0)
    if mode == "not_back":
        fans = cl.user_followers(me(cl), amount=0)
        users = [u for pk, u in following.items() if pk not in fans]
    elif mode == "these":
        wanted = {n.strip().lstrip("@").lower() for n in usernames if n.strip()}
        users = [u for u in following.values() if u.username.lower() in wanted]
    else:
        users = list(following.values())
    return _users_plan(
        "unfollow", "Unfollow", "unfollowed", users, lambda i: cl.user_unfollow(i.id), keep, limit
    )


def plan_remove_followers(cl, mode: str = "not_back", keep: set[str] = frozenset(), limit=None) -> Plan:
    """mode: "not_back" (people you don't follow back) or "all"."""
    followers = cl.user_followers(me(cl), amount=0)
    if mode == "not_back":
        following = cl.user_following(me(cl), amount=0)
        users = [u for pk, u in followers.items() if pk not in following]
    else:
        users = list(followers.values())
    return _users_plan(
        "remove-followers",
        "Remove followers",
        "removed",
        users,
        lambda i: cl.user_remove_follower(i.id),
        keep,
        limit,
    )


def plan_decline_requests(cl) -> Plan:
    users = cl.user_follow_requests(amount=0)
    return _users_plan(
        "decline-requests",
        "Decline follow requests",
        "declined",
        users,
        lambda i: cl.user_follow_request_decline(i.id),
        set(),
        None,
    )


# ---------------------------------------------------------------- chats & groups
def leave_group(cl, thread_id: str) -> None:
    _ok(
        cl.private_request(
            f"direct_v2/threads/{thread_id}/leave/", data={"_uuid": cl.uuid}, with_signature=False
        )
    )


def plan_delete_chats(cl, months: int | None = None, include_groups: bool = False, picked=None) -> Plan:
    """Delete chats for you only (like the app's Delete). months=N keeps chats active in the last N months."""
    cutoff = datetime.now(UTC) - timedelta(days=30 * months) if months else None
    threads = [
        t
        for t in cl.direct_threads(amount=0)
        if (include_groups or not t.is_group)
        and (cutoff is None or _utc(t.last_activity_at) < cutoff)
        and (picked is None or str(t.id) in picked)
    ]
    items = [Item(str(t.id), _thread_label(t)) for t in threads]
    return Plan("delete-chats", "Delete chats", "deleted", items, lambda i: cl.direct_thread_hide(int(i.id)))


def plan_leave_groups(cl, picked=None) -> Plan:
    groups = [
        t for t in cl.direct_threads(amount=0) if t.is_group and (picked is None or str(t.id) in picked)
    ]

    def leave_and_delete(item: Item) -> None:
        leave_group(cl, item.id)
        cl.direct_thread_hide(int(item.id))

    items = [Item(str(t.id), _thread_label(t)) for t in groups]
    return Plan("leave-groups", "Leave group chats", "left", items, leave_and_delete, experimental=True)


# ---------------------------------------------------------------- posts, reels & stories
def plan_posts(cl, action: str = "archive", older_than_months: int | None = None) -> Plan:
    """action: "archive" (hide, can be undone) or "delete" (permanent)."""
    cutoff = datetime.now(UTC) - timedelta(days=30 * older_than_months) if older_than_months else None
    medias = [m for m in cl.user_medias(me(cl), amount=0) if cutoff is None or _utc(m.taken_at) < cutoff]
    items = [Item(m.id, _media_label(m)) for m in medias]
    if action == "delete":
        return Plan("delete-posts", "Delete posts & reels", "deleted", items, lambda i: cl.media_delete(i.id))
    return Plan("archive-posts", "Archive posts & reels", "archived", items, lambda i: cl.media_archive(i.id))


def plan_unarchive(cl) -> Plan:
    items = [Item(m.id, _media_label(m)) for m in cl.archive_medias(amount=0)]
    return Plan(
        "unarchive-posts", "Restore archived posts", "restored", items, lambda i: cl.media_unarchive(i.id)
    )


def plan_stories(cl) -> Plan:
    items = [
        Item(str(s.pk), f"story from {_utc(s.taken_at):%Y-%m-%d %H:%M}") for s in cl.user_stories(me(cl))
    ]
    return Plan("delete-stories", "Delete current stories", "deleted", items, lambda i: cl.story_delete(i.id))


def plan_highlights(cl) -> Plan:
    items = [Item(str(h.pk), f'highlight "{h.title}"') for h in cl.user_highlights(me(cl), amount=0)]
    return Plan(
        "delete-highlights", "Delete highlights", "deleted", items, lambda i: cl.highlight_delete(i.id)
    )


# ---------------------------------------------------------------- likes, saves, comments & tags
def plan_unlike(cl) -> Plan:
    items = [Item(m.id, _media_label(m)) for m in cl.liked_medias(amount=0)]
    return Plan("unlike", "Unlike everything you liked", "unliked", items, lambda i: cl.media_unlike(i.id))


def plan_unsave(cl) -> Plan:
    medias = cl.collection_medias("ALL_MEDIA_AUTO_COLLECTION", amount=0)
    items = [Item(m.id, _media_label(m)) for m in medias]
    return Plan("unsave", "Unsave everything you saved", "unsaved", items, lambda i: cl.media_unsave(i.id))


def plan_comments_on_my_posts(cl) -> Plan:
    medias = [m for m in cl.user_medias(me(cl), amount=0) if (m.comment_count or 0) > 0]

    def wipe(item: Item) -> None:
        pks = [int(c.pk) for c in cl.media_comments(item.id, amount=0)]
        for start in range(0, len(pks), 25):  # shortcut: 25 per call is a guess at Instagram's limit
            cl.comment_bulk_delete(item.id, pks[start : start + 25])

    items = [Item(m.id, f"{_media_label(m)} ({m.comment_count} comments)") for m in medias]
    return Plan("wipe-comments", "Delete all comments on your posts", "cleaned", items, wipe)


def plan_untag(cl) -> Plan:
    def untag(item: Item) -> None:
        _ok(cl.private_request(f"usertags/{item.id}/remove/", data={"_uuid": cl.uuid}, with_signature=False))

    items = [
        Item(m.id, f"{_media_label(m)} by @{m.user.username}") for m in cl.usertag_medias(me(cl), amount=0)
    ]
    return Plan(
        "untag", "Remove yourself from photos you're tagged in", "untagged", items, untag, experimental=True
    )


# ---------------------------------------------------------------- profile
def _clear_name(cl) -> None:
    info = cl.account_info()
    fields = ("external_url", "username", "biography", "phone_number", "email")
    data = {k: getattr(info, k, None) for k in fields} | {"first_name": ""}
    _ok(cl.private_request("accounts/edit_profile/", cl.with_default_data(data)))


def _remove_picture(cl) -> None:
    _ok(cl.private_request("accounts/remove_profile_picture/", cl.with_action_data({"_uuid": cl.uuid})))


# key: (label, action, experimental)
PROFILE_PARTS: dict[str, tuple[str, Callable, bool]] = {
    "bio": ("Clear your bio", lambda cl: cl.account_set_biography(""), False),
    "link": ("Remove the link in your bio", lambda cl: cl.account_edit(external_url=""), False),
    "name": ("Clear your display name", _clear_name, True),
    "picture": ("Remove your profile picture", _remove_picture, True),
    "private": ("Make your account private", lambda cl: cl.account_set_private(), False),
}


def plan_profile(cl, parts: Iterable[str] = tuple(PROFILE_PARTS)) -> Plan:
    parts = [p for p in parts if p in PROFILE_PARTS]
    items = [Item(p, PROFILE_PARTS[p][0] + (" (experimental)" if PROFILE_PARTS[p][2] else "")) for p in parts]
    return Plan(
        "profile",
        "Profile clean-up",
        "done",
        items,
        lambda i: PROFILE_PARTS[i.id][1](cl),
        experimental=any(PROFILE_PARTS[p][2] for p in parts),
    )


# ---------------------------------------------------------------- nuke
# Order matters: profile first (it is the most visible), follows last (so the account stays usable
# until the end). Each step builds its plan fresh right before it runs, so counts are current.
NUKE_STEPS: list[tuple[str, str, Callable]] = [
    (
        "profile",
        "Clear bio, link, name, profile picture; make account private",
        lambda cl, keep: plan_profile(cl),
    ),
    ("comments", "Delete all comments on your posts", lambda cl, keep: plan_comments_on_my_posts(cl)),
    ("tags", "Remove you from photos you're tagged in", lambda cl, keep: plan_untag(cl)),
    ("likes", "Unlike everything you ever liked", lambda cl, keep: plan_unlike(cl)),
    ("saves", "Unsave everything you saved", lambda cl, keep: plan_unsave(cl)),
    ("stories", "Delete your current stories", lambda cl, keep: plan_stories(cl)),
    ("highlights", "Delete all highlights", lambda cl, keep: plan_highlights(cl)),
    ("posts", "Delete every post and reel", lambda cl, keep: plan_posts(cl, "delete")),
    ("groups", "Leave every group chat", lambda cl, keep: plan_leave_groups(cl)),
    ("chats", "Delete every chat", lambda cl, keep: plan_delete_chats(cl, include_groups=True)),
    ("requests", "Decline all follow requests", lambda cl, keep: plan_decline_requests(cl)),
    (
        "following",
        "Unfollow everyone (except your keep-list)",
        lambda cl, keep: plan_unfollow(cl, "all", keep),
    ),
    (
        "followers",
        "Remove every follower (except your keep-list)",
        lambda cl, keep: plan_remove_followers(cl, "all", keep),
    ),
]
