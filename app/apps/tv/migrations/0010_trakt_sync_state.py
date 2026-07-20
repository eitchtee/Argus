from django.db import migrations, models


def populate_watchlist_state(apps, schema_editor):
    UserShow = apps.get_model("tv", "UserShow")
    UserShow.objects.filter(status="tracked").update(on_watchlist=True)


def preserve_watchlist_state(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("tv", "0009_show_tvdb_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="show",
            name="trakt_id",
            field=models.CharField(blank=True, max_length=32, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="episode",
            name="trakt_id",
            field=models.CharField(blank=True, max_length=32, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="usershow",
            name="on_watchlist",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(populate_watchlist_state, preserve_watchlist_state),
    ]
