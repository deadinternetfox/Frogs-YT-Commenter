"""oauth.py — Google/YouTube OAuth 2.0 (so the app can post replies as your channel).

An API key only reads; posting needs OAuth. You create a free OAuth "Desktop app"
client once in Google Cloud Console, download client_secret.json, and the first
login opens a browser to authorize. The token is cached and refreshed silently.

The google libraries are imported lazily so the rest of the app works without them
installed (read-only / drafting still function).
"""

import os

from . import config

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]

SETUP_HINT = (
    "To post replies you need a Google OAuth client:\n"
    "  1. console.cloud.google.com → create/select a project\n"
    "  2. Enable 'YouTube Data API v3'\n"
    "  3. APIs & Services → Credentials → Create OAuth client ID → type 'Desktop app'\n"
    "  4. Download the JSON and point this app at it (or drop it in the config dir as\n"
    "     client_secret.json).\n"
    "Then click 'Login with Google' — a browser opens once to authorize."
)


class OAuthError(Exception):
    """Login / token problems surfaced to the UI."""


def _require_libs():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        return Request, Credentials, InstalledAppFlow, build
    except ImportError as e:
        raise OAuthError(
            "Google libraries are not installed. Run ./frogs (the launcher installs "
            "google-auth-oauthlib and google-api-python-client)."
        ) from e


def load_cached_credentials():
    """Return valid cached credentials (refreshing if needed), or None.

    Does NOT trigger the interactive browser flow.
    """
    Request, Credentials, _Flow, _build = _require_libs()
    tpath = config.token_path()
    if not os.path.exists(tpath):
        return None
    try:
        creds = Credentials.from_authorized_user_file(tpath, SCOPES)
    except Exception:
        return None
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save(creds)
            return creds
        except Exception:
            return None
    return None


def login(client_secret_file):
    """Run the interactive OAuth flow (opens a browser). BLOCKING — call in a thread.

    Returns credentials. Raises OAuthError on problems.
    """
    _Request, _Credentials, InstalledAppFlow, _build = _require_libs()
    if not os.path.exists(client_secret_file):
        raise OAuthError(
            f"Missing client_secret.json at:\n  {client_secret_file}\n\n{SETUP_HINT}"
        )
    try:
        flow = InstalledAppFlow.from_client_secrets_file(client_secret_file, SCOPES)
        creds = flow.run_local_server(port=0)
    except Exception as e:
        raise OAuthError(f"Login failed: {e}") from e
    _save(creds)
    return creds


def _save(creds):
    tpath = config.token_path()
    fd = os.open(tpath, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(creds.to_json())


def build_service(creds):
    """Build the authorized youtube service used by core.post_reply()."""
    _Request, _Credentials, _Flow, build = _require_libs()
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def channel_title(creds):
    """Return the authorized account's channel title (best-effort, for display)."""
    try:
        service = build_service(creds)
        resp = service.channels().list(part="snippet", mine=True).execute()
        items = resp.get("items", [])
        if items:
            return items[0]["snippet"]["title"]
    except Exception:
        pass
    return "your channel"


def logout():
    """Delete the cached token (forces re-login next time)."""
    tpath = config.token_path()
    if os.path.exists(tpath):
        os.remove(tpath)
