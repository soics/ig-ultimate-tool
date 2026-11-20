import pytest
from instagrapi.exceptions import ClientError, FeedbackRequired

from ig_ultimate import tools
from ig_ultimate.engine import execute
from ig_ultimate.fake import FakeClient


def run(plan, ui):
    return execute(plan, ui, sleep=lambda s: None)


def test_unfollow_people_who_dont_follow_back_respects_keep_list(ui):
    cl = FakeClient()
    plan = tools.plan_unfollow(cl, "not_back")
    assert [i.label for i in plan.items][:2] == ["@alex.nova", "@mia_rose"] and len(plan.items) == 10

    keep = {"2000"}  # alex.nova
    plan = tools.plan_unfollow(cl, "not_back", keep)
    assert len(plan.items) == 9 and "@alex.nova" not in [i.label for i in plan.items]
    run(plan, ui)
    assert set(cl._following) == {"2000"} | {str(2000 + i) for i in range(10, 30)}


def test_unfollow_pasted_usernames_and_limit():
    cl = FakeClient()
    assert [i.label for i in tools.plan_unfollow(cl, "these", usernames=["@Luna.Sky", "nobody"]).items] == [
        "@luna.sky"
    ]
    assert len(tools.plan_unfollow(cl, "all", limit=7).items) == 7


def test_remove_followers_you_dont_follow_back():
    assert len(tools.plan_remove_followers(FakeClient(), "not_back").items) == 10  # 30-39


def test_delete_chats_older_than_six_months_skips_groups_by_default():
    cl = FakeClient()
    labels = [i.label for i in tools.plan_delete_chats(cl, months=6).items]
    assert len(labels) == 5  # chats idle 7, 9, 13, 20, 30 months
    assert len(tools.plan_delete_chats(cl, months=6, include_groups=True).items) == 6  # + "old class chat"


def test_leave_groups_is_flagged_experimental():
    plan = tools.plan_leave_groups(FakeClient())
    assert plan.experimental and len(plan.items) == 3


def test_archive_then_restore_posts(ui):
    cl = FakeClient()
    run(tools.plan_posts(cl, "archive", older_than_months=12), ui)
    assert len(cl._medias) == 4 and len(cl._archived) == 3
    run(tools.plan_unarchive(cl), ui)
    assert len(cl._medias) == 7 and not cl._archived


def test_full_nuke_empties_the_account_except_keep_list(ui):
    cl = FakeClient()
    keep = {"2015"}  # someone you follow who follows you back
    for _key, _label, build in tools.NUKE_STEPS:
        run(build(cl, keep), ui)
    a = cl._account
    assert (a.biography, a.external_url, a.full_name, a.is_private) == ("", "", "", True)
    assert not (cl._medias or cl._liked or cl._saved or cl._stories or cl._highlights or cl._tagged)
    assert not (cl._threads or cl._requests)
    assert set(cl._following) == keep and set(cl._followers) == keep
    assert all(m.comment_count == 0 for m in cl._medias)


def test_nuke_skips_a_step_instagram_wont_read_but_stops_on_pushback():
    cl = FakeClient()
    sorry = "We're sorry, but something went wrong. Please try again."

    def refuse(*a, **k):
        raise ClientError(sorry)

    cl.liked_medias = refuse
    plans, failed = tools.build_nuke(cl, set(), skip={"posts"})
    assert len(plans) == len(tools.NUKE_STEPS) - 2
    assert {"unlike", "delete-posts"}.isdisjoint(p.job for p in plans)
    assert failed == [("Unlike everything you ever liked", sorry)]

    def blocked(*a, **k):
        raise FeedbackRequired("feedback_required")

    cl.liked_medias = blocked
    with pytest.raises(FeedbackRequired):
        tools.build_nuke(cl, set())
