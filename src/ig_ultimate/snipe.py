"""Username finder: hunts for a free, clean username and claims it for the account you're logged into.

Order (shortest first): 2-letter words, 2-character combos, 3-letter words, 3-character combos
(like pvp, qc1), then 4-, 5- and 6+-letter uncommon words. Every third check goes to the uncommon
words instead, because that's where free names actually turn up; otherwise they'd wait days behind
the ~35,000 short combos, almost all of which are taken.

How a name is checked: a normal profile lookup. "No such account" isn't proof (Instagram holds the
names of banned and deactivated accounts), so then it tries to claim it; a refusal means "held".
One account, human pace, and it slows right down when Instagram says so. It never switches
accounts to get around Instagram's limits.
"""

from __future__ import annotations

import random
import re
import string
import threading
from collections.abc import Callable

from instagrapi import exceptions as ig_errors

from . import paths
from .engine import explain

WORDS = {
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
EXPLAIN = (
    "Hunts for a free username and gives it to the account you're logged into, then stops.\n"
    "Order: 2-letter words, 2-character combos, 3-letter words, 3-character combos (like pvp or qc1), "
    "then rare 4-, 5- and 6+-letter words. Every third check tries a rare word, because that's where "
    "free names really turn up.\n"
    "Be realistic: almost every 2- and 3-character name is taken or held by Instagram, so the hunt can "
    "run for days or weeks. It checks about 120 names an hour and pauses by itself when Instagram says "
    "slow down. It carries on where it stopped next time, and keeps going round as long as the "
    "computer is on and awake.\n"
    "When it gets one, your account is RENAMED to it straight away and your old username becomes free "
    "for anyone. Instagram only lets you change your username a few times in 14 days."
)
OK_NAME = re.compile(r"^[a-z0-9._]{1,30}$")
LOOKUP_GAP = (20.0, 40.0)  # seconds between lookups: ~120 an hour, like someone browsing profiles
CLAIM_GAP = 180.0  # at most one username change attempt every 3 minutes
BACKOFF = (15 * 60.0, 2 * 3600.0)  # when Instagram says "too many requests": 15 min, doubling, max 2 h
SLOW_DOWN = (ig_errors.PleaseWaitFewMinutes, ig_errors.RateLimitError, ig_errors.ClientThrottledError)
NOT_AN_ANSWER = (*SLOW_DOWN, ig_errors.ClientConnectionError, ig_errors.ClientRequestTimeout)


def words(n: int) -> list[str]:
    return WORDS[n].split()


def combos(n: int) -> list[str]:
    """Every n-character name that starts with a letter: letters only first, then with digits.
    Shuffled with a fixed seed so the order is the same on every run (resume) but not a-b-c."""
    letters, both = string.ascii_lowercase, string.ascii_lowercase + string.digits
    names = [""]
    for i in range(n):
        names = [p + c for p in names for c in (letters if i == 0 else both)]
    plain = [x for x in names if x.isalpha()]
    mixed = [x for x in names if not x.isalpha()]
    rng = random.Random(f"ig-ultimate-{n}")
    rng.shuffle(plain)
    rng.shuffle(mixed)
    return plain + mixed


def queue(length: int | None = None, mine: list[str] = ()) -> list[str]:
    """The whole hunt in order. `length` keeps one length only (6 means 6 or longer)."""
    main = [*words(2), *combos(2), *words(3), *combos(3), *words(4), *words(5), *words(6)]
    rare = [*words(4), *words(5), *words(6)]
    if length:
        keep = (lambda x: len(x) >= 6) if length >= 6 else (lambda x: len(x) == length)
        main, rare = [x for x in main if keep(x)], [x for x in rare if keep(x)]
    out, seen = [], set()

    def add(name: str) -> None:
        name = name.strip().lstrip("@").lower()
        if name and name not in seen and OK_NAME.match(name):
            seen.add(name)
            out.append(name)

    for name in mine:  # your own ideas go first
        add(name)
    lane = iter(rare)
    for i, name in enumerate(main):
        if i % 3 == 2:
            add(next((x for x in lane if x not in seen), ""))
        add(name)
    return out


def state_file():
    return paths.data_dir() / "username-finder.json"


class Finder:
    def __init__(
        self,
        cl,
        log: Callable[[str], None],
        stop: threading.Event,
        me: str,
        length: int | None = None,
        mine: list[str] = (),
        wait: Callable[[float], None] | None = None,
    ):
        self.cl, self.log, self.stop, self.me = cl, log, stop, me.lower()
        self.wait = wait or stop.wait  # a pause that ends at once when you press Stop
        self.names = queue(length, mine)
        key = f"{length or 'all'}:{','.join(mine)}"
        saved = paths.read_json(state_file(), {})
        self.state = saved if saved.get("key") == key else {"key": key, "pos": 0, "round": 1, "held": []}
        self.last_claim = -CLAIM_GAP
        self.clock = 0.0  # seconds waited so far (keeps claim spacing testable without real time)
        self.won = ""

    def _pause(self, seconds: float) -> None:
        self.clock += seconds
        self.wait(seconds)

    def _save(self) -> None:
        paths.write_json(state_file(), self.state)

    def run(self) -> str:
        """Hunts until a name is claimed (returns it) or you press Stop / Instagram objects (returns "")."""
        self.log(
            f"Hunting from name {self.state['pos'] + 1} of {len(self.names)} (round {self.state['round']}). "
            "About 120 checks an hour."
        )
        backoff = BACKOFF[0]
        while not self.stop.is_set():
            if self.state["pos"] >= len(self.names):
                self.state.update(
                    pos=0, round=self.state["round"] + 1
                )  # names free up as accounts get banned
                self.log(f"Checked every name. Starting round {self.state['round']}.")
            name = self.names[self.state["pos"]]
            if name == self.me:  # already yours: no lookup, no pause
                self.state["pos"] += 1
                continue
            try:
                if self._try(name):
                    return name
                backoff = BACKOFF[0]
            except SLOW_DOWN:
                self.log(f"Instagram said slow down. Pausing {int(backoff // 60)} minutes, then carrying on.")
                self._pause(backoff)
                backoff = min(backoff * 2, BACKOFF[1])
                continue  # same name again after the pause
            except Exception as exc:  # noqa: BLE001
                if why := explain(exc):
                    self.log(why)
                    return ""
                self.log(f"Couldn't check {name}: {str(exc)[:100]}")
            self.state["pos"] += 1
            self._save()
            self._pause(random.uniform(*LOOKUP_GAP))
        self.log("Stopped. Next time it carries on where it left off.")
        return ""

    def _try(self, name: str) -> bool:
        try:
            self.cl.user_info_by_username_v1(name)
            return False  # someone has it
        except ig_errors.UserNotFound:
            pass
        gap = self.last_claim + CLAIM_GAP - self.clock
        if gap > 0:
            self._pause(gap)
            if self.stop.is_set():
                return False
        self.last_claim = self.clock
        self.log(f"@{name}: no account has it. Trying to claim it...")
        try:
            account = self.cl.account_edit(username=name)
        except ig_errors.ClientError as exc:
            if explain(exc) or isinstance(exc, NOT_AN_ANSWER):
                self.last_claim = -CLAIM_GAP  # no answer about this name: retry it after the pause
                raise
            self.state["held"] = [*self.state["held"][-199:], name]
            self.log(f"@{name} is held by Instagram (usually a banned or deactivated account). Moving on.")
            return False
        if (getattr(account, "username", "") or "").lower() != name:
            self.log(f"@{name}: Instagram didn't apply the change. Moving on.")
            return False
        self.won = name
        self.state["won"] = name
        self._save()
        self.log(f"GOT IT: your account is now @{name}. The hunt is over.")
        return True
