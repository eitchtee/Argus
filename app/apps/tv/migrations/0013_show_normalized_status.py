from django.db import migrations, models
def _normalized_status(raw_status, has_season):
    value = (raw_status or "").strip().casefold()

    if value in {"ended", "canceled", "cancelled", "completed", "finished"}:
        return "Ended"
    if value in {"continuing", "returning series"}:
        return "Continuing"
    if value in {"upcoming", "planned"}:
        return "Upcoming"
    if value in {"in production", "pilot"}:
        return "Continuing" if has_season else "Upcoming"
    return None


def populate_normalized_status(apps, schema_editor):
    Show = apps.get_model("tv", "Show")
    Season = apps.get_model("tv", "Season")
    shows_with_seasons = set(
        Season.objects.filter(season_number__gt=0)
        .values_list("show_id", flat=True)
    )
    shows = []
    for show in Show.objects.all().only(
        "id",
        "status",
    ):
        normalized_status = _normalized_status(
            show.status,
            show.id in shows_with_seasons,
        )
        if normalized_status is not None:
            show.normalized_status = normalized_status
            shows.append(show)

    if shows:
        Show.objects.bulk_update(shows, ["normalized_status"])


class Migration(migrations.Migration):
    dependencies = [
        ("tv", "0012_show_airs_timezone"),
    ]

    operations = [
        migrations.AddField(
            model_name="show",
            name="normalized_status",
            field=models.CharField(
                blank=True,
                choices=[
                    ("Upcoming", "Upcoming"),
                    ("Continuing", "Continuing"),
                    ("Ended", "Ended"),
                ],
                max_length=10,
                null=True,
            ),
        ),
        migrations.RunPython(populate_normalized_status, migrations.RunPython.noop),
    ]
