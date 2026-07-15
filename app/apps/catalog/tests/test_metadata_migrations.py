from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class MetadataLanguageMigrationTests(TransactionTestCase):
    migrate_from = [
        ("users", "0002_alter_user_managers"),
        ("catalog", "0001_initial"),
        ("movies", "0002_movie_cast_director_trailer_url"),
        ("tv", "0006_remove_show_airs_schedule_show_airs_time"),
    ]
    migrate_to = [
        ("users", "0003_usersettings_metadata_languages"),
        ("catalog", "0002_genre_translations"),
        ("movies", "0003_movie_translations"),
        ("tv", "0008_normalize_episode_still_urls"),
    ]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps

        User = old_apps.get_model("users", "User")
        UserSettings = old_apps.get_model("users", "UserSettings")
        Genre = old_apps.get_model("catalog", "Genre")
        Movie = old_apps.get_model("movies", "Movie")
        Show = old_apps.get_model("tv", "Show")
        Season = old_apps.get_model("tv", "Season")
        Episode = old_apps.get_model("tv", "Episode")

        user = User.objects.create(email="migration@example.com")
        UserSettings.objects.create(user_id=user.pk, language="auto")
        self.user_id = user.pk

        self.tmdb_genre_id = Genre.objects.create(
            provider="tmdb",
            external_id="18",
            name="Drama",
        ).pk
        self.tvdb_genre_id = Genre.objects.create(
            provider="tvdb",
            external_id="2",
            name="Fantasy",
        ).pk
        self.movie_id = Movie.objects.create(
            provider="tmdb",
            external_id="550",
            title="Fight Club",
            overview="An insomniac forms an underground club.",
            tagline="Mischief. Mayhem. Soap.",
        ).pk
        show = Show.objects.create(
            provider="tvdb",
            external_id="121361",
            name="Game of Thrones",
            overview="Nine noble families fight for control.",
        )
        self.show_id = show.pk
        season = Season.objects.create(
            show_id=show.pk,
            season_number=1,
            name="Season 1",
            overview="The first season.",
        )
        self.season_id = season.pk
        self.episode_id = Episode.objects.create(
            show_id=show.pk,
            season_id=season.pk,
            season_number=1,
            episode_number=1,
            name="Winter Is Coming",
            overview="The royal family travels north.",
            still_path="/banners/episodes/winter-is-coming.jpg",
        ).pk

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def test_existing_settings_receive_provider_defaults(self):
        settings = self.apps.get_model("users", "UserSettings").objects.get(
            user_id=self.user_id
        )

        self.assertEqual(settings.language, "auto")
        self.assertEqual(settings.tvdb_metadata_language, "eng")
        self.assertEqual(settings.tmdb_metadata_language, "en-US")

    def test_existing_catalog_text_is_seeded_without_changing_scalars(self):
        Genre = self.apps.get_model("catalog", "Genre")
        Movie = self.apps.get_model("movies", "Movie")
        Show = self.apps.get_model("tv", "Show")
        Season = self.apps.get_model("tv", "Season")
        Episode = self.apps.get_model("tv", "Episode")

        tmdb_genre = Genre.objects.get(pk=self.tmdb_genre_id)
        tvdb_genre = Genre.objects.get(pk=self.tvdb_genre_id)
        movie = Movie.objects.get(pk=self.movie_id)
        show = Show.objects.get(pk=self.show_id)
        season = Season.objects.get(pk=self.season_id)
        episode = Episode.objects.get(pk=self.episode_id)

        self.assertEqual(tmdb_genre.translations, {"en-US": {"name": "Drama"}})
        self.assertEqual(tvdb_genre.translations, {"eng": {"name": "Fantasy"}})
        self.assertEqual(
            movie.translations,
            {
                "en-US": {
                    "title": "Fight Club",
                    "overview": "An insomniac forms an underground club.",
                    "tagline": "Mischief. Mayhem. Soap.",
                }
            },
        )
        self.assertEqual(
            show.translations,
            {
                "eng": {
                    "name": "Game of Thrones",
                    "overview": "Nine noble families fight for control.",
                }
            },
        )
        self.assertEqual(
            season.translations,
            {"eng": {"name": "Season 1", "overview": "The first season."}},
        )
        self.assertEqual(
            episode.translations,
            {
                "eng": {
                    "name": "Winter Is Coming",
                    "overview": "The royal family travels north.",
                }
            },
        )
        self.assertEqual(
            episode.still_path,
            "https://artworks.thetvdb.com/banners/episodes/winter-is-coming.jpg",
        )
        self.assertEqual(movie.title, "Fight Club")
        self.assertEqual(show.name, "Game of Thrones")
