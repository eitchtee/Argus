from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0004_usersettings_datetime_format"),
    ]

    operations = [
        migrations.AddField(
            model_name="usersettings",
            name="show_specials",
            field=models.BooleanField(default=False, verbose_name="Show Specials"),
        ),
    ]
