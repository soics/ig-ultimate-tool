# Security

## What the tool keeps on your computer

All files live in your per-user app-data folder (not the download folder), readable only by your user account:

| File | What it is | Sensitive? |
|---|---|---|
| `session.json` | Instagram's login session (cookies + device ids) | **Yes: full access to your account.** Never share it. Settings → *Log out of this computer* deletes it. |
| `settings.json` | Your choices: speed, keep-list, whitelist, AI settings (including the AI key you pasted) | Yes, it holds your AI key |
| `runs/*.json` | Progress of interrupted runs (Instagram ids only) | No |
| `lists/*.txt` | The last list each tool showed you | Usernames only |

Your Instagram **password is never written anywhere.**

## What leaves your computer

- To Instagram: everything the tools do, from your own internet connection.
- To the AI service you chose (only when AI auto-reply is on): the last few messages of a chat, and the photos / voice notes in it, to write a reply.
- Photos and voice notes are downloaded into memory only from Instagram's own servers (`cdninstagram.com`, `fbcdn.net`), size-capped, and never saved to disk (except as temporary files for Claude Code / Codex, deleted right after).

Nothing is sent to the authors. There is no telemetry.

## AI safety rails

- DM text is treated as untrusted: the AI is told never to follow instructions inside messages.
- Claude Code runs with `--restricted` and no tools (or only file-reading tools inside the one agent folder you choose). Codex runs read-only. AIs that can read files refuse to run in *Everyone* mode.
- Hourly reply caps prevent runaway bot-to-bot conversations.

## Reporting a vulnerability

Please use GitHub's **private vulnerability reporting** (Security tab → *Report a vulnerability*) instead of a public issue. You'll get a reply as soon as possible.
