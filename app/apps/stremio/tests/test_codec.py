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


class WatchedBitfieldRealignmentTests(SimpleTestCase):
    def test_state_survives_videos_added_ahead_of_the_anchor(self):
        # Stremio wrote the state against the list as it stood then.
        original = [f"tt1:1:{number}" for number in range(1, 11)]
        serialized = encode_watched_bitfield({"tt1:1:3", "tt1:1:7"}, original)

        # The metadata provider later gained two specials, which sort ahead of
        # season 1 and push the anchor past anchor_length.
        current = ["tt1:0:1", "tt1:0:2", *original]
        anchor, anchor_length, _packed = serialized.rsplit(":", 2)
        self.assertGreaterEqual(current.index(anchor), int(anchor_length))

        self.assertEqual(
            decode_watched_bitfield(serialized, current),
            {"tt1:1:3", "tt1:1:7"},
        )

    def test_state_survives_videos_appended_after_the_anchor(self):
        original = [f"tt1:1:{number}" for number in range(1, 11)]
        serialized = encode_watched_bitfield({"tt1:1:2"}, original)
        current = [*original, "tt1:1:11", "tt1:1:12"]

        self.assertEqual(decode_watched_bitfield(serialized, current), {"tt1:1:2"})
