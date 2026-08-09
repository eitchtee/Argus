import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_API_BASE_URL = "https://api.strem.io"
DEFAULT_LINK_BASE_URL = "https://link.stremio.com/api/v2"
DEFAULT_CINEMETA_BASE_URL = "https://v3-cinemeta.strem.io"
LIBRARY_COLLECTION = "libraryItem"


class StremioAPIError(Exception):
    def __init__(self, message: str, *, code: int | None = None):
        super().__init__(message)
        self.code = code


class StremioClient:
    def __init__(
        self,
        auth_key: str,
        *,
        opener=urlopen,
        timeout: float = 10,
        api_base_url: str = DEFAULT_API_BASE_URL,
        link_base_url: str = DEFAULT_LINK_BASE_URL,
        cinemeta_base_url: str = DEFAULT_CINEMETA_BASE_URL,
    ):
        self.auth_key = auth_key or ""
        self._opener = opener
        self.timeout = timeout
        self.api_base_url = api_base_url.rstrip("/")
        self.link_base_url = link_base_url.rstrip("/")
        self.cinemeta_base_url = cinemeta_base_url.rstrip("/")

    def create_link_code(self) -> dict:
        payload = self._request_json(
            "GET",
            f"{self.link_base_url}/create",
            params={"type": "Create"},
            operation="link creation",
        )
        result = _result(payload, "link creation")
        if not isinstance(result, dict) or not all(
            result.get(key) for key in ("code", "link", "qrcode")
        ):
            raise StremioAPIError("Stremio link creation returned incomplete data")
        return result

    def read_link_code(self, code: str) -> str | None:
        normalized = code.strip().upper()
        if not normalized:
            raise StremioAPIError("Stremio link code is required")
        try:
            payload = self._request_json(
                "GET",
                f"{self.link_base_url}/read",
                params={"type": "Read", "code": normalized},
                operation="link authorization",
            )
            result = _result(payload, "link authorization")
        except StremioAPIError as exc:
            if exc.code == 101:
                return None
            raise
        if not isinstance(result, dict) or not result.get("authKey"):
            raise StremioAPIError("Stremio link authorization returned no auth key")
        return str(result["authKey"])

    def get_user(self) -> dict:
        result = self._api_request(
            "getUser",
            {"type": "GetUser", "authKey": self.auth_key},
            "account validation",
        )
        if not isinstance(result, dict) or not result.get("_id"):
            raise StremioAPIError("Stremio account validation returned incomplete data")
        return result

    def datastore_meta(self) -> list[list]:
        result = self._api_request(
            "datastoreMeta",
            {"authKey": self.auth_key, "collection": LIBRARY_COLLECTION},
            "library metadata pull",
        )
        if not isinstance(result, list):
            raise StremioAPIError("Stremio library metadata pull returned an invalid result")
        return result

    def datastore_get(
        self,
        *,
        ids: list[str] | None = None,
        all_items: bool = False,
    ) -> list[dict]:
        result = self._api_request(
            "datastoreGet",
            {
                "authKey": self.auth_key,
                "collection": LIBRARY_COLLECTION,
                "ids": ids or [],
                "all": all_items,
            },
            "library pull",
        )
        if not isinstance(result, list):
            raise StremioAPIError("Stremio library pull returned an invalid result")
        return [item for item in result if isinstance(item, dict)]

    def datastore_put(self, changes: list[dict]) -> None:
        if not changes:
            return
        result = self._api_request(
            "datastorePut",
            {
                "authKey": self.auth_key,
                "collection": LIBRARY_COLLECTION,
                "changes": changes,
            },
            "library push",
        )
        if not isinstance(result, dict) or result.get("success") is not True:
            raise StremioAPIError("Stremio library push was not acknowledged")

    def logout(self) -> None:
        self._api_request(
            "logout",
            {"type": "Logout", "authKey": self.auth_key},
            "logout",
        )

    def get_cinemeta_series(self, imdb_id: str) -> dict:
        payload = self._request_json(
            "GET",
            f"{self.cinemeta_base_url}/meta/series/{imdb_id}.json",
            operation="Cinemeta lookup",
        )
        meta = payload.get("meta") if isinstance(payload, dict) else None
        if not isinstance(meta, dict):
            raise StremioAPIError(f"Cinemeta series {imdb_id} was not found")
        return meta

    def _api_request(self, path: str, body: dict, operation: str):
        return _result(
            self._request_json(
                "POST",
                f"{self.api_base_url}/api/{path}",
                json_body=body,
                operation=operation,
            ),
            operation,
        )

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
        operation: str,
    ):
        if params:
            url = f"{url}?{urlencode(params)}"
        data = None
        headers = {"Accept": "application/json", "User-Agent": "Argus Stremio Sync/1.0"}
        if json_body is not None:
            data = json.dumps(json_body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            response = self._opener(request, timeout=self.timeout)
            status = response.getcode() if hasattr(response, "getcode") else 200
            if status >= 400:
                raise StremioAPIError(f"Stremio {operation} failed ({status})", code=status)
            raw_body = response.read()
        except HTTPError as exc:
            raw_body = exc.read()
            try:
                payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
            except (UnicodeDecodeError, ValueError):
                payload = {}
            if isinstance(payload, dict) and payload.get("error"):
                try:
                    _result(payload, operation)
                except StremioAPIError as api_error:
                    code = api_error.code if api_error.code is not None else exc.code
                    raise StremioAPIError(str(api_error), code=code) from exc
            raise StremioAPIError(
                f"Stremio {operation} failed ({exc.code})",
                code=exc.code,
            ) from exc
        except (URLError, OSError) as exc:
            raise StremioAPIError(f"Stremio {operation} request failed: {exc}") from exc
        try:
            return json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except (UnicodeDecodeError, ValueError) as exc:
            raise StremioAPIError(f"Stremio {operation} returned invalid JSON") from exc


def _result(payload, operation: str):
    if not isinstance(payload, dict):
        raise StremioAPIError(f"Stremio {operation} returned an invalid response")
    error = payload.get("error")
    if error:
        if isinstance(error, dict):
            message = str(error.get("message") or "Unknown API error")
            code = error.get("code")
            try:
                code = int(code) if code is not None else None
            except (TypeError, ValueError):
                code = None
            raise StremioAPIError(f"Stremio {operation} failed: {message}", code=code)
        raise StremioAPIError(f"Stremio {operation} failed: {error}")
    if "result" not in payload:
        raise StremioAPIError(f"Stremio {operation} returned no result")
    return payload["result"]
