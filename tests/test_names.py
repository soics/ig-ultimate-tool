"""Name intelligence: offline, deterministic, and the score ranks good names above bad ones."""

import itertools
import statistics

from ig_ultimate import names

# Benchmark: hand-labelled usernames. The score must order the groups, not just individual names.
EXCELLENT = "lux onyx aether nyx sora velvet halcyon zen umbra noir arete kairos lumen".split()
MEDIOCRE = "lux7 bloomy x7k tm9 velvet_7 ab3 glimz zentop".split()
POOR = "xq_9__z lll1 a.b_c1 zzxq q0o0 rnrn1 x_x_x_9".split()


def test_benchmark_ranks_excellent_over_mediocre_over_poor():
    ex, md, pr = ([names.score(n) for n in g] for g in (EXCELLENT, MEDIOCRE, POOR))
    assert statistics.mean(ex) > statistics.mean(md) + 10 > statistics.mean(pr) + 20
    assert min(ex) > max(md) and min(md) > max(pr)  # no overlap at all between the groups
    pairs = list(itertools.product(EXCELLENT, POOR))
    assert all(names.score(a) > names.score(b) for a, b in pairs)


def test_scores_are_reproducible_and_explained():
    a, b = names.explain("aether", "rare"), names.explain("aether", "rare")
    assert a == b and 0 <= a["score"] <= 100
    assert [p["part"] for p in a["parts"]] == list(names.COMPONENTS)
    assert "worth money" in a["note"]  # a quality score never promises resale value
    assert names.score("aether", "rare") != names.score("aether", "ultra")  # profiles really differ


def test_brand_impersonation_and_offensive_names_are_flagged():
    assert names.score("hermes") < names.explain("hermes")["parts"][1]["value"]  # brand penalty applied
    assert "brand" in names.risks("gucci") and not names.blocked("gucci")
    assert names.blocked("instagram") and names.score("official") <= 5
    assert names.blocked("fuckboy") and names.score("fuckboy") == 0
    assert not names.risks("cumin") and not names.risks("grape")  # no false hits inside real words
    assert "sensitive" in names.risks("isis")


def test_normalize_validate_and_transliterate():
    assert names.normalize("@Élan") == "elan" and names.normalize("Cœur") == "coeur"
    assert names.normalize("Jökull") == "jokull" and names.normalize("Straße") == "strasse"
    assert names.validate("a.b") == ("ok", "")
    for bad in (".abc", "abc.", "a..b", "a b", "x" * 31, "é", ""):
        assert names.validate(bad)[0] == "invalid", bad
    assert names.validate("12345")[0] == "unverified"  # numbers only: only Instagram can say
    assert names.validate("ab")[0] == "unverified"  # 2 characters stay in, but aren't promised


def test_lexicon_merges_duplicates_and_keeps_provenance():
    lex = names.lexicon()
    assert len(lex) > 1500
    assert {"la", "it"} <= set(lex["alba"]["langs"]) and len(lex["alba"]["senses"]) == 2  # one entry
    d = names.describe("ame")
    assert d["ambiguous"] and "Japanese" in d["note"] and d["senses"][0]["original"] == "âme"
    assert d["license"] == "MIT" and d["source"]
    assert names.describe("zqzq")["source"].startswith("generated")
    assert all(names.validate(n)[0] != "invalid" for n in lex)


def test_generators_are_deterministic_bounded_and_clean():
    u = list(names.ultra())
    assert len(u) == len(set(u)) == 26 * 36 * 36 and all(x[0].isalpha() for x in u)  # no 1-2 chars
    assert u == list(names.ultra())  # same order every run, so a hunt can resume
    a = list(names.alnum())
    assert len(a) == len(set(a)) and all(any(c.isdigit() for c in x) and len(x) >= 3 for x in a)
    assert all(p[3] >= 3 for k, p in names.PRESETS.items() if k != "custom")
    f = list(names.four())
    assert len(f) == len(set(f)) == 26**4 and [i for i, _ in f] == list(range(26**4))
    assert all(n.isalpha() and len(n) == 4 for _, n in f)
    assert list(names.four(f[200_000][0]))[:3] == f[200_000:200_003]  # resumes mid-way
    first = [n for _, n in f[:1000]]
    assert sum(map(names._pronounce, first)) > sum(
        names._pronounce(n) for _, n in f[-1000:]
    )  # say-able first
    inv = list(itertools.islice(names.invented(), 300))
    assert inv == list(itertools.islice(names.invented(), 300))
    assert list(itertools.islice(names.invented(inv[100][0]), 5)) == inv[100:105]  # resumes mid-way
    words = names.lexicon()
    for _i, n in inv:
        assert 3 <= len(n) <= 7 and n not in words and not names.risks(n)
        assert names._pronounce(n) >= 85
    assert {"short", "premium", "rare", "multi", "luxury", "dark"} <= {
        c for e in words.values() for c in e["cats"]
    }
