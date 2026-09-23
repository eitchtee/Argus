from django.db import migrations


# Trakt synchronization was removed; this clears what the old ``trakt`` app
# left behind (including the encrypted OAuth tokens) on existing installs.
DROP_TRAKT_SYNC_SQL = """
DROP TABLE IF EXISTS trakt_traktsyncintent;
DROP TABLE IF EXISTS trakt_traktwatchedepisode;
DROP TABLE IF EXISTS trakt_traktaccount;
DELETE FROM django_migrations WHERE app = 'trakt';
DO $$
BEGIN
    IF to_regclass('procrastinate_periodic_defers') IS NOT NULL THEN
        DELETE FROM procrastinate_periodic_defers
        WHERE task_name = 'periodic_trakt_sync';
    END IF;
    IF to_regclass('procrastinate_jobs') IS NOT NULL THEN
        DELETE FROM procrastinate_jobs
        WHERE task_name IN ('sync_trakt_account', 'periodic_trakt_sync')
          AND status <> 'doing';
    END IF;
END
$$;
"""


def remove_trakt_content_types(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    ContentType.objects.filter(app_label="trakt").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("admin", "0003_logentry_add_action_flag_choices"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunSQL(DROP_TRAKT_SYNC_SQL, migrations.RunSQL.noop),
        migrations.RunPython(remove_trakt_content_types, migrations.RunPython.noop),
    ]
