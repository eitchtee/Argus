import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


API_BASE_URL = "https://api.mdblist.com"
AUTHORIZE_URL = "https://mdblist.com/oauth/authorize/"
USER_AGENT = "argus"

READ_INTERVAL = 0.1
WRITE_INTERVAL = 0.5
TRANSIENT_STATUSES = {500, 502, 503, 504}
_BACKOFF_SECONDS = (1, 2, 4, 8, 16)
# Sync reads page through with the largest page MDBList serves.
PAGE_LIMIT = 1000
# ``/sync/watched`` rejects a request with more than 200 shows.
MAX_SHOWS_PER_WRITE = 200
MAX_PAGES = 200


class MdblistError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, error: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.error = error


class MdblistAuthenticationError(MdblistError):
    pass


class MdblistNotFound(MdblistError):
    pass


class MdblistRateLimited(MdblistError):
    def __init__(self, retry_after: int, message: str | None = None):
        self.retry_after = max(1, int(retry_after))
        super().__init__(
            message or f"MDBList rate limit; retry after {self.retry_after} seconds",
            status_code=429,
            error="rate_limit",
        )


@dataclass(frozen=True)
class TokenResponse:
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "Bearer"
    scope: str = "write"


def pkce_pair() -> tuple[str, str]:
    """A ``(verifier, S256 challenge)`` pair; MDBList requires PKCE on every
    authorization request, confidential apps included."""
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


def build_authorize_url(*, client_id: str, redirect_uri: str, state: str, code_challenge: str) -> str:
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "write",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


class MdblistClient:
    """Thin MDBList API client.

    ``access_token`` is an OAuth bearer token, or a personal API key when
    ``api_key`` is true (MDBList takes those as an ``apikey`` query parameter).
    """

    def __init__(
        self,
        access_token: str | None,
        *,
        api_key: bool = False,
        client_id: str = "",
        client_secret: str = "",
        opener=None,
        sleeper=time.sleep,
        clock=monotonic,
        wall_clock=time.time,
        timeout: float = 30,
        api_base_url: str = API_BASE_URL,
        max_attempts: int = 5,
    ):
        self.access_token = access_token or ""
        self.api_key = api_key
        self.client_id = client_id
        self.client_secret = client_secret
        self._opener = opener or urlopen
        self._sleeper = sleeper
        self._clock = clock
        self._wall_clock = wall_clock
        self.timeout = timeout
        self.api_base_url = api_base_url.rstrip("/")
        self.max_attempts = max(1, int(max_attempts))
        self._last_read_at: float | None = None
        self._last_write_at: float | None = None

    # -- OAuth ---------------------------------------------------------------

    def exchange_code(self, code: str, redirect_uri: str, code_verifier: str) -> TokenResponse:
        return self._token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            }
        )

    def refresh(self, refresh_token: str) -> TokenResponse:
        return self._token({"grant_type": "refresh_token", "refresh_token": refresh_token})

    def _token(self, fields: dict) -> TokenResponse:
        payload, _headers, _status = self._request(
            "POST",
            "/oauth/token/",
            form_body={
                **fields,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            include_auth=False,
        )
        if not isinstance(payload, dict) or not payload.get("access_token"):
            raise MdblistError("MDBList OAuth response did not contain an access token")
        return TokenResponse(
            access_token=str(payload["access_token"]),
            refresh_token=str(payload.get("refresh_token") or ""),
            expires_in=int(payload.get("expires_in") or 0),
            token_type=str(payload.get("token_type") or "Bearer"),
            scope=str(payload.get("scope") or "write"),
        )

    # -- User data -----------------------------------------------------------

    def get_user(self) -> dict:
        return self._get_dict("/user")

    def get_activities(self) -> dict:
        return self._get_dict("/sync/last_activities")

    def get_journal(self, since: str) -> dict:
        """Replay the sync journal from ``since``, following every page.

        Returns ``{"journal": [...], "server_time": ..., "requires_full_sync": bool}``.
        """
        rows: list = []
        params: dict[str, str] = {"since": since, "limit": str(PAGE_LIMIT)}
        server_time = None
        for _page in range(MAX_PAGES):
            payload = self._get_dict("/sync/journal", params=params)
            if payload.get("requires_full_sync"):
                return {"journal": [], "server_time": payload.get("server_time"), "requires_full_sync": True}
            server_time = server_time or payload.get("server_time")
            rows.extend(item for item in payload.get("journal") or [] if isinstance(item, dict))
            cursor = (payload.get("pagination") or {}).get("next_cursor")
            if not cursor:
                break
            # ``since`` and ``cursor`` must not be combined.
            params = {"cursor": cursor, "limit": str(PAGE_LIMIT)}
        return {"journal": rows, "server_time": server_time, "requires_full_sync": False}

    def get_watched(self, *, ids_only: bool = False) -> dict:
        params = {"extended": "ids_only"} if ids_only else {}
        return self._paged("/sync/watched", ("movies", "shows", "seasons", "episodes"), params)

    def get_ratings(self) -> dict:
        return self._paged("/sync/ratings", ("movies", "shows", "seasons", "episodes"))

    def get_dropped(self) -> dict:
        return self._paged("/sync/dropped", ("shows",))

    def get_watchlist(self, *, ids_only: bool = True) -> dict:
        params = {"extended": "ids_only"} if ids_only else {}
        return self._paged("/watchlist/items", ("movies", "shows"), params)

    # -- Writes --------------------------------------------------------------

    def add_watched(self, payload: dict) -> dict:
        return self._write("/sync/watched", payload)

    def remove_watched(self, payload: dict) -> dict:
        return self._write("/sync/watched/remove", payload)

    def add_ratings(self, payload: dict) -> dict:
        return self._write("/sync/ratings", payload)

    def remove_ratings(self, payload: dict) -> dict:
        return self._write("/sync/ratings/remove", payload)

    def add_dropped(self, payload: dict) -> dict:
        return self._write("/sync/dropped", payload)

    def remove_dropped(self, payload: dict) -> dict:
        return self._write("/sync/dropped/remove", payload)

    def add_to_watchlist(self, payload: dict) -> dict:
        return self._write("/watchlist/items/add", payload)

    def remove_from_watchlist(self, payload: dict) -> dict:
        return self._write("/watchlist/items/remove", payload)

    # -- Catalog -------------------------------------------------------------

    def get_media(self, provider: str, media_type: str, media_id, *, append: tuple[str, ...] = ()) -> dict | None:
        params = {"append_to_response": ",".join(append)} if append else {}
        try:
            payload = self._get_dict(f"/{provider}/{media_type}/{media_id}/", params=params)
        except MdblistNotFound:
            return None
        if not payload or "error" in payload or not payload.get("title"):
            return None
        return payload

    def get_media_batch(self, provider: str, media_type: str, ids: list) -> list[dict]:
        """Look up many titles in one request; unknown ids are left out."""
        if not ids:
            return []
        payload, _headers, _status = self._request(
            "POST",
            f"/{provider}/{media_type}/",
            json_body={"ids": list(ids)},
            throttle_as="GET",
        )
        return [item for item in payload or [] if isinstance(item, dict)] if isinstance(payload, list) else []

    def get_streaming_chart(self, media_type: str, *, period: str = "1d", size: int = 20) -> list[dict]:
        payload = self._get_dict(
            f"/justwatch/streaming-charts/{media_type}",
            params={"period": period, "x": str(min(20, int(size)))},
        )
        return [item for item in payload.get("results") or [] if isinstance(item, dict)]

    def get_list_items(self, path: str, *, limit: int = 50, mediatype: str | None = None, append: tuple[str, ...] = ("poster",)) -> dict:
        params = {"limit": str(int(limit))}
        if mediatype:
            params["mediatype"] = mediatype
        if append:
            params["append_to_response"] = ",".join(append)
        return self._get_dict(path, params=params)

    def get_official_list_items(self, slug: str, **kwargs) -> dict:
        return self.get_list_items(f"/lists/official/{slug}/items", **kwargs)

    def get_recommendation_sections(self) -> list[dict]:
        payload = self._get_dict("/lists/recommended")
        return [item for item in payload.get("sections") or [] if isinstance(item, dict)]

    def get_recommendation_items(self, section: str, **kwargs) -> dict:
        return self.get_list_items(f"/lists/recommended/{section}/items", **kwargs)

    # -- Internals -----------------------------------------------------------

    def _get_dict(self, path: str, *, params: dict | None = None) -> dict:
        payload, _headers, _status = self._request("GET", path, params=params)
        return payload if isinstance(payload, dict) else {}

    def _paged(self, path: str, keys: tuple[str, ...], params: dict | None = None) -> dict:
        merged: dict[str, list] = {key: [] for key in keys}
        params = {**(params or {}), "limit": str(PAGE_LIMIT)}
        for _page in range(MAX_PAGES):
            payload = self._get_dict(path, params=params)
            for key in keys:
                merged[key].extend(item for item in payload.get(key) or [] if isinstance(item, dict))
            cursor = (payload.get("pagination") or {}).get("next_cursor")
            if not cursor:
                break
            params = {**params, "cursor": cursor}
        return merged

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
        form_body: dict | None = None,
        include_auth: bool = True,
        throttle_as: str | None = None,
    ) -> tuple[object, object, int]:
        query = dict(params or {})
        if include_auth and self.api_key and self.access_token:
            query["apikey"] = self.access_token
        url = _build_url(path, params=query, api_base_url=self.api_base_url)
        data = None
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if json_body is not None:
            data = json.dumps(json_body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif form_body is not None:
            data = urlencode(form_body).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if include_auth and self.access_token and not self.api_key:
            headers["Authorization"] = f"Bearer {self.access_token}"

        attempt = 0
        while True:
            attempt += 1
            self._throttle(throttle_as or method)
            request = Request(url, data=data, headers=headers, method=method)
            try:
                response = self._opener(request, timeout=self.timeout)
                status = response.getcode() if hasattr(response, "getcode") else 200
                response_headers = getattr(response, "headers", {})
                raw_body = response.read()
                if status >= 400:
                    error = self._error_for_status(status, response_headers, raw_body)
                else:
                    return _decode(raw_body, path), response_headers, status
            except HTTPError as exc:
                status = exc.code
                response_headers = exc.headers
                try:
                    raw_body = exc.read()
                except Exception:  # pragma: no cover - defensive
                    raw_body = b""
                error = self._error_for_status(status, response_headers, raw_body)
            except (URLError, OSError) as exc:
                status = None
                error = MdblistError(f"MDBList request failed: {exc}")

            if not _should_retry(method, status) or attempt >= self.max_attempts:
                raise error
            self._sleeper(_BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS)) - 1])

    def _error_for_status(self, status: int, headers, raw_body: bytes) -> MdblistError:
        message = None
        if raw_body:
            try:
                body = json.loads(raw_body.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                body = None
            if isinstance(body, dict):
                message = body.get("error_description") or body.get("error") or body.get("detail")
        if status == 429:
            return MdblistRateLimited(self._retry_after(headers))
        if status in {401, 403} and not (message and "supporter" in str(message).lower()):
            return MdblistAuthenticationError(
                f"MDBList rejected the credentials ({message or status}); reconnect the account.",
                status_code=status,
                error="invalid_credentials",
            )
        if status == 404:
            return MdblistNotFound(message or "MDBList resource was not found", status_code=status)
        return MdblistError(
            f"MDBList request failed with HTTP {status}" + (f": {message}" if message else ""),
            status_code=status,
            error=str(message) if message else None,
        )

    def _retry_after(self, headers) -> int:
        retry_after = _header_int(headers, "Retry-After")
        if retry_after:
            return retry_after
        # The daily quota reports its reset as an epoch timestamp.
        reset = _header_int(headers, "X-RateLimit-Reset")
        if reset:
            return max(60, int(reset - self._wall_clock()))
        return 60


def _should_retry(method: str, status: int | None) -> bool:
    if status is None or status in TRANSIENT_STATUSES:
        return True
    return False


def _decode(raw_body: bytes, path: str):
    if not raw_body:
        return None
    try:
        return json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise MdblistError(f"MDBList returned a non-JSON body for {path}") from exc


def _build_url(path: str, *, params: dict | None, api_base_url: str) -> str:
    url = f"{api_base_url.rstrip('/')}/{path.lstrip('/')}"
    if not params:
        return url
    parsed = urlsplit(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.extend((key, value) for key, value in params.items())
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def _header_int(headers, name: str) -> int | None:
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    value = getter(name) if getter is not None else None
    if value is None:
        name_casefold = name.casefold()
        for key, candidate in getattr(headers, "items", lambda: [])():
            if str(key).casefold() == name_casefold:
                value = candidate
                break
    try:
        return int(float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None
