import json
from io import BytesIO
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from django.test import SimpleTestCase

from apps.stremio.client import StremioAPIError, StremioClient


class FakeResponse:
    def __init__(self, payload, *, status=200):
        self.status = status
        self.headers = {}
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def getcode(self):
        return self.status


class FakeOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class StremioClientTests(SimpleTestCase):
    def test_link_creation_uses_create_endpoint(self):
        opener = FakeOpener(
            [
                FakeResponse(
                    {
                        "result": {
                            "code": "ABCD",
                            "link": "https://link.stremio.com/authorize/ABCD",
                            "qrcode": "data:image/png;base64,qr",
                        }
                    }
                )
            ]
        )

        result = StremioClient("", opener=opener).create_link_code()

        self.assertEqual(result["code"], "ABCD")
        request = opener.requests[0][0]
        self.assertEqual(
            request.full_url,
            "https://link.stremio.com/api/v2/create?type=Create",
        )

    def test_pending_link_authorization_returns_none(self):
        opener = FakeOpener(
            [FakeResponse({"error": {"code": 101, "message": "Not found"}})]
        )

        result = StremioClient("", opener=opener).read_link_code(" abcd ")

        self.assertIsNone(result)
        query = parse_qs(urlsplit(opener.requests[0][0].full_url).query)
        self.assertEqual(query, {"type": ["Read"], "code": ["ABCD"]})

    def test_datastore_read_and_write_use_library_collection(self):
        opener = FakeOpener(
            [
                FakeResponse({"result": [["movie-id", 123]]}),
                FakeResponse({"result": [{"_id": "movie-id"}]}),
                FakeResponse({"result": {"success": True}}),
            ]
        )
        client = StremioClient("auth-key", opener=opener)

        self.assertEqual(client.datastore_meta(), [["movie-id", 123]])
        self.assertEqual(client.datastore_get(ids=["movie-id"]), [{"_id": "movie-id"}])
        client.datastore_put([{"_id": "movie-id", "type": "movie"}])

        meta_request = opener.requests[0][0]
        self.assertEqual(meta_request.full_url, "https://api.strem.io/api/datastoreMeta")
        self.assertEqual(
            json.loads(meta_request.data),
            {"authKey": "auth-key", "collection": "libraryItem"},
        )
        get_request = opener.requests[1][0]
        self.assertEqual(
            json.loads(get_request.data),
            {
                "authKey": "auth-key",
                "collection": "libraryItem",
                "ids": ["movie-id"],
                "all": False,
            },
        )
        put_request = opener.requests[2][0]
        self.assertEqual(
            json.loads(put_request.data),
            {
                "authKey": "auth-key",
                "collection": "libraryItem",
                "changes": [{"_id": "movie-id", "type": "movie"}],
            },
        )

    def test_datastore_meta_rejects_non_list_results(self):
        opener = FakeOpener([FakeResponse({"result": {"unexpected": True}})])

        with self.assertRaisesRegex(StremioAPIError, "library metadata pull"):
            StremioClient("auth-key", opener=opener).datastore_meta()

    def test_datastore_get_rejects_non_list_results(self):
        opener = FakeOpener([FakeResponse({"result": {"unexpected": True}})])

        with self.assertRaisesRegex(StremioAPIError, "library pull"):
            StremioClient("auth-key", opener=opener).datastore_get(all_items=True)

    def test_error_payload_raises_stremio_api_error(self):
        opener = FakeOpener(
            [FakeResponse({"error": {"code": 400, "message": "Bad auth"}})]
        )

        with self.assertRaisesRegex(StremioAPIError, "Bad auth"):
            StremioClient("auth-key", opener=opener).get_user()

    def test_http_auth_status_is_preserved_when_error_payload_has_no_code(self):
        error = HTTPError(
            "https://api.strem.io/api/getUser",
            401,
            "Unauthorized",
            {},
            BytesIO(json.dumps({"error": {"message": "Bad auth"}}).encode()),
        )
        opener = FakeOpener([error])

        with self.assertRaises(StremioAPIError) as raised:
            StremioClient("auth-key", opener=opener).get_user()

        self.assertEqual(raised.exception.code, 401)
