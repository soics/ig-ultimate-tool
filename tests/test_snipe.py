import threading

from instagrapi import exceptions as ig_errors

from ig_ultimate import snipe
from ig_ultimate.fake import FakeClient


def finder(cl, mine=(), length=None, me="you"):
    waits, logs = [], []
    f = snipe.Finder(cl, logs.append, threading.Event(), me, length, list(mine), wait=waits.append)
    return f, waits, logs


def test_hunt_order_is_shortest_first_with_rare_words_mixed_in():
    q = snipe.queue()
    assert q[:2] == ["ah", "am"]  # 2-letter words first
    assert q[2] in snipe.words(4) + snipe.words(5) + snipe.words(6)  # every third: a rare word
    assert len(q) == len(set(q)) and all(snipe.OK_NAME.match(x) for x in q)
    first3 = q.index("ace")
    assert all(len(x) == 2 or x in snipe.WORDS[4] + snipe.WORDS[5] + snipe.WORDS[6] for x in q[:first3])
    assert {"pvp", "qc1", "quantum", "larpin"} <= set(q)
    assert all(x[0].isalpha() for x in snipe.combos(3)) and len(snipe.combos(3)) == 26 * 36 * 36
    assert snipe.combos(2) == snipe.combos(2)  # same order every run, so it can resume
    assert {len(x) for x in snipe.queue(3)} == {3}
    assert min(len(x) for x in snipe.queue(6)) == 6
    assert snipe.queue(mine=["@MyIdea", "bad name!"])[0] == "myidea"  # yours first; invalid ones dropped


def test_held_name_is_skipped_and_the_first_free_one_is_claimed():
    cl = FakeClient()
    f, waits, logs = finder(cl, mine=["taken1", "zen", "quill", "never"])
    assert f.run() == "quill" and cl._account.username == "quill"
    assert f.state["held"] == ["zen"] and "won" in snipe.paths.read_json(snipe.state_file(), {})
    assert any("GOT IT" in s for s in logs)
    # two claim attempts (zen, quill) are spaced at least CLAIM_GAP apart
    assert sum(waits) >= snipe.CLAIM_GAP


def test_slow_down_pauses_and_retries_the_same_name():
    cl = FakeClient()
    real = cl.user_info_by_username_v1
    seen = []

    def flaky(name):
        seen.append(name)
        if len(seen) == 1:
            raise ig_errors.ClientThrottledError("429")
        return real(name)

    cl.user_info_by_username_v1 = flaky
    f, waits, _ = finder(cl, mine=["quill"])
    assert f.run() == "quill" and seen[:2] == ["quill", "quill"] and snipe.BACKOFF[0] in waits


def test_429_while_claiming_is_not_mistaken_for_held():
    cl = FakeClient()
    tries = []

    def edit(**data):
        tries.append(data["username"])
        if len(tries) == 1:
            raise ig_errors.ClientThrottledError("429")
        cl._account.username = data["username"]
        return cl._account

    cl.account_edit = edit
    f, _, _ = finder(cl, mine=["quill"])
    assert f.run() == "quill" and tries == ["quill", "quill"] and f.state["held"] == []


def test_instagram_challenge_stops_the_hunt():
    cl = FakeClient()

    def challenge(name):
        raise ig_errors.ChallengeRequired("challenge")

    cl.user_info_by_username_v1 = challenge
    f, _, logs = finder(cl, mine=["quill"])
    assert f.run() == "" and any("confirm it's you" in s for s in logs)


def test_resumes_where_it_stopped_and_skips_your_own_name():
    cl = FakeClient()
    cl.free_names = set()
    f, _, _ = finder(cl, mine=["you", "a1x", "b2y"])
    checked = []
    real = cl.user_info_by_username_v1
    cl.user_info_by_username_v1 = lambda n: (checked.append(n), real(n))[1]
    calls = {"n": 0}

    def stop_after_two(_s):
        calls["n"] += 1
        if calls["n"] == 2:
            f.stop.set()

    f.wait = stop_after_two
    assert f.run() == "" and checked == ["a1x", "b2y"]  # never looks up itself
    again, _, _ = finder(cl, mine=["you", "a1x", "b2y"])
    assert again.state["pos"] == 3
    other, _, _ = finder(cl, mine=["different"])
    assert other.state["pos"] == 0  # a different hunt starts from the top
