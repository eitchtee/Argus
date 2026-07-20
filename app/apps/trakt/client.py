import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl
from urllib.request import Request, urlopen


API_BASE_URL = "https://api.trakt.tv"
OAUTH_TOKEN_URL = f"{API_BASE_URL}/oauth/token"
DEFAULT_USER_AGENT = "Argus Trakt Sync/1.0"
DEFAULT_PAGE_SIZE = 100


class TraktError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class TraktAuthenticationError(TraktError):
    pass


class TraktNotFound(TraktError):
    pass


class TraktRateLimited(TraktError):
    def __init__(self, retry_after: int, message: str | None = None):
        self.retry_after = max(1, int(retry_after))
        super().__init__(message or f"Trakt rate limit; retry after {self.retry_after} seconds", status_code=429)


@dataclass(frozen=True)
class TokenResponse:
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "bearer"
    created_at: int | None = None
    scope: str = ""


@dataclass(frozen=True)
class TraktSnapshot:
    watchlist_movies: list[dict]
    watchlist_shows: list[dict]
    watched_movies: list[dict]
    watched_shows: list[dict]
    dropped_shows: list[dict]
    watched_episodes: list[dict] = field(default_factory=list)


class TraktClient:
    def __init__(
        self,
        access_token: str | None,
        *,
        client_id: str,
        client_secret: str,
        opener=urlopen,
        sleeper=time.sleep,
        clock=monotonic,
        timeout: float = 10,
        api_base_url: str = API_BASE_URL,
        user_agent: str = DEFAULT_USER_AGENT,
    ):
        self.access_token = access_token or ""
        self.client_id = client_id
        self.client_secret = client_secret
        self._opener = opener
        self._sleeper = sleeper
        self._clock = clock
        self.timeout = timeout
        self.api_base_url = api_base_url.rstrip("/")
        self.user_agent = user_agent
        self._last_write_at: float | None = None

    def exchange_code(self, code: str, redirect_uri: str) -> TokenResponse:
        return self._token_request(
            {
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            }
        )

    def refresh_access_token(self, refresh_token: str, redirect_uri: str) -> TokenResponse:
        try:
            return self._token_request(
                {
                    "refresh_token": refresh_token,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "refresh_token",
                }
            )
        except TraktError as exc:
            if exc.status_code == 400:
                raise TraktAuthenticationError(
                    "Trakt rejected the refresh token; reconnect the account.",
                    status_code=400,
                ) from exc
            raise

    def get_paginated(self, path: str, *, params: dict | None = None) -> list[dict]:
        base_params = dict(params or {})
        limit = int(base_params.get("limit", DEFAULT_PAGE_SIZE))
        page = 1
        results: list[dict] = []

        while True:
            page_params = {**base_params, "page": page, "limit": limit}
            payload, headers = self._request("GET", path, params=page_params)
            if isinstance(payload, list):
                page_items = payload
            elif payload:
                page_items = [payload]
            else:
                page_items = []
            results.extend(page_items)

            page_count = _header_int(headers, "X-Pagination-Page-Count")
            if page_count is not None:
                if page >= page_count:
                    break
            elif len(page_items) < limit:
                break
            page += 1

        return results

    def get_user_settings(self) -> dict:
        payload, _headers = self._request("GET", "/users/settings")
        return payload if isinstance(payload, dict) else {}

    def get_snapshot(
        self,
        *,
        episode_history_start_at: datetime | str | None = None,
    ) -> TraktSnapshot:
        episode_history_params = {}
        if episode_history_start_at is not None:
            episode_history_params["start_at"] = (
                episode_history_start_at.isoformat()
                if isinstance(episode_history_start_at, datetime)
                else str(episode_history_start_at)
            )
        return TraktSnapshot(
            watchlist_movies=self.get_paginated("/sync/watchlist/movies"),
            watchlist_shows=self.get_paginated("/sync/watchlist/shows"),
            watched_movies=self.get_paginated("/sync/watched/movies"),
            watched_shows=self.get_paginated(
                "/sync/watched/shows",
                params={"extended": "full"},
            ),
            dropped_shows=self.get_paginated(
                "/users/hidden/dropped",
                params={"type": "show"},
            ),
            watched_episodes=self.get_paginated(
                "/sync/history/episodes",
                params=episode_history_params,
            ),
        )

    def post_watchlist(self, items_by_type: dict[str, list[dict]], *, remove: bool = False):
        if not items_by_type:
            return None
        return self._write_json(
            "/sync/watchlist/remove" if remove else "/sync/watchlist",
            items_by_type,
        )

    def post_history(self, movies: list[dict], shows: list[dict]):
        if not movies and not shows:
            return None
        return self._write_json("/sync/history", {"movies": movies, "shows": shows})

    def post_dropped(self, shows: list[dict], *, remove: bool = False):
        if not shows:
            return None
        return self._write_json(
            "/users/hidden/dropped/remove" if remove else "/users/hidden/dropped",
            {"shows": shows},
        )

    def _token_request(self, form_data: dict[str, str]) -> TokenResponse:
        payload, _headers = self._request(
            "POST",
            OAUTH_TOKEN_URL,
            form_data=form_data,
            include_auth=False,
        )
        if not isinstance(payload, dict) or not payload.get("access_token"):
            raise TraktError("Trakt OAuth response did not contain an access token")
        return TokenResponse(
            access_token=str(payload["access_token"]),
            refresh_token=str(payload.get("refresh_token") or ""),
            expires_in=int(payload.get("expires_in") or 0),
            token_type=str(payload.get("token_type") or "bearer"),
            created_at=(
                int(payload["created_at"])
                if payload.get("created_at") is not None
                else None
            ),
            scope=str(payload.get("scope") or ""),
        )

    def _write_json(self, path: str, payload: dict):
        self._wait_for_write()
        return self._request("POST", path, json_body=payload)[0]

    def _wait_for_write(self):
        now = self._clock()
        if self._last_write_at is not None:
            delay = 1.0 - (now - self._last_write_at)
            if delay > 0:
                self._sleeper(delay)
                now = self._clock()
        self._last_write_at = now

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
        form_data: dict | None = None,
        include_auth: bool = True,
    ) -> tuple[object, object]:
        url = _build_url(path, params=params, api_base_url=self.api_base_url)
        data = None
        headers = {
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }
        if form_data is not None:
            data = urlencode(form_data).encode("ascii")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif json_body is not None:
            data = json.dumps(json_body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"

        if path == OAUTH_TOKEN_URL:
            headers.update(
                {
                    "trakt-api-key": self.client_id,
                    "trakt-api-version": "2",
                }
            )
        elif include_auth:
            headers.update(
                {
                    "trakt-api-key": self.client_id,
                    "trakt-api-version": "2",
                }
            )
            if self.access_token:
                headers["Authorization"] = f"Bearer {self.access_token}"
            headers.setdefault("Content-Type", "application/json")

        request = Request(url, data=data, headers=headers, method=method)
        try:
            response = self._opener(request, timeout=self.timeout)
            status = response.getcode() if hasattr(response, "getcode") else 200
            if status >= 400:
                raise _error_for_status(status, getattr(response, "headers", {}))
            raw_body = response.read()
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
            return payload, getattr(response, "headers", {})
        except HTTPError as exc:
            raise _error_for_status(exc.code, exc.headers) from exc
        except (URLError, OSError) as exc:
            raise TraktError(f"Trakt request failed: {exc}") from exc


def _build_url(path: str, *, params: dict | None, api_base_url: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        url = path
    else:
        url = f"{api_base_url.rstrip('/')}/{path.lstrip('/')}"
    if not params:
        return url
    parsed = urlsplit(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.extend((key, value) for key, value in params.items())
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


def _header_value(headers, name: str):
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if getter is not None:
        value = getter(name)
        if value is not None:
            return value
    name_casefold = name.casefold()
    for key, value in getattr(headers, "items", lambda: [])():
        if str(key).casefold() == name_casefold:
            return value
    return None


def _header_int(headers, name: str) -> int | None:
    value = _header_value(headers, name)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _error_for_status(status: int, headers) -> TraktError:
    if status == 429:
        retry_after = _header_int(headers, "Retry-After") or 60
        return TraktRateLimited(retry_after)
    if status in {401, 403}:
        return TraktAuthenticationError(
            "Trakt rejected the credentials; reconnect the account.",
            status_code=status,
        )
    if status == 404:
        return TraktNotFound("Trakt resource was not found", status_code=status)
    return TraktError(f"Trakt request failed with HTTP {status}", status_code=status)
