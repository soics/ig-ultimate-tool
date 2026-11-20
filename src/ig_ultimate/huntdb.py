"""The username hunt's memory: one SQLite file per Instagram account.

Why SQLite and not the old JSON file: the hunt now remembers a status for tens of thousands of
names, updated every ~30 seconds, and the dashboard searches and filters them. Rewriting a
multi-megabyte JSON file on every check is slow and one crash away from losing it; SQLite (part of
Python, no extra install) writes one row inside a transaction and survives a crash mid-write.

Files are keyed by the account's numeric Instagram id, which never changes, not by its username,
which is exactly what the hunt replaces. The old username-finder.json is imported once and kept
(renamed), so going back to v1.2 only means renaming it back.
"""

from __future__ import annotations

import contextlib
import csv
import io
import itertools
import json
import os
import random
import re
import sqlite3
import string
import threading
import time
from pathlib import Path

from . import names, paths

DAY = 86400.0
STATUSES = {
    "unchecked": "Not checked yet",
    "invalid": "Not a valid username",
    "taken": "Taken (an account has it)",
    "possibly_available": "Possibly available (no account found; not proof)",
    "verified_available": "Verified available (Instagram itself confirmed it)",
    "held": "Held by Instagram (refused when claimed)",
    "unknown": "Unknown (no clear answer)",
    "rate_limited": "Rate limited (Instagram said slow down)",
    "failed_verification": "Failed verification (change didn't show on the account)",
    "claiming": "Claim in progress",
    "claimed": "Claimed (confirmed on your account)",
}
# How long a result is trusted before the name is looked at again. No evidence of change = no recheck.
RECHECK = {
    "taken": 30 * DAY,
    "held": 14 * DAY,
    "possibly_available": 3 * DAY,
    "unknown": 600.0,
    "rate_limited": 0.0,  # the hunter already waited out the backoff: same name again, first
    "failed_verification": 7 * DAY,
}
NEVER_PICK = ("invalid", "claiming", "claimed")
FIELDS = (
    "name, category, tags, score, adj, info, status, attempts, checked_at, next_at, "
    "favorite, excluded, priority, note"
)
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS candidates (
  name TEXT PRIMARY KEY, category TEXT NOT NULL, tags TEXT NOT NULL, score INTEGER NOT NULL,
  adj INTEGER NOT NULL DEFAULT 0, info TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'unchecked', attempts INTEGER NOT NULL DEFAULT 0,
  checked_at REAL, next_at REAL NOT NULL DEFAULT 0,
  favorite INTEGER NOT NULL DEFAULT 0, excluded INTEGER NOT NULL DEFAULT 0,
  priority INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS by_status ON candidates(status, excluded, next_at);
CREATE TABLE IF NOT EXISTS history (
  id INTEGER PRIMARY KEY, at REAL NOT NULL, name TEXT NOT NULL, event TEXT NOT NULL, detail TEXT NOT NULL);
"""
CORRUPT = re.compile(r"not a database|malformed|corrupt", re.I)
GEN_VERSION = "2"  # bump when a generator's output changes, so categories get topped up again
MAX_CUSTOM = 500


class HuntBusy(RuntimeError):
    """Another hunt holds this account."""


def db_path(account_id) -> Path:
    key = str(account_id or "").strip()
    if not (
        key.isascii() and key.isdigit()
    ):  # an id is digits; anything else could be a path trick or a mix-up
        raise ValueError("Unknown Instagram account id.")
    return paths.data_dir() / "hunter" / f"account-{key}.sqlite3"


class Store:
    def __init__(self, account_id):
        self.path = db_path(account_id)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()  # the hunt thread and dashboard requests share one connection
        self.repaired = ""
        self.db = None
        try:
            self._open()
        except sqlite3.DatabaseError as exc:
            if self.db:
                self.db.close()
            # "locked", "unable to open", disk errors: not damage. Never set a live file aside for those.
            if isinstance(exc, sqlite3.OperationalError) or not CORRUPT.search(str(exc)):
                raise
            # Damaged file: keep it (and its journal) for the user, start a fresh one.
            stamp = int(time.time())
            broken = self.path.with_suffix(f".corrupt-{stamp}")
            for extra in ("", "-wal", "-shm"):
                part = self.path.with_name(self.path.name + extra)
                if part.exists():
                    part.replace(broken.with_name(broken.name + extra))
            self.repaired = (
                f"The hunt's saved data was damaged. It was kept as {broken.name}; starting fresh."
            )
            self._open()

    def _open(self):
        if not self.path.exists():  # owner-only from the first byte; SQLite's journal copies these
            os.close(os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600))
        with contextlib.suppress(OSError):
            self.path.chmod(0o600)
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        if self.db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError("database disk image is malformed (quick_check failed)")
        self.db.executescript(SCHEMA)

    def close(self):
        with self.lock:
            self.db.close()

    @contextlib.contextmanager
    def hunting(self):
        """One hunt per account across every window and process (web app, terminal, a second copy).
        An OS file lock: released by the system even if the app is killed, so it can't go stale."""
        fd = os.open(self.path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise HuntBusy(
                "Another hunt is already running for this account (another window?). Nothing changed."
            ) from exc
        try:
            yield
        finally:
            os.close(fd)  # closing releases the lock

    def _read(self, sql: str, args=()) -> list:
        """Every read takes the lock too: the hunt thread may be mid-transaction on this connection."""
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    # -------------------------------------------------------------- meta
    def get(self, key: str, default: str = "") -> str:
        row = self._read("SELECT v FROM meta WHERE k=?", (key,))
        return row[0][0] if row else default

    def put(self, key: str, value) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, str(value)))

    def log(self, name: str, event: str, detail: str = "") -> None:
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO history (at, name, event, detail) VALUES (?, ?, ?, ?)",
                (time.time(), name, event, detail[:300]),
            )

    # -------------------------------------------------------------- filling the queue
    def _insert(self, rows) -> int:
        """rows: (name, category). Scores them with that category's profile. Existing names only
        gain the new category tag: their status and history are never reset."""
        profile = lambda c: c if c in names.PROFILES else "balanced"  # noqa: E731
        data = []
        for name, cat in rows:
            info = names.describe(name)
            tags = "," + ",".join(names._tags(name, cat)) + ","
            status = "invalid" if info["eligibility"] == "invalid" else "unchecked"
            data.append((name, cat, tags, names.score(name, profile(cat)), json.dumps(info),
                         status, int(names.blocked(name))))  # fmt: skip
        with self.lock, self.db:
            before = self.db.total_changes
            self.db.executemany(
                "INSERT INTO candidates (name, category, tags, score, info, status, excluded) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET tags = CASE "
                "WHEN instr(candidates.tags, ',' || excluded.category || ',') THEN candidates.tags "
                "ELSE candidates.tags || excluded.category || ',' END",
                data,
            )
            return self.db.total_changes - before

    def ensure(self, categories) -> None:
        """Generate each finite category once; the open-ended invented one is topped up in batches."""
        for cat in categories:
            if cat in ("custom",):
                continue
            if cat == "invented":
                self.top_up()
            elif self.get(f"ingested:{cat}") != GEN_VERSION:
                self._insert((n, cat) for n in names.generate(cat))
                self.put(f"ingested:{cat}", GEN_VERSION)

    def top_up(self, low: int = 200, batch: int = 500) -> None:
        left = self._read(
            "SELECT COUNT(*) FROM candidates WHERE tags LIKE '%,invented,%' AND status='unchecked'"
        )[0][0]
        if left >= low:
            return
        got = list(itertools.islice(names.invented(int(self.get("cursor:invented", "0"))), batch))
        self._insert((name, "invented") for _i, name in got)
        self.put("cursor:invented", got[-1][0] + 1)

    def add_custom(self, raw: list[str], priority: int = 1) -> dict:
        """Your own names. priority: 1 = before everything, 0 = by score, -1 = after everything."""
        priority = max(-1, min(1, int(priority)))
        good, bad = [], []
        for item in list(raw)[:MAX_CUSTOM]:
            name = names.normalize(item)
            (bad if not name or names.validate(name)[0] == "invalid" else good).append(name or str(item)[:40])
        good = list(dict.fromkeys(good))
        self._insert((n, "custom") for n in good)
        with self.lock, self.db:
            self.db.executemany(
                "UPDATE candidates SET priority=? WHERE name=?", [(priority, n) for n in good]
            )
        return {"added": good, "rejected": bad[:50], "capped": len(raw) > MAX_CUSTOM}

    # -------------------------------------------------------------- picking and recording
    @staticmethod
    def _where(cats=(), min_len=1, max_len=30, langs=(), q="", status="", fav=None, excl=None):
        sql, args = ["length(name) BETWEEN ? AND ?"], [int(min_len), int(max_len)]
        if cats:
            sql.append("(" + " OR ".join("tags LIKE ?" for _ in cats) + ")")
            args += [f"%,{c},%" for c in cats]
        if langs:
            sql.append("(" + " OR ".join("tags LIKE ?" for _ in langs) + ")")
            args += [f"%,lang:{x},%" for x in langs]
        if q:
            sql.append("(name LIKE ? ESCAPE '\\' OR info LIKE ? ESCAPE '\\')")
            like = "%" + re.sub(r"([%_\\])", r"\\\1", q[:40].lower()) + "%"
            args += [like, like]
        if status:
            sql.append("status = ?")
            args.append(status)
        if fav is not None:
            sql.append("favorite = ?")
            args.append(int(fav))
        if excl is not None:
            sql.append("excluded = ?")
            args.append(int(excl))
        return " AND ".join(sql), args

    def pick(self, cats, min_len=1, max_len=30, langs=(), skip=(), now=None) -> str | None:
        """The next name worth checking: favourites, then your priority, then retries of names that got
        no answer, then the best score. Names with a trusted result wait for their recheck time.
        Starred names and custom names marked "first" are tried in every hunt, whatever its style."""
        where, args = self._where(cats, min_len, max_len, langs)
        never = ",".join("?" * len(NEVER_PICK))
        skip = list(skip)
        not_these = f"AND name NOT IN ({','.join('?' * len(skip))})" if skip else ""
        sql = (
            f"SELECT name FROM candidates WHERE (({where}) OR favorite=1 OR priority>0) "  # noqa: S608
            f"AND excluded=0 AND status NOT IN ({never}) "
            f"AND next_at <= ? {not_these} "
            "ORDER BY favorite DESC, priority DESC, "
            "status IN ('rate_limited', 'unknown', 'possibly_available') DESC, "
            "score + adj DESC, rowid LIMIT 1"
        )
        row = self._read(sql, [*args, *NEVER_PICK, now or time.time(), *skip])
        return row[0][0] if row else None

    def row(self, name: str) -> dict | None:
        r = self._read(f"SELECT {FIELDS} FROM candidates WHERE name=?", (name,))  # noqa: S608
        return _row(r[0]) if r else None

    def mark(self, name: str, status: str, detail: str = "", soon: bool = False) -> None:
        """Record one result (and its history line) in a single transaction. soon = no recheck wait."""
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}")
        now = time.time()
        with self.lock, self.db:
            r = self.db.execute("SELECT attempts FROM candidates WHERE name=?", (name,)).fetchone()
            tries = (r[0] if r else 0) + (status in ("unknown", "rate_limited"))
            wait = 0.0 if soon else RECHECK.get(status, 0.0)
            if status == "unknown" and tries >= 3:
                wait = DAY  # three times no answer: leave it a day instead of hammering it
            self.db.execute(
                "INSERT INTO candidates (name, category, tags, score, status) "
                "VALUES (?, 'custom', ',custom,', 0, ?) "
                "ON CONFLICT(name) DO UPDATE SET status=excluded.status",
                (name, status),
            )
            self.db.execute(
                "UPDATE candidates SET attempts=?, checked_at=?, next_at=?, note=? WHERE name=?",
                (0 if status in ("taken", "held", "possibly_available") else tries, now, now + wait,
                 detail[:200], name),
            )  # fmt: skip
            self.db.execute(
                "INSERT INTO history (at, name, event, detail) VALUES (?, ?, ?, ?)",
                (now, name, status, detail[:300]),
            )

    def interrupted_claims(self) -> list[str]:
        """Names whose claim started but whose outcome was never recorded (crash, power cut)."""
        return [r[0] for r in self._read("SELECT name FROM candidates WHERE status='claiming'")]

    # -------------------------------------------------------------- feedback
    def feedback(self, name: str, action: str) -> None:
        """favorite / unfavorite / exclude / include, and like / dislike, which also nudge every name in
        the same category up or down a little (at most 6 points), so rankings learn your taste."""
        sets = {
            "favorite": "favorite=1",
            "unfavorite": "favorite=0",
            "exclude": "excluded=1",
            "include": "excluded=0",
            "like": "favorite=1",
            "dislike": "excluded=1",
        }
        if action not in sets:
            raise ValueError("Unknown feedback.")
        row = self.row(name)
        if row is None:
            raise ValueError("That name isn't in the list.")
        if action == "include" and names.blocked(name):
            raise ValueError("That name is blocked (offensive, impersonation or invalid).")
        with self.lock, self.db:
            self.db.execute(f"UPDATE candidates SET {sets[action]} WHERE name=?", (name,))  # noqa: S608
            if action in ("like", "dislike"):
                cat = row["category"]
                adj = max(-6, min(6, int(self.get(f"adj:{cat}", "0")) + (1 if action == "like" else -1)))
                self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (f"adj:{cat}", str(adj)))
                self.db.execute("UPDATE candidates SET adj=? WHERE category=?", (adj, cat))
            self.db.execute(
                "INSERT INTO history (at, name, event, detail) VALUES (?, ?, 'feedback', ?)",
                (time.time(), name, action),
            )

    # -------------------------------------------------------------- reading
    def query(self, sort="score", limit=50, offset=0, **filters) -> dict:
        where, args = self._where(**filters)
        order = {
            "score": "score + adj DESC, rowid",
            "name": "name",
            "recent": "checked_at DESC NULLS LAST, name",
            "length": "length(name), score DESC",
        }.get(sort, "score + adj DESC, rowid")
        total = self._read(f"SELECT COUNT(*) FROM candidates WHERE {where}", args)[0][0]  # noqa: S608
        rows = self._read(
            f"SELECT {FIELDS} FROM candidates WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",  # noqa: S608
            [*args, max(1, min(int(limit), 100_000)), max(0, int(offset))],
        )
        return {"total": total, "rows": [_row(r) for r in rows]}

    def counts(self) -> dict:
        out = {k: 0 for k in STATUSES}
        for status, n in self._read("SELECT status, COUNT(*) FROM candidates GROUP BY status"):
            out[status] = n
        out["favorites"] = self._read("SELECT COUNT(*) FROM candidates WHERE favorite=1")[0][0]
        out["excluded"] = self._read("SELECT COUNT(*) FROM candidates WHERE excluded=1")[0][0]
        return out

    def history(self, limit=100, events=()) -> list[dict]:
        sql, args = "SELECT at, name, event, detail FROM history", []
        if events:
            sql += f" WHERE event IN ({','.join('?' * len(events))})"
            args = list(events)
        rows = self._read(sql + " ORDER BY id DESC LIMIT ?", [*args, int(limit)])
        return [dict(r) for r in rows]

    def export(self, fmt="json", **filters) -> str:
        """The candidate list only (no account details, no session data)."""
        rows = self.query(limit=100_000, **filters)["rows"]
        if fmt == "json":
            return json.dumps(rows, indent=1)
        out = io.StringIO()
        cols = ["name", "category", "score", "status", "checked_at", "attempts", "favorite", "excluded",
                "priority", "note", "langs", "meaning"]  # fmt: skip
        w = csv.DictWriter(out, cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            senses = r["info"].get("senses", [])
            w.writerow({**r, "langs": " ".join(r["info"].get("langs", [])),
                        "meaning": "; ".join(f"{s['lang']}: {s['meaning']}" for s in senses)})  # fmt: skip
        return out.getvalue()

    # -------------------------------------------------------------- v1 migration
    def migrate_v1(self) -> str:
        """Import username-finder.json (v1.2) once: names v1 already checked keep their result instead of
        being checked again. The old file is renamed, not deleted (rename it back to roll back)."""
        old_file = paths.data_dir() / "username-finder.json"
        if self.get("migrated_v1") or not old_file.exists():
            return ""
        old = paths.read_json(old_file, None)  # a broken file is set aside as .corrupt by read_json
        if not isinstance(old, dict):
            self.put("migrated_v1", time.time())
            return "The old username finder file was unreadable; it was kept as username-finder.corrupt."
        length_s, _, mine_s = str(old.get("key", "all:")).partition(":")
        legacy = _v1_queue(int(length_s) if length_s.isdigit() else None, [m for m in mine_s.split(",") if m])
        pos = max(0, int(old.get("pos", 0) or 0))
        done = legacy if int(old.get("round", 1) or 1) > 1 else legacy[:pos]
        held = {str(n) for n in old.get("held", []) if isinstance(n, str)}
        self._insert((n, "ultra" if len(n) <= 3 else "custom") for n in {*done, *held})
        now = time.time()
        with self.lock, self.db:
            # v1 recorded "not claimable" but not why, so these are rechecked after a week, not 30 days
            self.db.executemany(
                "UPDATE candidates SET status='taken', checked_at=?, next_at=?, note='checked by v1.2' "
                "WHERE name=? AND status='unchecked'",
                [(now, now + 7 * DAY, n) for n in done if n not in held],
            )
            self.db.executemany(
                "UPDATE candidates SET status='held', checked_at=?, next_at=?, note='held (v1.2)' "
                "WHERE name=?",
                [(now, now + RECHECK["held"], n) for n in held],
            )
            detail = f"{len(done)} checked, {len(held)} held" + (
                f", won @{old['won']}" if old.get("won") else ""
            )
            self.db.execute(
                "INSERT INTO history (at, name, event, detail) VALUES (?, '', 'migrated_v1', ?)",
                (now, detail),
            )
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('migrated_v1', ?)", (str(now),))
        old_file.replace(old_file.with_name("username-finder.v1-migrated.json"))
        return f"Imported your earlier hunt ({detail}). Those names won't be checked again for now."


def _row(r) -> dict:
    d = dict(r)
    d["info"] = json.loads(d["info"] or "{}")
    d["tags"] = [t for t in d["tags"].split(",") if t]
    d["label"] = STATUSES.get(d["status"], d["status"])
    return d


# ------------------------------------------------------------------ v1.2 queue (migration only)
# Kept byte-for-byte so a v1 position ("pos") still points at the same names.
_V1_WORDS = {
    2: """ah am an as at aw ax be by do eh ex go ha he hi hm id if in is it jo ka la lo ma me mi mu my
        no nu of oh ok om on op or ox oy pa pi qi re so ta ti to uh um un up us we xi ya ye yo zo""",
    3: """ace act air ale alt amp ape arc ark art ash awe axe bay bee bit boa bog bot bow box bud bug
        cab cam cap cob cog cow cub cue cup dab dew dim din dot dub dun dye eel elf elk elm emu era eve
        fad fan fax fez fig fin fir fix fly fob foe fog fox fry fur gem gig gnu goo gum gym hex hog hop
        how hub hue hum ice icy ink inn ion ivy jab jam jar jaw jet jig jog joy jug keg key kin kit lab
        lag lap lax lid lit lob log lot lux map mew mix mob mod moo mud mug nab nap neo net nib nil nod
        nox nub nut oak oar oat odd ode ohm oil orb ore owl pad pal paw pax pea peg pew pod pop pry pug
        pun pup rad ram ray rex rib rig rim rot row rue rum rye sap saw sky sly sob sod soy spa spy sub
        sum tab tag tan tar tea tic tin tip toe ton tow toy tux urn van vat vex vim vow wax web wig wit
        wok wow yak yam yen yew yin zap zen zip zoo""",
    4: """aeon apex aqua aria aura axis bane bard bask bevy blip bloc bolt brim brio byte calm chic chum
        clad coda coil cove crux cusp czar daze deft drip dune dusk echo edge epic etch fern fizz flux
        foam fray fume fuse gale gaze glib glow goat gust halo haze hymn iris isle jade jinx jolt keen
        kiln kite knot koan lark larp lava lens lilt lore lull lume lure lush lynx mesa mint mire mist
        moss muse myth neon node nova onyx opal oryx pith plum pyre quay rift rime rizz rune sage saga
        silk silt slab sloe soot spry stoa sumo swag tarn teal tide tome tusk vale vamp veil vibe void
        volt wade waif wane wasp whim wick wisp yeti yoke zeal zest zinc zing""",
    5: """amber arbor aspen azure basil bayou blaze bliss bloom brine cadet cairn cedar chaos chime
        cinch civic clove cobra comet coral crest crisp crypt cumin cyber delta dingo ditto dogma drift
        druid ember epoch ether fable fjord flair flint flora froth gecko ghost glade gleam glyph gnome
        grail gusto haiku haven helix heron hydra igloo inlet ivory jewel joust karma kayak koala larps
        latch lilac lotus lucid lunar lyric macaw magma mango manor maple matte mirth mocha nomad oasis
        olive omega optic orbit otter oxide pearl pecan petal pixel plume prism pulse quail qualm quark
        query quest quill quilt quirk radar raven realm relic rhino rogue runic sable sauce sepia shire
        siren slate sonic spire squid stoic storm swirl synth tango tapir terra thorn thyme tiara tonic
        totem trove tulip umbra vapor vault venom verge vigil vinyl viper vivid vogue waltz whirl xenon
        yacht zebra zesty""",
    6: """quantum larpin larping everclear aether amulet arcade astral aurora banshee bazaar bonsai
        cactus candor cipher citrus cobalt cosmic coyote cypher dazzle eclipse elixir enigma falcon
        fathom frenzy galaxy garnet geyser glacier glitch gothic hermit hollow indigo jasper jigsaw
        jovial kismet lagoon lantern legend lichen lowkey marble meadow mirage mosaic myriad nebula
        nectar nimbus obsidian octane orchid oracle paprika phantom phoenix plasma prairie quartz quasar
        quiver ravine raptor reverie riddle saffron salvo sapphire scarab sequin sherpa shogun sierra
        solace sphinx spiral static stellar summit tandem thicket thistle thunder timber topaz torque
        toucan trident truffle tundra turbo tuxedo unicorn utopia velvet verdant vertex vessel vintage
        vortex voyage walrus wander willow wizard wombat yonder zealot zenith zephyr zigzag""",
}


def _v1_combos(n: int) -> list[str]:
    letters, both = string.ascii_lowercase, string.ascii_lowercase + string.digits
    out = [""]
    for i in range(n):
        out = [p + c for p in out for c in (letters if i == 0 else both)]
    plain, mixed = [x for x in out if x.isalpha()], [x for x in out if not x.isalpha()]
    rng = random.Random(f"ig-ultimate-{n}")
    rng.shuffle(plain)
    rng.shuffle(mixed)
    return plain + mixed


def _v1_queue(length: int | None = None, mine=()) -> list[str]:
    w = {k: v.split() for k, v in _V1_WORDS.items()}
    main = [*w[2], *_v1_combos(2), *w[3], *_v1_combos(3), *w[4], *w[5], *w[6]]
    rare = [*w[4], *w[5], *w[6]]
    if length:
        keep = (lambda x: len(x) >= 6) if length >= 6 else (lambda x: len(x) == length)
        main, rare = [x for x in main if keep(x)], [x for x in rare if keep(x)]
    out, seen, ok = [], set(), re.compile(r"^[a-z0-9._]{1,30}$")

    def add(name: str) -> None:
        name = name.strip().lstrip("@").lower()
        if name and name not in seen and ok.match(name):
            seen.add(name)
            out.append(name)

    for name in mine:
        add(name)
    lane = iter(rare)
    for i, name in enumerate(main):
        if i % 3 == 2:
            add(next((x for x in lane if x not in seen), ""))
        add(name)
    return out
