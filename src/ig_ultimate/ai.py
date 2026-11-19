"""AI providers for the auto-reply.

One function, `reply(settings, system, turns)`, whatever the provider:
- OpenAI-style APIs (Groq, Gemini, OpenRouter, Ollama, OpenAI, any custom URL) over plain HTTPS,
- Claude through Anthropic's official SDK,
- Claude Code and Codex through their own command-line tools (the person's own subscription).

A turn is {"role": "user"|"assistant", "text": str, "images": [jpeg bytes]}.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
import tempfile
from pathlib import Path

import requests
from urllib3.util import parse_url

TIMEOUT = 60
MAX_MEDIA_BYTES = 8 * 1024 * 1024
MEDIA_HOSTS = ("cdninstagram.com", "fbcdn.net")

# key -> settings. "prefer" picks the default model from the provider's live model list, so a
# renamed model doesn't break setup. All OpenAI-style unless "kind" says otherwise.
PRESETS: dict[str, dict] = {
    "groq": {
        "label": "Groq: free key, very fast, understands photos and voice notes (recommended)",
        "base_url": "https://api.groq.com/openai/v1",
        "key_url": "https://console.groq.com/keys",
        "prefer": ["llama-4-scout", "llama-4-maverick", "llama-3.3-70b"],
        "stt_prefer": ["whisper-large-v3-turbo", "whisper-large-v3"],
        "privacy": "Messages are sent to Groq to write the reply.",
    },
    "gemini": {
        "label": "Google Gemini: free key, photos too",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "key_url": "https://aistudio.google.com/apikey",
        "prefer": ["flash-lite", "flash"],
        "privacy": (
            "WARNING: on Gemini's free tier Google may use what you send to improve its products and human "
            "reviewers may read it. Your contacts' DMs are personal messages. Prefer Groq or a paid key."
        ),
    },
    "openrouter": {
        "label": "OpenRouter: free models available (low daily limit)",
        "base_url": "https://openrouter.ai/api/v1",
        "key_url": "https://openrouter.ai/keys",
        "prefer": [":free"],
        "privacy": "Messages are sent to OpenRouter and the model's provider.",
    },
    "ollama": {
        "label": "Ollama on this computer: private, free, needs a decent PC",
        "base_url": "http://localhost:11434/v1",
        "key_url": "https://ollama.com/download",
        "prefer": ["gemma3", "llama3.2-vision", "llava", "llama3"],
        "privacy": "Nothing leaves your computer.",
        "no_key": True,
    },
    "openai": {
        "label": "OpenAI API key (paid)",
        "base_url": "https://api.openai.com/v1",
        "key_url": "https://platform.openai.com/api-keys",
        "prefer": ["gpt-4.1-mini", "gpt-4o-mini", "mini"],
        "stt_prefer": ["whisper-1", "transcribe"],
        "privacy": "Messages are sent to OpenAI.",
    },
    "anthropic": {
        "label": "Claude API key (paid, by Anthropic)",
        "kind": "anthropic",
        "key_url": "https://console.anthropic.com/settings/keys",
        "models": [
            ("claude-opus-5-5", "Claude Opus 5.5: smartest ($4 / $20 per million tokens)"),
            ("claude-sonnet-5-5", "Claude Sonnet 5.5: balanced ($2 / $10)"),
            ("claude-haiku-5-5", "Claude Haiku 5.5: fastest and cheapest ($0.10 / $0.50)"),
        ],
        "privacy": "Messages are sent to Anthropic. API data isn't used to train models.",
    },
    "claude_code": {
        "label": "Claude Code (uses your Claude subscription, slower)",
        "kind": "cli",
        "privacy": "Messages are sent to Anthropic through Claude Code.",
    },
    "codex": {
        "label": "Codex (uses your ChatGPT subscription, slower, can read ALL your files)",
        "kind": "cli",
        "privacy": (
            "Messages are sent to OpenAI through Codex. WARNING: Codex can read every file your user "
            "account can (its read-only mode isn't limited to one folder). Only let people you fully "
            "trust talk to it."
        ),
    },
    "custom": {
        "label": "Any other OpenAI-compatible server (advanced)",
        "base_url": "",
        "prefer": [],
        "privacy": "Messages are sent to the server you enter.",
    },
}


class AIError(Exception):
    """str(exc) is a plain-English message for the user."""


# ---------------------------------------------------------------- Instagram media
def fetch_media(url: str) -> bytes | None:
    """Download a photo/voice note from Instagram's own servers only, in memory, size-capped."""
    if not url or not url.startswith("https://") or "\\" in url or "@" in url:
        return None
    # Check the host the HTTP library will really connect to, not just what urlparse thinks.
    host = (parse_url(requests.Request("GET", url).prepare().url).host or "").lower()
    if not any(host == h or host.endswith("." + h) for h in MEDIA_HOSTS):
        return None
    with requests.get(url, stream=True, timeout=20, allow_redirects=False) as resp:
        resp.raise_for_status()
        data = b""
        for chunk in resp.iter_content(64 * 1024):
            data += chunk
            if len(data) > MAX_MEDIA_BYTES:
                return None
    return data


# ---------------------------------------------------------------- OpenAI-style
def _headers(s: dict) -> dict:
    return {"Authorization": f"Bearer {s['key']}"} if s.get("key") else {}


def _check(resp: requests.Response) -> dict:
    if resp.status_code == 401:
        raise AIError("The AI service rejected the key. Check it in AI auto-reply > Set up AI.")
    if resp.status_code == 429:
        raise AIError("The AI service's free limit was reached for now. Replies resume when it resets.")
    if resp.status_code >= 400:
        raise AIError(f"The AI service answered {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def list_models(s: dict) -> list[str]:
    resp = requests.get(f"{s['base_url'].rstrip('/')}/models", headers=_headers(s), timeout=TIMEOUT)
    return sorted(m["id"] for m in _check(resp).get("data", []))


def pick(models: list[str], prefer: list[str]) -> str:
    for want in prefer:
        for model in models:
            if want in model:
                return model
    return models[0] if models else ""


def _openai(s: dict, system: str, turns: list[dict]) -> str:
    messages = [{"role": "system", "content": system}]
    for t in turns:
        if t.get("images") and t["role"] == "user":
            parts = [{"type": "text", "text": t["text"] or "(photo)"}] + [
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(i).decode()},
                }
                for i in t["images"]
            ]
            messages.append({"role": "user", "content": parts})
        else:
            messages.append({"role": t["role"], "content": t["text"]})
    body = {"model": s["model"], "messages": messages, "max_tokens": 300}
    url = f"{s['base_url'].rstrip('/')}/chat/completions"
    data = _check(requests.post(url, json=body, headers=_headers(s), timeout=TIMEOUT))
    return (data["choices"][0]["message"].get("content") or "").strip()


def transcribe(s: dict, audio: bytes) -> str | None:
    """Voice note -> text, when the provider has a speech-to-text model (Groq, OpenAI)."""
    if not s.get("stt_model"):
        return None
    url = f"{s['base_url'].rstrip('/')}/audio/transcriptions"
    files = {"file": ("voice.m4a", audio, "audio/mp4")}
    data = _check(
        requests.post(url, files=files, data={"model": s["stt_model"]}, headers=_headers(s), timeout=TIMEOUT)
    )
    return (data.get("text") or "").strip() or None


# ---------------------------------------------------------------- Claude API (official SDK)
def _anthropic(s: dict, system: str, turns: list[dict]) -> str:
    import anthropic

    while turns and turns[0]["role"] != "user":  # the conversation must start with the other person
        turns = turns[1:]
    messages = []
    for t in turns:
        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.b64encode(i).decode(),
                },
            }
            for i in t.get("images", [])
        ] + [{"type": "text", "text": t["text"] or "(photo)"}]
        messages.append({"role": t["role"], "content": content})
    client = anthropic.Anthropic(api_key=s["key"], timeout=TIMEOUT)
    model = s.get("model") or "claude-opus-5-5"
    kwargs = {
        "model": model,
        "max_tokens": 1024,
        "system": system,
        "messages": messages,
        "output_config": {"effort": "low"},
    }
    try:
        if model.startswith("claude-haiku"):
            resp = client.messages.create(**kwargs)
        else:  # if the model declines, Anthropic retries on a fallback model inside the same call
            resp = client.beta.messages.create(
                **kwargs, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
            )
    except anthropic.AuthenticationError:
        raise AIError("Anthropic rejected the API key. Check it in AI auto-reply > Set up AI.") from None
    except anthropic.RateLimitError:
        raise AIError("Anthropic's rate limit was reached for now. Replies resume shortly.") from None
    except anthropic.APIConnectionError:
        raise AIError("Couldn't reach Anthropic. Check your internet connection.") from None
    if resp.stop_reason == "refusal":
        return ""
    return "".join(b.text for b in resp.content if b.type == "text").strip()


# ---------------------------------------------------------------- Claude Code / Codex
def _transcript(system: str, turns: list[dict], image_names: list[str]) -> str:
    lines = [system, "", "Conversation so far (oldest first):"]
    for t in turns:
        who = "You" if t["role"] == "assistant" else "Them"
        lines.append(f"{who}: {t['text']}")
    if image_names:
        lines.append(f"(They also sent photo(s) saved here: {', '.join(image_names)}. Look at them.)")
    lines.append("\nWrite only your next message, nothing else.")
    return "\n".join(lines)


def _cli(provider: str, s: dict, system: str, turns: list[dict]) -> str:
    exe = "claude" if provider == "claude_code" else "codex"
    if not shutil.which(exe):
        raise AIError(f"'{exe}' isn't installed on this computer, or isn't on PATH.")
    images = [i for t in turns[-1:] for i in t.get("images", [])]
    folder = s.get("agent_folder") or ""
    with tempfile.TemporaryDirectory(prefix="igut-") as tmp:
        names = []
        for n, data in enumerate(images, 1):
            (Path(tmp) / f"photo{n}.jpg").write_bytes(data)
            names.append(str(Path(tmp) / f"photo{n}.jpg"))
        prompt = _transcript(system, turns, names)
        if provider == "claude_code":
            # --restricted removes the tools that run commands; without an agent folder it can read
            # nothing but this message's photos.
            tools = "Read,Glob,Grep" if folder else ("Read" if names else "")
            cmd = [
                "claude",
                "-p",
                "--restricted",
                "--strict-mcp-config",
                "--no-session-persistence",
                "--tools",
                tools,
            ]
            cmd += ["--add-dir", folder] if folder else []
            cmd += ["--model", s["model"]] if s.get("model") else []
            run = subprocess.run(  # noqa: S603 - fixed argv list, no shell; the DM text goes in on stdin
                cmd, input=prompt, cwd=folder or tmp, capture_output=True, text=True, timeout=180
            )
            out = run.stdout
        else:
            out_file = Path(tmp) / "reply.txt"
            cmd = [
                "codex",
                "exec",
                "--skip-git-repo-check",
                "--ignore-user-config",  # your own Codex MCP servers/settings never run on a DM's say-so
                "--ephemeral",
                "--sandbox",
                "read-only",
                "-C",
                folder or tmp,
            ]
            cmd += ["-o", str(out_file)] + [arg for name in names for arg in ("-i", name)]
            cmd += ["-m", s["model"]] if s.get("model") else []
            run = subprocess.run(  # noqa: S603 - fixed argv list, no shell; "--" ends option parsing
                [*cmd, "--", prompt],
                cwd=folder or tmp,
                stdin=subprocess.DEVNULL,  # otherwise codex waits for extra input from the keyboard
                capture_output=True,
                text=True,
                timeout=180,
            )
            out = out_file.read_text(encoding="utf-8") if out_file.exists() else ""
        if run.returncode != 0 and not out.strip():
            lines = [ln.strip() for ln in (run.stderr or run.stdout).splitlines() if ln.strip()]
            reason = next((ln for ln in reversed(lines) if "error" in ln.lower()), lines[-1] if lines else "")
            raise AIError(f"{exe} failed: {reason[:200]}")
        return out.strip()


def needs_trusted_people(s: dict) -> bool:
    """Providers that can read files must never answer strangers (Everyone mode)."""
    return s.get("provider") == "codex" or (
        s.get("provider") == "claude_code" and bool(s.get("agent_folder"))
    )


# ---------------------------------------------------------------- entry point
def reply(s: dict, system: str, turns: list[dict]) -> str:
    provider = s.get("provider", "")
    kind = PRESETS.get(provider, {}).get("kind")
    if kind == "anthropic":
        return _anthropic(s, system, turns)
    if kind == "cli":
        return _cli(provider, s, system, turns)
    if not s.get("base_url") or not s.get("model"):
        raise AIError("The AI isn't set up yet. Go to AI auto-reply > Set up AI.")
    try:
        return _openai(s, system, turns)
    except requests.RequestException:
        raise AIError("Couldn't reach the AI service. Check your internet connection.") from None
