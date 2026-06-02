"""dashboard.py — landing screen with the frog banner, status, and menu."""

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header

from ..widgets import FrogBanner, StatusPanel


class DashboardScreen(Screen):
    BINDINGS = [
        ("1", "go('search')", "Search"),
        ("2", "go('login')", "Login"),
        ("3", "go('settings')", "Settings"),
        ("?", "help", "Help"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield FrogBanner(subtitle="find videos · harvest comments · reply with AI")
        yield StatusPanel(id="status")
        with Vertical(id="menu"):
            yield Button("🔍  Search & Harvest", id="btn-search", variant="success")
            yield Button("🔑  Login / API Key", id="btn-login", variant="primary")
            yield Button("⚙   Settings", id="btn-settings")
            yield Button("✖   Quit", id="btn-quit", variant="error")
        yield Footer()

    def on_screen_resume(self) -> None:
        self.refresh_status()

    def on_mount(self) -> None:
        self.refresh_status()

    def refresh_status(self) -> None:
        app = self.app
        cfg = app.cfg
        login = (
            (f"as {app.channel}" if app.channel else "yes")
            if app.is_logged_in()
            else "not logged in"
        )
        llm = cfg["llm"]
        harvested = sum(len(c) for _, c in app.harvest_blocks)
        rows = [
            ("YouTube API key", "set" if cfg.has_api_key() else "missing", cfg.has_api_key()),
            ("Google login", login, app.is_logged_in()),
            ("LLM provider", f"{llm['model']} @ {llm['base_url']}", cfg.has_llm_key()),
            ("LLM key", "set" if cfg.has_llm_key() else "stub mode", cfg.has_llm_key()),
            ("Reply mode", cfg["reply_mode"] + (" · DRY-RUN" if cfg["dry_run"] else ""), True),
            ("Harvested (session)", f"{len(app.harvest_videos)} videos, {harvested} comments", True),
            ("Posted (session)", str(app.session_posted), True),
            ("Replied (all time)", str(app.replied.count()), True),
        ]
        self.query_one("#status", StatusPanel).show(rows)

    # -- actions ----------------------------------------------------------
    def action_go(self, screen: str) -> None:
        self.app.push_screen(screen)

    def action_help(self) -> None:
        self.app.notify(
            "API key = read (search/comments). Google login = post replies. "
            "Set an LLM key in Settings for real AI drafts.",
            title="How it works",
            timeout=8,
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "btn-search": "search",
            "btn-login": "login",
            "btn-settings": "settings",
        }
        if event.button.id == "btn-quit":
            self.app.exit()
        elif event.button.id in mapping:
            self.app.push_screen(mapping[event.button.id])
