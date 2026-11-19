"""The fake account must never be able to hide a broken real call (this happened in the old tool)."""

import inspect
import pathlib
import re

import instagrapi
from instagrapi import types as T

from ig_ultimate.fake import FakeClient

SRC = pathlib.Path(__file__).parents[1] / "src" / "ig_ultimate"
REAL = instagrapi.Client


def test_every_client_call_in_the_code_exists_on_instagrapi():
    called = set()
    for f in SRC.rglob("*.py"):
        if f.name != "fake.py":
            called |= set(re.findall(r"\bcl\.([a-z_]+)\(", f.read_text(encoding="utf-8")))
    assert len(called) > 20
    missing = sorted(m for m in called if not callable(getattr(REAL, m, None)))
    assert not missing, f"not on instagrapi.Client: {missing}"


def test_fake_client_methods_match_real_signatures():
    for name, fn in inspect.getmembers(FakeClient, inspect.isfunction):
        if name.startswith("_"):
            continue
        real = getattr(REAL, name, None)
        assert callable(real), f"FakeClient.{name} does not exist on instagrapi.Client"
        real_params = inspect.signature(real).parameters
        real_takes_kwargs = any(p.kind is p.VAR_KEYWORD for p in real_params.values())
        for pname, p in inspect.signature(fn).parameters.items():
            if pname == "self":
                continue
            if p.kind is p.VAR_KEYWORD:
                assert real_takes_kwargs, f"{name}(**{pname})"
            else:
                assert pname in real_params, f"{name}({pname}=...) is not a real parameter"


def test_fake_objects_only_use_real_fields():
    cl = FakeClient()
    checks = [
        (T.UserShort, list(cl._following.values()) + cl._requests),
        (T.DirectThread, cl._threads),
        (T.Media, cl._medias + cl._liked + cl._saved + cl._tagged + cl._archived),
        (T.Story, cl._stories),
        (T.Highlight, cl._highlights),
        (T.Account, [cl._account]),
        (T.Comment, cl.media_comments(cl._medias[0].id)),
    ]
    for model, objs in checks:
        for obj in objs:
            extra = set(vars(obj)) - set(model.model_fields)
            assert not extra, f"{model.__name__} has no field(s) {extra}"
