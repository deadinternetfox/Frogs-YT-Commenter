"""widgets.py — shared Textual widgets (frog banner, status panel, modals)."""

from rich.align import Align
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Grid, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

FROG_ART = r"""
         .--.        .--.
        /    \      /    \        F R O G S
       | (o ) |    | ( o)|        ─────────────
        \    /------\    /        YouTube  Replier
         '--'   ..   '--'
        /  _.-'    '-._  \
       |  /            \  |
        \ \    \__/    / /
         '.'._      _.'.'
            `--`----`--`
"""


class FrogBanner(Static):
    """ASCII frog + title banner."""

    def __init__(self, subtitle="", **kwargs):
        super().__init__(**kwargs)
        self._subtitle = subtitle

    def on_mount(self):
        art = Text(FROG_ART, style="bold #57c84d", justify="center")
        if self._subtitle:
            art.append("\n" + self._subtitle, style="dim italic")
        self.update(Align.center(art))


class StatusPanel(Static):
    """A simple key→value status block, refreshed from a list of rows."""

    border_title = "status"

    def show(self, rows):
        t = Text()
        for label, value, ok in rows:
            t.append(f"  {label:<18}", style="bold")
            t.append(f"{value}\n", style="green" if ok else "red")
        self.update(t)


class ConfirmModal(ModalScreen[bool]):
    """A yes/no confirmation dialog. Dismisses with True/False."""

    BINDINGS = [("escape", "cancel", "Cancel"), ("y", "yes", "Yes"), ("n", "cancel", "No")]

    def __init__(self, question, confirm_label="Confirm", **kwargs):
        super().__init__(**kwargs)
        self._question = question
        self._confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Grid(id="confirm-grid"):
            yield Label(self._question, id="confirm-question")
            yield Button(f"{self._confirm_label} (y)", variant="error", id="confirm-yes")
            yield Button("Cancel (n)", variant="primary", id="confirm-no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-yes")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


HELP_TEXT = """\
[b #57c84d]How Frogs YouTube Replier works[/]

[b]1. API key[/]  — enables searching videos & reading comments (read-only).
[b]2. Google login[/]  — required only to POST replies, as your channel.
[b]3. LLM key[/]  — DeepSeek (or any OpenAI-compatible) drafts the replies.
   Without an LLM key you still get a clearly-labelled stub draft.

[b #57c84d]Workflow[/]
 • [b]Search & Harvest[/] — keywords + depth; the "buyer-intent preset" filters
   to comments saying where / buy / price etc. Comments sort by likes.
 • [b]Review[/] mode — for each AI draft: [b]a[/] accept (keeps edits), [b]r[/] regenerate,
   [b]s[/] skip, [b]c[/] copy link. Then [b]p[/] to batch-post everything you accepted.
 • [b]Auto-post[/] mode — generates + posts on a timer with a per-run cap.
 • [b]Dry-run[/] (Settings) — logs replies instead of posting. Test with this ON.
 • [b]Temperature[/] (Settings) — lower = safe/consistent, higher = varied drafts.

[b #57c84d]Global keys[/]
 [b]?[/] help      [b]s[/] settings (from dashboard)      [b]q[/] quit
 [b]esc[/] back    [b]ctrl+p[/] command palette

[b #57c84d]Safety[/]
 Replies post publicly as your channel. Keep the rate-limit & cap modest —
 YouTube flags rapid automated replies as spam. Dedupe prevents double replies.

[dim]Press esc to close[/]"""


class HelpModal(ModalScreen):
    """Scrollable help / keybindings overlay."""

    BINDINGS = [("escape", "close", "Close"), ("question_mark", "close", "Close")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="help-box"):
            yield Static(HELP_TEXT)

    def action_close(self) -> None:
        self.dismiss()
