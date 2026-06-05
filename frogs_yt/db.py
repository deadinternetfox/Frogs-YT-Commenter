"""db.py — SQLite persistence shared by the TUI and the spider daemon.

The database is the single source of truth for harvested videos/comments, our
posted replies (dedupe), spider state, and match tags. It lives at
config.db_path() under the 0700 config dir.

Concurrency model
-----------------
WAL mode lets many readers coexist with one writer, so the TUI and the detached
spider daemon can both touch the file. We never share a Connection across
threads — every operation opens a short-lived connection via connect() (which
applies the PRAGMAs) and closes it. busy_timeout makes the rare writer-vs-writer
overlap a transparent retry instead of a "database is locked" error.

All timestamps are ISO8601 UTC strings (utcnow().isoformat()), so they sort
lexically and match the shapes the YouTube API already hands us.
"""

import json
import os
import sqlite3
from datetime import datetime

from . import config

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS videos (
    video_id        TEXT PRIMARY KEY,
    title           TEXT,
    channel         TEXT,
    channel_id      TEXT,
    published       TEXT,
    url             TEXT,
    views           INTEGER DEFAULT 0,
    likes           INTEGER DEFAULT 0,
    total_comments  INTEGER DEFAULT 0,
    matched_keyword TEXT,
    status          TEXT DEFAULT 'ok',   -- 'ok' | 'gone' | 'comments_disabled'
    first_seen      TEXT,
    fetched_at      TEXT
);

CREATE TABLE IF NOT EXISTS comments (
    comment_id        TEXT PRIMARY KEY,
    video_id          TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    parent_id         TEXT,              -- NULL for top-level, else parent comment_id
    is_reply          INTEGER DEFAULT 0,
    thread_id         TEXT,              -- top-level comment_id of the whole thread
    author            TEXT,
    author_channel_id TEXT,
    text              TEXT,
    likes             INTEGER DEFAULT 0,
    published         TEXT,
    updated           TEXT,
    link              TEXT,
    status            TEXT DEFAULT 'ok', -- 'ok' | 'gone'
    fetched_at        TEXT,
    first_seen        TEXT
);

CREATE TABLE IF NOT EXISTS our_replies (
    comment_id  TEXT PRIMARY KEY,        -- the comment we replied to (dedupe key)
    reply_id    TEXT,                    -- YouTube id of our reply ('DRY-RUN' in dry-run)
    text        TEXT,
    dry_run     INTEGER DEFAULT 0,
    posted_at   TEXT,
    source      TEXT                     -- 'review'|'auto'|'agent'|'batch'|'inline'|'tui'
);

CREATE TABLE IF NOT EXISTS spider_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT,
    ended_at      TEXT,
    status        TEXT,                  -- 'running'|'done'|'error'|'quota'
    keywords      TEXT,
    videos_seen   INTEGER DEFAULT 0,
    comments_seen INTEGER DEFAULT 0,
    quota_used    INTEGER DEFAULT 0,
    note          TEXT
);

CREATE TABLE IF NOT EXISTS spider_status (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    pid              INTEGER,
    state            TEXT,               -- 'starting'|'idle'|'crawling'|'backoff'|'stopped'|'error'
    heartbeat_at     TEXT,
    current_task     TEXT,
    quota_used_today INTEGER DEFAULT 0,
    quota_reset_at   TEXT,
    last_error       TEXT,
    started_at       TEXT
);

CREATE TABLE IF NOT EXISTS match_tags (
    comment_id  TEXT NOT NULL REFERENCES comments(comment_id) ON DELETE CASCADE,
    tag         TEXT NOT NULL,
    score       REAL DEFAULT 0,
    matched_at  TEXT,
    PRIMARY KEY (comment_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_comments_video  ON comments(video_id);
CREATE INDEX IF NOT EXISTS idx_comments_thread ON comments(thread_id);
CREATE INDEX IF NOT EXISTS idx_comments_parent ON comments(parent_id);
CREATE INDEX IF NOT EXISTS idx_comments_likes  ON comments(likes);
CREATE INDEX IF NOT EXISTS idx_comments_pub    ON comments(published);
CREATE INDEX IF NOT EXISTS idx_comments_status ON comments(status);
CREATE INDEX IF NOT EXISTS idx_tags_tag        ON match_tags(tag);
"""

_initialized = False


def _now():
    return datetime.utcnow().isoformat()


def connect(path=None):
    """Open a connection with our standard PRAGMAs. Caller closes it.

    Row factory is sqlite3.Row so callers can use dict-style access.
    """
    conn = sqlite3.connect(path or config.db_path(), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(path=None):
    """Create tables (idempotent) and run one-time migrations.

    Cheap to call repeatedly; the first call per process does the work and a
    module flag short-circuits the rest.
    """
    global _initialized
    if _initialized and path is None:
        return
    conn = connect(path)
    try:
        conn.executescript(_SCHEMA)
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO schema_meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        _migrate_replied_json(conn)
    finally:
        conn.close()
    if path is None:
        _initialized = True


def _meta_get(conn, key):
    row = conn.execute("SELECT value FROM schema_meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def _meta_set(conn, key, value):
    with conn:
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )


def _migrate_replied_json(conn):
    """Import the legacy replied.json dedupe store exactly once.

    The JSON is left in place as a backup; a schema_meta sentinel prevents a
    second import (which would otherwise clobber DB rows on every boot).
    """
    if _meta_get(conn, "replied_migrated"):
        return
    path = config.replied_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = {}
        now = _now()
        with conn:
            for cid, entry in (data or {}).items():
                entry = entry or {}
                conn.execute(
                    "INSERT OR IGNORE INTO our_replies"
                    "(comment_id, reply_id, text, dry_run, posted_at, source) "
                    "VALUES(?, ?, NULL, ?, ?, 'migrated')",
                    (cid, entry.get("reply_id"),
                     1 if entry.get("dry_run") else 0, now),
                )
    _meta_set(conn, "replied_migrated", "1")


# --------------------------------------------------------------------------
# Replies / dedupe — backs RepliedStore
# --------------------------------------------------------------------------
def record_reply(comment_id, reply_id=None, text=None, dry_run=False, source="tui"):
    conn = connect()
    try:
        with conn:
            conn.execute(
                "INSERT INTO our_replies"
                "(comment_id, reply_id, text, dry_run, posted_at, source) "
                "VALUES(?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(comment_id) DO UPDATE SET "
                "  reply_id=excluded.reply_id, text=excluded.text, "
                "  dry_run=excluded.dry_run, posted_at=excluded.posted_at, "
                "  source=excluded.source",
                (comment_id, reply_id, text, 1 if dry_run else 0, _now(), source),
            )
    finally:
        conn.close()


def has_reply(comment_id):
    """True only when a non-dry-run reply exists (mirrors RepliedStore.has)."""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT dry_run FROM our_replies WHERE comment_id=?", (comment_id,)
        ).fetchone()
        return bool(row) and not row["dry_run"]
    finally:
        conn.close()


def reply_count():
    conn = connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM our_replies WHERE dry_run=0"
        ).fetchone()
        return row["n"] if row else 0
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Upserts for harvested data (used by Phase 2 harvest + Phase 3 spider)
# --------------------------------------------------------------------------
def upsert_video(video, conn=None):
    """Insert/refresh a video row. Keeps first_seen, refreshes the rest."""
    own = conn is None
    conn = conn or connect()
    try:
        now = _now()
        with conn:
            conn.execute(
                "INSERT INTO videos"
                "(video_id, title, channel, channel_id, published, url, views, "
                " likes, total_comments, matched_keyword, status, first_seen, fetched_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, 'ok'), ?, ?) "
                "ON CONFLICT(video_id) DO UPDATE SET "
                "  title=excluded.title, channel=excluded.channel, "
                "  channel_id=excluded.channel_id, published=excluded.published, "
                "  url=excluded.url, views=excluded.views, likes=excluded.likes, "
                "  total_comments=excluded.total_comments, "
                "  matched_keyword=COALESCE(videos.matched_keyword, excluded.matched_keyword), "
                "  fetched_at=excluded.fetched_at",
                (video["videoId"], video.get("title"), video.get("channel"),
                 video.get("channelId"), video.get("published"), video.get("url"),
                 int(video.get("views", 0) or 0), int(video.get("likes", 0) or 0),
                 int(video.get("total_comments", 0) or 0),
                 video.get("matched_keyword"), video.get("status"), now, now),
            )
    finally:
        if own:
            conn.close()


def upsert_comment(comment, conn=None):
    """Insert/refresh a comment row. Refreshes text/likes/fetched_at; keeps first_seen.

    The comment dict uses the shapes core.py produces (commentId, author, text,
    likes, published, videoId, link) plus the nested-thread fields parentId,
    isReply, threadId added in Phase 2.
    """
    own = conn is None
    conn = conn or connect()
    try:
        now = _now()
        with conn:
            conn.execute(
                "INSERT INTO comments"
                "(comment_id, video_id, parent_id, is_reply, thread_id, author, "
                " author_channel_id, text, likes, published, updated, link, status, "
                " fetched_at, first_seen) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, 'ok'), ?, ?) "
                "ON CONFLICT(comment_id) DO UPDATE SET "
                "  text=excluded.text, likes=excluded.likes, updated=excluded.updated, "
                "  fetched_at=excluded.fetched_at, status='ok'",
                (comment["commentId"], comment.get("videoId"),
                 comment.get("parentId"), 1 if comment.get("isReply") else 0,
                 comment.get("threadId") or comment["commentId"],
                 comment.get("author"), comment.get("authorChannelId"),
                 comment.get("text"), int(comment.get("likes", 0) or 0),
                 comment.get("published"), comment.get("updated"),
                 comment.get("link"), comment.get("status"), now, now),
            )
    finally:
        if own:
            conn.close()


def tag_match(comment_id, tag, score=0.0, conn=None):
    own = conn is None
    conn = conn or connect()
    try:
        with conn:
            conn.execute(
                "INSERT INTO match_tags(comment_id, tag, score, matched_at) "
                "VALUES(?, ?, ?, ?) "
                "ON CONFLICT(comment_id, tag) DO UPDATE SET score=excluded.score, "
                "  matched_at=excluded.matched_at",
                (comment_id, tag, float(score), _now()),
            )
    finally:
        if own:
            conn.close()


def mark_video_status(video_id, status):
    conn = connect()
    try:
        with conn:
            conn.execute("UPDATE videos SET status=? WHERE video_id=?", (status, video_id))
    finally:
        conn.close()


def mark_comment_status(comment_id, status):
    conn = connect()
    try:
        with conn:
            conn.execute("UPDATE comments SET status=? WHERE comment_id=?",
                         (status, comment_id))
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Reads (used by Phase 2 UI + Phase 4 agent)
# --------------------------------------------------------------------------
def get_comment(comment_id):
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM comments WHERE comment_id=?",
                           (comment_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_video(video_id):
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM videos WHERE video_id=?",
                           (video_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_thread(thread_or_comment_id):
    """Return {'top': comment_dict|None, 'replies': [comment_dict,...]} for a thread.

    Accepts either the thread (top-level) id or any comment id within it.
    """
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM comments WHERE comment_id=?",
                           (thread_or_comment_id,)).fetchone()
        thread_id = (row["thread_id"] if row else None) or thread_or_comment_id
        top = conn.execute(
            "SELECT * FROM comments WHERE comment_id=?", (thread_id,)
        ).fetchone()
        replies = conn.execute(
            "SELECT * FROM comments WHERE thread_id=? AND is_reply=1 "
            "ORDER BY published ASC", (thread_id,)
        ).fetchall()
        return {
            "top": dict(top) if top else None,
            "replies": [dict(r) for r in replies],
        }
    finally:
        conn.close()


def stats():
    """Aggregate counts for dashboards / the agent's get_stats tool."""
    conn = connect()
    try:
        def scalar(sql, *params):
            r = conn.execute(sql, params).fetchone()
            return r[0] if r else 0
        return {
            "videos": scalar("SELECT COUNT(*) FROM videos"),
            "comments": scalar("SELECT COUNT(*) FROM comments"),
            "top_level": scalar("SELECT COUNT(*) FROM comments WHERE is_reply=0"),
            "replies_to_comments": scalar("SELECT COUNT(*) FROM comments WHERE is_reply=1"),
            "replied": scalar("SELECT COUNT(*) FROM our_replies WHERE dry_run=0"),
            "pending": scalar(
                "SELECT COUNT(*) FROM comments c WHERE c.status='ok' AND "
                "NOT EXISTS (SELECT 1 FROM our_replies r "
                "            WHERE r.comment_id=c.comment_id AND r.dry_run=0)"
            ),
        }
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Spider state (used by Phase 3)
# --------------------------------------------------------------------------
def start_run(keywords=None):
    conn = connect()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO spider_runs(started_at, status, keywords) VALUES(?, 'running', ?)",
                (_now(), json.dumps(keywords) if keywords is not None else None),
            )
            return cur.lastrowid
    finally:
        conn.close()


def end_run(run_id, status="done", videos_seen=0, comments_seen=0, quota_used=0, note=None):
    conn = connect()
    try:
        with conn:
            conn.execute(
                "UPDATE spider_runs SET ended_at=?, status=?, videos_seen=?, "
                "comments_seen=?, quota_used=?, note=? WHERE id=?",
                (_now(), status, videos_seen, comments_seen, quota_used, note, run_id),
            )
    finally:
        conn.close()


def update_status(**fields):
    """Upsert the singleton spider_status row. Always stamps heartbeat_at."""
    fields.setdefault("heartbeat_at", _now())
    cols = list(fields.keys())
    conn = connect()
    try:
        with conn:
            placeholders = ", ".join("?" for _ in cols)
            updates = ", ".join(f"{c}=excluded.{c}" for c in cols)
            conn.execute(
                f"INSERT INTO spider_status(id, {', '.join(cols)}) "
                f"VALUES(1, {placeholders}) "
                f"ON CONFLICT(id) DO UPDATE SET {updates}",
                tuple(fields[c] for c in cols),
            )
    finally:
        conn.close()


def read_status():
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM spider_status WHERE id=1").fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
