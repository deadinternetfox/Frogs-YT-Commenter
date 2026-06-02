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
from ..widgets import PresetPickerModal, SavePresetModal


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
            yield Label("Sort results by")
            yield Select(
                core.order_options(),
                value=core.valid_order(d["order"]),
                id="order",
                allow_blank=False,
            )
            yield Label("Only keep comments containing these words (optional, space/comma sep)")
            yield Input(" ".join(d["match_words"]), id="match", placeholder="where buy link shop price")
            with Horizontal(classes="actions"):
                yield Button("🐸  Run Harvest", id="run", variant="success")
                yield Button("💾  Save preset", id="save-preset")
                yield Button("📂  Open preset", id="open-presets")
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

    def _collect(self) -> dict:
        """Read the whole form into a config dict (the preset/defaults shape)."""
        match_raw = self.query_one("#match", Input).value
        return {
            "keywords": self._parse_keywords(),
            "max_videos": self._int("max-videos", 10),
            "max_comments": self._int("max-comments", 50),
            "order": self.query_one("#order", Select).value,
            "match_words": [w for w in match_raw.replace(",", " ").split() if w],
        }

    def _apply(self, cfg: dict) -> None:
        """Load a preset/defaults dict back into the form widgets."""
        self.query_one("#keywords", TextArea).load_text("\n".join(cfg.get("keywords", [])))
        self.query_one("#max-videos", Input).value = str(cfg.get("max_videos", 10))
        self.query_one("#max-comments", Input).value = str(cfg.get("max_comments", 50))
        self.query_one("#order", Select).value = core.valid_order(cfg.get("order"))
        self.query_one("#match", Input).value = " ".join(cfg.get("match_words", []))

    # -- preset name tracking (used to pre-fill the Save dialog) -----------
    _active_preset = None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-preset":
            self._open_save_dialog()
        elif event.button.id == "open-presets":
            self.app.push_screen(PresetPickerModal(), self._load_preset)
        elif event.button.id == "run":
            self._run()

    # -- presets ----------------------------------------------------------
    def _open_save_dialog(self) -> None:
        names = [p.get("name", "") for p in self.app.cfg.presets()]
        self.app.push_screen(
            SavePresetModal(self._active_preset or "", names), self._save_preset
        )

    def _save_preset(self, name) -> None:
        if not name:
            return
        self.app.cfg.save_preset(name, self._collect())
        self._active_preset = name
        self.app.notify(f"Saved preset “{name}”.")

    def _load_preset(self, name) -> None:
        if not name:
            return
        preset = self.app.cfg.get_preset(name)
        if not preset:
            return
        self._apply(preset)
        self._active_preset = name
        self.app.notify(f"Loaded preset “{name}”.")

    # -- run --------------------------------------------------------------
    def _run(self) -> None:
        if not self.app.cfg.has_api_key():
            self.app.notify("Set a YouTube API key first (Login screen).", severity="error")
            return
        cfg = self._collect()
        if not cfg["keywords"]:
            self.app.notify("Enter at least one keyword.", severity="warning")
            return

        # Persist these as the new defaults.
        self.app.cfg["defaults"].update(cfg)
        self.app.cfg.save()

        self.query_one("#run", Button).disabled = True
        self.query_one("#progress", ProgressBar).update(total=None)  # indeterminate
        self._set_status("Starting harvest…")
        self._harvest(
            cfg["keywords"], cfg["max_videos"], cfg["max_comments"],
            cfg["match_words"], cfg["order"],
        )

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
                match_words=match_words, order=core.api_order(order),
                progress=progress,
            )
        except core.YouTubeError as e:
            self.app.call_from_thread(self._harvest_failed, _friendly(e))
            return
        # Apply the precise client-side ordering (least/most, comment counts…).
        videos, blocks = core.sort_results(videos, blocks, order)
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
