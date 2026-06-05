"""core.py — YouTube Data API read/write logic.

Pure logic, no UI. Network failures raise exceptions (never sys.exit) so the TUI
can present them. Read paths (search/comments) need only an API key; the write
path (post_reply) takes an already-built, OAuth-authorized service object.
"""

import csv
import html
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from . import matching

API_BASE = "https://www.googleapis.com/youtube/v3"

# --------------------------------------------------------------------------
# Result orderings offered by the TUI
# --------------------------------------------------------------------------
# YouTube's search `order` only sorts descending and can't sort by comment
# count, so we map each friendly ordering to a best-effort API order for the
# initial fetch and apply the precise sort client-side (after stats + comment
# counts are known). This is what lets us offer "least viewed", "fewest
# comments", "oldest", etc. — directions the API itself won't return.
#   (id, label, api_order)
RESULT_ORDERS = [
    ("comments_desc", "Most comments",   "relevance"),
    ("comments_asc",  "Fewest comments", "relevance"),
    ("views_desc",    "Most viewed",     "viewCount"),
    ("views_asc",     "Least viewed",    "viewCount"),
    ("likes_desc",    "Most liked",      "rating"),
    ("likes_asc",     "Least liked",     "rating"),
    ("newest",        "Newest first",    "date"),
    ("oldest",        "Oldest first",    "date"),
    ("relevance",     "Most relevant",   "relevance"),
    ("title",         "Title A → Z",     "title"),
]

# id -> (key function over a video dict, reverse?). 'relevance' is absent: it
# keeps whatever order the API returned.
_SORT_KEYS = {
    "comments_desc": (lambda v: v.get("comment_count", 0), True),
    "comments_asc":  (lambda v: v.get("comment_count", 0), False),
    "views_desc":    (lambda v: v.get("views", 0), True),
    "views_asc":     (lambda v: v.get("views", 0), False),
    "likes_desc":    (lambda v: v.get("likes", 0), True),
    "likes_asc":     (lambda v: v.get("likes", 0), False),
    "newest":        (lambda v: v.get("published", ""), True),
    "oldest":        (lambda v: v.get("published", ""), False),
    "title":         (lambda v: (v.get("title") or "").lower(), False),
}

DEFAULT_ORDER = "comments_desc"


def order_options():
    """(label, id) pairs for a Select widget, in display order."""
    return [(label, oid) for oid, label, _api in RESULT_ORDERS]


def valid_order(order_id):
    """Return order_id if recognised, else the default (handles old configs)."""
    known = {oid for oid, _l, _a in RESULT_ORDERS}
    return order_id if order_id in known else DEFAULT_ORDER


def api_order(order_id):
    """The YouTube search `order` to request for a friendly ordering id."""
    for oid, _label, api in RESULT_ORDERS:
        if oid == order_id:
            return api
    return "relevance"


def sort_results(videos, blocks, order_id):
    """Return (videos, blocks) re-ordered per a RESULT_ORDERS id.

    Sorting is stable, so ties keep the API's original (e.g. relevance) order.
    'relevance' and unknown ids pass through untouched.
    """
    spec = _SORT_KEYS.get(order_id)
    if spec is None:
        return videos, blocks
    key, reverse = spec
    videos = sorted(videos, key=key, reverse=reverse)
    blocks = sorted(blocks, key=lambda vb: key(vb[0]), reverse=reverse)
    return videos, blocks


# --------------------------------------------------------------------------
# Exceptions
# --------------------------------------------------------------------------
class YouTubeError(Exception):
    """Base for all YouTube errors raised by this module."""


class YouTubeAPIError(YouTubeError):
    """An HTTP error from the API. Carries the status code, message and reason."""

    def __init__(self, code, message, reason=None, endpoint=None):
        self.code = code
        self.message = message
        self.reason = reason  # e.g. 'quotaExceeded', 'commentsDisabled', 'videoNotFound'
        self.endpoint = endpoint
        super().__init__(f"API error {code} on /{endpoint}: {message}")


class YouTubeNetworkError(YouTubeError):
    """A transport-level failure (DNS, timeout, connection refused, ...)."""


def friendly_error(err):
    """A short, human message for a YouTubeError — shared by TUI/daemon/agent."""
    if isinstance(err, YouTubeAPIError):
        if err.reason == "quotaExceeded":
            return "Daily API quota exceeded — try tomorrow or use another key."
        if err.reason == "commentsDisabled":
            return "Comments are disabled on this video."
        if err.reason == "videoNotFound":
            return "That video no longer exists."
        if err.reason in ("forbidden", "insufficientPermissions"):
            return "Not allowed — re-check the login / permissions."
        if err.code == 400:
            return f"Bad request: {err.message}"
        if err.code in (401, 403):
            return f"Access denied — check the API key. ({err.message})"
        return err.message
    return str(err)


# --------------------------------------------------------------------------
# Low-level API helper
# --------------------------------------------------------------------------
def api_get(endpoint, params, api_key, timeout=30):
    """GET a YouTube Data API endpoint. Returns parsed JSON or raises."""
    params = dict(params)
    params["key"] = api_key
    url = f"{API_BASE}/{endpoint}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        message, reason = body, None
        try:
            err = json.loads(body)["error"]
            message = err.get("message", body)
            errors = err.get("errors") or []
            if errors:
                reason = errors[0].get("reason")
        except Exception:
            pass
        raise YouTubeAPIError(e.code, message, reason, endpoint) from e
    except urllib.error.URLError as e:
        raise YouTubeNetworkError(f"Network error on /{endpoint}: {e.reason}") from e


# --------------------------------------------------------------------------
# Search videos by keyword
# --------------------------------------------------------------------------
def search_videos(keywords, api_key, max_results=25, order="relevance", progress=None):
    """Search videos for each keyword. Returns a deduped list of video dicts.

    progress(done, total, label) is called as keywords are processed (optional).
    """
    if isinstance(keywords, str):
        keywords = [keywords]
    keywords = [k.strip() for k in keywords if k and k.strip()]
    seen = {}
    total = len(keywords)
    for i, kw in enumerate(keywords):
        if progress:
            progress(i, total, f"searching: {kw}")
        remaining = max_results
        page_token = None
        while remaining > 0:
            data = api_get(
                "search",
                {
                    "part": "snippet",
                    "q": kw,
                    "type": "video",
                    "maxResults": min(50, remaining),
                    "order": order,
                    **({"pageToken": page_token} if page_token else {}),
                },
                api_key,
            )
            for item in data.get("items", []):
                vid = item["id"].get("videoId")
                if not vid or vid in seen:
                    continue
                sn = item["snippet"]
                seen[vid] = {
                    "videoId": vid,
                    "title": sn["title"],
                    "channel": sn["channelTitle"],
                    "published": sn["publishedAt"],
                    "matched_keyword": kw,
                    "url": f"https://www.youtube.com/watch?v={vid}",
                }
            page_token = data.get("nextPageToken")
            remaining -= 50
            if not page_token:
                break
            time.sleep(0.2)
    if progress:
        progress(total, total, "search complete")
    return list(seen.values())


# --------------------------------------------------------------------------
# List comments on a video
# --------------------------------------------------------------------------
def _top_comment_dict(top, video_id):
    """Build our comment dict from a commentThreads topLevelComment resource."""
    cs = top["snippet"]
    cid = top["id"]
    return {
        "commentId": cid,
        "author": cs.get("authorDisplayName"),
        "authorChannelId": (cs.get("authorChannelId") or {}).get("value"),
        "text": cs.get("textDisplay", ""),
        "likes": cs.get("likeCount", 0),
        "published": cs.get("publishedAt"),
        "updated": cs.get("updatedAt"),
        "videoId": video_id,
        "link": f"https://www.youtube.com/watch?v={video_id}&lc={cid}",
        "parentId": None,
        "isReply": False,
        "threadId": cid,
    }


def _reply_comment_dict(item, video_id, thread_id):
    """Build our comment dict from a comment resource (a reply)."""
    cs = item["snippet"]
    rid = item["id"]
    return {
        "commentId": rid,
        "author": cs.get("authorDisplayName"),
        "authorChannelId": (cs.get("authorChannelId") or {}).get("value"),
        "text": cs.get("textDisplay", ""),
        "likes": cs.get("likeCount", 0),
        "published": cs.get("publishedAt"),
        "updated": cs.get("updatedAt"),
        "videoId": video_id,
        "link": f"https://www.youtube.com/watch?v={video_id}&lc={rid}",
        "parentId": cs.get("parentId") or thread_id,
        "isReply": True,
        "threadId": thread_id,
    }


def list_replies(parent_comment_id, api_key, video_id=None, max_results=200, progress=None):
    """Return all replies under a top-level comment (comments.list, 1 unit/page).

    Returns [] (not an error) if the parent/thread is gone.
    """
    out = []
    page_token = None
    while len(out) < max_results:
        try:
            data = api_get(
                "comments",
                {
                    "part": "snippet",
                    "parentId": parent_comment_id,
                    "maxResults": min(100, max_results - len(out)),
                    "textFormat": "plainText",
                    **({"pageToken": page_token} if page_token else {}),
                },
                api_key,
            )
        except YouTubeAPIError as e:
            if e.reason in ("commentNotFound", "videoNotFound", "processingFailure"):
                break
            raise
        for item in data.get("items", []):
            out.append(_reply_comment_dict(item, video_id, parent_comment_id))
        if progress:
            progress(len(out), max_results, f"replies: {parent_comment_id}")
        page_token = data.get("nextPageToken")
        if not page_token:
            break
        time.sleep(0.2)
    return out


def list_comments(video_id, api_key, max_results=100, match_words=None, *,
                  match_query=None, include_replies=False, progress=None):
    """Return matching comments for a video.

    Matching: `match_query` (the matching.py query language) takes precedence;
    otherwise the legacy `match_words` list (OR of words) is used; otherwise
    everything matches. When `include_replies` is set, reply threads are pulled
    too — a matched top-level comment brings its whole thread along for context,
    and replies that independently match are returned with their parent. The
    `max_results` budget counts top-level threads fetched (as before); replies
    are additional.

    Returns [] (not an error) when comments are disabled or the video is gone.
    """
    matcher = (matching.compile_query(match_query) if match_query
               else matching.from_words(match_words))
    out, seen = [], set()
    page_token = None
    fetched = 0

    def add(comment):
        if comment["commentId"] not in seen:
            seen.add(comment["commentId"])
            out.append(comment)

    while fetched < max_results:
        try:
            data = api_get(
                "commentThreads",
                {
                    "part": "snippet,replies" if include_replies else "snippet",
                    "videoId": video_id,
                    "maxResults": min(100, max_results - fetched),
                    "order": "time",
                    "textFormat": "plainText",
                    **({"pageToken": page_token} if page_token else {}),
                },
                api_key,
            )
        except YouTubeAPIError as e:
            # Comments disabled / video gone -> treat as "no comments", not fatal.
            # Match on the REASON only: a blanket `code == 403` would also swallow
            # quotaExceeded / bad-key / rateLimitExceeded and report them as "0
            # comments", which is dangerously misleading mid-harvest.
            if e.reason in ("commentsDisabled", "videoNotFound"):
                break
            raise
        for item in data.get("items", []):
            sn = item["snippet"]
            top = _top_comment_dict(sn["topLevelComment"], video_id)
            top_matches = matcher.matches(top)

            replies = []
            if include_replies and sn.get("totalReplyCount", 0):
                inline = [
                    _reply_comment_dict(r, video_id, top["commentId"])
                    for r in (item.get("replies") or {}).get("comments", [])
                ]
                # Inline gives up to ~5; page the rest only when there are more.
                if len(inline) >= sn["totalReplyCount"]:
                    replies = inline
                else:
                    replies = list_replies(top["commentId"], api_key, video_id)

            if top_matches:
                add(top)
                for r in replies:        # whole thread as context
                    add(r)
            else:
                hit = [r for r in replies if matcher.matches(r)]
                if hit:
                    add(top)             # parent of a matching reply (context)
                    for r in hit:
                        add(r)
        fetched += len(data.get("items", []))
        if progress:
            progress(min(fetched, max_results), max_results, f"comments: {video_id}")
        page_token = data.get("nextPageToken")
        if not page_token:
            break
        time.sleep(0.2)
    return out


# --------------------------------------------------------------------------
# Video statistics (views / likes / total comment count)
# --------------------------------------------------------------------------
def fetch_video_stats(video_ids, api_key):
    """Return {videoId: {"views", "likes", "total_comments"}} for the given ids.

    Batches 50 ids per call (videos.list costs ~1 quota unit). Missing fields
    (e.g. hidden like counts) default to 0. Ids absent from the response are
    simply omitted.
    """
    ids = [v for v in video_ids if v]
    out = {}
    for i in range(0, len(ids), 50):
        chunk = ids[i : i + 50]
        data = api_get("videos", {"part": "statistics", "id": ",".join(chunk)}, api_key)
        for item in data.get("items", []):
            s = item.get("statistics", {})
            out[item["id"]] = {
                "views": int(s.get("viewCount", 0) or 0),
                "likes": int(s.get("likeCount", 0) or 0),
                "total_comments": int(s.get("commentCount", 0) or 0),
            }
    return out


# --------------------------------------------------------------------------
# Harvest: search keywords, then pull comments from each hit
# --------------------------------------------------------------------------
def harvest(keywords, api_key, max_videos=10, max_comments=50, match_words=None,
            order="relevance", *, match_query=None, include_replies=False,
            progress=None):
    """Search keywords and gather comments for every video found.

    Returns (videos, blocks) where blocks = [(video_dict, [comments]), ...] for
    videos that had at least one (matching) comment. Each video dict gains
    'comment_count' (matching comments harvested) plus 'views', 'likes' and
    'total_comments' from the video's public statistics. `match_query`/
    `include_replies` are passed through to list_comments (the spider sets both;
    the live TUI harvest leaves include_replies off to keep its quota cost flat).
    """
    videos = search_videos(keywords, api_key, max_videos, order, progress=progress)
    # Enrich with public statistics so the UI can sort by popularity. A failure
    # here must not sink the harvest — fall back to zeroes.
    try:
        stats = fetch_video_stats([v["videoId"] for v in videos], api_key)
    except YouTubeError:
        stats = {}
    for v in videos:
        s = stats.get(v["videoId"], {})
        v["views"] = s.get("views", 0)
        v["likes"] = s.get("likes", 0)
        v["total_comments"] = s.get("total_comments", 0)
    blocks = []
    total = len(videos)
    for i, v in enumerate(videos):
        if progress:
            progress(i, total, f"[{i + 1}/{total}] {v['title'][:48]}")
        comments = list_comments(
            v["videoId"], api_key, max_comments, match_words,
            match_query=match_query, include_replies=include_replies,
        )
        v["comment_count"] = len(comments)
        if comments:
            blocks.append((v, comments))
    if progress:
        progress(total, total, "harvest complete")
    return videos, blocks


# --------------------------------------------------------------------------
# Post a reply (write path) — needs an OAuth-authorized service from oauth.py
# --------------------------------------------------------------------------
def post_reply(youtube_service, parent_comment_id, text):
    """Insert a reply to a top-level comment. Returns the new comment id.

    `youtube_service` is a googleapiclient resource built by oauth.build_service().
    Raises YouTubeError on failure.
    """
    try:
        resp = (
            youtube_service.comments()
            .insert(
                part="snippet",
                body={"snippet": {"parentId": parent_comment_id, "textOriginal": text}},
            )
            .execute()
        )
        return resp["id"]
    except Exception as e:  # googleapiclient.errors.HttpError or transport errors
        # Try to surface the API reason if present.
        reason = None
        code = None
        try:
            code = getattr(e, "status_code", None) or getattr(getattr(e, "resp", None), "status", None)
            content = getattr(e, "content", b"") or b""
            data = json.loads(content.decode("utf-8")) if content else {}
            err = data.get("error", {})
            reason = (err.get("errors") or [{}])[0].get("reason")
            msg = err.get("message") or str(e)
        except Exception:
            msg = str(e)
        raise YouTubeAPIError(code or 0, msg, reason, "comments.insert") from e


# --------------------------------------------------------------------------
# Markdown rendering (used by the CLI and the TUI "export" action)
# --------------------------------------------------------------------------
def render_videos_md(videos):
    lines = [f"# {len(videos)} videos\n"]
    for v in videos:
        lines.append(
            f"- [{v['title']}]({v['url']}) — {v['channel']} "
            f"_(kw: {v['matched_keyword']})_"
        )
    return "\n".join(lines) + "\n"


def render_comments_md(blocks):
    """blocks = list of (video_meta_or_None, [comments])"""
    lines = []
    total = 0
    for meta, comments in blocks:
        if meta:
            lines.append(f"\n## [{meta['title']}]({meta['url']}) — {meta['channel']}")
        for c in comments:
            total += 1
            snippet = c["text"].replace("\n", " ").strip()
            if len(snippet) > 200:
                snippet = snippet[:197] + "..."
            lines.append(
                f"- **{c['author']}** ({c['likes']}👍): {snippet}\n"
                f"  - reply → [{c['link']}]({c['link']})\n"
                f"  - commentId: `{c['commentId']}`"
            )
    header = f"# {total} comments\n"
    return header + "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Other export formats (used by the Harvest screen's "Export…" picker)
# --------------------------------------------------------------------------
# (id, label, file extension, one-line description)
EXPORT_FORMATS = [
    ("html", "🐸 HTML document", "html", "Polished, frog-themed page with a clickable table"),
    ("md",   "Markdown (.md)",   "md",   "Headed list with reply links — notes & GitHub"),
    ("txt",  "Plain text (.txt)", "txt", "Simple and readable, no formatting"),
    ("csv",  "CSV (.csv)",       "csv",  "Spreadsheet: one row per comment"),
    ("json", "JSON (.json)",     "json", "Raw structured data for scripts"),
]


def _count(blocks):
    return sum(len(c) for _, c in blocks)


def render_comments_txt(blocks):
    """Plain-text export: a video heading, then each comment + reply link."""
    out = [
        f"FROGS — {_count(blocks)} harvested comments across {len(blocks)} videos",
        "=" * 64,
    ]
    for meta, comments in blocks:
        if meta:
            out += ["", f"{meta.get('title', '')}  —  {meta.get('channel', '')}",
                    f"  {meta.get('url', '')}", "-" * 48]
        for c in comments:
            out.append(f"  [{c['likes']:>4} likes]  {c['author']}: {c['text'].strip()}")
            out.append(f"               reply: {c['link']}")
    return "\n".join(out) + "\n"


def render_comments_csv(blocks):
    """CSV export: one row per comment, with its video's metadata."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "video_title", "channel", "video_url", "matched_keyword", "video_views",
        "author", "likes", "published", "comment", "comment_link", "comment_id",
    ])
    for meta, comments in blocks:
        m = meta or {}
        for c in comments:
            w.writerow([
                m.get("title", ""), m.get("channel", ""), m.get("url", ""),
                m.get("matched_keyword", ""), m.get("views", ""),
                c["author"], c["likes"], c.get("published", ""),
                c["text"], c["link"], c["commentId"],
            ])
    return buf.getvalue()


def render_comments_json(blocks):
    """JSON export: a list of {video, comments} objects (raw harvested data)."""
    data = [{"video": meta, "comments": comments} for meta, comments in blocks]
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


_HTML_STYLE = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin: 0; padding: 2rem; font-family: ui-sans-serif, system-ui, sans-serif;
       background: #0e1a10; color: #e6f4d8; }
header { max-width: 1100px; margin: 0 auto 1.5rem; }
h1 { color: #57c84d; margin: 0 0 .25rem; font-size: 1.6rem; }
.sub { color: #8aa888; font-size: .9rem; }
.wrap { max-width: 1100px; margin: 0 auto; }
table { width: 100%; border-collapse: collapse; background: #13251a;
        border: 1px solid #2c4a36; border-radius: 10px; overflow: hidden; }
th { text-align: left; background: #1b3324; color: #a4e057; padding: .6rem .8rem;
     position: sticky; top: 0; font-size: .8rem; text-transform: uppercase;
     letter-spacing: .04em; }
td { padding: .55rem .8rem; border-top: 1px solid #20392a; vertical-align: top; }
tr.vid td { background: #16291d; }
tr.vid a { color: #7fd4ff; font-weight: 600; text-decoration: none; }
tr.cmt:hover td { background: #18301f; }
.author { font-weight: 600; white-space: nowrap; }
.likes { color: #e0c84a; text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }
.muted { color: #8aa888; font-weight: 400; }
a.reply { color: #57c84d; text-decoration: none; white-space: nowrap; }
a.reply:hover { text-decoration: underline; }
footer { max-width: 1100px; margin: 1.5rem auto 0; color: #6f8a70; font-size: .8rem; }
"""


def render_comments_html(blocks, generated=None):
    """A self-contained, frog-themed HTML page with a clickable comment table."""
    esc = html.escape
    rows = []
    for meta, comments in blocks:
        if meta:
            views = meta.get("views")
            extra = f" · {views:,} views" if isinstance(views, int) and views else ""
            rows.append(
                '<tr class="vid"><td colspan="4">'
                f'<a href="{esc(meta.get("url", "#"))}" target="_blank" rel="noopener">'
                f'{esc(meta.get("title", "(untitled)"))}</a>'
                f'<span class="muted"> — {esc(meta.get("channel", ""))}'
                f' · kw: {esc(meta.get("matched_keyword", ""))}{extra}</span></td></tr>'
            )
        for c in comments:
            rows.append(
                '<tr class="cmt">'
                f'<td class="author">{esc(c["author"])}</td>'
                f'<td class="likes">{c["likes"]}&#128077;</td>'
                f'<td>{esc(c["text"]).replace(chr(10), "<br>")}</td>'
                f'<td><a class="reply" href="{esc(c["link"])}" target="_blank" '
                'rel="noopener">reply &#8599;</a></td>'
                "</tr>"
            )
    sub = f"{_count(blocks)} comments across {len(blocks)} videos"
    if generated:
        sub += f" · generated {esc(generated)}"
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>Frogs — harvested comments</title>"
        f"<style>{_HTML_STYLE}</style></head><body>"
        f'<header><h1>&#128056; Frogs — harvested comments</h1>'
        f'<div class="sub">{sub}</div></header>'
        '<div class="wrap"><table><thead><tr>'
        "<th>Author</th><th>Likes</th><th>Comment</th><th>Reply</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
        "<footer>Exported by Frogs YouTube Replier &#128056; — reply links open the "
        "comment on YouTube.</footer></body></html>\n"
    )
