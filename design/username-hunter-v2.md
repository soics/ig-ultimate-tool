# Username Hunter v2: design, audit and decisions

Branch `feat/username-hunter-v2`, built on v1.2.0 (`64c90b8`).

## 1. Baseline audit (v1.2.0)

| # | Finding in v1.2 | Effect | Fixed by |
|---|---|---|---|
| 1 | Any failed lookup (`except Exception`) still advanced `pos` | The name counted as checked and was never retried | Failed lookups are `unknown` (retried after 10 min, then daily); offline errors record nothing and retry the same name |
| 2 | Every non-throttle `ClientError` from `account_edit` was recorded as "held" | Once Instagram's username-change limit kicked in, every later free name was buried as held | Claim errors are classified by message: held / invalid / change-limit (stops the hunt) / unknown |
| 3 | Pressing Stop during the 180 s claim gap returned "not claimed" and advanced `pos` | A possibly-free name was skipped for good | The lookup result is recorded first; an interrupted claim leaves it at the front of the queue |
| 4 | One `username-finder.json` for every account | Account B resumed account A's position | One SQLite file per numeric Instagram account id |
| 5 | The web hunt was a daemon thread with no presence check; `serve()` never stopped it | It kept hunting (and could rename the account) after the page closed | Dashboard heartbeat with sticky server-side expiry; `serve()` stops and joins the hunter |
| 6 | Success was judged from `account_edit`'s return value | A 200 reply that didn't apply would have been reported as success | The account is read back (`account_info`) and both id and username must match |
| 7 | Exception text went into logs unfiltered | Possible cookie or token leakage into the dashboard | `snipe.redact()` / `clean()` scrub cookies, bearer/IGT tokens and passwords |
| 8 | POSTs were checked for token + Host + JSON only | Fine, but with no defense in depth against cross-site posts | `Origin` must be this app's own origin when present |
| 9 | Ordering was 2-letter words, then ~35k combos, with a rare word every 3rd check | Quality-blind ordering | A deterministic OG score with per-style profiles |

Kept as they were (already sound): the one-time boot link exchanged for an HttpOnly `SameSite=Strict` cookie, the `X-Token` header, the Host allowlist (DNS rebinding), 0700/0600 app data, and account-level mutual exclusion (`busy()` + `start_lock`).

## 2. Platform facts that shaped the design

- Meta's official Instagram API exposes `username` as a **read** field on the IG User node. It has no endpoint to check whether a name is free, and none to change one.
- Instagram's Terms prohibit accessing or collecting information by automated means without permission. Like the rest of this app, the hunter uses instagrapi, an unofficial client of the private app API. The dashboard says so on the Security tab.
- Username rules: 1-30 characters from `a-z 0-9 . _`. That much is documented. The rules on periods (not first, not last, never two in a row) are reported by third parties; they are enforced here and labelled as such. Numbers-only and 1-2 character names are marked `unverified`: only a claim attempt can tell.

Consequences:
- **Default = manual claiming.** The hunter finds names and you claim them in the Instagram app. *I changed it: check my account* then confirms by reading the account.
- **Automatic claiming** is the v1 behaviour, kept but not expanded: off by default, authorized per hunt and per account (tick the box + type the username), held in memory only, bound to the numeric account id, and re-checked against the session before every claim.
- No rate-limit evasion, no account rotation, no disguised traffic. Pace stays at ~120 lookups an hour and one claim every 3 minutes, with exponential back-off.

## 3. Architecture

```
names.py (offline)          huntdb.Store (per account)         snipe.py
  lexicon + generators  ->    dedupe, queue, results,     <->    InstagrapiVerifier (replaceable)
  validate + score            history, feedback, export          Hunter (thread) + Presence (heartbeat)
                                                                  ^ webapp.py /api/hunt/*  ^ cli.py
```

### Names (`names.py`, `data/lexicon.txt`)
- **Lexicon:** 1,590 merged entries: short 506, premium 524, rare 162 (with glosses), multilingual 296 across 18 languages, luxury 146, dark 119. Each entry keeps its language, meaning, original script or spelling, an ambiguity note, a tier, source and licence (project-curated, MIT).
- **Investigated, not bundled:** wordfreq (its data is CC-BY-SA 4.0, share-alike, and would bring several dependencies) and CMUdict (English-only pronunciations, ~3 MB).
- **Generators:**
  - Ultra OG: all 1-3 character names starting with a letter, 34,658 of them.
  - Structured alphanumeric: 16,000 patterns (`x7`, `x77`, `ok7`, `k9x`, `zen7`).
  - Invented: an open-ended stream of pronounceable syllable names (first-syllable clusters only, no dictionary words, a pronounceability score of at least 85). It is pulled in batches of 500 from a resumable cursor.
  - All orders are seeded, so they are the same on every run.
- **Score:** 9 components (length, visual, pronounce, memorable, meaning, rarity, clarity, culture, brand), weighted per profile (10 profiles), with the full breakdown shown. Offensive names score 0 and impersonation names at most 5; both are never auto-claimed. Brands are flagged and lose 20 %. It is deterministic: same name and profile, same score.
- **Benchmark** (`tests/test_names.py`): excellent names score 86-96, mediocre 55-84, poor 22-49. The groups don't overlap, and every excellent name beats every poor one.

### Store (`huntdb.py`): why SQLite
The hunt now tracks a status per name for tens of thousands of names, updated every ~30 s, and the dashboard searches and filters them. JSON would rewrite a multi-MB file on every check, with no per-row atomicity. SQLite is in the standard library (no new dependency) and gives one transaction per result, WAL with `synchronous=FULL`, `quick_check` on open, and indexes.
- **File:** `hunter/account-<numeric id>.sqlite3`, created 0600 before SQLite opens it. A corrupt file is renamed `.corrupt-<time>` and a fresh one is started.
- **Statuses:** unchecked, invalid, taken, possibly_available, verified_available (reserved for an integration that can verify without claiming), held, unknown, rate_limited, failed_verification, claiming, claimed.
- **Recheck windows:**

  | Status | Rechecked after |
  |---|---|
  | taken | 30 days |
  | held | 14 days |
  | possibly available | 3 days |
  | failed verification | 7 days |
  | unknown | 10 minutes, then 1 day after 3 tries |
  | invalid, claimed | never |

- **Queue order:** starred → custom "first" → retries → score + learned adjustment. Starred and "first" names join every style.
- **Feedback:** like/dislike also nudge the whole category by ±1 point, capped at ±6.
- **v1 migration:** names v1 already checked (`pos`, or every name when `round` > 1) become `taken`, rechecked after 7 days because v1 didn't record why. The held list stays `held`. The old file is renamed `username-finder.v1-migrated.json`.
- **Rollback:** rename that file back to `username-finder.json` and run v1.2. It ignores the `hunter/` folder.

### Hunter lifecycle (`snipe.py`)
- **Start** reads the account from Instagram (id + username), creates a `Presence` and starts one thread. `start_lock` + `busy()` prevent duplicate or overlapping hunts and clashes with other tools.
- **Pause** makes no requests at all. **Stop** takes effect within about a second, because every wait is sliced into 1 s steps.
- Before every request it checks: not stopped, not paused, dashboard alive.
- **Claim:**
  1. Record `claiming` (write-ahead).
  2. Check the session's account id against the authorized one.
  3. Send `account_edit`.
  4. Read the account back. Only an id + username match is `claimed`, and the hunt ends.
  5. If the account can't be read back, the hunt stops rather than risk a second rename.
- **Crash recovery:** a `claiming` row found at the next start is settled by reading the account before anything else.

## 4. What happens when the dashboard closes

1. Every tab runs a Web Worker that posts `/api/hunt/beat` every 5 s. Workers are not throttled like background-tab timers, so switching tabs or minimizing is fine.
2. On `pagehide` the page sends a keepalive *goodbye*. If no other tab is open, the hunt expires 5 s later unless a reload checks back in.
3. If heartbeats simply stop (browser crash, laptop sleep, network gone), the server expires the session after the timeout: 30 s by default, adjustable 10-300 on the Security tab. It uses wall-clock time, so a sleeping laptop counts as silence.
4. Expiry is sticky: a late heartbeat gets `alive: false`, and the hunt never restarts by itself. A request already sent finishes and its result is written down; no new request starts.
5. Server exit (Ctrl+C) stops and joins the hunter for up to 10 s. A killed process leaves at most one `claiming` row, which the next start settles.

## 5. Tests

`pytest`: 126 passed (86 at baseline).
- `test_names.py`: benchmark, determinism, risk flags, transliteration, generators.
- `test_huntdb.py`: ordering, recheck windows, per-account isolation, 0600 permissions, corrupt-DB recovery, crash survival, v1 migration and broken v1 file, search escaping, export contents, thread safety.
- `test_snipe.py`: claim safety, every error class, verify-after-claim, recovery, lifecycle, presence, redaction.
- `test_webapp.py`: authorization, heartbeat expiry, goodbye vs reload, pause/resume/restart, Origin check, endpoints, account isolation, migration, logout.
- `test_cli.py`: the terminal finder.

A real-Chromium run (preview account) checked every tab, keyboard tab navigation, no console errors, no page-level horizontal scroll at 390 px, and that the hunt stops by itself after the tab closes.

## 6. Known limits

- "Possibly available" is the best a lookup can say. Only Instagram accepting the change proves availability.
- The offensive-word filter is English-centric plus a few known cases. Invented names in other languages may still collide with slang.
- Meanings are curated, standard dictionary senses; there are no pronunciations yet (the field is not filled).
- Starred names and the claim workflow depend on Instagram's private API behaving as today; instagrapi is pinned (3.2.19).
- There is no independent background mode. That would need its own explicit opt-in, controls and authorization, and was deliberately not built.
