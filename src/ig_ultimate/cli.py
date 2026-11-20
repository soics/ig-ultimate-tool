"""Terminal menu. Every destructive tool goes through `run_plan`: preview, optional dry run,
type `yes` to confirm. Ctrl+C at any question goes back one menu."""

from __future__ import annotations

import time
from collections.abc import Callable

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__, config, engine, tools
from .engine import Plan, Stopped

console = Console(highlight=False)
PREVIEW_ROWS = 15


class Back(Exception):
    """User pressed Ctrl+C or chose 0: go back one menu."""


class TermUI:
    def step(self, n, total, item, ok, note=""):
        mark = "[green]done[/]" if ok else "[red]skip[/]"
        console.print(f"  {mark} [dim]{n}/{total}[/] {item.label}" + (f" [dim]({note})[/]" if note else ""))

    def say(self, text):
        console.print(f"  [dim]{text}[/]")


# ---------------------------------------------------------------- small helpers
def ask(text: str) -> str:
    try:
        return console.input(f"[bold yellow]{text}[/] ").strip()
    except (KeyboardInterrupt, EOFError):
        console.print()
        raise Back from None


def ask_int(text: str, empty_means: int | None = None) -> int | None:
    while True:
        raw = ask(text)
        if not raw:
            return empty_means
        if raw.isdigit():
            return int(raw)
        console.print("[red]Please type a number.[/]")


def menu(title: str, options: list[tuple[str, str]], back: str = "Back") -> str:
    console.print()
    console.print(f"[bold]{title}[/]")
    for key, label in options:
        console.print(f"  [bold #FF3D7F]{key}[/]  {label}")
    console.print(f"  [bold #FF3D7F]0[/]  {back}")
    while True:
        choice = ask(">")
        if choice in ("0", ""):
            raise Back
        if choice in {k for k, _ in options}:
            return choice
        console.print("[red]Pick one of the numbers above.[/]")


def confirm_yes(question: str) -> bool:
    """Destructive actions need the word yes typed out. Enter alone never says yes."""
    return ask(f"{question} Type [white]yes[/] to go ahead, anything else cancels:").lower() == "yes"


# ---------------------------------------------------------------- running a plan
class App:
    def __init__(self, cl, cfg: dict, preview: bool = False, sleep: Callable[[float], None] = time.sleep):
        self.cl, self.cfg, self.preview = cl, cfg, preview
        self.sleep = (lambda s: sleep(min(s, 0.05))) if preview else sleep

    @property
    def keep(self) -> set[str]:
        return set(self.cfg.get("keep", {}))

    def show(self, plan: Plan, rows: int = PREVIEW_ROWS) -> None:
        table = Table(show_header=False, box=None, padding=(0, 2))
        for item in plan.items[:rows]:
            table.add_row(item.label)
        console.print(table)
        if len(plan.items) > rows:
            console.print(f"  [dim]...and {len(plan.items) - rows} more.[/]")
        console.print(f"  [dim]Full list saved to: {engine.save_list(plan)}[/]")

    def run_plan(self, plan: Plan) -> None:
        if not plan.items:
            console.print("[green]Nothing to do here, already clean.[/]")
            return
        console.print(f"\n[bold]{plan.title}: {len(plan.items)} item(s)[/]")
        self.show(plan)
        if plan.experimental:
            console.print(
                "[yellow]Experimental: this uses an Instagram feature we could not fully test yet. "
                "If it fails, nothing else is affected.[/]"
            )
        if engine.unfinished(plan.job):
            if ask("A previous run of this was interrupted. Continue it? (y/n)").lower().startswith("n"):
                engine.forget(plan.job)
        choice = menu("What now?", [("1", "Do it"), ("2", "Dry run: show each step, change nothing")])
        dry = choice == "2"
        if not dry and not confirm_yes(f"{plan.title}: {len(plan.items)} item(s)."):
            console.print("Cancelled. Nothing changed.")
            return
        speed = self.cfg.get("speed", "safe")
        if not dry:
            console.print(
                f"[dim]Speed: {speed}. You can stop any time with Ctrl+C; it continues next time.[/]"
            )
        try:
            ok, failed = engine.execute(plan, TermUI(), speed=speed, dry_run=dry, sleep=self.sleep)
        except Stopped as exc:
            console.print(Panel(str(exc), title="Stopped", border_style="yellow"))
            return
        console.print(
            f"[green]Finished: {ok} {plan.verb}[/]" + (f", [red]{failed} skipped[/]" if failed else "")
        )

    def run_built(self, build: Callable[[], Plan]) -> None:
        with console.status("Reading your account..."):
            plan = build()
        self.run_plan(plan)

    # ------------------------------------------------------------ menus
    def followers_menu(self):
        cl, keep = self.cl, self.keep
        c = menu(
            "Followers & following",
            [
                ("1", "Show who doesn't follow me back (just a list)"),
                ("2", "Unfollow people who don't follow me back"),
                ("3", "Unfollow everyone"),
                ("4", "Unfollow specific usernames (paste them)"),
                ("5", "Remove followers I don't follow back"),
                ("6", "Remove all my followers"),
                ("7", "Decline all follow requests"),
            ],
        )
        if c == "1":
            with console.status("Comparing your followers and following..."):
                plan = tools.plan_unfollow(cl, "not_back")
            console.print(f"\n[bold]{len(plan.items)} people don't follow you back[/]")
            self.show(plan, rows=200)
            return
        if c == "7":
            return self.run_built(lambda: tools.plan_decline_requests(cl))
        limit = ask_int("How many at most? (leave empty for all of them)")
        if c == "2":
            self.run_built(lambda: tools.plan_unfollow(cl, "not_back", keep, limit=limit))
        elif c == "3":
            self.run_built(lambda: tools.plan_unfollow(cl, "all", keep, limit=limit))
        elif c == "4":
            names = ask("Usernames, separated by commas or spaces:").replace(",", " ").split()
            self.run_built(lambda: tools.plan_unfollow(cl, "these", keep, usernames=names, limit=limit))
        elif c == "5":
            self.run_built(lambda: tools.plan_remove_followers(cl, "not_back", keep, limit=limit))
        elif c == "6":
            self.run_built(lambda: tools.plan_remove_followers(cl, "all", keep, limit=limit))

    def pick(self, plan: Plan) -> set[str] | None:
        if not plan.items:
            console.print("[green]Nothing here.[/]")
            return None
        for n, item in enumerate(plan.items, 1):
            console.print(f"  [bold #FF3D7F]{n}[/]  {item.label}")
        raw = ask("Numbers to include, separated by commas (e.g. 1,4,7):").replace(",", " ").split()
        return {plan.items[int(n) - 1].id for n in raw if n.isdigit() and 1 <= int(n) <= len(plan.items)}

    def chats_menu(self):
        cl = self.cl
        c = menu(
            "Chats & groups",
            [
                ("1", "Delete chats with no messages in the last N months"),
                ("2", "Pick chats to delete"),
                ("3", "Delete all chats (groups stay)"),
                ("4", "Leave group chats I pick (experimental)"),
                ("5", "Leave all group chats (experimental)"),
            ],
        )
        if c == "1":
            months = ask_int("Delete chats quiet for more than how many months? (e.g. 4)")
            if not months:
                raise Back
            groups = ask("Include group chats too? (y/n)").lower().startswith("y")
            self.run_built(lambda: tools.plan_delete_chats(cl, months=months, include_groups=groups))
        elif c == "2":
            with console.status("Loading your chats..."):
                every = tools.plan_delete_chats(cl, include_groups=True)
            picked = self.pick(every)
            if picked:
                self.run_plan(tools.plan_delete_chats(cl, include_groups=True, picked=picked))
        elif c == "3":
            self.run_built(lambda: tools.plan_delete_chats(cl))
        elif c == "4":
            with console.status("Loading your group chats..."):
                every = tools.plan_leave_groups(cl)
            picked = self.pick(every)
            if picked:
                self.run_plan(tools.plan_leave_groups(cl, picked=picked))
        elif c == "5":
            self.run_built(lambda: tools.plan_leave_groups(cl))

    def posts_menu(self):
        cl = self.cl
        c = menu(
            "Posts, reels & stories",
            [
                ("1", "Archive posts & reels (hidden, you can restore them)"),
                ("2", "Delete posts & reels (permanent)"),
                ("3", "Restore archived posts"),
                ("4", "Delete my current stories"),
                ("5", "Delete my highlights"),
            ],
        )
        if c in ("1", "2"):
            months = ask_int("Only posts older than how many months? (leave empty for all)")
            action = "archive" if c == "1" else "delete"
            self.run_built(lambda: tools.plan_posts(cl, action, older_than_months=months))
        else:
            build = {"3": tools.plan_unarchive, "4": tools.plan_stories, "5": tools.plan_highlights}[c]
            self.run_built(lambda: build(cl))

    def footprint_menu(self):
        c = menu(
            "Likes, saves, comments & tags",
            [
                ("1", "Unlike everything I ever liked"),
                ("2", "Unsave everything I saved"),
                ("3", "Delete all comments on my posts"),
                ("4", "Remove me from photos I'm tagged in (experimental)"),
            ],
        )
        build = {
            "1": tools.plan_unlike,
            "2": tools.plan_unsave,
            "3": tools.plan_comments_on_my_posts,
            "4": tools.plan_untag,
        }[c]
        self.run_built(lambda: build(self.cl))

    def profile_menu(self):
        console.print("\n[bold]Profile clean-up[/]")
        keys = list(tools.PROFILE_PARTS)
        for n, key in enumerate(keys, 1):
            label, _fn, experimental = tools.PROFILE_PARTS[key]
            console.print(
                f"  [bold #FF3D7F]{n}[/]  {label}" + (" [dim](experimental)[/]" if experimental else "")
            )
        raw = ask("Numbers to do, separated by commas (empty = all):").replace(",", " ").split()
        parts = [keys[int(n) - 1] for n in raw if n.isdigit() and 1 <= int(n) <= len(keys)] or keys
        self.run_plan(tools.plan_profile(self.cl, parts))

    def settings_menu(self):
        speed = self.cfg.get("speed", "safe")
        c = menu(
            "Settings",
            [
                ("1", f"Speed: [bold]{speed}[/] (switch to {'fast' if speed == 'safe' else 'safe'})"),
                ("2", f"Keep-list: {len(self.cfg['keep'])} account(s) no tool will ever unfollow or remove"),
                ("3", "Log out of this computer"),
            ],
        )
        if c == "1":
            new = "fast" if speed == "safe" else "safe"
            if new == "fast":
                console.print(
                    "[yellow]Fast mode uses shorter breaks. Most aged accounts are fine, but Instagram is "
                    "more likely to show 'Action Blocked' for a day or two. Your choice.[/]"
                )
            self.cfg["speed"] = new
            self.save()
        elif c == "2":
            self.keep_menu()
        elif c == "3":
            from . import session

            session.logout(self.cl)
            console.print("Logged out. Your Instagram account itself is untouched.")
            raise SystemExit(0)

    def keep_menu(self):
        keep = self.cfg["keep"]
        console.print("  " + (", ".join(f"@{n}" for n in keep.values()) or "[dim](empty)[/]"))
        c = menu("Keep-list", [("1", "Add usernames"), ("2", "Remove usernames")])
        names = {
            n.lstrip("@").lower() for n in ask("Usernames, separated by spaces:").replace(",", " ").split()
        }
        if c == "2":
            self.cfg["keep"] = {pk: n for pk, n in keep.items() if n.lower() not in names}
        else:
            with console.status("Looking them up..."):
                known = {
                    u.username.lower(): u
                    for u in self.cl.user_following(tools.me(self.cl), amount=0).values()
                }
                known |= {
                    u.username.lower(): u
                    for u in self.cl.user_followers(tools.me(self.cl), amount=0).values()
                }
            for name in names:
                if name in known:
                    keep[str(known[name].pk)] = known[name].username
                else:
                    console.print(
                        f"[yellow]@{name} doesn't follow you and you don't follow them, skipped.[/]"
                    )
        self.save()
        console.print(f"Keep-list now has {len(self.cfg['keep'])} account(s).")

    def save(self):
        if not self.preview:
            config.save(self.cfg)

    # ------------------------------------------------------------ nuke
    def nuke(self, countdown: int = 5) -> None:
        console.print(
            Panel(
                "NUKE wipes your Instagram account back to empty: profile, posts, reels, stories, "
                "highlights, likes, saves, comments, tags, chats, groups, follows and followers.\n\n"
                "[bold]It cannot be undone.[/] Instagram has no recycle bin for most of this.",
                title="NUKE",
                border_style="red",
            )
        )
        console.print(
            "Before you start, download a copy of your data: Instagram app > Settings > Accounts Center > "
            "Your information and permissions > Download your information. (Menu names can change.)"
        )
        steps = list(tools.NUKE_STEPS)
        for n, (_key, label, _build) in enumerate(steps, 1):
            console.print(f"  [bold #FF3D7F]{n:>2}[/]  {label}")
        raw = (
            ask("Press Enter to keep all steps, or type the numbers to SKIP (e.g. 3,9):")
            .replace(",", " ")
            .split()
        )
        nums = {int(n) for n in raw if n.isdigit()}
        skip = {key for n, (key, _l, _b) in enumerate(steps, 1) if n in nums}
        if len(skip) == len(steps):
            raise Back

        with console.status("Counting everything (this reads your whole account, it can take a while)..."):
            plans, failed = tools.build_nuke(self.cl, self.keep, skip)
        for label, why in failed:
            console.print(f"[yellow]Skipped, Instagram wouldn't let us read it:[/] {label} [dim]({why})[/]")
        table = Table(title="What NUKE will do", show_lines=False)
        table.add_column("Step")
        table.add_column("Items", justify="right")
        for plan in plans:
            table.add_row(plan.title + (" (experimental)" if plan.experimental else ""), str(len(plan.items)))
        console.print(table)
        total = sum(len(p.items) for p in plans)
        if not total:
            console.print("[green]Your account is already empty.[/]")
            return

        if not confirm_yes(f"Confirmation 1 of 2: I understand {total} things will be deleted for good."):
            console.print("Cancelled. Nothing changed.")
            return
        username = self.cl.account_info().username
        if (
            ask(f"Confirmation 2 of 2: type your username ([white]{username}[/]) to confirm:").lstrip("@")
            != username
        ):
            console.print("That doesn't match. Cancelled. Nothing changed.")
            return
        try:
            for s in range(countdown, 0, -1):
                console.print(
                    f"[bold red]  Nuking in {s}...[/] [dim](close this window or press Ctrl+C to cancel)[/]"
                )
                self.sleep(1)
        except KeyboardInterrupt:
            console.print("[green]Cancelled. Nothing changed.[/]")
            return

        for plan in plans:
            console.print(f"\n[bold red]{plan.title}[/] ({len(plan.items)})")
            try:
                engine.execute(plan, TermUI(), speed=self.cfg.get("speed", "safe"), sleep=self.sleep)
            except Stopped as exc:
                console.print(Panel(str(exc), title="NUKE paused", border_style="yellow"))
                console.print("Run NUKE again later: finished steps will show 0 left, the rest continues.")
                return
        console.print(Panel("Done. Your account is wiped.", border_style="green"))

    # ------------------------------------------------------------ username finder
    def finder(self) -> None:
        """Terminal version: hunts while this window is open; Ctrl+C stops it and keeps the progress."""
        import threading

        from . import huntdb, names, snipe

        console.print(Panel(snipe.EXPLAIN, title="Username finder", border_style="#FF3D7F"))
        keys = list(names.PRESETS)
        for i, k in enumerate(keys, 1):
            console.print(f"  {i:>2}. {names.PRESETS[k][0]}")
        raw = ask("Which style? Type a number, or press Enter for the OG path:")
        preset = keys[int(raw) - 1] if raw.isdigit() and 1 <= int(raw) <= len(keys) else "og"
        verifier = snipe.InstagrapiVerifier(self.cl)
        account_id, me = verifier.account()
        store = huntdb.Store(account_id)
        for said in (store.repaired, store.migrate_v1()):
            if said:
                console.print(said)
        mine = ask("Your own ideas to try first (optional, separated by spaces):").replace(",", " ").split()
        if mine:
            store.add_custom(mine, 1)
        grant = None
        if (
            ask(
                "Claim a free name automatically? (unofficial; renames your account) Type yes or press Enter:"
            )
            == "yes"
        ):
            if (
                ask(f"Type your username ([white]{me}[/]) to confirm it may be renamed:").lstrip("@").lower()
                != me
            ):
                console.print("That doesn't match. Nothing changed.")
                return
            grant = {"account_id": account_id, "username": me, "granted_at": time.time()}
        hunter = snipe.Hunter(
            verifier,
            store,
            account_id,
            me,
            log=lambda s: console.print(f"  [dim]{time.strftime('%H:%M')}[/] {s}"),
            preset=preset,
            auto_claim=grant,
        )
        worker = threading.Thread(target=hunter.run, daemon=True)
        worker.start()
        console.print("[green]Hunting. Leave this window open. Press Ctrl+C to stop.[/]")
        try:
            while worker.is_alive():
                worker.join(0.5)
        except KeyboardInterrupt:
            hunter.stop()
            worker.join(15)  # a request already sent finishes and is written down
        if hunter.won:
            self.cfg["username"] = hunter.won
            self.save()
        if not worker.is_alive():  # still finishing a sent request: let it record the outcome
            store.close()

    # ------------------------------------------------------------ main loop
    def ai_menu(self):
        from . import chat

        chat.menu(self)

    def loop(self) -> int:
        sections: dict[str, tuple[str, Callable]] = {
            "1": ("Followers & following", self.followers_menu),
            "2": ("Chats & groups", self.chats_menu),
            "3": ("Posts, reels & stories", self.posts_menu),
            "4": ("Likes, saves, comments & tags", self.footprint_menu),
            "5": ("Profile clean-up", self.profile_menu),
            "6": ("AI auto-reply", self.ai_menu),
            "7": ("Username finder: hunt for a short or rare username", self.finder),
            "8": ("[red]NUKE: wipe everything[/]", self.nuke),
            "9": ("Settings (speed, keep-list, log out)", self.settings_menu),
        }
        while True:
            try:
                choice = menu(
                    f"IG Ultimate Tool {__version__}"
                    + ("  [yellow](preview: pretend account)[/]" if self.preview else ""),
                    [(k, v[0]) for k, v in sections.items()],
                    back="Exit",
                )
            except Back:
                console.print("Bye.")
                return 0
            try:
                sections[choice][1]()
            except Back:
                continue


NOTICE = (
    "IG Ultimate Tool is free, open-source software. It runs only on your computer and talks only to "
    "Instagram (and, if you turn on AI replies, the AI service you pick). It is not made by or affiliated "
    "with Instagram or Meta. Automating your account goes against Instagram's terms and can get it limited "
    "or banned. Some tools delete things permanently. You use it at your own risk; the software comes "
    "with no warranty (see LICENSE)."
)


def accept_notice(cfg: dict) -> bool:
    if cfg.get("accepted_notice"):
        return True
    console.print(Panel(NOTICE, title="Before you start", border_style="yellow"))
    if ask("Type [white]I agree[/] to continue:").lower() != "i agree":
        return False
    cfg["accepted_notice"] = True
    return True


def run(preview: bool = False) -> int:
    console.print(
        Panel.fit(
            f"[bold]IG Ultimate Tool[/] {__version__}\nFree & open source. Runs only on your computer.",
            border_style="#FF3D7F",
        )
    )
    cfg = config.load()
    try:
        if not accept_notice(cfg):
            console.print("No problem. Nothing was done.")
            return 0
        if not preview:
            config.save(cfg)
            start = menu(
                "How do you want to start?",
                [
                    ("1", "Log in to my Instagram (here in this window)"),
                    ("2", "Try everything on a pretend account first (no login, nothing real changes)"),
                    ("3", "Use it in my web browser instead (easier: buttons and checkboxes)"),
                ],
                back="Exit",
            )
            if start == "3":
                from . import webapp

                return webapp.serve()
            if start == "2":
                import os

                from . import paths

                os.environ["IG_ULTIMATE_HOME"] = str(paths.data_dir() / "preview")
                preview = True
        if preview:
            from .fake import FakeClient

            console.print("[yellow]Preview mode: a pretend account, no login, nothing real changes.[/]")
            cl = FakeClient()
        else:
            from . import session

            config.save(cfg)
            console.print("Logging in to Instagram...")
            try:
                cl = session.login(cfg.get("username", ""), ask=lambda p: ask(p))
            except session.LoginFailed as exc:
                console.print(Panel(str(exc), title="Login failed", border_style="red"))
                return 1
            cfg["username"] = getattr(cl, "username", "") or cfg.get("username", "")
            config.save(cfg)
            console.print("[green]Logged in.[/]")
    except Back:
        return 0
    return App(cl, cfg, preview=preview).loop()
