"""config.py — load/save app settings & secrets to ~/.config/frogs_yt_replier/.

A single config.json holds everything (settings + secrets), locked to 0o600.
OAuth token, the user's client_secret, and the replied-comment dedupe store live
beside it. Defaults are merged over the file so adding new fields never breaks an
old config.
"""

import copy
import json
import os

APP_DIR_NAME = "frogs_yt_replier"

DEFAULT_SYSTEM_PROMPT = (
    "You are the friendly social-media voice of FrogTalk, a small brand that makes "
    "adorable frog plushies and crochet patterns. Reply to the YouTube comment below "
    "in a warm, genuine, non-spammy way. Keep it to one or two short sentences, match "
    "the commenter's energy, and use at most one frog emoji. Never sound like an ad or "
    "a bot; do not include links unless the comment explicitly asks where to buy."
)

# Filter words that surface comments with purchase intent. Ships as a built-in
# search preset so the old "buyer-intent" one-click is still a click away.
BUYER_INTENT_WORDS = [
    "where", "buy", "link", "shop", "price", "sell",
    "available", "purchase", "order", "how much",
]

# Fields that make up a search preset (mirrors the Search form).
PRESET_FIELDS = ("keywords", "max_videos", "max_comments", "match_words", "order")

DEFAULTS = {
    "youtube_api_key": "",
    "client_secret_path": "",  # set by user; '' -> use <config_dir>/client_secret.json
    "llm": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "api_key": "",
        "temperature": 0.8,    # 0 = focused/repeatable, 1.5 = wild
        "max_tokens": 200,     # reply length ceiling
    },
    "system_prompt": DEFAULT_SYSTEM_PROMPT,
    "reply_mode": "review",       # "review" | "auto"
    "rate_limit_seconds": 20,     # min seconds between posts
    "rate_limit_max_seconds": 0,  # if > min, wait a RANDOM time in [min, max]
    "per_run_cap": 25,
    "dry_run": False,
    "defaults": {
        "keywords": ["frog plushie", "amigurumi frog"],
        "max_videos": 10,
        "max_comments": 50,
        "match_words": [],
        "order": "comments_desc",
    },
    # Saved search presets. Seeded with the built-in buyer-intent filter so the
    # old one-click button lives on as a reusable, editable preset.
    "presets": [
        {
            "name": "Buyer intent",
            "keywords": ["frog plushie", "amigurumi frog"],
            "max_videos": 10,
            "max_comments": 50,
            "match_words": list(BUYER_INTENT_WORDS),
            "order": "comments_desc",
        }
    ],
}


def config_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    d = os.path.join(base, APP_DIR_NAME)
    os.makedirs(d, exist_ok=True)
    try:
        os.chmod(d, 0o700)  # holds secrets — keep it owner-only
    except OSError:
        pass
    return d


def config_path():
    return os.path.join(config_dir(), "config.json")


def token_path():
    return os.path.join(config_dir(), "token.json")


def replied_path():
    return os.path.join(config_dir(), "replied.json")


def _deep_merge(base, override):
    """Return base with override applied recursively (override wins)."""
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    """In-memory settings backed by config.json. Access fields as attributes."""

    def __init__(self, data):
        self._data = data

    # -- loading / saving -------------------------------------------------
    @classmethod
    def load(cls):
        data = copy.deepcopy(DEFAULTS)
        path = config_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = _deep_merge(data, json.load(f))
            except (json.JSONDecodeError, OSError):
                pass  # corrupt/unreadable -> fall back to defaults
        # Env override for the YouTube key only if config doesn't set one.
        if not data["youtube_api_key"]:
            data["youtube_api_key"] = os.environ.get("YT_API_KEY", "")
        return cls(data)

    def save(self):
        path = config_path()
        tmp = path + ".tmp"
        # Create the temp file 0600 up front so secrets are never briefly world-readable.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    # -- convenient access ------------------------------------------------
    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value

    @property
    def data(self):
        return self._data

    # -- search presets ---------------------------------------------------
    def presets(self):
        """The list of saved search presets (creates the list if missing)."""
        return self._data.setdefault("presets", [])

    def get_preset(self, name):
        """Return the preset matching name (case-insensitive), or None."""
        key = (name or "").strip().lower()
        for p in self.presets():
            if p.get("name", "").lower() == key:
                return p
        return None

    def save_preset(self, name, fields):
        """Create or overwrite a preset, then persist. Returns the stored dict."""
        name = (name or "").strip()
        stored = {"name": name, **{k: fields[k] for k in PRESET_FIELDS if k in fields}}
        existing = self.get_preset(name)
        if existing is not None:
            existing.clear()
            existing.update(stored)
        else:
            self.presets().append(stored)
        self.save()
        return stored

    def delete_preset(self, name):
        """Remove a preset by name (case-insensitive) and persist."""
        key = (name or "").strip().lower()
        before = len(self.presets())
        self._data["presets"] = [
            p for p in self.presets() if p.get("name", "").lower() != key
        ]
        if len(self._data["presets"]) != before:
            self.save()

    # -- derived helpers --------------------------------------------------
    def client_secret_file(self):
        return self._data["client_secret_path"] or os.path.join(
            config_dir(), "client_secret.json"
        )

    def has_api_key(self):
        return bool(self._data["youtube_api_key"])

    def has_llm_key(self):
        return bool(self._data["llm"]["api_key"])
