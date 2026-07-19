from django.db import migrations, models
from django.db.models import F


def populate_tvdb_ids(apps, schema_editor):
    Show = apps.get_model("tv", "Show")
    Show.objects.filter(provider="tvdb", tvdb_id__isnull=True).update(
        tvdb_id=F("external_id")
    )


def preserve_tvdb_ids(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("tv", "0008_normalize_episode_still_urls"),
    ]

    operations = [
        migrations.AddField(
            model_name="show",
            name="tvdb_id",
            field=models.CharField(blank=True, max_length=32, null=True),
        ),
        migrations.RunPython(populate_tvdb_ids, preserve_tvdb_ids),
    ]
