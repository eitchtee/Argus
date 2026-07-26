from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0003_usersettings_metadata_languages"),
    ]

    operations = [
        migrations.AddField(
            model_name="usersettings",
            name="datetime_format",
            field=models.CharField(
                default="SHORT_DATETIME_FORMAT",
                max_length=100,
                verbose_name="Datetime Format",
            ),
        ),
    ]
