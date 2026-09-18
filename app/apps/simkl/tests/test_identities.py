from django.test import SimpleTestCase

from apps.simkl.identities import (
    episode_key,
    history_item,
    identity_key_for_payload,
    media_identity_key,
    media_tokens,
    normalize_ids,
    parse_episode_key,
    simkl_ids,
)


class IdentityTests(SimpleTestCase):
    def test_normalize_ids_folds_simkl_id_and_drops_blanks(self):
        ids = normalize_ids({"simkl_id": 5, "tmdb": 1399, "imdb": "", "slug": "x"})

        self.assertEqual(ids, {"simkl": "5", "tmdb": "1399", "slug": "x"})

    def test_simkl_ids_keeps_only_writable_keys(self):
        ids = simkl_ids({"trakt": 10, "simkl": "5", "tmdb": "1399", "imdb": "tt1", "tvdb": 7})

        self.assertEqual(ids, {"simkl": 5, "imdb": "tt1", "tmdb": "1399", "tvdb": "7"})

    def test_identity_key_prefers_simkl_then_imdb(self):
        self.assertEqual(media_identity_key({"tmdb": "1", "simkl": 2}), "simkl:2")
        self.assertEqual(media_identity_key({"tmdb": "1", "imdb": "tt1"}), "imdb:tt1")
        self.assertEqual(media_identity_key({}, title="Dune", year=2021), "title:dune:2021")

    def test_media_tokens_ignore_trakt(self):
        self.assertEqual(
            media_tokens({"trakt": 1, "imdb": "tt1", "tmdb": 2}),
            {"imdb:tt1", "tmdb:2"},
        )

    def test_episode_identity_key_uses_show_ids_and_position(self):
        payload = {
            "show": {"title": "Lost", "ids": {"trakt": 1, "tvdb": "73739"}},
            "seasons": [{"number": 2, "episodes": [{"number": 5}]}],
        }

        self.assertEqual(
            identity_key_for_payload("episode_history", payload),
            "episode:tvdb:73739:s2:e5",
        )

    def test_history_item_converts_trakt_episode_payload(self):
        payload = {
            "show": {"title": "Lost", "ids": {"trakt": 1, "tvdb": "73739"}},
            "seasons": [
                {
                    "number": 2,
                    "episodes": [{"number": 5, "watched_at": "2026-01-01T00:00:00+00:00"}],
                }
            ],
        }

        item = history_item(payload, "episode")

        self.assertEqual(item["ids"], {"tvdb": "73739"})
        self.assertEqual(item["title"], "Lost")
        self.assertTrue(item["use_tvdb_anime_seasons"])
        self.assertEqual(
            item["seasons"],
            [{"number": 2, "episodes": [{"number": 5, "watched_at": "2026-01-01T00:00:00+00:00"}]}],
        )

    def test_history_item_converts_movie_payload(self):
        item = history_item(
            {"title": "Dune", "year": 2021, "ids": {"trakt": 1, "tmdb": "438631"}, "watched_at": "x"},
            "movie",
        )

        self.assertEqual(item, {"ids": {"tmdb": "438631"}, "title": "Dune", "year": 2021, "watched_at": "x"})

    def test_episode_key_round_trips(self):
        self.assertEqual(parse_episode_key(episode_key(3, 12)), (3, 12))
