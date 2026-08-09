from django.test import SimpleTestCase

from apps.stremio.codec import decode_watched_bitfield, encode_watched_bitfield


class StremioCodecTests(SimpleTestCase):
    def test_decode_reads_the_stremio_bitfield_against_video_order(self):
        video_ids = ["tt:1:1", "tt:1:2", "tt:1:3"]

        watched = decode_watched_bitfield(
            "tt:1:3:3:eJxjBQAABgAG",
            video_ids,
        )

        self.assertEqual(watched, {"tt:1:1", "tt:1:3"})

    def test_encode_round_trips_watched_video_ids(self):
        video_ids = ["tt:1:1", "tt:1:2", "tt:1:3"]

        serialized = encode_watched_bitfield({"tt:1:1", "tt:1:3"}, video_ids)

        self.assertEqual(
            decode_watched_bitfield(serialized, video_ids),
            {"tt:1:1", "tt:1:3"},
        )

    def test_malformed_or_empty_bitfields_are_ignored(self):
        self.assertEqual(decode_watched_bitfield("not-valid", ["tt:1:1"]), set())
        self.assertEqual(decode_watched_bitfield(None, ["tt:1:1"]), set())
        self.assertIsNone(encode_watched_bitfield({"tt:1:1"}, []))

