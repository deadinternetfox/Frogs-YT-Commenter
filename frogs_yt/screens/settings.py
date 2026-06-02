"""settings.py — edit prompt, LLM provider, reply mode, throttle, defaults."""

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
    Select,
    Static,
    Switch,
    TextArea,
)

from .. import llm


class SettingsScreen(Screen):
    BINDINGS = [("escape", "back", "Back")]

    def compose(self) -> ComposeResult:
        cfg = self.app.cfg
        llm_cfg = cfg["llm"]
        yield Header()
        with VerticalScroll(classes="form"):
            yield Label("Reply prompt (system instructions for the AI)")
            yield Label("Tip: the comment text & author are added automatically.", classes="hint")
            yield TextArea(cfg["system_prompt"], id="prompt")

            yield Label("— LLM provider (OpenAI-compatible) —", classes="title")
            yield Label("Base URL")
            yield Input(llm_cfg["base_url"], id="llm-base", placeholder="https://api.deepseek.com")
            yield Label("Model")
            yield Input(llm_cfg["model"], id="llm-model", placeholder="deepseek-chat")
            yield Label("API key")
            yield Input(llm_cfg["api_key"], id="llm-key", password=True, placeholder="sk-…")
            with Horizontal(classes="row"):
                yield Input(str(llm_cfg.get("temperature", 0.8)), id="llm-temp", type="number")
                yield Input(str(llm_cfg.get("max_tokens", 200)), id="llm-maxtok", type="integer")
            yield Label("↑ temperature (0 = focused, 1.5 = creative)   ·   max reply length (tokens)",
                        classes="hint")
            with Horizontal(classes="actions"):
                yield Button("🧪  Test LLM", id="test-llm")
            yield Static("", id="llm-test-result", classes="hint")

            yield Label("— Replying —", classes="title")
            yield Label("Reply mode")
            yield Select(
                [("Review each (safe)", "review"), ("Auto-post (throttled)", "auto")],
                value=cfg["reply_mode"], id="reply-mode", allow_blank=False,
            )
            with Horizontal(classes="row"):
                yield Input(str(cfg["rate_limit_seconds"]), id="rate", type="integer")
                yield Input(str(cfg["per_run_cap"]), id="cap", type="integer")
            yield Label("↑ seconds between posts   ·   max posts per auto run", classes="hint")
            with Horizontal(classes="actions"):
                yield Label("Dry-run (log instead of posting)  ")
                yield Switch(value=cfg["dry_run"], id="dry-run")

            with Horizontal(classes="actions"):
                yield Button("💾  Save settings", id="save", variant="success")
                yield Button("⬅  Back", id="back")
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    def _int(self, wid, fallback):
        try:
            return max(0, int(self.query_one(f"#{wid}", Input).value))
        except (ValueError, TypeError):
            return fallback

    def _float(self, wid, fallback, lo=0.0, hi=2.0):
        try:
            return max(lo, min(hi, float(self.query_one(f"#{wid}", Input).value)))
        except (ValueError, TypeError):
            return fallback

    def _collect(self):
        cfg = self.app.cfg
        cfg["llm"]["base_url"] = self.query_one("#llm-base", Input).value.strip()
        cfg["llm"]["model"] = self.query_one("#llm-model", Input).value.strip()
        cfg["llm"]["api_key"] = self.query_one("#llm-key", Input).value.strip()
        cfg["llm"]["temperature"] = self._float("llm-temp", 0.8)
        cfg["llm"]["max_tokens"] = self._int("llm-maxtok", 200)
        cfg.set("system_prompt", self.query_one("#prompt", TextArea).text)
        cfg.set("reply_mode", self.query_one("#reply-mode", Select).value)
        cfg.set("rate_limit_seconds", self._int("rate", 20))
        cfg.set("per_run_cap", self._int("cap", 25))
        cfg.set("dry_run", self.query_one("#dry-run", Switch).value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self._collect()
            self.app.cfg.save()
            self.app.notify("Settings saved.", title="Saved")
        elif event.button.id == "back":
            self.action_back()
        elif event.button.id == "test-llm":
            self._collect()  # use the values currently on screen
            self.query_one("#llm-test-result", Static).update("…testing…")
            self._test_llm()

    @work(thread=True, exclusive=True)
    def _test_llm(self) -> None:
        try:
            text = llm.test_connection(self.app.cfg)
        except llm.LLMError as e:
            self.app.call_from_thread(self._test_result, f"❌ {e}", True)
            return
        self.app.call_from_thread(self._test_result, f"✅ {text}", False)

    def _test_result(self, msg, is_error) -> None:
        self.query_one("#llm-test-result", Static).update(msg)
        if is_error:
            self.app.notify("LLM test failed.", severity="error")
        else:
            self.app.notify("LLM responded!", title="Test OK")
