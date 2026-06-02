"""login.py — enter the YouTube API key and log in with Google (OAuth)."""

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, Static

from .. import oauth


class LoginScreen(Screen):
    BINDINGS = [("escape", "back", "Back")]

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(classes="form"):
            yield Label("YouTube Data API key (enables search & reading comments)")
            yield Input(
                value=self.app.cfg["youtube_api_key"],
                placeholder="AIza...",
                password=True,
                id="api-key",
            )
            with Horizontal(classes="actions"):
                yield Button("Save API key", id="save-key", variant="success")

            yield Label("Google login (required to POST replies as your channel)")
            yield Static(oauth.SETUP_HINT, classes="hint")
            yield Label("Path to client_secret.json (blank = config dir)")
            yield Input(
                value=self.app.cfg["client_secret_path"],
                placeholder=self.app.cfg.client_secret_file(),
                id="secret-path",
            )
            with Horizontal(classes="actions"):
                yield Button("Login with Google", id="login", variant="primary")
                yield Button("Log out", id="logout", variant="error")
            yield Static("", id="login-status", classes="hint")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_login_status()

    def _refresh_login_status(self) -> None:
        status = self.query_one("#login-status", Static)
        if self.app.is_logged_in():
            who = self.app.channel or "your channel"
            status.update(f"✅  Logged in as {who}")
        else:
            status.update("Not logged in — replying is disabled until you log in.")

    # -- actions ----------------------------------------------------------
    def action_back(self) -> None:
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "save-key":
            key = self.query_one("#api-key", Input).value.strip()
            self.app.cfg.set("youtube_api_key", key)
            self.app.cfg.save()
            self.app.notify("API key saved.", title="Saved")
        elif bid == "logout":
            oauth.logout()
            self.app.creds = None
            self.app.channel = None
            self._refresh_login_status()
            self.app.notify("Logged out.")
        elif bid == "login":
            secret = self.query_one("#secret-path", Input).value.strip()
            self.app.cfg.set("client_secret_path", secret)
            self.app.cfg.save()
            self.query_one("#login-status", Static).update("⏳  Opening browser — authorize in the window that pops up…")
            self._do_login(self.app.cfg.client_secret_file())

    # -- OAuth in a thread so the UI never freezes ------------------------
    @work(thread=True, exclusive=True)
    def _do_login(self, client_secret_file: str) -> None:
        try:
            creds = oauth.login(client_secret_file)
            channel = oauth.channel_title(creds)
        except oauth.OAuthError as e:
            self.app.call_from_thread(self._login_failed, str(e))
            return
        self.app.call_from_thread(self._login_ok, creds, channel)

    def _login_ok(self, creds, channel) -> None:
        self.app.set_credentials(creds, channel)
        self._refresh_login_status()
        self.app.notify(f"Logged in as {channel}.", title="Google login")

    def _login_failed(self, message: str) -> None:
        self.query_one("#login-status", Static).update(f"❌  {message}")
        self.app.notify("Login failed — see details on screen.", severity="error")
