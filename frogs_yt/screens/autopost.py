"""autopost.py — post replies with throttle & cap, in a stoppable worker.

Two ways to use it:
  • Auto mode (from Harvest): pass nothing — it builds a queue from the harvest
    and GENERATES a draft for each comment as it goes.
  • Accepted mode (from Review): pass `items` = [(video, comment, text), ...] of
    drafts you already approved — it posts those verbatim.
"""

import threading

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, ProgressBar, RichLog

from .. import core, llm, replier
from ..widgets import ConfirmModal


class AutopostScreen(Screen):
    BINDINGS = [("escape", "back", "Back")]

    def __init__(self, items=None, **kwargs):
        super().__init__(**kwargs)
        # items: list of (video, comment, text|None). None text -> generate.
        self._preset_items = items

    def compose(self) -> ComposeResult:
        accepted = self._preset_items is not None
        title = "Post accepted drafts" if accepted else "Auto-post (generates as it goes)"
        yield Header()
        yield Label(title, classes="title")
        yield ProgressBar(id="progress", total=100, show_eta=False)
        yield RichLog(id="autopost-log", wrap=True, markup=True)
        with Horizontal(classes="actions"):
            yield Button("▶  Start run", id="start", variant="success")
            yield Button("■  Stop", id="stop", variant="error", disabled=True)
            yield Button("⬅  Back", id="back")
        yield Footer()

    def on_mount(self) -> None:
        self._cancel = threading.Event()
        if self._preset_items is not None:
            # Pre-approved drafts: post all of them (skip ones already replied to).
            self.queue = [
                (v, c, t) for (v, c, t) in self._preset_items
                if not self.app.replied.has(c["commentId"])
            ]
            self.run_size = len(self.queue)
        else:
            # Generate-as-you-go from the harvest, capped per run.
            pend = replier.pending_comments(self.app.harvest_blocks, self.app.replied)
            self.queue = [(v, c, None) for (v, c) in pend]
            self.run_size = min(len(self.queue), self.app.cfg["per_run_cap"])

        log = self.query_one("#autopost-log", RichLog)
        log.write(f"[bold]{len(self.queue)}[/] comments queued.")
        log.write(f"Will post up to [bold]{self.run_size}[/] this run "
                  f"(delay={self.app.cfg['rate_limit_seconds']}s).")
        if self.app.cfg["dry_run"]:
            log.write("[yellow]DRY-RUN is ON — nothing will actually post.[/]")

    # -- buttons ----------------------------------------------------------
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start":
            self._start()
        elif event.button.id == "stop":
            self._cancel.set()
            self.query_one("#autopost-log", RichLog).write("[yellow]Stopping after current reply…[/]")
        elif event.button.id == "back":
            self.action_back()

    def action_back(self) -> None:
        self._cancel.set()
        self.app.pop_screen()

    def _start(self) -> None:
        if self.run_size == 0:
            self.app.notify("Nothing to post.", severity="warning")
            return
        dry = self.app.cfg["dry_run"]
        if not dry and not self.app.is_logged_in():
            self.app.notify("Log in with Google first (or enable DRY-RUN in Settings).",
                            severity="error")
            return
        who = self.app.channel or "your channel"
        verb = "log (dry-run)" if dry else "POST"
        self.app.push_screen(
            ConfirmModal(
                f"This will {verb} up to {self.run_size} replies as {who}. Continue?",
                confirm_label="Run it",
            ),
            lambda ok: self._run() if ok else None,
        )

    # -- the run worker ---------------------------------------------------
    @work(thread=True, exclusive=True)
    def _run(self) -> None:
        self.app.call_from_thread(self._set_running, True)
        cfg = self.app.cfg
        dry = cfg["dry_run"]
        delay = cfg["rate_limit_seconds"]
        service = None if dry else self.app.youtube_service()
        posted = skipped = errors = 0

        for i in range(self.run_size):
            if self._cancel.is_set():
                break
            video, comment, text = self.queue[i]
            self.app.call_from_thread(self._progress, i, self.run_size)

            if text is None:  # generate-as-you-go mode
                try:
                    text = llm.generate_reply(comment, video, cfg)
                except llm.LLMError as e:
                    errors += 1
                    self.app.call_from_thread(self._log, f"[red]LLM error[/] for {comment['author']}: {e}")
                    continue

            try:
                reply_id = replier.post_one(service, comment, text, self.app.replied, dry_run=dry)
            except core.YouTubeError as e:
                errors += 1
                self.app.call_from_thread(self._log, f"[red]post failed[/] for {comment['author']}: {e}")
                continue

            if reply_id is None:
                skipped += 1
                continue
            posted += 1
            tag = "[yellow]would post[/]" if dry else "[green]posted[/]"
            self.app.call_from_thread(self._log, f"{tag} → {comment['author']}: {text[:60]}")

            # Throttle between real posts (not in dry-run; skip after the last).
            if not dry and i + 1 < self.run_size and not self._cancel.is_set():
                self.app.call_from_thread(self._log, f"  …waiting {delay}s")
                if self._cancel.wait(timeout=delay):
                    break

        self.app.call_from_thread(self._progress, self.run_size, self.run_size)
        self.app.call_from_thread(self._finish, posted, skipped, errors)

    # -- UI callbacks -----------------------------------------------------
    # These run via call_from_thread; if the user pressed Back mid-run the screen
    # is unmounted, so guard every widget query to avoid NoMatches in the worker.
    def _set_running(self, running: bool) -> None:
        if not self.is_mounted:
            return
        self.query_one("#start", Button).disabled = running
        self.query_one("#stop", Button).disabled = not running

    def _progress(self, done, total) -> None:
        if not self.is_mounted:
            return
        self.query_one("#progress", ProgressBar).update(total=total or 1, progress=done)

    def _log(self, msg) -> None:
        if not self.is_mounted:
            return
        self.query_one("#autopost-log", RichLog).write(msg)

    def _finish(self, posted, skipped, errors) -> None:
        self.app.session_posted += 0 if self.app.cfg["dry_run"] else posted
        if not self.is_mounted:
            return
        self._set_running(False)
        self._log(f"[bold]Run complete[/] — posted {posted}, skipped {skipped}, errors {errors}.")
        self.app.notify(f"Done: {posted} posted, {errors} errors.", title="Auto-post")
