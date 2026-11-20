"""Instagram login.

- The password is typed hidden and never saved. Only the login session (session.json, owner-only)
  is kept, so you don't log in from scratch every time.
- Handles two-factor codes and Instagram's "is this you?" security codes.
- When a saved session expires, it logs in again as the *same phone* (same device ids), because a
  brand-new device on every login is what makes Instagram suspicious.
"""

from __future__ import annotations

import contextlib
import getpass
from collections.abc import Callable

from instagrapi import Client
from instagrapi import exceptions as ig_errors

from . import paths
from .engine import explain


class LoginFailed(Exception):
    """str(exc) is a plain-English message for the user."""


def new_client() -> Client:
    cl = Client()
    cl.delay_range = [1, 3]  # small human-like gap before every request
    return cl


def session_file():
    return paths.data_dir() / "session.json"


def logout(cl=None) -> None:
    """Forget this computer's login. Your Instagram account itself is untouched.
    With a client, Instagram also ends this login, so a copied session.json stops working."""
    if cl is not None:
        with contextlib.suppress(Exception):  # offline or already expired: forgetting it locally is enough
            cl.logout()
    session_file().unlink(missing_ok=True)
    session_file().with_suffix(".corrupt").unlink(missing_ok=True)  # read_json keeps broken copies


def login(
    username: str,
    ask: Callable[[str], str] = input,
    ask_secret: Callable[[str], str] = getpass.getpass,
    make_client: Callable[[], Client] | None = None,
):
    cl = (make_client or new_client)()
    saved = paths.read_json(session_file(), None)
    if saved:
        cl.set_settings(saved)
        try:
            cl.account_info()
            return cl
        except Exception:  # noqa: BLE001 - expired or revoked session: log in again as the same device
            cl.set_settings({})
            cl.set_uuids(saved.get("uuids") or {})

    username = (username or ask("Instagram username: ")).strip().lstrip("@")
    if not username:
        raise LoginFailed("No username given.")
    password = ask_secret("Instagram password (hidden while you type): ")
    cl.challenge_code_handler = lambda _user, choice: ask(
        f"Instagram sent you a security code ({getattr(choice, 'name', choice)}). Type it here: "
    ).strip()

    try:
        try:
            cl.login(username, password)
        except ig_errors.TwoFactorRequired:
            code = ask("Two-factor code from your authenticator app or SMS: ").strip()
            cl.login(username, password, verification_code=code)
    except ig_errors.BadPassword:
        raise LoginFailed("Wrong username or password.") from None
    except Exception as exc:  # noqa: BLE001
        raise LoginFailed(explain(exc) or f"Login failed: {str(exc)[:200]}") from None

    paths.write_json(session_file(), cl.get_settings())
    return cl
