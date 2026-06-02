"""harvest.py — show harvested videos + comments, launch the reply flow."""

import os

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Label

from .. import core


class HarvestScreen(Screen):
    BINDINGS = [
        ("escape", "back", "Back"),
        ("e", "export", "Export .md"),
        ("r", "reply", "Reply to comments"),
        ("c", "copy_link", "Copy link"),
        ("o", "toggle_sort", "Sort"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.video_filter = None       # None = all videos
        self.sort_by_likes = True      # default: most-liked first
        self.highlighted_comment = None
        self._repopulating = False     # ignore auto-highlight events during rebuild

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label("Videos — select one to filter comments below", classes="title")
        yield DataTable(id="videos-table", cursor_type="row")
        yield Label(id="comments-title", classes="title")
        yield DataTable(id="comments-table", cursor_type="row")
        with Horizontal(classes="actions"):
            mode = self.app.cfg["reply_mode"]
            label = "Auto-post replies" if mode == "auto" else "Review & reply"
            yield Button(f"💬  {label}", id="reply", variant="success")
            yield Button("📄  Export markdown", id="export")
            yield Button("🔗  Copy link", id="copy")
            yield Button("⬅  Back", id="back")
        yield Footer()

    def on_mount(self) -> None:
        vt = self.query_one("#videos-table", DataTable)
        vt.add_columns("Title", "Channel", "Keyword", "Comments", "Views")
        ct = self.query_one("#comments-table", DataTable)
        ct.add_columns("Author", "👍", "Replied", "Comment")
        self._populate_videos()
        self._show_comments()

    # -- data helpers -----------------------------------------------------
    def _total_comments(self) -> int:
        return sum(len(c) for _, c in self.app.harvest_blocks)

    def _populate_videos(self) -> None:
        vt = self.query_one("#videos-table", DataTable)
        self._repopulating = True
        vt.clear()
        vt.add_row(
            "‹ All videos ›", "", "", str(self._total_comments()), "", key="__all__"
        )
        for v in self.app.harvest_videos:
            vt.add_row(
                _trim(v["title"], 50),
                _trim(v["channel"], 20),
                _trim(v["matched_keyword"], 18),
                str(v.get("comment_count", 0)),
                _fmt_count(v.get("views", 0)),
                key=v["videoId"],
            )
        self._repopulating = False

    def _comments_for(self, video_id):
        out = []
        for video, comments in self.app.harvest_blocks:
            if video_id is None or video["videoId"] == video_id:
                out.extend(comments)
        if self.sort_by_likes:
            out = sorted(out, key=lambda c: c["likes"], reverse=True)
        return out

    def _show_comments(self) -> None:
        ct = self.query_one("#comments-table", DataTable)
        ct.clear()
        rows = self._comments_for(self.video_filter)
        for c in rows:
            replied = "✓" if self.app.replied.has(c["commentId"]) else ""
            ct.add_row(
                _trim(c["author"], 18),
                str(c["likes"]),
                replied,
                _trim(c["text"].replace("\n", " "), 70),
                key=c["commentId"],
            )
        sort_label = "by likes" if self.sort_by_likes else "by recency"
        scope = "all videos" if self.video_filter is None else "selected video"
        self.query_one("#comments-title", Label).update(
            f"Comments — {len(rows)} ({scope}, sorted {sort_label})"
        )
        self.highlighted_comment = rows[0]["commentId"] if rows else None

    # -- events -----------------------------------------------------------
    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "videos-table":
            if self._repopulating:
                return  # auto-highlight of row 0 during rebuild — don't reset filter
            key = event.row_key.value
            self.video_filter = None if key == "__all__" else key
            self._show_comments()
        elif event.data_table.id == "comments-table":
            self.highlighted_comment = event.row_key.value

    def on_button_pressed(self, event: Button.Pressed) -> None:
        handler = {
            "back": self.action_back,
            "export": self.action_export,
            "reply": self.action_reply,
            "copy": self.action_copy_link,
        }.get(event.button.id)
        if handler:
            handler()

    # -- actions ----------------------------------------------------------
    def action_back(self) -> None:
        self.app.pop_screen()

    def action_toggle_sort(self) -> None:
        self.sort_by_likes = not self.sort_by_likes
        self._show_comments()

    def _find_comment(self, comment_id):
        for _v, comments in self.app.harvest_blocks:
            for c in comments:
                if c["commentId"] == comment_id:
                    return c
        return None

    def action_copy_link(self) -> None:
        c = self._find_comment(self.highlighted_comment)
        if not c:
            self.app.notify("Highlight a comment first.", severity="warning")
            return
        self.app.copy_to_clipboard(c["link"])
        self.app.notify("Comment link copied to clipboard.")

    def action_export(self) -> None:
        path = os.path.join(os.getcwd(), "frogs_leads.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(core.render_comments_md(self.app.harvest_blocks))
        self.app.notify(f"Exported to {path}", title="Saved")

    def action_reply(self) -> None:
        if not self.app.harvest_blocks:
            self.app.notify("Nothing to reply to.", severity="warning")
            return
        if self.app.cfg["reply_mode"] == "auto":
            self.app.push_screen("autopost")
        else:
            self.app.push_screen("review")

    def on_screen_resume(self) -> None:
        # Refresh "replied" flags after returning from a reply flow.
        self._populate_videos()
        self._show_comments()


def _trim(s, n):
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


def _fmt_count(n):
    """Compact view count: 1234 -> 1.2k, 3_400_000 -> 3.4M."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{n / 1_000:.1f}k".replace(".0k", "k")
    return str(n)
