from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0003_media_artwork_preferences"),
    ]

    operations = [
        migrations.AddField(
            model_name="usermediaartworkpreference",
            name="use_original_title",
            field=models.BooleanField(default=False, verbose_name="Use original title"),
        ),
    ]
