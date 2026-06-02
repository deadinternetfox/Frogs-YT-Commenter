"""search.py — keywords + search depth, runs the harvest in a worker."""

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ProgressBar,
    Select,
    Static,
    TextArea,
)

from .. import core

ORDERS = ["relevance", "date", "viewCount", "rating", "title"]


class SearchScreen(Screen):
    BINDINGS = [("escape", "back", "Back")]

    def compose(self) -> ComposeResult:
        d = self.app.cfg["defaults"]
        yield Header()
        with VerticalScroll(classes="form"):
            yield Label("Keywords (one per line or comma-separated)")
            yield TextArea("\n".join(d["keywords"]), id="keywords")
            with Horizontal(classes="row"):
                yield Input(str(d["max_videos"]), id="max-videos", type="integer")
                yield Input(str(d["max_comments"]), id="max-comments", type="integer")
            yield Label("↑ max videos per keyword   ·   max comments per video")
            yield Label("Order results by")
            yield Select(
                [(o, o) for o in ORDERS],
                value=d["order"] if d["order"] in ORDERS else "relevance",
                id="order",
                allow_blank=False,
            )
            yield Label("Only keep comments containing these words (optional, space/comma sep)")
            yield Input(" ".join(d["match_words"]), id="match", placeholder="where buy link shop price")
            with Horizontal(classes="actions"):
                yield Button("🐸  Run Harvest", id="run", variant="success")
                yield Button("🎯  Buyer-intent preset", id="preset")
            yield ProgressBar(id="progress", total=100, show_eta=False)
            yield Static("", id="status-line")
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    def _parse_keywords(self):
        raw = self.query_one("#keywords", TextArea).text
        parts = []
        for line in raw.replace(",", "\n").splitlines():
            line = line.strip()
            if line:
                parts.append(line)
        return parts

    def _int(self, widget_id, fallback):
        try:
            return max(1, int(self.query_one(f"#{widget_id}", Input).value))
        except (ValueError, TypeError):
            return fallback

    BUYER_INTENT = "where buy link shop price sell available purchase order how much"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "preset":
            self.query_one("#match", Input).value = self.BUYER_INTENT
            self.app.notify("Filled buyer-intent filter words.")
            return
        if event.button.id != "run":
            return
        if not self.app.cfg.has_api_key():
            self.app.notify("Set a YouTube API key first (Login screen).", severity="error")
            return
        keywords = self._parse_keywords()
        if not keywords:
            self.app.notify("Enter at least one keyword.", severity="warning")
            return
        max_videos = self._int("max-videos", 10)
        max_comments = self._int("max-comments", 50)
        order = self.query_one("#order", Select).value
        match_raw = self.query_one("#match", Input).value
        match_words = [w for w in match_raw.replace(",", " ").split() if w]

        # Persist these as the new defaults.
        self.app.cfg["defaults"].update(
            keywords=keywords, max_videos=max_videos, max_comments=max_comments,
            match_words=match_words, order=order,
        )
        self.app.cfg.save()

        self.query_one("#run", Button).disabled = True
        self.query_one("#progress", ProgressBar).update(total=None)  # indeterminate
        self._set_status("Starting harvest…")
        self._harvest(keywords, max_videos, max_comments, match_words, order)

    def _set_status(self, msg: str) -> None:
        self.query_one("#status-line", Static).update(msg)

    # -- worker -----------------------------------------------------------
    @work(thread=True, exclusive=True)
    def _harvest(self, keywords, max_videos, max_comments, match_words, order) -> None:
        def progress(done, total, label):
            self.app.call_from_thread(self._set_status, label)

        try:
            videos, blocks = core.harvest(
                keywords, self.app.cfg["youtube_api_key"],
                max_videos=max_videos, max_comments=max_comments,
                match_words=match_words, order=order, progress=progress,
            )
        except core.YouTubeError as e:
            self.app.call_from_thread(self._harvest_failed, _friendly(e))
            return
        self.app.call_from_thread(self._harvest_done, videos, blocks)

    def _harvest_done(self, videos, blocks) -> None:
        self.app.harvest_videos = videos
        self.app.harvest_blocks = blocks
        n_comments = sum(len(c) for _, c in blocks)
        self.query_one("#run", Button).disabled = False
        self._set_status(f"Done: {len(videos)} videos, {n_comments} comments.")
        if not videos:
            self.app.notify("No videos found for those keywords.", severity="warning")
            return
        self.app.push_screen("harvest")

    def _harvest_failed(self, message: str) -> None:
        self.query_one("#run", Button).disabled = False
        self._set_status(f"❌ {message}")
        self.app.notify(message, title="Harvest failed", severity="error")


def _friendly(err: "core.YouTubeError") -> str:
    if isinstance(err, core.YouTubeAPIError):
        if err.reason == "quotaExceeded":
            return "Daily API quota exceeded — try tomorrow or use another key."
        if err.code == 400:
            return f"Bad request: {err.message}"
        if err.code in (401, 403):
            return f"Access denied — check the API key. ({err.message})"
        return err.message
    return str(err)
