"""app.py — FrogsApp: the Textual application shell, theme and shared state."""

from textual.app import App
from textual.theme import Theme

from . import config, oauth
from .replier import RepliedStore
from .screens.dashboard import DashboardScreen
from .screens.harvest import HarvestScreen
from .screens.login import LoginScreen
from .screens.search import SearchScreen
from .screens.settings import SettingsScreen
from .widgets import HelpModal

FROG_THEME = Theme(
    name="frog",
    primary="#2e8bba",
    secondary="#8bc34a",
    accent="#a4e057",
    foreground="#e6f4d8",
    background="#0e1a10",
    surface="#13251a",
    panel="#1b3324",
    success="#57c84d",
    warning="#e0c84a",
    error="#e05a4a",
    dark=True,
)


class FrogsApp(App):
    TITLE = "🐸 Frogs YouTube Replier"
    CSS_PATH = "app.tcss"

    # Review and Auto-post are intentionally NOT registered here: they're pushed
    # as fresh instances from Harvest so their reply queue is rebuilt each run.
    # Registered screens are reused (on_mount fires once), which would go stale.
    SCREENS = {
        "dashboard": DashboardScreen,
        "login": LoginScreen,
        "search": SearchScreen,
        "harvest": HarvestScreen,
        "settings": SettingsScreen,
    }

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("ctrl+c", "quit", "Quit"),
        ("question_mark", "help", "Help"),
    ]

    def __init__(self):
        super().__init__()
        self.cfg = config.Config.load()
        self.replied = RepliedStore()
        self.creds = None            # OAuth credentials, populated on login
        self.channel = None          # channel title once logged in
        # In-memory harvest results shared across screens.
        self.harvest_videos = []
        self.harvest_blocks = []
        # Comments chosen on the Harvest screen for the next reply run
        # (list of (video, comment) pairs). None -> reply to all harvested.
        self.reply_targets = None
        # Session counters (for the dashboard).
        self.session_posted = 0

    def on_mount(self) -> None:
        self.register_theme(FROG_THEME)
        self.theme = "frog"
        # Try to reuse a cached Google login silently (non-interactive).
        try:
            creds = oauth.load_cached_credentials()
            if creds:
                self.creds = creds
                self.run_worker(self._resolve_channel, thread=True)
        except oauth.OAuthError:
            pass  # libs missing — login screen will explain
        if self.cfg.has_api_key():
            self.push_screen("dashboard")
        else:
            self.push_screen("login")

    def _resolve_channel(self) -> None:
        """Look up the logged-in channel name in the background (cosmetic)."""
        try:
            name = oauth.channel_title(self.creds)
        except Exception:
            return
        self.call_from_thread(setattr, self, "channel", name)

    def action_help(self) -> None:
        self.push_screen(HelpModal())

    # -- shared helpers ---------------------------------------------------
    def is_logged_in(self) -> bool:
        return self.creds is not None

    def youtube_service(self):
        """Build (and cache) the authorized YouTube service, or None."""
        if not self.creds:
            return None
        if not getattr(self, "_yt_service", None):
            self._yt_service = oauth.build_service(self.creds)
        return self._yt_service

    def set_credentials(self, creds, channel):
        self.creds = creds
        self.channel = channel
        self._yt_service = None  # rebuild lazily with fresh creds


def main(argv=None):
    # Keep the TUI clean: dependency import warnings (e.g. google-api-core's
    # Python-version FutureWarning) must not bleed onto the screen.
    import warnings

    warnings.filterwarnings("ignore")
    FrogsApp().run()


if __name__ == "__main__":
    main()
