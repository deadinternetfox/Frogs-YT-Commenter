"""review.py — review mode: curate AI drafts, then batch auto-post the accepted ones.

Flow: for each comment you Accept (keeping any edits), Regenerate, or Skip. Accepted
drafts collect in a list; "Post accepted" hands them to the throttled auto-poster.
"""

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Static, TextArea

from .. import llm, replier
from .autopost import AutopostScreen

PLACEHOLDER = "…generating draft…"
REGEN_PLACEHOLDER = "…regenerating…"


class ReviewScreen(Screen):
    BINDINGS = [
        ("escape", "back", "Back"),
        ("a", "accept", "Accept draft"),
        ("s", "skip", "Skip"),
        ("r", "regenerate", "Regenerate"),
        ("c", "copy_link", "Copy link"),
        ("p", "post_accepted", "Post accepted"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="queue-pos")
        yield Static(id="comment-panel")
        yield TextArea("", id="draft-area")
        with Horizontal(classes="actions"):
            yield Button("✅  Accept (a)", id="accept", variant="success")
            yield Button("🔁  Regenerate (r)", id="regen")
            yield Button("⏭   Skip (s)", id="skip")
            yield Button("🔗  Copy link (c)", id="copy")
            yield Button("🚀  Post accepted (p)", id="post", variant="primary")
            yield Button("⬅  Back", id="back")
        yield Footer()

    def on_mount(self) -> None:
        self.queue = replier.pending_comments(self.app.harvest_blocks, self.app.replied)
        self.idx = 0
        self._drafts = {}     # idx -> generated text (cache + prefetch)
        self._gen_seq = {}    # idx -> latest generation token
        self._first_resume = True
        self.accepted = []    # list of (video, comment, edited_text)
        self._accepted_ids = set()
        if not self.queue:
            self.app.notify("No un-replied comments to review.", severity="warning")
            self.app.call_later(self.app.pop_screen)
            return
        self._show_current()

    # -- rendering --------------------------------------------------------
    def _show_current(self) -> None:
        video, comment = self.queue[self.idx]
        dry = self.app.cfg["dry_run"]
        self.query_one("#queue-pos", Static).update(
            f"  Comment {self.idx + 1} / {len(self.queue)}   ·   "
            f"accepted: {len(self.accepted)}" + ("   ·   DRY-RUN" if dry else "")
        )

        panel = Text()
        panel.append(f"{comment['author']}", style="bold")
        panel.append(f"  ·  {comment['likes']}👍\n", style="dim")
        panel.append(comment["text"] + "\n\n")
        panel.append(f"on: {video['title']}\n", style="italic dim")
        panel.append(comment["link"], style="blue underline")
        self.query_one("#comment-panel", Static).update(panel)

        draft = self.query_one("#draft-area", TextArea)
        if self.idx in self._drafts:               # prefetched — instant
            draft.text = self._drafts[self.idx]
        else:
            draft.text = PLACEHOLDER
            self._generate(self.idx)
        self._prefetch(self.idx + 1)               # warm the next one

    # -- draft generation (worker) ---------------------------------------
    @work(thread=True)
    def _generate(self, idx, temperature=None, nudge=None, regen=False) -> None:
        # Tag each generation so a stale/superseded result can't clobber a newer
        # one for the same comment (e.g. the original draft landing after regen).
        seq = self._gen_seq.get(idx, 0) + 1
        self._gen_seq[idx] = seq
        video, comment = self.queue[idx]
        try:
            text = llm.generate_reply(comment, video, self.app.cfg,
                                      temperature=temperature, nudge=nudge)
        except llm.LLMError as e:
            text = f"[draft failed: {e}]"
        self.app.call_from_thread(self._draft_ready, idx, seq, text, regen)

    def _prefetch(self, idx) -> None:
        if 0 <= idx < len(self.queue) and idx not in self._drafts \
                and idx not in self._gen_seq:
            self._generate(idx)

    def _draft_ready(self, idx, seq, text, regen=False) -> None:
        if seq != self._gen_seq.get(idx):
            return                                 # superseded by a newer generation
        self._drafts[idx] = text
        if idx != self.idx:
            return
        area = self.query_one("#draft-area", TextArea)
        # Don't trample text the user has already typed/edited over the placeholder;
        # a regenerate is an explicit request so it always applies.
        if regen or area.text in (PLACEHOLDER, REGEN_PLACEHOLDER):
            area.text = text

    # -- actions ----------------------------------------------------------
    def action_back(self) -> None:
        self.app.pop_screen()

    def action_skip(self) -> None:
        self._advance()

    def action_copy_link(self) -> None:
        _video, comment = self.queue[self.idx]
        self.app.copy_to_clipboard(comment["link"])
        self.app.notify("Comment link copied to clipboard.")

    def action_regenerate(self) -> None:
        # Nudge temperature up a touch so the new draft actually differs.
        base = self.app.cfg["llm"].get("temperature", 0.8)
        temp = min(1.5, base + 0.2)
        self.query_one("#draft-area", TextArea).text = REGEN_PLACEHOLDER
        self._generate(self.idx, temperature=temp,
                       nudge="Give a different, fresh phrasing.", regen=True)

    def action_accept(self) -> None:
        text = self.query_one("#draft-area", TextArea).text.strip()
        if not text or text.startswith("[draft failed") or text.startswith("…"):
            self.app.notify("Draft isn't ready yet.", severity="warning")
            return
        video, comment = self.queue[self.idx]
        cid = comment["commentId"]
        if cid not in self._accepted_ids:
            self.accepted.append((video, comment, text))
            self._accepted_ids.add(cid)
        self.app.notify(f"Accepted ({len(self.accepted)} queued).")
        self._advance()

    def action_post_accepted(self) -> None:
        if not self.accepted:
            self.app.notify("Accept some drafts first (press a).", severity="warning")
            return
        if not self.app.cfg["dry_run"] and not self.app.is_logged_in():
            self.app.notify("Log in with Google first to post (or enable DRY-RUN).",
                            severity="error")
            return
        # Hand the curated drafts to the throttled auto-poster.
        self.app.push_screen(AutopostScreen(items=list(self.accepted)))

    def _advance(self) -> None:
        self.idx += 1
        if self.idx >= len(self.queue):
            if self.accepted:
                self.app.notify(
                    f"End of queue — {len(self.accepted)} accepted. Press p to post them.",
                    title="Ready to post 🐸", timeout=8,
                )
                self.idx = len(self.queue) - 1     # stay on last card
            else:
                self.app.notify("Reached the end of the queue.", title="Done")
                self.app.pop_screen()
            return
        self._show_current()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        handler = {
            "accept": self.action_accept,
            "regen": self.action_regenerate,
            "skip": self.action_skip,
            "copy": self.action_copy_link,
            "post": self.action_post_accepted,
            "back": self.action_back,
        }.get(event.button.id)
        if handler:
            handler()

    def on_screen_resume(self) -> None:
        # Fires on the initial push too — skip that so we don't double-generate.
        if self._first_resume:
            self._first_resume = False
            return
        if not self.queue:
            return
        # Returning from the auto-poster: drop drafts that actually posted.
        before = len(self.accepted)
        self.accepted = [(v, c, t) for (v, c, t) in self.accepted
                         if not self.app.replied.has(c["commentId"])]
        self._accepted_ids = {c["commentId"] for _v, c, _t in self.accepted}
        if len(self.accepted) != before:
            self.app.notify(f"{before - len(self.accepted)} posted; "
                            f"{len(self.accepted)} still queued.")
        if 0 <= self.idx < len(self.queue):
            self._show_current()