import pytest


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """Every test gets its own empty app-data folder."""
    monkeypatch.setenv("IG_ULTIMATE_HOME", str(tmp_path))
    return tmp_path


class RecordingUI:
    def __init__(self):
        self.steps, self.said = [], []

    def step(self, n, total, item, ok, note=""):
        self.steps.append((item.id, ok, note))

    def say(self, text):
        self.said.append(text)


@pytest.fixture
def ui():
    return RecordingUI()
