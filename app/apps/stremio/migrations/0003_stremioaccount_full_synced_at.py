from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stremio', '0002_stremioaccount_deferred_content_ids_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='stremioaccount',
            name='full_synced_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
