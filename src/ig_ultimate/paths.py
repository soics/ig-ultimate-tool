"""Where the tool keeps its files, and how it writes them.

Everything lives in the per-user app-data folder (never the project folder, which people
zip, share, or keep inside OneDrive/Dropbox). Files are written atomically and readable
only by the owner, because session.json is full access to the Instagram account.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from platformdirs import user_data_dir


def data_dir() -> Path:
    root = Path(os.environ.get("IG_ULTIMATE_HOME") or user_data_dir("IG Ultimate Tool", appauthor=False))
    root.mkdir(parents=True, exist_ok=True)
    return root


def read_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, UnicodeDecodeError):
        # Keep the broken file for the user instead of silently overwriting it later.
        Path(path).replace(Path(path).with_suffix(".corrupt"))
        return default


def write_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")  # mkstemp creates the file as 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
