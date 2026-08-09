import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import apps.trakt.fields


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="StremioAccount",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("stremio_user_id", models.CharField(blank=True, max_length=255)),
                ("stremio_username", models.CharField(blank=True, max_length=255)),
                ("auth_key", apps.trakt.fields.EncryptedTextField(default="")),
                ("initial_sync_complete", models.BooleanField(default=False)),
                ("library_synced_at", models.DateTimeField(blank=True, null=True)),
                (
                    "sync_status",
                    models.CharField(
                        choices=[
                            ("ok", "OK"),
                            ("error", "Error"),
                            ("reauthorize", "Reauthorize"),
                        ],
                        default="ok",
                        max_length=16,
                    ),
                ),
                ("last_error", models.TextField(blank=True)),
                ("last_synced_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="stremio_account",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="StremioSyncIntent",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("movie_watchlist", "Movie watchlist"),
                            ("show_watchlist", "Show watchlist"),
                            ("movie_history", "Movie history"),
                            ("episode_history", "Episode history"),
                        ],
                        max_length=32,
                    ),
                ),
                ("identity_key", models.CharField(max_length=512)),
                ("payload", models.JSONField(default=dict)),
                ("desired", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="stremio_sync_intents",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ("kind", "identity_key")},
        ),
        migrations.AddConstraint(
            model_name="stremiosyncintent",
            constraint=models.UniqueConstraint(
                fields=("user", "kind", "identity_key"),
                name="stremio_intent_user_kind_identity_uniq",
            ),
        ),
    ]
