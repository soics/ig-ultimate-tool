"""The hunter: honest results, claims only when authorized, confirmed by reading the account back,
and it stops the moment the dashboard goes away. Fake client + fake clock: no network, no waiting."""

import pytest
from instagrapi import exceptions as ig_errors

from ig_ultimate import huntdb, snipe
from ig_ultimate.fake import FakeClient


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def make(cl=None, auto=True, names=("zen", "quill"), first=("zen",), max_checks=50, **kw):
    cl = cl or FakeClient()
    store = huntdb.Store("1000")
    store.add_custom([n for n in names if n not in first], 0)
    store.add_custom(list(first), 1)  # tried first, so the order in each test is certain
    clock, logs = Clock(), []
    h = snipe.Hunter(
        snipe.InstagrapiVerifier(cl),
        store,
        "1000",
        "you",
        log=logs.append,
        preset="custom",
        auto_claim={"account_id": "1000", "username": "you"} if auto else None,
        sleep=clock.sleep,
        clock=clock.now,
        **kw,
    )
    if "alive" not in kw:  # stop after N lookups, or after a (fake) day of nothing left to check
        h.alive = lambda: h.checks < max_checks and clock.t < 86400
    return h, cl, store, clock, logs


def test_held_name_is_recorded_and_the_free_one_is_claimed_and_confirmed():
    h, cl, store, clock, logs = make()
    assert h.run() == "quill" and h.state == "won"
    assert cl._account.username == "quill"
    assert store.row("zen")["status"] == "held" and store.row("quill")["status"] == "claimed"
    assert any("GOT IT" in s for s in logs)
    assert clock.t >= snipe.CLAIM_GAP  # two claim attempts, spaced at least CLAIM_GAP apart
    events = [e["event"] for e in store.history(events=("claiming", "claimed", "held"))]
    assert events == ["claimed", "claiming", "held", "claiming"]  # written down BEFORE each claim was sent


def test_manual_mode_never_touches_the_account():
    h, cl, store, _, logs = make(auto=False, max_checks=2)
    assert h.run() == "" and cl._account.username == "you"
    assert not [c for c in cl.calls if c[0] == "account_edit"]
    assert store.row("quill")["status"] == "possibly_available"  # possibly, never "available"
    assert store.row("zen")["status"] == "possibly_available"  # the lookup alone can't tell held from free
    assert any("Claim it in the Instagram app" in s for s in logs)


def test_auto_claim_is_bound_to_the_authorized_account():
    store = huntdb.Store("1000")
    with pytest.raises(ValueError):
        snipe.Hunter(snipe.InstagrapiVerifier(FakeClient()), store, "1000", "you", log=print,
                     auto_claim={"account_id": "2000", "username": "other"})  # fmt: skip
    h, cl, store, _, _ = make()
    cl.user_id = "2000"  # the session now belongs to someone else
    assert h.run() == "" and h.state == "blocked" and cl._account.username == "you"


def test_slow_down_pauses_and_retries_the_same_name_without_counting_it():
    cl = FakeClient()
    real, seen = cl.user_info_by_username_v1, []

    def flaky(name):
        seen.append(name)
        if len(seen) == 1:
            raise ig_errors.ClientThrottledError("429")
        return real(name)

    cl.user_info_by_username_v1 = flaky
    h, cl, store, clock, _ = make(cl, names=("quill",), first=())
    assert h.run() == "quill" and seen[:2] == ["quill", "quill"] and clock.t >= snipe.BACKOFF[0]


def test_offline_records_nothing_and_tries_the_same_name_again():
    cl = FakeClient()
    real, seen = cl.user_info_by_username_v1, []

    def offline(name):
        seen.append(name)
        if len(seen) < 3:
            raise ig_errors.ClientConnectionError("network is unreachable")
        return real(name)

    cl.user_info_by_username_v1 = offline
    h, cl, store, clock, _ = make(cl, names=("quill",), first=())
    assert h.run() == "quill" and seen[:3] == ["quill"] * 3
    assert clock.t >= snipe.OFFLINE[0] * 3  # 1 min, then 2 min
    assert not [e for e in store.history() if e["event"] == "unknown"]  # never "checked" while offline


def test_an_odd_reply_is_unknown_never_taken_or_free():
    cl = FakeClient()

    def odd(name):
        raise KeyError("user")

    cl.user_info_by_username_v1 = odd
    h, cl, store, _, _ = make(cl, auto=False, names=("quill",), first=(), max_checks=1)
    h.run()
    assert store.row("quill")["status"] == "unknown" and store.row("quill")["attempts"] == 1


def test_challenge_stops_the_hunt_and_says_why():
    cl = FakeClient()

    def challenge(name):
        raise ig_errors.ChallengeRequired("challenge")

    cl.user_info_by_username_v1 = challenge
    h, _, store, _, logs = make(cl)
    assert h.run() == "" and h.state == "blocked" and "confirm it's you" in h.message
    assert store.row("zen")["status"] == "unchecked"  # nothing learned, nothing recorded


def test_username_change_limit_is_not_mistaken_for_held():
    cl = FakeClient()

    def limited(**data):
        raise ig_errors.ClientError("You can't change your username right now. Try again later.")

    cl.account_edit = limited
    h, cl, store, _, _ = make(cl, names=("quill",), first=())
    assert h.run() == "" and h.state == "blocked" and "14 days" in h.message
    assert store.row("quill")["status"] == "unknown"  # v1 wrongly buried every later free name as held


def test_already_exists_is_held_and_the_hunt_moves_on():
    cl = FakeClient()
    cl.free_names.add("folio")  # the lookup finds nobody...
    real, tried = cl.account_edit, []

    def edit(**data):
        tried.append(data["username"])
        if data["username"] == "folio":  # live reply on 2026-11-20: the lookup found nobody
            raise ig_errors.ClientError("{'errors': ['A user with that username already exists.']}")
        return real(**data)

    cl.account_edit = edit
    h, cl, store, _, _ = make(cl, names=("folio", "quill"), first=("folio",))
    assert h.run() == "quill" and tried == ["folio", "quill"]
    assert store.row("folio")["status"] == "held"


def test_a_refused_claim_is_not_retried_every_few_minutes(monkeypatch):
    cl = FakeClient()
    cl.free_names = {"zen", "quill"}
    tried = []

    def refuse(**data):
        tried.append(data["username"])
        raise ig_errors.ClientError("something Instagram never said before")

    cl.account_edit = refuse
    h, cl, store, clock, _ = make(cl, max_checks=10**6)  # runs a whole (fake) day
    monkeypatch.setattr(huntdb.time, "time", lambda: 1e9 + clock.t)  # recheck times follow the fake clock
    assert h.run() == "" and clock.t >= 86400
    assert sorted(tried) == ["quill", "zen"]  # once each, not every 10 minutes
    assert store.row("zen")["next_at"] >= 1e9 + snipe.CLAIM_REFUSED


def test_a_yes_from_instagram_is_not_success_until_the_account_shows_it():
    cl = FakeClient()
    cl.account_edit = lambda **data: cl._account  # replies OK, but nothing changed
    h, cl, store, _, logs = make(cl, names=("quill",), first=())
    assert h.run() == "" and cl._account.username == "you"
    assert store.row("quill")["status"] == "failed_verification"


def test_lost_reply_to_a_claim_is_settled_by_reading_the_account():
    cl = FakeClient()

    def edit_then_drop(**data):
        cl._account.username = data["username"]
        raise ig_errors.ClientRequestTimeout("read timed out")

    cl.account_edit = edit_then_drop
    h, cl, store, _, _ = make(cl, names=("quill",), first=())
    assert h.run() == "quill" and store.row("quill")["status"] == "claimed"


def test_unreadable_account_after_a_claim_stops_instead_of_claiming_again():
    cl = FakeClient()
    real = cl.account_info
    calls = {"n": 0}

    def flaky_info():
        calls["n"] += 1
        raise ig_errors.ClientConnectionError("down")

    cl.account_edit = lambda **data: (_ for _ in ()).throw(ig_errors.ClientRequestTimeout("timeout"))
    cl.account_info = flaky_info
    h, cl, store, _, _ = make(cl, names=("quill", "lumen"), first=())
    assert h.run() == "" and h.state == "blocked"
    assert store.row("lumen")["status"] == "unchecked"  # no second claim while the first is unclear
    cl.account_info = real


def test_interrupted_claim_is_resolved_on_the_next_start():
    store = huntdb.Store("1000")
    store.add_custom(["quill", "lumen"], 0)
    store.mark("quill", "claiming", "claim sent")  # the app died right after sending
    cl = FakeClient()
    cl._account.username = "quill"  # ...and it had gone through
    h = snipe.Hunter(snipe.InstagrapiVerifier(cl), store, "1000", "you", log=print, preset="custom")
    assert h.run() == "quill" and store.row("quill")["status"] == "claimed"

    store.mark("lumen", "claiming", "claim sent")
    cl._account.username = "quill"
    h, *_ = make(cl, names=(), first=(), max_checks=0)
    h.store = store
    h.run()
    assert store.row("lumen")["status"] == "unknown" and "interrupted" in store.row("lumen")["note"]


def test_stop_during_the_claim_gap_keeps_the_name_for_next_time():
    h, cl, store, clock, _ = make(names=("zen", "quill"))
    real_sleep = h.sleep

    def stop_in_gap(s):
        if store.row("quill")["status"] == "possibly_available":  # looked up, now waiting to claim
            h.stop()
        real_sleep(s)

    h.sleep = stop_in_gap
    assert h.run() == "" and cl._account.username == "you"
    assert store.row("quill")["status"] == "possibly_available"  # v1 skipped it for good here...
    assert store.pick(["custom"]) == "quill"  # ...v2 tries it first next time


def test_closed_dashboard_ends_the_hunt_before_the_next_request():
    alive = {"v": True}
    h, cl, store, _, logs = make(alive=lambda: alive["v"])
    sent = []
    real = cl.user_info_by_username_v1

    def lookup(name):
        sent.append(name)
        alive["v"] = False  # the tab closes while this lookup is in flight
        return real(name)

    cl.user_info_by_username_v1 = lookup
    assert h.run() == "" and h.state == "expired" and sent == ["zen"]
    assert store.row("zen")["status"] == "possibly_available"  # the in-flight result is still recorded
    assert not [c for c in cl.calls if c[0] == "account_edit"]  # but no claim after the page went away
    assert any("dashboard closed" in s for s in logs)


def test_paused_makes_no_requests():
    h, cl, store, clock, _ = make(auto=False)
    h.pause()
    ticks = {"n": 0}

    def sleep(s):
        ticks["n"] += 1
        if ticks["n"] == 30:
            h.stop()
        clock.t += s

    h.sleep = sleep
    assert h.run() == "" and h.checks == 0 and h.info()["state"] == "stopped"


def test_presence_heartbeat_reload_tabs_and_expiry():
    t = {"now": 0.0}
    p = snipe.Presence(timeout=30, clock=lambda: t["now"])
    assert p.beat("tab1") and p.alive()
    t["now"] = 25
    assert p.beat("tab2") and p.info()["tabs"] == 2
    p.bye("tab1")  # one of two tabs closed: still open
    t["now"] = 50
    assert p.alive()
    p.bye("tab2")  # the last tab closed (or reloaded)
    t["now"] = 50 + snipe.Presence.GRACE - 1
    assert p.beat("tab3")  # a reload checks back in within the grace period
    t["now"] += 31  # then silence (closed for real, or the laptop slept)
    assert not p.alive()
    assert not p.beat("tab3")  # a late heartbeat never brings a hunt back
    assert p.info() == {"alive": False, "tabs": 0, "timeout": 30.0, "quiet_for": 31.0}


def test_secrets_never_reach_the_log():
    text = 'Cookie: sessionid=12345%3Aabc; csrftoken=XYZ Authorization: Bearer IGT:2:eyJ password="hunter2"'
    out = snipe.redact(text)
    assert "12345" not in out and "XYZ" not in out and "eyJ" not in out and "hunter2" not in out
    assert len(snipe.clean("x" * 500)) == 160


def test_one_client_is_never_used_by_two_threads_at_once():
    import threading

    cl = FakeClient()
    v = snipe.InstagrapiVerifier(cl)
    assert snipe.InstagrapiVerifier(cl).lock is v.lock  # the dashboard and the hunter share it
    real, other = cl.user_info_by_username_v1, {}

    def slow_lookup(name):
        t = threading.Thread(target=lambda: other.setdefault("acct", v.account()))
        t.start()
        t.join(0.2)
        other["waited"] = t.is_alive()  # the account read had to wait for this reply
        return real(name)

    cl.user_info_by_username_v1 = slow_lookup
    v.lookup("taken1")
    assert other["waited"]


def test_only_one_hunt_per_account_even_across_windows():
    h, cl, store, _, _ = make()
    with store.hunting():  # another window / the terminal is already hunting this account
        assert h.run() == "" and h.state == "blocked" and "Another hunt" in h.message
    assert cl._account.username == "you"


def test_stop_arriving_while_the_claim_is_written_down_sends_nothing():
    h, cl, store, _, _ = make(names=("quill",), first=())
    real = store.mark

    def mark(name, status, detail="", soon=False, wait=None):
        real(name, status, detail, soon, wait)
        if status == "claiming":
            h.stop()

    store.mark = mark
    assert h.run() == "" and not [c for c in cl.calls if c[0] == "account_edit"]
    assert store.row("quill")["status"] == "possibly_available"
