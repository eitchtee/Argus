from urllib.parse import urljoin

from django.conf import settings
from django.db import migrations


def seed_legacy_show_artwork(apps, schema_editor):
    Show = apps.get_model("tv", "Show")
    MediaArtwork = apps.get_model("catalog", "MediaArtwork")
    existing = set(
        MediaArtwork.objects.filter(media_type="tv").values_list(
            "provider",
            "media_type",
            "external_id",
            "kind",
        )
    )
    rows = []
    for show in Show.objects.filter(provider__in={"tmdb", "tvdb"}).only(
        "provider",
        "external_id",
        "poster_path",
        "backdrop_path",
    ):
        for kind, path in (
            ("poster", show.poster_path),
            ("background", show.backdrop_path),
        ):
            image_url = _legacy_image_url(
                show.provider,
                path,
                "w342" if kind == "poster" else "w1280",
            )
            identity = (show.provider, "tv", show.external_id, kind)
            if not image_url or identity in existing:
                continue
            rows.append(
                MediaArtwork(
                    provider=show.provider,
                    media_type="tv",
                    external_id=show.external_id,
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
        ("tv", "0013_show_normalized_status"),
    ]

    operations = [
        migrations.RunPython(seed_legacy_show_artwork, migrations.RunPython.noop),
    ]
