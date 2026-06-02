"""widgets.py — shared Textual widgets (frog banner, status panel, modals)."""

import random

from rich.align import Align
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

FROG_ART = r"""
         .--.        .--.
        /    \      /    \        F R O G S
        |(o )|      |( o)|        ─────────────
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


class PondBackground(Static):
    """A subtle field of slowly rising bubbles, drawn behind the dashboard.

    Pure ambience and deliberately cheap: it repaints a few dim glyphs about
    twice a second. It sits on its own (lower) layer and ignores the pointer, so
    it never interferes with the menu on top.
    """

    GLYPHS = "·∘°"

    def on_mount(self) -> None:
        self._bubbles = []
        self.set_interval(0.5, self._tick)

    def _seed(self, w, h) -> None:
        n = max(4, w // 9)
        self._bubbles = [
            [random.randint(0, w - 1), random.uniform(0, h),
             random.uniform(0.2, 0.7), random.choice(self.GLYPHS)]
            for _ in range(n)
        ]

    def _tick(self) -> None:
        # Don't burn cycles painting while another screen covers the dashboard.
        if self.app.screen is not self.screen:
            return
        w, h = self.size.width, self.size.height
        if w <= 0 or h <= 0:
            return
        if len(self._bubbles) != max(4, w // 9):
            self._seed(w, h)
        for b in self._bubbles:
            b[1] -= b[2]                       # drift upward
            if b[1] < 0:                       # re-enter from the bottom
                b[0] = random.randint(0, w - 1)
                b[1] = h + random.uniform(0, 3)
                b[2] = random.uniform(0.2, 0.7)
                b[3] = random.choice(self.GLYPHS)
        grid = [[" "] * w for _ in range(h)]
        for x, y, _s, g in self._bubbles:
            yi = int(y)
            if 0 <= yi < h and 0 <= x < w:
                grid[yi][x] = g
        self.update(Text("\n".join("".join(r) for r in grid), style="#274b35"))


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
 • [b]Search & Harvest[/] — keywords + depth, plus an optional word filter.
   [b]Sort results[/] by most/fewest comments, most/least viewed or liked, or
   newest/oldest. [b]Save[/] a config as a named preset and [b]Open[/] it later to
   reuse it; the built-in "Buyer intent" preset filters to where / buy / price.
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


# -- search presets -------------------------------------------------------
def preset_summary(preset) -> str:
    """A one-line, human-readable description of a saved search preset."""
    kw = preset.get("keywords") or []
    n = len(kw)
    bits = [f"{n} keyword{'s' if n != 1 else ''}"]
    bits.append(f"{preset.get('max_videos', '?')}×{preset.get('max_comments', '?')}")
    bits.append(str(preset.get("order", "relevance")))
    words = preset.get("match_words") or []
    if words:
        shown = " ".join(words[:4]) + (" …" if len(words) > 4 else "")
        bits.append(f"filter: {shown}")
    return "  ·  ".join(bits)


class SavePresetModal(ModalScreen):
    """Prompt for a preset name. Dismisses with the trimmed name, or None."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, suggested="", existing_names=(), **kwargs):
        super().__init__(**kwargs)
        self._suggested = suggested
        self._existing = {n.lower() for n in existing_names}

    def compose(self) -> ComposeResult:
        with Vertical(id="save-preset-box"):
            yield Label("💾  Save search preset", id="save-preset-title")
            yield Input(
                value=self._suggested,
                placeholder="e.g. Buyer intent, Plushie sweep…",
                id="preset-name",
            )
            yield Static("", id="save-preset-hint")
            with Horizontal(classes="actions"):
                yield Button("Save (enter)", variant="success", id="save-ok")
                yield Button("Cancel (esc)", variant="primary", id="save-cancel")

    def on_mount(self) -> None:
        inp = self.query_one("#preset-name", Input)
        inp.focus()
        inp.cursor_position = len(inp.value)
        self._reflect(inp.value)

    def on_input_changed(self, event: Input.Changed) -> None:
        self._reflect(event.value)

    def _reflect(self, value: str) -> None:
        name = value.strip()
        hint = self.query_one("#save-preset-hint", Static)
        if name and name.lower() in self._existing:
            hint.update(Text(f"“{name}” exists — saving overwrites it.", style="yellow"))
        else:
            hint.update("")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_save()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-ok":
            self.action_save()
        else:
            self.action_cancel()

    def action_save(self) -> None:
        name = self.query_one("#preset-name", Input).value.strip()
        if not name:
            self.app.notify("Give the preset a name first.", severity="warning")
            self.query_one("#preset-name", Input).focus()
            return
        self.dismiss(name)

    def action_cancel(self) -> None:
        self.dismiss(None)


class PresetPickerModal(ModalScreen):
    """Browse saved presets. Load one (dismiss with its name) or delete in place."""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("d", "delete", "Delete"),
        ("delete", "delete", "Delete"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="preset-picker-box"):
            yield Label("📂  Open a saved preset", id="preset-picker-title")
            yield OptionList(id="preset-list")
            yield Static(
                "[b]enter[/] load   ·   [b]d[/] delete   ·   [b]esc[/] cancel",
                id="preset-picker-hint",
            )
            with Horizontal(classes="actions"):
                yield Button("Load (enter)", variant="success", id="preset-load")
                yield Button("Delete (d)", variant="error", id="preset-delete")
                yield Button("Cancel (esc)", variant="primary", id="preset-cancel")

    def on_mount(self) -> None:
        self._refresh()
        self.query_one("#preset-list", OptionList).focus()

    def _refresh(self) -> None:
        olist = self.query_one("#preset-list", OptionList)
        olist.clear_options()
        presets = self.app.cfg.presets()
        if not presets:
            olist.add_option(Option(Text("No saved presets yet.", style="dim"), disabled=True))
            self.query_one("#preset-load", Button).disabled = True
            self.query_one("#preset-delete", Button).disabled = True
            return
        self.query_one("#preset-load", Button).disabled = False
        self.query_one("#preset-delete", Button).disabled = False
        for p in presets:
            label = Text()
            label.append(p.get("name", "(unnamed)"), style="bold")
            label.append("\n  " + preset_summary(p), style="dim")
            olist.add_option(Option(label))
        olist.highlighted = 0

    def _selected_name(self):
        olist = self.query_one("#preset-list", OptionList)
        presets = self.app.cfg.presets()
        idx = olist.highlighted
        if idx is None or not (0 <= idx < len(presets)):
            return None
        return presets[idx].get("name")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.action_load()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "preset-load":
            self.action_load()
        elif event.button.id == "preset-delete":
            self.action_delete()
        else:
            self.action_cancel()

    def action_load(self) -> None:
        name = self._selected_name()
        if name is None:
            return
        self.dismiss(name)

    def action_delete(self) -> None:
        name = self._selected_name()
        if name is None:
            return
        self.app.cfg.delete_preset(name)
        self.app.notify(f"Deleted preset “{name}”.")
        self._refresh()

    def action_cancel(self) -> None:
        self.dismiss(None)
