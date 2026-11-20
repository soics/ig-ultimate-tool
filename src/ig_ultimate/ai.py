"""AI providers for the auto-reply.

One function, `reply(settings, system, turns)`, whatever the provider:
- OpenAI-style APIs (Groq, Gemini, OpenRouter, Ollama, OpenAI, any custom URL) over plain HTTPS,
- Claude through Anthropic's official SDK,
- Claude Code and Codex through their own command-line tools (the person's own subscription).

A turn is {"role": "user"|"assistant", "text": str, "images": [jpeg bytes]}.
"""

from __future__ import annotations

import base64
import re
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
        "steps": [
            "Groq (with a Q) is a free AI service. It is not Elon Musk's Grok.",
            "Open console.groq.com/keys and sign in with Google, GitHub or email. No card needed.",
            "Click Create API Key, type any name (like instagram), then copy the key. It is shown only once.",
            "Paste it in the API key box and press Save and test.",
        ],
        "base_url": "https://api.groq.com/openai/v1",
        "key_url": "https://console.groq.com/keys",
        # Groq retired its Llama models in 2026; Qwen 3.8 reads photos too. gpt-oss is text-only.
        "prefer": ["qwen3.8", "qwen", "llama-4-scout", "llama-4-maverick", "gpt-oss-120b", "llama-3.3-70b"],
        "stt_prefer": ["whisper-large-v3-turbo", "whisper-large-v3"],
        "privacy": "Messages are sent to Groq to write the reply.",
    },
    "gemini": {
        "label": "Google Gemini: free key, photos too",
        "steps": [
            "Open aistudio.google.com/apikey and sign in with a Google account.",
            "Click Create API key. If it asks for a project, let it make one. Copy the key.",
            "Paste it in the API key box and press Save and test. Read the privacy warning first.",
        ],
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
        "steps": [
            "Open openrouter.ai and sign up.",
            "Go to openrouter.ai/keys, click Create Key and copy it.",
            "Paste it in the API key box and press Save and test.",
            "Free models have a small daily limit. Adding a few dollars of credit raises it.",
        ],
        "base_url": "https://openrouter.ai/api/v1",
        "key_url": "https://openrouter.ai/keys",
        "prefer": [":free"],
        "privacy": "Messages are sent to OpenRouter and the model's provider.",
    },
    "ollama": {
        "label": "Ollama on this computer: private, free, needs a decent PC",
        "steps": [
            "Install Ollama from ollama.com/download and open it. It needs about 8 GB of RAM.",
            "In a terminal, run: ollama pull gemma3 (a 3 GB model that also understands photos).",
            "Leave Ollama running, then press Save and test here. No key needed.",
        ],
        "base_url": "http://localhost:11434/v1",
        "key_url": "https://ollama.com/download",
        "prefer": ["gemma3", "llama3.2-vision", "llava", "llama3"],
        "privacy": "Nothing leaves your computer.",
        "no_key": True,
    },
    "openai": {
        "label": "OpenAI API key (paid)",
        "steps": [
            "A ChatGPT Plus or Pro subscription does NOT include an API key. Have one? Pick Codex instead.",
            "Open platform.openai.com, sign in and add credit under Settings > Billing (pay as you go).",
            "Go to platform.openai.com/api-keys, click Create new secret key and copy it.",
            "Paste it in the API key box and press Save and test.",
        ],
        "base_url": "https://api.openai.com/v1",
        "key_url": "https://platform.openai.com/api-keys",
        "prefer": ["gpt-4.1-mini", "gpt-4o-mini", "mini"],
        "stt_prefer": ["whisper-1", "transcribe"],
        "privacy": "Messages are sent to OpenAI.",
    },
    "anthropic": {
        "label": "Claude API key (paid, by Anthropic)",
        "steps": [
            "A Claude Pro or Max plan does NOT include an API key. Have one? Pick Claude Code instead.",
            "Open console.anthropic.com, sign in and buy credit under Billing (pay as you go).",
            "Go to Settings > API keys, click Create Key and copy it.",
            "Paste it in the API key box, choose which Claude, and press Save and test.",
        ],
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
        "steps": [
            "For people who pay for Claude Pro or Max. No API key: replies use your plan's usage limits.",
            "Install Claude Code. Mac or Linux terminal: curl -fsSL https://claude.ai/install.sh | bash",
            "Or in Windows PowerShell: irm https://claude.ai/install.ps1 | iex",
            "Open a new terminal, run claude, sign in with your Claude account, then type /exit.",
            "Close and reopen IG Ultimate Tool, pick Claude Code and press Save and test.",
        ],
        "key_url": "https://code.claude.com/docs/en/setup",  # install guide (no key for this one)
        "kind": "cli",
        "privacy": "Messages are sent to Anthropic through Claude Code.",
    },
    "codex": {
        "label": "Codex (uses your ChatGPT subscription, slower, can read ALL your files)",
        "steps": [
            "For people who pay for ChatGPT Plus, Pro or Business. No API key needed.",
            "Install Node.js from nodejs.org. Then in a terminal run: npm install -g @openai/codex",
            "Run codex, choose Sign in with ChatGPT, then exit it.",
            "Close and reopen IG Ultimate Tool, pick Codex and press Save and test. Read the warning first.",
        ],
        "key_url": "https://developers.openai.com/codex/cli",  # install guide (no key for this one)
        "kind": "cli",
        "privacy": (
            "Messages are sent to OpenAI through Codex. WARNING: Codex can read every file your user "
            "account can (its read-only mode isn't limited to one folder). Only let people you fully "
            "trust talk to it."
        ),
    },
    "custom": {
        "label": "Any other OpenAI-compatible server (advanced)",
        "steps": [
            "For businesses or advanced users with their own AI server (LM Studio, vLLM, LiteLLM, ...).",
            "Enter the address ending in /v1. Use https://, or http://localhost for one on this computer.",
            "Paste the server's key if it needs one, then press Save and test.",
        ],
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
def check_url(url: str) -> str:
    """A server URL the key may be sent to: https, or plain http only to this computer."""
    u = parse_url(url.strip())
    if u.host and (
        u.scheme == "https" or (u.scheme == "http" and u.host in ("localhost", "127.0.0.1", "[::1]"))
    ):
        return url.strip()
    raise AIError("Use an https:// address. Plain http is only allowed for a server on this computer.")


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


NOT_CHAT = ("whisper", "guard", "orpheus", "tts", "embed", "moderation", "transcribe")


def pick(models: list[str], prefer: list[str]) -> str:
    for want in prefer:
        for model in models:
            if want in model:
                return model
    chat = [m for m in models if not any(w in m for w in NOT_CHAT)]  # never a speech or filter model
    return (chat or models or [""])[0]


def refresh_model(s: dict, log=lambda _s: None) -> None:
    """At Start: providers retire models, so an automatically picked one is re-picked from the live list."""
    p = PRESETS.get(s.get("provider", ""), {})
    if p.get("kind") or not s.get("base_url"):
        return  # Claude / CLI providers name their own models
    try:
        models = list_models(s)
    except (AIError, requests.RequestException):
        return  # offline: the reply itself will say what's wrong
    if not models:
        return
    if s.get("model_auto", True) or s.get("model") not in models:
        best = pick(models, p.get("prefer", []))
        if best and best != s.get("model"):
            log(
                f"Switched the AI model from {s.get('model') or 'none'} to {best} (best one available now)."
            )
            s["model"] = best
    if p.get("stt_prefer") and s.get("stt_model") not in models:
        s["stt_model"] = pick([m for m in models if any(w in m for w in p["stt_prefer"])], p["stt_prefer"])


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
    text = data["choices"][0]["message"].get("content") or ""
    return re.sub(r"<think>.*?(</think>|$)", "", text, flags=re.S).strip()  # reasoning models' notes


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
    path = shutil.which(exe)  # full path: on Windows npm installs a .cmd that a bare name won't start
    if not path:
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
                path,
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
                path,
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
            # The DM text goes in on stdin ("-"), never argv: argv is visible to other users (ps) and,
            # for a Windows .cmd, gets re-parsed by cmd.exe, where a crafted message could run commands.
            run = subprocess.run(  # noqa: S603 - fixed argv list, no shell
                [*cmd, "--", "-"],
                input=prompt,
                cwd=folder or tmp,
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
