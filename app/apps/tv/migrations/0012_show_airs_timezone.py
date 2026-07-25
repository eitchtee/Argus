from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tv", "0011_normalize_default_titles"),
    ]

    operations = [
        migrations.AddField(
            model_name="show",
            name="airs_timezone",
            field=models.CharField(
                blank=True,
                default="UTC",
                max_length=64,
                null=True,
            ),
        ),
    ]
