import pytest
from instagrapi import exceptions as E

from ig_ultimate.engine import Item, Plan, Stopped, execute, unfinished


def make_plan(action, n=5):
    return Plan("job", "Test", "done", [Item(str(i), f"#{i}") for i in range(n)], action)


def no_sleep(_seconds):
    pass


def test_runs_everything_and_clears_progress(ui):
    calls = []
    assert execute(make_plan(lambda i: calls.append(i.id)), ui, sleep=no_sleep) == (5, 0)
    assert calls == ["0", "1", "2", "3", "4"]
    assert unfinished("job") == 0


def test_action_block_stops_then_resumes_where_it_left_off(ui):
    calls, blocked = [], {"on": True}

    def act(item):
        if item.id == "2" and blocked["on"]:
            raise E.FeedbackRequired("feedback_required")
        calls.append(item.id)

    with pytest.raises(Stopped, match="Action Blocked"):
        execute(make_plan(act), ui, sleep=no_sleep)
    assert calls == ["0", "1"] and unfinished("job") == 2

    blocked["on"] = False
    assert execute(make_plan(act), ui, sleep=no_sleep) == (3, 0)
    assert calls == ["0", "1", "2", "3", "4"]  # 0 and 1 were not done twice


def test_rate_limit_and_security_check_also_stop():
    for exc in (E.PleaseWaitFewMinutes("wait"), E.ChallengeRequired("challenge")):
        with pytest.raises(Stopped):
            execute(make_plan(lambda i, e=exc: (_ for _ in ()).throw(e)), ui=_Null(), sleep=no_sleep)


class _Null:
    def step(self, *a, **k):
        pass

    def say(self, *a):
        pass


def test_one_bad_item_is_skipped_but_five_in_a_row_stops(ui):
    def act(item):
        if item.id == "1":
            raise RuntimeError("user not found")

    assert execute(make_plan(act), ui, sleep=no_sleep) == (4, 1)

    with pytest.raises(Stopped, match="5 errors in a row"):
        execute(make_plan(lambda i: (_ for _ in ()).throw(RuntimeError("x")), n=8), ui, sleep=no_sleep)


def test_dry_run_changes_nothing(ui):
    calls = []
    assert execute(make_plan(lambda i: calls.append(i)), ui, dry_run=True, sleep=no_sleep) == (5, 0)
    assert calls == []


def test_pacing_waits_between_actions_and_takes_a_batch_break(ui):
    sleeps = []
    execute(make_plan(lambda i: None, n=12), ui, speed="safe", sleep=sleeps.append)
    assert len(sleeps) == 11  # between actions, not after the last one
    assert sleeps.count(60.0) == 1  # the batch break after 10
    assert all(3.0 <= s <= 15.0 for s in sleeps if s != 60.0)


def test_ctrl_c_keeps_progress(ui):
    def act(item):
        if item.id == "3":
            raise KeyboardInterrupt

    with pytest.raises(Stopped, match="Stopped by you"):
        execute(make_plan(act), ui, sleep=no_sleep)
    assert unfinished("job") == 3


def test_cancel_flag_stops_before_the_next_item(ui):
    calls = []
    with pytest.raises(Stopped):
        execute(
            make_plan(lambda i: calls.append(i.id)), ui, sleep=no_sleep, cancelled=lambda: len(calls) >= 2
        )
    assert calls == ["0", "1"]
