"""Interactive OAuth2 device/browser login flow (RFC 8628 + loopback redirect).

Pure stdlib: http.server for the loopback redirect, urllib for token requests.
Google/Outlook both supported via provider-specific endpoints.

Flow:
  1. Build authorization URL and open it in the default browser.
  2. Start a temporary local HTTP server on a free port to catch the redirect.
  3. Exchange the authorization code for an access + refresh token.
  4. Persist tokens into the account (config.json).
Refresh: an expired access token is refreshed automatically at login time.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import socket
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from fengtang.core.errors import AuthError

# Built-in OAuth2 providers, mirroring Thunderbird's OAuth2Providers.sys.mjs:
# Google/Microsoft ship Thunderbird public client_ids that permit the mail
# scopes for installed apps (loopback redirect on any port; Google adds PKCE).
BUILTIN_PROVIDERS: dict[str, dict[str, Any]] = {
    "gmail": {
        "name": "gmail",
        "auth_url": "https://accounts.google.com/o/oauth2/auth",
        "token_url": "https://www.googleapis.com/oauth2/v3/token",
        "client_id": "406964657835-aq8lmia8j95dhl1a2bvharmfk3t1hgqj.apps.googleusercontent.com",
        "client_secret": "kSmqreRr0qwBWJgbf5Y-PjSU",
        "redirect_base": "http://127.0.0.1",
        "scope": "https://mail.google.com/",
        "use_pkce": True,
    },
    "outlook": {
        "name": "outlook",
        "auth_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "client_id": "9e5f94bc-e8a4-4e73-b8be-63364c29d753",
        "redirect_base": "http://localhost",
        "scope": (
            "https://outlook.office.com/IMAP.AccessAsUser.All "
            "https://outlook.office.com/POP.AccessAsUser.All "
            "https://outlook.office.com/SMTP.Send offline_access"
        ),
        "use_pkce": False,
    },
    "yandex": {
        "name": "yandex",
        "auth_url": "https://oauth.yandex.com/authorize",
        "token_url": "https://oauth.yandex.com/token",
        "client_id": "2a00bba7374047a6ab79666485ffce31",
        "redirect_base": "http://localhost",
        "scope": "mail:read_mail mail:send_email",
        "use_pkce": False,
    },
}

# Backwards-compatible alias.
PROVIDERS = BUILTIN_PROVIDERS


class _RedirectHandler(BaseHTTPRequestHandler):
    """Catches ?code=... from the OAuth redirect and shuts the server down."""

    code: str | None = None
    state: str | None = None
    error: str | None = None

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        if "code" in params:
            _RedirectHandler.code = params["code"][0]
            _RedirectHandler.state = params.get("state", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>&#9989; Login OK</h2>"
                b"<p>You can close this tab and return to the terminal.</p>"
                b"</body></html>"
            )
        else:
            _RedirectHandler.error = params.get(
                "error_description", params.get("error", ["unknown"])
            )[0]
            self.send_response(400)
            self.end_headers()
            self.wfile.write(f"<h1>Login failed: {_RedirectHandler.error}</h1>".encode())
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, *args: Any) -> None:  # silence default logging
        pass


def _pkce_pair() -> tuple[str, str]:
    """RFC 7636 PKCE verifier/challenge (S256)."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )
    return verifier, challenge


def _free_port(preferred: int | None = None) -> int:
    if preferred:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", preferred)) != 0:
                return preferred
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _token_request(token_url: str, data: dict[str, str]) -> dict[str, Any]:
    import urllib.request

    body = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(
        token_url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as resp:
        payload: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
    if "access_token" not in payload:
        raise AuthError(f"Token exchange failed: {payload}")
    return payload


def interactive_login(
    email: str,
    provider: str = "gmail",
    client_id: str = "",
    open_browser: bool = True,
    timeout: int = 300,
    localhost_port: int | None = None,
) -> dict[str, Any]:
    """Run the browser-based OAuth2 login for `email` (Thunderbird-style).

    Uses the built-in Thunderbird public client_id unless overridden. Opens
    the consent page, catches the redirect on a local loopback port, and
    exchanges the authorization code (PKCE where the provider supports it).

    Returns: {"access_token", "refresh_token", "expires_in", "scope",
              "obtained_at"}
    """
    key = provider.lower()
    endpoints = PROVIDERS.get(key)
    if not endpoints:
        raise AuthError(
            f"No OAuth2 endpoints for provider {provider!r}. Supported: {', '.join(PROVIDERS)}"
        )
    client_id = client_id or str(endpoints.get("client_id", ""))
    client_secret = str(endpoints.get("client_secret", ""))

    # Redirect: provider's loopback base with any free port (TB-style).
    redirect_base = str(endpoints.get("redirect_base", "http://127.0.0.1"))
    parsed = urllib.parse.urlparse(redirect_base)
    port = _free_port(localhost_port)
    redirect_uri = f"{parsed.scheme}://{parsed.hostname}:{port}"

    state = secrets.token_urlsafe(16)
    use_pkce = bool(endpoints.get("use_pkce"))
    code_verifier, code_challenge = _pkce_pair() if use_pkce else ("", "")

    auth_params: dict[str, Any] = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": endpoints["scope"],
        "login_hint": email,
        "state": state,
    }
    if use_pkce:
        auth_params["code_challenge"] = code_challenge
        auth_params["code_challenge_method"] = "S256"
    if key == "gmail":
        auth_params["access_type"] = "offline"
        auth_params["prompt"] = "consent"
        auth_params["include_granted_scopes"] = "true"

    auth_url = str(endpoints["auth_url"]) + "?" + urllib.parse.urlencode(auth_params)

    _RedirectHandler.code = None
    _RedirectHandler.state = None
    _RedirectHandler.error = None
    server = HTTPServer(("127.0.0.1", port), _RedirectHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    print("Opening browser for login…")
    print(f"If it does not open, open this URL manually:\n\n  {auth_url}\n")
    if open_browser:
        try:
            webbrowser.open(auth_url)
        except Exception:
            pass

    deadline = time.time() + timeout
    while time.time() < deadline:
        server_thread.join(timeout=1)
        if not server_thread.is_alive():
            break
    else:
        server.server_close()
        raise AuthError(f"Timed out waiting for OAuth redirect ({timeout}s)")
    server.server_close()

    if _RedirectHandler.error:
        raise AuthError(f"Login failed: {_RedirectHandler.error}")
    if not _RedirectHandler.code:
        raise AuthError("No authorization code received")
    if _RedirectHandler.state != state:
        raise AuthError("OAuth state mismatch (possible CSRF) — aborting")

    token_data: dict[str, Any] = {
        "client_id": client_id,
        "code": _RedirectHandler.code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    if client_secret:
        token_data["client_secret"] = client_secret
    if use_pkce:
        token_data["code_verifier"] = code_verifier
    token_resp = _token_request(str(endpoints["token_url"]), token_data)
    return {
        "access_token": str(token_resp["access_token"]),
        "refresh_token": str(token_resp.get("refresh_token", "")),
        "expires_in": int(token_resp.get("expires_in", 3600)),
        "scope": str(token_resp.get("scope", endpoints["scope"])),
        "obtained_at": int(time.time()),
    }


def refresh_access_token(
    provider: str,
    refresh_token: str,
    client_id: str = "",
) -> dict[str, Any]:
    """Exchange a refresh token for a fresh access token."""
    endpoints = PROVIDERS.get(provider.lower())
    if not endpoints:
        raise AuthError(f"Unknown provider: {provider}")
    if not refresh_token:
        raise AuthError("No refresh token stored for this account")
    client_id = client_id or str(endpoints.get("client_id", ""))
    token_data: dict[str, Any] = {
        "client_id": client_id,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    client_secret = str(endpoints.get("client_secret", ""))
    if client_secret:
        token_data["client_secret"] = client_secret
    return _token_request(str(endpoints["token_url"]), token_data)


def ensure_fresh_token(account: Any) -> str:
    """Return a usable access token for `account`, refreshing if stale.

    Reads/writes account.oauth2_token and account.extra['refresh_token'].
    """
    provider_hint = str(dict(account.extra or {}).get("oauth_provider", "")).lower()
    if not provider_hint:
        for name in PROVIDERS:
            if (
                name in (account.smtp_host or "").lower()
                or name in (account.imap_host or "").lower()
            ):
                provider_hint = name
                break
    if not provider_hint:
        raise AuthError(
            "Cannot determine OAuth provider for this account; set account.extra['oauth_provider']"
        )
    provider_hint = "gmail" if provider_hint in ("gmail", "google") else provider_hint

    extras: dict[str, Any] = dict(account.extra or {})
    obtained_at = int(extras.get("token_obtained_at", 0))
    expires_in = int(extras.get("token_expires_in", 3600))
    if account.oauth2_token and obtained_at and time.time() < obtained_at + expires_in - 120:
        return str(account.oauth2_token)

    refreshed = refresh_access_token(
        provider_hint,
        str(extras.get("refresh_token", "")),
        str(extras.get("client_id", "")),
    )
    account.oauth2_token = str(refreshed["access_token"])
    account.extra["refresh_token"] = str(
        refreshed.get("refresh_token") or extras.get("refresh_token", "")
    )
    account.extra["token_obtained_at"] = int(time.time())
    account.extra["token_expires_in"] = int(refreshed.get("expires_in", 3600))
    return str(account.oauth2_token)
