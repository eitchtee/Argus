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
            name="TraktAccount",
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
                ("trakt_username", models.CharField(blank=True, max_length=255)),
                (
                    "access_token",
                    apps.trakt.fields.EncryptedTextField(default=""),
                ),
                (
                    "refresh_token",
                    apps.trakt.fields.EncryptedTextField(default=""),
                ),
                (
                    "token_expires_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                ("initial_sync_complete", models.BooleanField(default=False)),
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
                (
                    "last_synced_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="trakt_account",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="TraktSyncIntent",
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
                            ("show_dropped", "Dropped show"),
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
                        related_name="trakt_sync_intents",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ("kind", "identity_key"),
            },
        ),
        migrations.AddConstraint(
            model_name="traktsyncintent",
            constraint=models.UniqueConstraint(
                fields=("user", "kind", "identity_key"),
                name="trakt_intent_user_kind_identity_uniq",
            ),
        ),
    ]
