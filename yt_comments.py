#!/usr/bin/env python3
"""
yt_comments.py — lightweight CLI for finding videos by keyword and harvesting
their comments into a clickable link list, plus replying to comments.

This is the thin command-line front-end. All the real logic lives in
`frogs_yt/core.py` (shared with the full TUI app — run `./frogs`).

READ operations (search, list comments) need only an API key.
WRITE operations (replying) need OAuth 2.0 — see `frogs_yt/oauth.py`.

Quick start
-----------
  export YT_API_KEY="AIza...your_key..."

  python3 yt_comments.py search "frog plushie" "amigurumi frog" --max 15
  python3 yt_comments.py comments VIDEO_ID --max 100
  python3 yt_comments.py harvest "crochet frog" --videos 10 --comments 50 \
          --match where buy link shop --out leads.md
  python3 yt_comments.py reply COMMENT_ID "Thanks! 🐸"
"""

import argparse
import os
import sys

from frogs_yt import core


def get_key(args):
    key = args.key or os.environ.get("YT_API_KEY")
    if not key:
        sys.exit("No API key. Pass --key or set YT_API_KEY in your environment.")
    return key


def write_out(text, out_path):
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Wrote {out_path}")
    else:
        print(text)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def cmd_search(args):
    key = get_key(args)
    try:
        vids = core.search_videos(args.keywords, key, args.max, args.order)
    except core.YouTubeError as e:
        sys.exit(f"\n{e}\n")
    write_out(core.render_videos_md(vids), args.out)


def cmd_comments(args):
    key = get_key(args)
    try:
        comments = core.list_comments(args.video_id, key, args.max, args.match)
    except core.YouTubeError as e:
        sys.exit(f"\n{e}\n")
    write_out(core.render_comments_md([(None, comments)]), args.out)


def cmd_harvest(args):
    key = get_key(args)

    def progress(done, total, label):
        print(f"  {label}", file=sys.stderr)

    try:
        _videos, blocks = core.harvest(
            args.keywords, key, max_videos=args.videos, max_comments=args.comments,
            match_words=args.match, order=args.order, progress=progress,
        )
    except core.YouTubeError as e:
        sys.exit(f"\n{e}\n")
    write_out(core.render_comments_md(blocks), args.out)


def cmd_draft(args):
    """Harvest comments and batch-generate AI reply drafts into a markdown file.

    Posts nothing — purely for reviewing what the AI would say. Great for tuning
    the prompt before enabling auto-post.
    """
    from frogs_yt import config, llm

    key = get_key(args)
    cfg = config.Config.load()

    def progress(done, total, label):
        print(f"  {label}", file=sys.stderr)

    try:
        _videos, blocks = core.harvest(
            args.keywords, key, max_videos=args.videos, max_comments=args.comments,
            match_words=args.match, order=args.order, progress=progress,
        )
    except core.YouTubeError as e:
        sys.exit(f"\n{e}\n")

    pairs = [(v, c) for v, comments in blocks for c in comments][: args.limit]
    mode = "DeepSeek" if cfg.has_llm_key() else "STUB (no LLM key set)"
    print(f"Generating {len(pairs)} drafts with {mode}…", file=sys.stderr)

    lines = [f"# {len(pairs)} draft replies ({mode})\n"]
    for i, (video, c) in enumerate(pairs, 1):
        print(f"  [{i}/{len(pairs)}] {c['author']}", file=sys.stderr)
        try:
            draft = llm.generate_reply(c, video, cfg)
        except llm.LLMError as e:
            draft = f"[draft failed: {e}]"
        snippet = c["text"].replace("\n", " ").strip()
        lines.append(
            f"\n## {c['author']} ({c['likes']}👍) — on “{_trim(video['title'])}”\n"
            f"> {snippet}\n\n"
            f"**Draft:** {draft}\n\n"
            f"- reply → [{c['link']}]({c['link']})\n"
            f"- commentId: `{c['commentId']}`"
        )
    write_out("\n".join(lines) + "\n", args.out)


def _trim(s, n=60):
    return s if len(s) <= n else s[: n - 1] + "…"


def cmd_reply(args):
    """Post a reply via OAuth (uses the same credentials as the TUI app)."""
    from frogs_yt import config, oauth

    cfg = config.Config.load()
    try:
        creds = oauth.load_cached_credentials()
        if not creds:
            print("Not logged in — launching browser authorization…", file=sys.stderr)
            creds = oauth.login(cfg.client_secret_file())
        service = oauth.build_service(creds)
        reply_id = core.post_reply(service, args.comment_id, args.text)
    except (oauth.OAuthError, core.YouTubeError) as e:
        sys.exit(f"\n{e}\n")
    print(f"Replied. New comment id: {reply_id}")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--key", help="API key (else uses $YT_API_KEY)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="search videos by keyword(s)")
    s.add_argument("keywords", nargs="+")
    s.add_argument("--max", type=int, default=25, help="max videos per keyword")
    s.add_argument("--order", default="relevance",
                   choices=["relevance", "date", "viewCount", "rating", "title"])
    s.add_argument("--out", help="write markdown to file instead of stdout")
    s.set_defaults(func=cmd_search)

    c = sub.add_parser("comments", help="list comments on one video")
    c.add_argument("video_id")
    c.add_argument("--max", type=int, default=100)
    c.add_argument("--match", nargs="*", help="only keep comments containing these words")
    c.add_argument("--out")
    c.set_defaults(func=cmd_comments)

    h = sub.add_parser("harvest", help="search keywords + pull comments from each hit")
    h.add_argument("keywords", nargs="+")
    h.add_argument("--videos", type=int, default=10, help="max videos per keyword")
    h.add_argument("--comments", type=int, default=50, help="max comments per video")
    h.add_argument("--match", nargs="*", help="only keep comments containing these words")
    h.add_argument("--order", default="relevance",
                   choices=["relevance", "date", "viewCount", "rating", "title"])
    h.add_argument("--out")
    h.set_defaults(func=cmd_harvest)

    dr = sub.add_parser("draft", help="batch-generate AI reply drafts to markdown (posts nothing)")
    dr.add_argument("keywords", nargs="+")
    dr.add_argument("--videos", type=int, default=10, help="max videos per keyword")
    dr.add_argument("--comments", type=int, default=50, help="max comments per video")
    dr.add_argument("--match", nargs="*", help="only keep comments containing these words")
    dr.add_argument("--limit", type=int, default=25, help="max drafts to generate (caps LLM calls)")
    dr.add_argument("--order", default="relevance",
                    choices=["relevance", "date", "viewCount", "rating", "title"])
    dr.add_argument("--out")
    dr.set_defaults(func=cmd_draft)

    r = sub.add_parser("reply", help="reply to a comment (needs OAuth login)")
    r.add_argument("comment_id")
    r.add_argument("text")
    r.set_defaults(func=cmd_reply)

    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    args.func(args)
