"""AI auto-reply (being built)."""

from __future__ import annotations


def menu(app) -> None:
    from .cli import Back, console

    console.print("[yellow]AI auto-reply is coming in the next update of this version.[/]")
    raise Back
