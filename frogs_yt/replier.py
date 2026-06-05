"""replier.py — shared reply machinery for both reply modes.

- RepliedStore: persistent dedupe so a comment is never replied to twice (even
  across restarts). Keyed by the stable YouTube comment id.
- post_one(): the single funnel both Review and Auto-post go through, so dedupe,
  dry-run, and the actual API call are identical in both.
"""

import random

from . import core, db


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
    """A persisted set of comment ids we've already replied to.

    Backed by the SQLite DB (our_replies table) so the TUI, the inline reply
    flow, the AI agent, and the spider daemon all dedupe against one store. The
    public surface (has/add/count) is unchanged; legacy replied.json is imported
    once by db.init_db().
    """

    def __init__(self, path=None):
        # path kept for backwards-compatible construction; the DB owns the data.
        db.init_db()

    def has(self, comment_id):
        # Dry-run entries are recorded for audit but must NOT block a later real
        # reply — otherwise previewing in dry-run would silently skip comments.
        return db.has_reply(comment_id)

    def count(self):
        return db.reply_count()

    def add(self, comment_id, reply_id=None, dry_run=False, text=None, source="tui"):
        db.record_reply(comment_id, reply_id=reply_id, text=text,
                        dry_run=dry_run, source=source)


def post_one(youtube_service, comment, text, store, dry_run=False, source="tui"):
    """Post (or, in dry-run, pretend to post) one reply and record it.

    Returns the new reply id ('DRY-RUN' when dry_run). Raises on API failure.
    Skips silently-via-return if the comment was already replied to. `source`
    tags where the reply came from (review/auto/agent/inline/batch).
    """
    cid = comment["commentId"]
    if store.has(cid):
        return None  # already handled — dedupe
    if dry_run:
        store.add(cid, reply_id="DRY-RUN", dry_run=True, text=text, source=source)
        return "DRY-RUN"
    reply_id = core.post_reply(youtube_service, cid, text)
    store.add(cid, reply_id=reply_id, dry_run=False, text=text, source=source)
    return reply_id


def pending_comments(blocks, store):
    """Flatten harvest blocks into (video, comment) pairs not yet replied to."""
    out = []
    for video, comments in blocks:
        for c in comments:
            if not store.has(c["commentId"]):
                out.append((video, c))
    return out
