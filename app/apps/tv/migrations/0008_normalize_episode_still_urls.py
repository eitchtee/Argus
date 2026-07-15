from urllib.parse import urljoin

from django.db import migrations


TVDB_ARTWORK_BASE_URL = "https://artworks.thetvdb.com/"


def normalize_episode_still_urls(apps, schema_editor):
    Episode = apps.get_model("tv", "Episode")

    for episode in Episode.objects.exclude(still_path__isnull=True).exclude(still_path="").iterator():
        if episode.still_path.startswith(("http://", "https://")):
            continue
        episode.still_path = urljoin(TVDB_ARTWORK_BASE_URL, episode.still_path)
        episode.save(update_fields=["still_path"])


class Migration(migrations.Migration):
    dependencies = [
        ("tv", "0007_metadata_translations"),
    ]

    operations = [
        migrations.RunPython(normalize_episode_still_urls, migrations.RunPython.noop),
    ]
