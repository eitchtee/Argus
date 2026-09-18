import json
import re
import time
from dataclasses import dataclass
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


API_BASE_URL = "https://api.simkl.com"
DATA_BASE_URL = "https://data.simkl.in"
AUTHORIZE_URL = "https://simkl.com/oauth/authorize"
DEFAULT_APP_NAME = "argus"
DEFAULT_APP_VERSION = "1.0"

# SIMKL caps every client id and every user token at 10 GET/s and 1 POST/s.
READ_INTERVAL = 0.12
WRITE_INTERVAL = 1.0
TRANSIENT_STATUSES = {500, 502, 503}
_BACKOFF_SECONDS = (1, 2, 4, 8, 16)
_REDIRECT_PATH = re.compile(r"/(movies|tv|anime)/(\d+)")


class _NoRedirectHandler(HTTPRedirectHandler):
    """SIMKL's ``/redirect`` answers with the id in ``Location``; never follow it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_default_opener = build_opener(_NoRedirectHandler()).open


class SimklError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, error: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.error = error


class SimklAuthenticationError(SimklError):
    pass


class SimklNotFound(SimklError):
    pass


class SimklRateLimited(SimklError):
    def __init__(self, retry_after: int, message: str | None = None):
        self.retry_after = max(1, int(retry_after))
        super().__init__(
            message or f"SIMKL rate limit; retry after {self.retry_after} seconds",
            status_code=429,
            error="rate_limit",
        )


@dataclass(frozen=True)
class TokenResponse:
    access_token: str
    expires_in: int
    token_type: str = "bearer"
    scope: str = "public"


@dataclass(frozen=True)
class RedirectTarget:
    section: str
    simkl_id: int

    @property
    def media_type(self) -> str:
        return {"movies": "movie", "anime": "anime"}.get(self.section, "show")


def build_authorize_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    app_name: str = DEFAULT_APP_NAME,
    app_version: str = DEFAULT_APP_VERSION,
) -> str:
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "app-name": app_name,
            "app-version": app_version,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


class SimklClient:
    def __init__(
        self,
        access_token: str | None,
        *,
        client_id: str,
        client_secret: str = "",
        app_name: str = DEFAULT_APP_NAME,
        app_version: str = DEFAULT_APP_VERSION,
        opener=None,
        sleeper=time.sleep,
        clock=monotonic,
        timeout: float = 30,
        api_base_url: str = API_BASE_URL,
        data_base_url: str = DATA_BASE_URL,
        max_attempts: int = 5,
    ):
        self.access_token = access_token or ""
        self.client_id = client_id
        self.client_secret = client_secret
        self.app_name = app_name
        self.app_version = app_version
        self._opener = opener or _default_opener
        self._sleeper = sleeper
        self._clock = clock
        self.timeout = timeout
        self.api_base_url = api_base_url.rstrip("/")
        self.data_base_url = data_base_url.rstrip("/")
        self.max_attempts = max(1, int(max_attempts))
        self._last_read_at: float | None = None
        self._last_write_at: float | None = None

    # -- OAuth ---------------------------------------------------------------

    def exchange_code(self, code: str, redirect_uri: str) -> TokenResponse:
        payload, _headers, _status = self._request(
            "POST",
            "/oauth/token",
            json_body={
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            include_auth=False,
        )
        if not isinstance(payload, dict) or not payload.get("access_token"):
            raise SimklError("SIMKL OAuth response did not contain an access token")
        return TokenResponse(
            access_token=str(payload["access_token"]),
            expires_in=int(payload.get("expires_in") or 0),
            token_type=str(payload.get("token_type") or "bearer"),
            scope=str(payload.get("scope") or "public"),
        )

    # -- User data -----------------------------------------------------------

    def get_user_settings(self) -> dict:
        payload, _headers, _status = self._request("POST", "/users/settings")
        return payload if isinstance(payload, dict) else {}

    def get_activities(self) -> dict:
        payload, _headers, _status = self._request("GET", "/sync/activities")
        return payload if isinstance(payload, dict) else {}

    def get_all_items(
        self,
        media_type: str | None = None,
        status: str | None = None,
        *,
        date_from: str | None = None,
        extended: str | None = None,
        episode_watched_at: bool = False,
        include_all_episodes: bool = False,
    ) -> dict:
        path = "/sync/all-items"
        if media_type:
            path = f"{path}/{media_type}"
            if status:
                path = f"{path}/{status}"
        params: dict[str, str] = {}
        if date_from:
            params["date_from"] = date_from
        if extended:
            params["extended"] = extended
        if episode_watched_at:
            params["episode_watched_at"] = "yes"
        if include_all_episodes:
            params["include_all_episodes"] = "yes"
        payload, _headers, _status = self._request("GET", path, params=params)
        return payload if isinstance(payload, dict) else {}

    # -- Writes --------------------------------------------------------------

    def add_to_history(self, payload: dict) -> dict:
        return self._write("/sync/history", payload)

    def remove_from_history(self, payload: dict) -> dict:
        return self._write("/sync/history/remove", payload)

    def add_to_list(self, payload: dict) -> dict:
        return self._write("/sync/add-to-list", payload)

    def add_ratings(self, payload: dict) -> dict:
        return self._write("/sync/ratings", payload)

    def remove_ratings(self, payload: dict) -> dict:
        return self._write("/sync/ratings/remove", payload)

    # -- Catalog -------------------------------------------------------------

    def resolve(
        self,
        *,
        media_type: str | None = None,
        imdb: str | None = None,
        tmdb: str | int | None = None,
        tvdb: str | int | None = None,
        title: str | None = None,
        year: int | None = None,
    ) -> RedirectTarget | None:
        """Translate external ids into a SIMKL id via the ``/redirect`` helper.

        SIMKL answers with a ``301`` whose ``Location`` carries the canonical
        page; the id is parsed from that header and the redirect is never
        followed.
        """
        params: dict[str, str] = {"to": "simkl"}
        if imdb:
            params["imdb"] = str(imdb)
        if tmdb not in (None, ""):
            params["tmdb"] = str(tmdb)
        if tvdb not in (None, ""):
            params["tvdb"] = str(tvdb)
        if title:
            params["title"] = str(title)
        if year:
            params["year"] = str(year)
        if media_type:
            params["type"] = {"show": "tv", "movie": "movie", "anime": "anime", "tv": "tv"}.get(
                media_type, media_type
            )
        if len(params) == 1 or (
            len(params) == 2 and "type" in params
        ):
            return None
        _payload, headers, status = self._request(
            "GET",
            "/redirect",
            params=params,
            include_auth=False,
        )
        location = _header_value(headers, "Location") if 300 <= status < 400 else None
        if not location:
            return None
        match = _REDIRECT_PATH.search(str(location))
        if match is None:
            return None
        return RedirectTarget(section=match.group(1), simkl_id=int(match.group(2)))

    def get_movie(self, simkl_id: int) -> dict | None:
        return self._detail(f"/movies/{int(simkl_id)}")

    def get_show(self, simkl_id: int) -> dict | None:
        return self._detail(f"/tv/{int(simkl_id)}")

    def get_anime(self, simkl_id: int) -> dict | None:
        return self._detail(f"/anime/{int(simkl_id)}")

    def get_detail(self, media_type: str, simkl_id: int) -> dict | None:
        getter = {
            "movie": self.get_movie,
            "movies": self.get_movie,
            "anime": self.get_anime,
        }.get(media_type, self.get_show)
        return getter(simkl_id)

    # -- CDN data files (no user token, but the client id is still required) --

    def get_trending(
        self,
        category: str | None = None,
        timeframe: str = "today",
        *,
        size: int = 100,
    ):
        prefix = f"{self.data_base_url}/discover/trending"
        if category:
            prefix = f"{prefix}/{category}"
        payload, _headers, _status = self._request(
            "GET",
            f"{prefix}/{timeframe}_{int(size)}.json",
            include_auth=False,
        )
        return payload

    def get_calendar(self, kind: str = "tv") -> dict:
        payload, _headers, _status = self._request(
            "GET",
            f"{self.data_base_url}/calendar/v2/{kind}.json",
            include_auth=False,
        )
        return payload if isinstance(payload, dict) else {}

    # -- Internals -----------------------------------------------------------

    def _detail(self, path: str) -> dict | None:
        payload, _headers, _status = self._request("GET", path, include_auth=False)
        # Unknown ids come back as ``200 []``; a 200 with an ``error`` key is
        # SIMKL's other way of saying "nothing here".
        if not isinstance(payload, dict) or not payload or "error" in payload:
            return None
        return payload

    def _write(self, path: str, payload: dict) -> dict:
        response, _headers, _status = self._request("POST", path, json_body=payload)
        return response if isinstance(response, dict) else {}

    def _throttle(self, method: str) -> None:
        now = self._clock()
        if method == "POST":
            last, interval = self._last_write_at, WRITE_INTERVAL
        else:
            last, interval = self._last_read_at, READ_INTERVAL
        if last is not None:
            delay = interval - (now - last)
            if delay > 0:
                self._sleeper(delay)
                now = self._clock()
        if method == "POST":
            self._last_write_at = now
        else:
            self._last_read_at = now

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
        include_auth: bool = True,
    ) -> tuple[object, object, int]:
        query = {
            "client_id": self.client_id,
            "app-name": self.app_name,
            "app-version": self.app_version,
            **(params or {}),
        }
        url = _build_url(path, params=query, api_base_url=self.api_base_url)
        data = None
        headers = {
            "Accept": "application/json",
            "User-Agent": f"{self.app_name}/{self.app_version}",
        }
        if json_body is not None:
            data = json.dumps(json_body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if include_auth and self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"

        attempt = 0
        while True:
            attempt += 1
            self._throttle(method)
            request = Request(url, data=data, headers=headers, method=method)
            try:
                response = self._opener(request, timeout=self.timeout)
                status = response.getcode() if hasattr(response, "getcode") else 200
                response_headers = getattr(response, "headers", {})
                if 300 <= status < 400:
                    return None, response_headers, status
                raw_body = response.read()
                if status >= 400:
                    error = _error_for_status(status, response_headers, raw_body)
                else:
                    return _decode(raw_body, url), response_headers, status
            except HTTPError as exc:
                status = exc.code
                response_headers = exc.headers
                if 300 <= status < 400:
                    return None, response_headers, status
                try:
                    raw_body = exc.read()
                except Exception:  # pragma: no cover - defensive
                    raw_body = b""
                error = _error_for_status(status, response_headers, raw_body)
            except (URLError, OSError) as exc:
                status = None
                error = SimklError(f"SIMKL request failed: {exc}")

            if not self._should_retry(method, status, error) or attempt >= self.max_attempts:
                raise error
            self._sleeper(_BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS)) - 1])

    @staticmethod
    def _should_retry(method: str, status: int | None, error: SimklError) -> bool:
        if status is None or status in TRANSIENT_STATUSES:
            return True
        if status == 429:
            # A throttled POST extends the block when hammered; hand the
            # retry to the caller instead of insisting.
            return method != "POST"
        # SIMKL serialises writes per user behind a 20-second lock and reports
        # a busy lock as ``400 rate_limit``; a short wait is all it needs.
        return status == 400 and error.error == "rate_limit"


def _decode(raw_body: bytes, url: str):
    if not raw_body:
        return None
    try:
        return json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SimklError(f"SIMKL returned a non-JSON body for {url}") from exc


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


def _error_for_status(status: int, headers, raw_body: bytes) -> SimklError:
    error_code = None
    message = None
    if raw_body:
        try:
            body = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            body = None
        if isinstance(body, dict):
            error_code = body.get("error")
            message = body.get("message") or body.get("error_description")
    if status == 429:
        return SimklRateLimited(_header_int(headers, "Retry-After") or 60)
    if status == 401:
        return SimklAuthenticationError(
            "SIMKL rejected the access token; reconnect the account.",
            status_code=status,
            error=error_code or "user_token_failed",
        )
    if status == 404:
        return SimklNotFound(
            message or "SIMKL resource was not found",
            status_code=status,
            error=error_code,
        )
    if status == 412:
        return SimklError(
            message
            or "SIMKL rejected the client id (wrong, suspended, or over its request limit).",
            status_code=status,
            error=error_code or "client_id_failed",
        )
    detail = f" ({error_code})" if error_code else ""
    return SimklError(
        message or f"SIMKL request failed with HTTP {status}{detail}",
        status_code=status,
        error=error_code,
    )
