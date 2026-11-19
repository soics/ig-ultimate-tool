# IG Ultimate Tool

**Clean up your Instagram in minutes instead of days.** Free, open source, and it runs only on your own computer.

Unfollow people who don't follow you back, delete chats you haven't touched in months, leave old group chats, unlike and unsave everything, wipe old posts and comments, or **nuke** the whole account back to a fresh start. It also has an **AI auto-reply** that answers DMs the way you text, including photos, memes, voice notes and reels.

No modded Instagram app, no paying a social media manager, no giving your password to a website. Every line of code is right here for anyone to read.

**Just want to know who doesn't follow you back?** Use the [no-login checker on the website](https://soics.github.io/ig-ultimate-tool/): drop the data file Instagram gives you, and it's read in your browser without uploading anything.

[![tests](https://github.com/soics/ig-ultimate-tool/actions/workflows/ci.yml/badge.svg)](https://github.com/soics/ig-ultimate-tool/actions/workflows/ci.yml) ![license: MIT](https://img.shields.io/badge/license-MIT-blue)

---

## What it can do

| | Tools |
|---|---|
| 👥 **Followers** | See who doesn't follow you back · unfollow them · unfollow everyone · unfollow a list you paste · remove followers you don't follow back · remove all followers · decline follow requests · a **keep-list** of people no tool will ever touch |
| 💬 **Chats** | Delete chats with no messages in the last *N* months (you pick *N*) · pick chats to delete · delete all chats · leave the group chats you pick, or all of them |
| 🖼️ **Posts** | Archive posts and reels (hidden, you can restore them) · delete them for good · only posts older than *N* months · restore archived posts · delete stories and highlights |
| ❤️ **Footprint** | Unlike everything you ever liked · unsave everything · delete all comments on your posts · remove yourself from photos you're tagged in |
| 🙍 **Profile** | Clear your bio, link and name · remove your profile picture · make the account private |
| ☢️ **Nuke** | All of the above in a safe order, to get a brand-new-looking account. Two confirmations plus a 5-second countdown you can cancel |
| 🤖 **AI auto-reply** | Answers DMs as you: **everyone**, only your **whitelist**, or only **usernames you paste**. Understands photos, memes, voice notes and reels. Free with Groq, or use Gemini, OpenRouter, Ollama, OpenAI, Claude, Claude Code or Codex |

Everything that changes your account shows you the full list first, offers a **dry run**, and only starts after you type `yes`.

## Is it safe?

- **Your password is never saved.** You type it hidden; only Instagram's login session is kept, in a file only your user account can read.
- **Nothing goes anywhere except Instagram** (and, if you turn it on, the AI service you pick for auto-replies). No tracking, no servers of ours, no accounts.
- **It goes slow on purpose.** It waits between actions like a person would, and stops by itself the moment Instagram says "slow down". There's a Fast mode in Settings if you want it.
- **Stopped halfway? It continues** where it left off next time.

> ⚠️ **Be honest with yourself about the risk.** IG Ultimate Tool is not made by Instagram or Meta. Automating an account goes against Instagram's terms; Instagram can temporarily block actions ("Action Blocked") or, rarely, limit an account. Older, real accounts are much lower risk than brand-new ones. Deleting is permanent. You use it at your own risk.

## Start it (3 steps)

**1. Download.** Click the green **Code** button at the top of this page → **Download ZIP**. Unzip it (right-click → *Extract All* on Windows, double-click on Mac).

**2. Open it.**

| Windows | Mac | Linux |
|---|---|---|
| Double-click **`Start-Windows.bat`**. If a blue box says *"Windows protected your PC"*, click **More info → Run anyway** (it shows that for any new file from the internet). | Right-click **`Start-Mac.command`** → **Open** → **Open**. (Only the first time; after that, double-click.) | Open a terminal in the folder and run `./start-linux.sh` |

The first time, it sets itself up (a minute or two). It installs [uv](https://docs.astral.sh/uv/), a small tool that downloads the right version of Python for you, so you don't have to install anything yourself.

**3. Follow the screen.** Type `I agree`, then choose **Try everything on a pretend account first** to look around safely, or **Log in to my Instagram** when you're ready. Type the number of what you want and press Enter. **Ctrl+C** always goes back.

## AI auto-reply in 2 minutes

1. Get a free key at [console.groq.com/keys](https://console.groq.com/keys) (sign in with Google, click *Create API Key*, copy it).
2. In the tool: **AI auto-reply → Set up AI → Groq** → paste the key.
3. **Personality**: your nickname and how you text ("lowercase, lots of lol, short replies").
4. **Who it replies to**: everyone, your whitelist, or usernames you paste.
5. **Try it here first** to chat with your AI without touching Instagram, then **Start replying**.

It only answers messages that arrive after you press Start, sends one message per reply, steps back if you reply yourself, and stays out of group chats unless you turn them on. If someone sincerely asks whether they're talking to a bot, it won't lie.

<details>
<summary><b>Which AI should I pick?</b></summary>

| AI | Cost | Photos | Voice notes | Privacy |
|---|---|---|---|---|
| **Groq** (recommended) | free | ✅ | ✅ | DMs go to Groq |
| Google Gemini | free | ✅ | ❌ | ⚠️ free tier: Google may use messages to improve its products and reviewers may read them |
| OpenRouter | free models, low daily limit | depends | ❌ | DMs go to OpenRouter |
| Ollama | free | depends on model | ❌ | nothing leaves your computer, needs a decent PC |
| OpenAI / Claude API | paid | ✅ | OpenAI ✅ | not used for training |
| Claude Code / Codex | your subscription | ✅ | ❌ | goes through Anthropic / OpenAI; slower |

**Claude Code and Codex can read files.** Give them one folder in *Agent folder* and they can answer questions about what's in it. For your safety they're never allowed to answer *Everyone*, only your whitelist or pasted usernames, because otherwise any stranger could ask the bot for your files.
</details>

## Questions

<details><summary><b>Will I get banned?</b></summary>
Very unlikely on an aged, real account at the default speed. What you may see is Instagram's temporary "Action Blocked" after lots of actions; the tool stops by itself when that happens. Wait 24–48 hours and run it again: it continues where it stopped.
</details>

<details><summary><b>It says "experimental"?</b></summary>
Leaving group chats, removing tags, removing your profile picture and clearing your name use Instagram features we couldn't fully test on every account yet. If one fails, nothing else is affected. Please <a href="https://github.com/soics/ig-ultimate-tool/issues">open an issue</a> if it doesn't work for you.
</details>

<details><summary><b>Does it work with two-factor login?</b></summary>
Yes. It asks for the code. If Instagram asks "Was this you?", approve it in the Instagram app, wait 15 minutes and try again.
</details>

<details><summary><b>Where are my files? How do I remove everything?</b></summary>
Settings → <b>Log out of this computer</b> deletes the login. To remove everything, delete the folder you unzipped and this folder:<br>
Windows: <code>%LOCALAPPDATA%\IG Ultimate Tool</code> · Mac: <code>~/Library/Application Support/IG Ultimate Tool</code> · Linux: <code>~/.local/share/IG Ultimate Tool</code>
</details>

<details><summary><b>How do I update?</b></summary>
Download the ZIP again and use the new folder. Your settings and login stay where they are.
</details>

<details><summary><b>For developers</b></summary>

```bash
git clone https://github.com/soics/ig-ultimate-tool && cd ig-ultimate-tool
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest && ruff check .
ig-ultimate --preview
```
Every tool is a *plan* (targets + one action) run by one safe-action engine (`src/ig_ultimate/engine.py`). Tests run against a fake account that mirrors the real library; a contract test fails if the code calls anything that doesn't exist in [instagrapi](https://github.com/subzeroid/instagrapi).
</details>

---

Built on [instagrapi](https://github.com/subzeroid/instagrapi). Not affiliated with, endorsed by, or connected to Instagram or Meta. [MIT license](LICENSE): free to use, change and share, with no warranty.
