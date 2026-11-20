"""The safety rules of the menus: Enter never confirms, the nuke needs both confirmations, and the
countdown can be cancelled."""

import pytest

from ig_ultimate import cli, tools
from ig_ultimate.fake import FakeClient


@pytest.fixture
def typed(monkeypatch):
    """Feed answers to every question the menus ask."""
    answers: list[str] = []

    def fake_input(prompt=""):
        if not answers:
            raise AssertionError(f"unexpected question: {prompt}")
        return answers.pop(0)

    monkeypatch.setattr(cli.console, "input", fake_input)
    return answers


def app(cl=None, sleep=lambda s: None):
    return cli.App(cl or FakeClient(), {"speed": "safe", "keep": {}}, sleep=sleep)


def test_pressing_enter_never_confirms(typed):
    a = app()
    typed += ["1", ""]  # "Do it", then just Enter at the yes question
    a.run_plan(tools.plan_unfollow(a.cl, "all"))
    assert a.cl.calls == []


def test_typing_yes_runs_it(typed):
    a = app()
    typed += ["1", "yes"]
    a.run_plan(tools.plan_unfollow(a.cl, "not_back"))
    assert len([c for c in a.cl.calls if c[0] == "user_unfollow"]) == 10


def test_dry_run_changes_nothing(typed):
    a = app()
    typed += ["2"]
    a.run_plan(tools.plan_unlike(a.cl))
    assert a.cl.calls == []


def test_nuke_with_wrong_username_changes_nothing(typed):
    a = app()
    typed += ["", "yes", "someone_else"]
    a.nuke()
    assert a.cl.calls == []


def test_nuke_needs_the_word_yes(typed):
    a = app()
    typed += ["", "y"]
    a.nuke()
    assert a.cl.calls == []


def test_ctrl_c_during_countdown_changes_nothing(typed):
    def interrupt(_seconds):
        raise KeyboardInterrupt

    a = app(sleep=interrupt)
    typed += ["", "yes", "you"]
    a.nuke()
    assert a.cl.calls == []


def test_full_nuke_after_both_confirmations(typed):
    a = app()
    typed += ["", "yes", "you"]
    a.nuke()
    cl = a.cl
    assert not (cl._following or cl._followers or cl._threads or cl._medias or cl._liked or cl._saved)


def test_nuke_can_skip_steps(typed):
    a = app()
    skip = [str(n) for n, (key, _l, _b) in enumerate(tools.NUKE_STEPS, 1) if key != "likes"]
    typed += [",".join(skip), "yes", "you"]
    a.nuke()
    assert not a.cl._liked and a.cl._medias and a.cl._following


def test_finder_in_the_terminal_claims_only_after_you_type_your_username(typed, monkeypatch):
    from ig_ultimate import names, snipe

    monkeypatch.setattr(snipe, "LOOKUP_GAP", (0, 0))
    monkeypatch.setattr(snipe, "CLAIM_GAP", 0)
    custom = str(list(names.PRESETS).index("custom") + 1)
    a = app()
    typed += [custom, "zen quill", "yes", "someone.else"]  # wrong username: nothing happens
    a.finder()
    assert a.cl._account.username == "you"
    typed += [custom, "", "yes", "@you"]
    a.finder()
    assert a.cl._account.username == "quill" and a.cfg["username"] == "quill"
