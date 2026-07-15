from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.catalog.models import Genre
from apps.movies.models import Movie
from apps.movies.views import _build_movie_context


class MovieMetadataLocalizationTests(TestCase):
    def test_tracked_detail_uses_tmdb_preference_per_field(self):
        user = get_user_model().objects.create_user("user@example.com")
        user.settings.language = "en"
        user.settings.tmdb_metadata_language = "pt-BR"
        user.settings.save()
        genre = Genre.objects.create(
            provider="tmdb",
            external_id="18",
            name="Drama",
            translations={"pt-BR": {"name": "Drama traduzido"}},
        )
        movie = Movie.objects.create(
            external_id="550",
            title="Fight Club",
            overview="English overview",
            tagline="English tagline",
            translations={
                "pt-BR": {"title": "Clube da Luta", "overview": ""},
                "en-US": {"overview": "English fallback"},
            },
        )
        movie.genres.add(genre)

        context = _build_movie_context(user, "550")

        self.assertEqual(context["title"], "Clube da Luta")
        self.assertEqual(context["overview"], "English fallback")
        self.assertEqual(context["tagline"], "English tagline")
        self.assertEqual(context["genres"], ["Drama traduzido"])
