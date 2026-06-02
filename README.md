# 🐸 Frogs YouTube Replier

A terminal app (TUI) that finds YouTube videos by keyword, harvests their
comments, drafts replies with AI (DeepSeek by default), and — once you approve —
posts them as your channel. Built for small-brand marketing (FrogTalk), spam-safe
by design.

```
   .--.        .--.
  /    \      /    \      F R O G S
 | (o ) |    | ( o)|      YouTube Replier
  \    /------\    /
```

---

## What you need

| Thing | Why | Where to get it |
|------|-----|-----------------|
| **Python 3.9+** | runs the app | <https://python.org> (on Windows, tick *Add Python to PATH*) |
| **YouTube Data API v3 key** | search videos + read comments (read-only) | Google Cloud Console → APIs & Services → Credentials → *API key* |
| **Google OAuth client** (`client_secret.json`) | **only needed to POST replies** | Cloud Console → Credentials → *OAuth client ID* → **Desktop app** → *Download JSON* |
| **DeepSeek (or OpenAI-compatible) API key** | AI drafts the replies | <https://platform.deepseek.com> (or leave blank to use stub drafts) |

> An **API key only reads.** Posting replies requires **Google login (OAuth)**, which
> needs the downloaded `client_secret.json` file — the client *ID* string alone is not
> enough.

---

## Quick start

### macOS / Linux
```bash
cd frogs-youtube-replier
./frogs
```
(If needed: `chmod +x frogs` once.)

### Windows
Double-click **`frogs.bat`**, or in a terminal:
```bat
cd frogs-youtube-replier
frogs.bat
```

The first launch creates a local `.venv` and installs dependencies automatically
(needs internet once). After that it starts instantly. Force a clean reinstall with
`./frogs --reinstall` (or `frogs.bat --reinstall`).

---

## Using it

1. **Login screen** — paste your YouTube API key and Save. To post replies, set the
   path to your `client_secret.json` (or drop it in the config folder) and click
   **Login with Google** (a browser opens once).
2. **Search & Harvest** — type keywords + how deep to search, plus an optional
   word filter. **Sort results by** most/fewest comments, most/least viewed or
   liked, or newest/oldest (the harvest table shows comment + view counts).
   **💾 Save preset** stores the whole config under a name, and **📂 Open preset**
   loads it back (load · delete in place). A built-in **Buyer intent** preset
   filters comments to ones saying *where / buy / price / link* etc. Within a
   video, comments still sort by likes (press `o` to flip to recency).
3. **Review** (default, safest) — for each AI draft:
   - `a` accept (keeps your edits)  ·  `r` regenerate  ·  `s` skip  ·  `c` copy link
   - then `p` to **batch-post everything you accepted** (throttled).
4. **Auto-post mode** — generates *and* posts on a timer with a per-run cap. Toggle it
   in **Settings**.
5. **Settings** — edit the AI **prompt**, switch LLM provider/model, set
   **temperature** (lower = consistent, higher = varied) and reply length, reply mode,
   rate-limit, per-run cap, and the **Dry-run** switch.

Press **`?`** anywhere for in-app help. **`q`** quits, **`esc`** goes back.

### 🔒 Test safely first
Turn **Dry-run ON** in Settings. Then accept some drafts and post — the app *logs*
what it would send without actually posting. When the drafts look right, turn Dry-run
off. Keep the rate-limit and per-run cap modest: YouTube flags rapid automated replies
as spam. The app never replies to the same comment twice (it remembers across runs).

---

## Command line (no TUI)

The same engine is scriptable via `yt_comments.py`:

```bash
export YT_API_KEY="AIza...yourkey..."

# Search videos -> markdown link list
python3 yt_comments.py search "frog plushie" "amigurumi frog" --max 15

# Harvest comments from matching videos, filtered to buyer-intent words
python3 yt_comments.py harvest "crochet frog" --videos 10 --comments 50 \
        --match where buy link shop price --out leads.md

# Batch-generate AI drafts to a file for review (posts NOTHING)
python3 yt_comments.py draft "crochet frog" --videos 5 --limit 20 --out drafts.md

# Reply to one comment (uses your Google login)
python3 yt_comments.py reply COMMENT_ID "Thanks so much! 🐸"
```

---

## Where settings & secrets live

Everything is stored **on your machine only**, never sent anywhere except the APIs you
configured:

```
~/.config/frogs_yt_replier/
├── config.json         # your settings + keys   (chmod 600)
├── client_secret.json  # your Google OAuth client (chmod 600)
├── token.json          # cached login            (chmod 600)
└── replied.json        # which comments you've replied to (dedupe)
```

`config.example.json` (in this folder) shows the shape — you normally never edit it by
hand; use the in-app Login + Settings screens.

---

## Troubleshooting

- **"Access blocked" on Google login** → on the OAuth *consent screen*, add your Google
  account as a **Test user**, and make sure **YouTube Data API v3** is enabled in the
  same project.
- **"Daily quota exceeded"** → the YouTube API has a daily cap (each search ≈ 100 units
  of 10,000/day). Wait a day or use another key.
- **Drafts say `[stub reply — no LLM key set]`** → add a DeepSeek key in Settings →
  *Test LLM*.
- **`python: command not found` (Windows)** → reinstall Python and tick *Add to PATH*.

---

🤖 Built with Claude Code.
