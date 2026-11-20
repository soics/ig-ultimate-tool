"""The hunt's memory: per account, crash-safe, never re-checks names without a reason, and imports the
v1.2 progress file once without losing it."""

import json
import os
import sqlite3
import sys
import time

import pytest

from ig_ultimate import huntdb, paths


def test_order_is_favourites_then_priority_then_retries_then_score():
    s = huntdb.Store("1")
    s.add_custom(["lumen", "zzxq", "kairos", "bloomy"], 0)
    assert s.pick(["custom"]) == "lumen"  # best score
    s.mark("lumen", "taken")
    s.mark("bloomy", "unknown", "odd reply")  # no answer: retried before fresh names
    assert s.pick(["custom"]) == "kairos"  # unknown waits 10 minutes first...
    s.db.execute("UPDATE candidates SET next_at=0 WHERE name='bloomy'")
    assert s.pick(["custom"]) == "bloomy"  # ...then comes before fresh names
    s.add_custom(["zzxq"], 1)
    assert s.pick(["custom"]) == "zzxq"  # "try these first"
    s.feedback("kairos", "favorite")
    assert s.pick(["custom"]) == "kairos"  # starred beats everything
    s.feedback("kairos", "exclude")
    assert s.pick(["custom"]) != "kairos"  # hidden never comes up
    assert s.pick(["custom"], skip=["zzxq"]) == "bloomy"


def test_results_are_trusted_until_their_recheck_time():
    s = huntdb.Store("1")
    s.add_custom(["lumen"], 0)
    s.mark("lumen", "taken")
    assert s.pick(["custom"]) is None
    assert s.pick(["custom"], now=time.time() + huntdb.RECHECK["taken"] + 1) == "lumen"
    s.mark("lumen", "claimed")
    assert s.pick(["custom"], now=time.time() + 10**9) is None  # claimed/invalid: never again
    for _ in range(3):
        s.mark("lumen", "unknown")
    assert s.row("lumen")["next_at"] > time.time() + 0.9 * huntdb.DAY  # 3x no answer: leave it a day


def test_fresh_names_come_before_rechecks_of_names_that_failed():
    s = huntdb.Store("1")
    s.add_custom(["lumen", "zzxq"], 0)  # lumen scores higher
    s.mark("lumen", "taken")
    later = time.time() + huntdb.RECHECK["taken"] + 1
    assert s.pick(["custom"], now=later) == "zzxq"  # its recheck is due, but a never-tried name goes first
    s.mark("zzxq", "held")
    assert s.pick(["custom"], now=later + huntdb.RECHECK["held"]) == "lumen"  # only once nothing new is left


def test_a_lookup_that_finds_nobody_keeps_the_no_answer_count():
    s = huntdb.Store("1")
    s.add_custom(["lumen"], 0)
    for _ in range(3):  # what a claim that keeps failing looks like: free on lookup, refused on claim
        s.mark("lumen", "possibly_available", soon=True)
        s.mark("lumen", "unknown")
    assert s.row("lumen")["next_at"] > time.time() + 0.9 * huntdb.DAY


def test_old_already_exists_refusals_are_relabelled_held_on_open():
    s = huntdb.Store("1")
    s.add_custom(["folio"], 0)
    s.mark("folio", "unknown", "Instagram refused the change without saying why: "
           "{'errors': ['A user with that username already exists.']}")  # fmt: skip
    s.close()
    s = huntdb.Store("1")
    assert s.row("folio")["status"] == "held" and s.pick(["custom"]) is None


def test_starred_and_first_names_join_every_hunt_style():
    s = huntdb.Store("1")
    s.ensure(["dark"])
    s.add_custom(["quill"], 1)
    assert s.pick(["dark"], 3, 10) == "quill"
    with pytest.raises(ValueError):
        s.mark("quill", "free!")  # only known statuses


def test_custom_names_are_validated_capped_and_never_reset_progress():
    s = huntdb.Store("1")
    out = s.add_custom(["@Élan", "a..b", "ok_name", "élan"], 0)
    assert out["added"] == ["elan", "ok_name"] and out["rejected"] == ["a..b"]
    s.mark("elan", "held")
    s.add_custom(["elan"], 1)
    assert s.row("elan")["status"] == "held"  # adding it again doesn't make it "unchecked"
    assert s.add_custom([f"n{i}" for i in range(600)], 0)["capped"]


def test_likes_teach_the_category_a_little():
    s = huntdb.Store("1")
    s.ensure(["dark"])
    for n in ("raven", "crypt", "dusk", "sable", "wraith", "ember", "abyss", "gloom"):
        s.feedback(n, "like")
    assert s.get("adj:dark") == "6" and s.row("onyx")["adj"] == 6  # capped at +6
    with pytest.raises(ValueError):
        s.feedback("not-a-name", "like")
    with pytest.raises(ValueError):
        s.feedback("raven", "explode")


def test_accounts_never_share_a_hunt():
    a, b = huntdb.Store("111"), huntdb.Store("222")
    a.add_custom(["quill"], 0)
    a.mark("quill", "taken")
    assert b.row("quill") is None and a.path != b.path
    for bad in ("", "abc", "../1", "1/2", None):
        with pytest.raises(ValueError):
            huntdb.db_path(bad)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_hunt_file_is_owner_only():
    s = huntdb.Store("1")
    assert os.stat(s.path).st_mode & 0o077 == 0


def test_corrupt_database_is_kept_aside_and_a_fresh_one_starts(home):
    path = huntdb.db_path("1")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"this is not a database" * 100)
    s = huntdb.Store("1")
    assert "damaged" in s.repaired and list(path.parent.glob("*.corrupt-*"))
    s.add_custom(["quill"], 0)
    assert s.row("quill")


def test_interrupted_claim_survives_a_crash():
    s = huntdb.Store("1")
    s.add_custom(["quill"], 0)
    s.mark("quill", "claiming", "sent")
    s.close()  # the process dies here
    assert huntdb.Store("1").interrupted_claims() == ["quill"]


def test_v1_progress_is_imported_once_and_kept_for_rollback():
    old = paths.data_dir() / "username-finder.json"
    q = huntdb._v1_queue(None, ["myidea"])
    paths.write_json(old, {"key": "all:myidea", "pos": 10, "round": 1, "held": ["zen"], "won": ""})
    s = huntdb.Store("1")
    said = s.migrate_v1()
    assert "10 checked, 1 held" in said
    assert all(s.row(n)["status"] in ("taken", "held") for n in q[:10])
    assert s.row(q[10]) is None or s.row(q[10])["status"] == "unchecked"  # not checked by v1
    assert s.row("zen")["status"] == "held"
    assert not old.exists() and json.loads(old.with_name("username-finder.v1-migrated.json").read_text())
    assert s.migrate_v1() == ""  # once only


def test_broken_v1_file_is_not_fatal():
    (paths.data_dir() / "username-finder.json").write_text("{nope", encoding="utf-8")
    assert "unreadable" in huntdb.Store("1").migrate_v1()
    assert (paths.data_dir() / "username-finder.corrupt").exists()


def test_search_filter_and_export_hold_no_account_data():
    s = huntdb.Store("4242")
    s.ensure(["multi"])
    r = s.query(cats=["multi"], q="moon", limit=100)
    assert r["total"] >= 5 and all("moon" in json.dumps(x["info"]) or "moon" in x["name"] for x in r["rows"])
    assert s.query(q="%_", cats=["multi"])["total"] == 0  # wildcards are literal
    assert s.query(cats=["multi"], langs=["ja"])["total"] >= 20
    csv = s.export("csv", cats=["multi"], langs=["ja"])
    assert csv.startswith("name,category,score,status") and "sora" in csv and "4242" not in csv
    assert isinstance(json.loads(s.export("json", cats=["multi"])), list)


def test_two_threads_share_the_store_safely():
    import threading

    s = huntdb.Store("1")
    s.add_custom([f"n{i}" for i in range(200)], 0)
    errors = []

    def work(lo):
        try:
            for i in range(lo, lo + 100):
                s.mark(f"n{i}", "taken")
        except sqlite3.Error as exc:
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(x,)) for x in (0, 100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and s.counts()["taken"] == 200


def test_a_locked_database_is_never_mistaken_for_a_damaged_one(monkeypatch):
    s = huntdb.Store("1")
    s.add_custom(["quill"], 0)
    s.close()

    def locked(self):
        self.db = sqlite3.connect(":memory:")
        raise sqlite3.OperationalError("database is locked")

    real_open = huntdb.Store._open
    monkeypatch.setattr(huntdb.Store, "_open", locked)
    with pytest.raises(sqlite3.OperationalError):
        huntdb.Store("1")
    monkeypatch.setattr(huntdb.Store, "_open", real_open)  # not undo(): that also drops the temp home
    assert huntdb.Store("1").row("quill") and not list(huntdb.db_path("1").parent.glob("*.corrupt-*"))
