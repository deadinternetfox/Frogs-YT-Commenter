"""replier.py — shared reply machinery for both reply modes.

- RepliedStore: persistent dedupe so a comment is never replied to twice (even
  across restarts). Keyed by the stable YouTube comment id.
- post_one(): the single funnel both Review and Auto-post go through, so dedupe,
  dry-run, and the actual API call are identical in both.
"""

import json
import os
import random

from . import config, core


def next_delay(cfg):
    """Seconds to wait before the next post.

    When a max is set above the min, return a random value in [min, max] so
    posting cadence looks human instead of metronomic; otherwise the fixed min.
    """
    lo = max(0, int(cfg.get("rate_limit_seconds", 20) or 0))
    hi = int(cfg.get("rate_limit_max_seconds", 0) or 0)
    return random.randint(lo, hi) if hi > lo else lo


def delay_label(cfg):
    """Human description of the post spacing, e.g. '20s' or '20–190s random'."""
    lo = max(0, int(cfg.get("rate_limit_seconds", 20) or 0))
    hi = int(cfg.get("rate_limit_max_seconds", 0) or 0)
    return f"{lo}–{hi}s random" if hi > lo else f"{lo}s"


class RepliedStore:
    """A persisted set of comment ids we've already replied to."""

    def __init__(self, path=None):
        self.path = path or config.replied_path()
        self._data = {}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def has(self, comment_id):
        # Dry-run entries are recorded for audit but must NOT block a later real
        # reply — otherwise previewing in dry-run would silently skip comments.
        entry = self._data.get(comment_id)
        return bool(entry) and not entry.get("dry_run")

    def count(self):
        return sum(1 for e in self._data.values() if not e.get("dry_run"))

    def add(self, comment_id, reply_id=None, dry_run=False):
        self._data[comment_id] = {"reply_id": reply_id, "dry_run": dry_run}
        self._save()

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)
        os.replace(tmp, self.path)


def post_one(youtube_service, comment, text, store, dry_run=False):
    """Post (or, in dry-run, pretend to post) one reply and record it.

    Returns the new reply id ('DRY-RUN' when dry_run). Raises on API failure.
    Skips silently-via-return if the comment was already replied to.
    """
    cid = comment["commentId"]
    if store.has(cid):
        return None  # already handled — dedupe
    if dry_run:
        store.add(cid, reply_id="DRY-RUN", dry_run=True)
        return "DRY-RUN"
    reply_id = core.post_reply(youtube_service, cid, text)
    store.add(cid, reply_id=reply_id, dry_run=False)
    return reply_id


def pending_comments(blocks, store):
    """Flatten harvest blocks into (video, comment) pairs not yet replied to."""
    out = []
    for video, comments in blocks:
        for c in comments:
            if not store.has(c["commentId"]):
                out.append((video, c))
    return out
