from urllib.parse import urljoin

from django.conf import settings
from django.db import migrations


def seed_legacy_movie_artwork(apps, schema_editor):
    Movie = apps.get_model("movies", "Movie")
    MediaArtwork = apps.get_model("catalog", "MediaArtwork")
    existing = set(
        MediaArtwork.objects.filter(media_type="movie").values_list(
            "provider",
            "media_type",
            "external_id",
            "kind",
        )
    )
    rows = []
    for movie in Movie.objects.filter(provider__in={"tmdb", "tvdb"}).only(
        "provider",
        "external_id",
        "poster_path",
        "backdrop_path",
    ):
        for kind, path, size in (
            ("poster", movie.poster_path, "w342"),
            ("background", movie.backdrop_path, "w1280"),
        ):
            image_url = _legacy_image_url(movie.provider, path, size)
            identity = (movie.provider, "movie", movie.external_id, kind)
            if not image_url or identity in existing:
                continue
            rows.append(
                MediaArtwork(
                    provider=movie.provider,
                    media_type="movie",
                    external_id=movie.external_id,
                    kind=kind,
                    image_url=image_url,
                    is_default=True,
                )
            )
            existing.add(identity)
    MediaArtwork.objects.bulk_create(rows, ignore_conflicts=True)


def _legacy_image_url(provider, path, size):
    if not path:
        return None
    if path.startswith(("http://", "https://")):
        return path
    if provider == "tmdb":
        base_url = settings.TMDB_IMAGE_BASE_URL
        return f"{base_url.rstrip('/')}/{size}/{path.lstrip('/')}"
    return urljoin("https://artworks.thetvdb.com/", path.lstrip("/"))


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0003_media_artwork_preferences"),
        ("movies", "0007_movie_normalized_status"),
    ]

    operations = [
        migrations.RunPython(seed_legacy_movie_artwork, migrations.RunPython.noop),
    ]
