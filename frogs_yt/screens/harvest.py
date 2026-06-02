"""harvest.py — show harvested videos + comments, pick targets, launch replies."""

import os
from datetime import datetime

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Label

from .. import core, replier
from ..widgets import ExportModal
from .autopost import AutopostScreen
from .review import ReviewScreen

# Comment sort modes cycled with `o`.
#   (id, label, key over a (video, comment) pair, reverse?)
SORT_MODES = [
    ("likes",   "most liked", lambda vc: int(vc[1].get("likes", 0) or 0), True),
    ("recent",  "newest",     lambda vc: vc[1].get("published", ""),      True),
    ("oldest",  "oldest",     lambda vc: vc[1].get("published", ""),      False),
    ("keyword", "video keyword",
     lambda vc: ((vc[0].get("matched_keyword") or "").lower(),
                 -int(vc[1].get("likes", 0) or 0)), False),
    ("author",  "author A–Z", lambda vc: (vc[1].get("author") or "").lower(), False),
]


class HarvestScreen(Screen):
    BINDINGS = [
        ("escape", "back", "Back"),
        ("e", "export", "Export"),
        ("r", "reply", "Reply"),
        ("c", "copy_link", "Copy link"),
        ("o", "cycle_sort", "Sort"),
        ("space", "toggle_select", "Select"),
        ("a", "select_all", "Select all"),
        ("n", "select_none", "Clear sel"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.video_filter = None       # None = all videos
        self.sort_idx = 0              # index into SORT_MODES
        self.highlighted_comment = None
        self.selected = set()          # commentIds chosen to reply to
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
            yield Button("📄  Export…", id="export")
            yield Button("🔗  Copy link", id="copy")
            yield Button("⬅  Back", id="back")
        yield Footer()

    def on_mount(self) -> None:
        vt = self.query_one("#videos-table", DataTable)
        vt.add_columns("Title", "Channel", "Keyword", "Comments", "Views")
        ct = self.query_one("#comments-table", DataTable)
        ct.add_columns("Sel", "Author", "👍", "Replied", "Comment")
        self._populate_videos()
        self._show_comments()

    # -- data helpers -----------------------------------------------------
    def _all_pairs(self):
        """Every (video, comment) pair across the whole harvest."""
        return [(v, c) for v, comments in self.app.harvest_blocks for c in comments]

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

    def _pairs_for(self, video_id):
        pairs = [
            (v, c) for v, c in self._all_pairs()
            if video_id is None or v["videoId"] == video_id
        ]
        _id, _label, key, reverse = SORT_MODES[self.sort_idx]
        return sorted(pairs, key=key, reverse=reverse)

    def _show_comments(self) -> None:
        # Forget selections for comments no longer present (e.g. a new harvest).
        self.selected &= {c["commentId"] for _v, c in self._all_pairs()}
        ct = self.query_one("#comments-table", DataTable)
        ct.clear()
        pairs = self._pairs_for(self.video_filter)
        for _video, c in pairs:
            replied = "✓" if self.app.replied.has(c["commentId"]) else ""
            sel = "✅" if c["commentId"] in self.selected else ""
            ct.add_row(
                sel,
                _trim(c["author"], 18),
                str(c["likes"]),
                replied,
                _trim(c["text"].replace("\n", " "), 64),
                key=c["commentId"],
            )
        mode_label = SORT_MODES[self.sort_idx][1]
        scope = "all videos" if self.video_filter is None else "selected video"
        nsel = len(self.selected)
        sel_txt = f"   ·   {nsel} selected" if nsel else "   ·   none selected → reply to all"
        self.query_one("#comments-title", Label).update(
            f"Comments — {len(pairs)} ({scope}, sorted by {mode_label}){sel_txt}"
        )
        self.highlighted_comment = pairs[0][1]["commentId"] if pairs else None

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

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # Enter / click on a comment row toggles whether it's a reply target.
        if event.data_table.id == "comments-table":
            self._toggle(event.row_key.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        handler = {
            "back": self.action_back,
            "export": self.action_export,
            "reply": self.action_reply,
            "copy": self.action_copy_link,
        }.get(event.button.id)
        if handler:
            handler()

    # -- selection --------------------------------------------------------
    def _toggle(self, comment_id) -> None:
        if not comment_id:
            return
        self.selected.discard(comment_id) if comment_id in self.selected \
            else self.selected.add(comment_id)
        self._refresh_keep_cursor()

    def action_toggle_select(self) -> None:
        self._toggle(self.highlighted_comment)

    def action_select_all(self) -> None:
        self.selected.update(c["commentId"] for _v, c in self._pairs_for(self.video_filter))
        self._refresh_keep_cursor()

    def action_select_none(self) -> None:
        self.selected.clear()
        self._refresh_keep_cursor()

    def _refresh_keep_cursor(self) -> None:
        ct = self.query_one("#comments-table", DataTable)
        row = ct.cursor_row
        self._show_comments()
        if row is not None and 0 <= row < ct.row_count:
            ct.move_cursor(row=row)

    # -- actions ----------------------------------------------------------
    def action_back(self) -> None:
        self.app.pop_screen()

    def action_cycle_sort(self) -> None:
        self.sort_idx = (self.sort_idx + 1) % len(SORT_MODES)
        self._refresh_keep_cursor()

    def _find_comment(self, comment_id):
        for _v, c in self._all_pairs():
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

    # -- reply ------------------------------------------------------------
    def action_reply(self) -> None:
        if not self.app.harvest_blocks:
            self.app.notify("No harvested comments yet — run a search first.",
                            severity="warning")
            return
        if self.selected:
            targets = [(v, c) for v, c in self._all_pairs()
                       if c["commentId"] in self.selected]
            scope = f"{len(targets)} selected"
        else:
            targets = self._all_pairs()
            scope = f"all {len(targets)} harvested"
        # Drop ones already replied to (dedupe never double-replies).
        pending = [(v, c) for v, c in targets
                   if not self.app.replied.has(c["commentId"])]
        if not pending:
            self.app.notify(
                f"Every one of the {scope} comments has already been replied to.",
                title="Nothing new to reply to", severity="warning", timeout=7,
            )
            return
        self.app.reply_targets = pending
        already = len(targets) - len(pending)
        note = f" ({already} already replied — skipped)" if already else ""
        self.app.notify(f"Replying to {len(pending)} comments{note}.")
        if self.app.cfg["reply_mode"] == "auto":
            self.app.push_screen(AutopostScreen())
        else:
            self.app.push_screen(ReviewScreen())

    # -- export -----------------------------------------------------------
    def action_export(self) -> None:
        if not self.app.harvest_blocks:
            self.app.notify("No harvested comments to export.", severity="warning")
            return
        self.app.push_screen(ExportModal(core.EXPORT_FORMATS), self._do_export)

    def _do_export(self, fmt) -> None:
        if not fmt:
            return
        now = datetime.now()
        blocks = self.app.harvest_blocks
        renderers = {
            "html": (lambda b: core.render_comments_html(
                b, generated=now.strftime("%Y-%m-%d %H:%M")), "html"),
            "md": (core.render_comments_md, "md"),
            "txt": (core.render_comments_txt, "txt"),
            "csv": (core.render_comments_csv, "csv"),
            "json": (core.render_comments_json, "json"),
        }
        render, ext = renderers[fmt]
        path = os.path.join(os.getcwd(), f"frogs_harvest_{now.strftime('%Y%m%d_%H%M%S')}.{ext}")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(render(blocks))
        except OSError as e:
            self.app.notify(f"Export failed: {e}", severity="error")
            return
        self.app.notify(f"Exported {self._total_comments()} comments → {path}",
                        title="Saved 🐸", timeout=7)

    def on_screen_resume(self) -> None:
        # Refresh "replied" flags + any new harvest after returning to this screen.
        # This screen is reused across harvests, so drop a filter that points at a
        # video from a previous harvest (otherwise the comment list looks empty).
        if self.video_filter not in {v["videoId"] for v in self.app.harvest_videos}:
            self.video_filter = None
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
