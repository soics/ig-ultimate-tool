import os
import stat

import pytest

from ig_ultimate import paths, session
from ig_ultimate.fake import FakeClient


def never(_prompt):
    raise AssertionError("should not have asked")


def test_fresh_login_saves_an_owner_only_session_and_never_the_password():
    cl = session.login("you", ask=never, ask_secret=lambda p: "hunter2", make_client=FakeClient)
    assert cl.calls[0] == ("login", "you")
    saved = session.session_file()
    assert "hunter2" not in saved.read_text()
    if os.name == "posix":
        assert stat.S_IMODE(saved.stat().st_mode) == 0o600


def test_two_factor_code_is_asked_for():
    asked = []
    session.login(
        "you",
        ask=lambda p: asked.append(p) or "123456",
        ask_secret=lambda p: "needs-2fa",
        make_client=FakeClient,
    )
    assert "Two-factor" in asked[0]


def test_wrong_password_gives_a_plain_message():
    with pytest.raises(session.LoginFailed, match="Wrong username or password"):
        session.login("you", ask=never, ask_secret=lambda p: "wrong", make_client=FakeClient)


def test_saved_session_is_reused_without_a_password():
    paths.write_json(session.session_file(), {"uuids": {"phone_id": "p1"}})
    cl = session.login("you", ask=never, ask_secret=never, make_client=FakeClient)
    assert cl.calls == []


def test_expired_session_logs_in_again_as_the_same_phone():
    paths.write_json(session.session_file(), {"expired": True, "uuids": {"phone_id": "same-phone"}})
    cl = session.login("you", ask=never, ask_secret=lambda p: "pw", make_client=FakeClient)
    assert cl.get_settings()["uuids"] == {"phone_id": "same-phone"}


def test_app_folder_and_lists_are_owner_only():
    from ig_ultimate import engine, tools

    plan = tools.plan_unfollow(FakeClient(), "not_back")
    listed = engine.save_list(plan)
    if os.name == "posix":
        assert stat.S_IMODE(paths.data_dir().stat().st_mode) == 0o700
        assert stat.S_IMODE(listed.stat().st_mode) == 0o600


def test_logout_forgets_the_session():
    paths.write_json(session.session_file(), {"uuids": {}})
    session.logout()
    assert not session.session_file().exists()


def test_logout_ends_the_instagram_login_and_survives_being_offline():
    cl = FakeClient()
    paths.write_json(session.session_file(), {"uuids": {}})
    broken = session.session_file().with_suffix(".corrupt")
    broken.write_text("{broken")
    session.logout(cl)
    assert ("logout",) in cl.calls
    assert not session.session_file().exists() and not broken.exists()

    class Offline:
        def logout(self):
            raise OSError("no internet")

    session.logout(Offline())  # still forgets it locally
