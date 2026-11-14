from django.db import migrations

from apps.catalog.aggregate import aggregate


def backfill(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    MdblistMediaInfo = apps.get_model("mdblist", "MdblistMediaInfo")
    SimklMediaInfo = apps.get_model("simkl", "SimklMediaInfo")

    for app_label, model_name in (("movies", "Movie"), ("tv", "Show")):
        content_type = ContentType.objects.filter(app_label=app_label, model=model_name.lower()).first()
        if content_type is None:
            continue
        ratings = {}
        # Same precedence as catalog.aggregate.collect_ratings: own TMDB vote, then SIMKL, then MDBList.
        if model_name == "Movie":
            for pk, average, count in (
                apps.get_model(app_label, model_name)
                .objects.filter(provider="tmdb", vote_average__gt=0)
                .values_list("pk", "vote_average", "vote_count")
            ):
                ratings.setdefault(pk, {})["tmdb"] = (average * 10, count)
        for info in SimklMediaInfo.objects.filter(content_type=content_type, status="ok"):
            found = ratings.setdefault(info.object_id, {})
            if info.simkl_rating:
                found["simkl"] = (info.simkl_rating, info.simkl_votes)
            if info.imdb_rating:
                found["imdb"] = (info.imdb_rating, info.imdb_votes)
        for info in MdblistMediaInfo.objects.filter(content_type=content_type, status="ok"):
            found = ratings.setdefault(info.object_id, {})
            for rating in info.ratings or []:
                if rating.get("value") is not None:
                    found[rating["source"]] = (rating["value"], rating.get("votes"))

        Model = apps.get_model(app_label, model_name)
        for pk, found in ratings.items():
            Model.objects.filter(pk=pk).update(aggregate_score=aggregate(found))


class Migration(migrations.Migration):
    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("mdblist", "0001_initial"),
        ("simkl", "0002_simklaccount_last_seen_at"),
        ("movies", "0011_aggregate_score"),
        ("tv", "0017_aggregate_score"),
    ]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
