"""core.py — YouTube Data API read/write logic.

Pure logic, no UI. Network failures raise exceptions (never sys.exit) so the TUI
can present them. Read paths (search/comments) need only an API key; the write
path (post_reply) takes an already-built, OAuth-authorized service object.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://www.googleapis.com/youtube/v3"


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
def list_comments(video_id, api_key, max_results=100, match_words=None, progress=None):
    """Return top-level comments for a video, optionally filtered by words.

    Returns [] (not an error) when comments are disabled on the video.
    """
    out = []
    page_token = None
    fetched = 0
    match_words = [w.lower() for w in (match_words or []) if w.strip()]
    while fetched < max_results:
        try:
            data = api_get(
                "commentThreads",
                {
                    "part": "snippet",
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
            top = item["snippet"]["topLevelComment"]
            cs = top["snippet"]
            text = cs["textDisplay"]
            if match_words and not any(w in text.lower() for w in match_words):
                continue
            cid = top["id"]
            out.append(
                {
                    "commentId": cid,
                    "author": cs["authorDisplayName"],
                    "text": text,
                    "likes": cs.get("likeCount", 0),
                    "published": cs["publishedAt"],
                    "videoId": video_id,
                    "link": f"https://www.youtube.com/watch?v={video_id}&lc={cid}",
                }
            )
        fetched += len(data.get("items", []))
        if progress:
            progress(min(fetched, max_results), max_results, f"comments: {video_id}")
        page_token = data.get("nextPageToken")
        if not page_token:
            break
        time.sleep(0.2)
    return out


# --------------------------------------------------------------------------
# Harvest: search keywords, then pull comments from each hit
# --------------------------------------------------------------------------
def harvest(keywords, api_key, max_videos=10, max_comments=50, match_words=None,
            order="relevance", progress=None):
    """Search keywords and gather comments for every video found.

    Returns (videos, blocks) where blocks = [(video_dict, [comments]), ...] for
    videos that had at least one (matching) comment. Each video dict gains a
    'comment_count' field.
    """
    videos = search_videos(keywords, api_key, max_videos, order, progress=progress)
    blocks = []
    total = len(videos)
    for i, v in enumerate(videos):
        if progress:
            progress(i, total, f"[{i + 1}/{total}] {v['title'][:48]}")
        comments = list_comments(v["videoId"], api_key, max_comments, match_words)
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
