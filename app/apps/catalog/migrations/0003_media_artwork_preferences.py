from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0002_genre_translations"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MediaArtwork",
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
                ("provider", models.CharField(max_length=16)),
                (
                    "media_type",
                    models.CharField(
                        choices=[("movie", "Movie"), ("tv", "TV")],
                        max_length=8,
                    ),
                ),
                ("external_id", models.CharField(max_length=32)),
                (
                    "kind",
                    models.CharField(
                        choices=[("poster", "Poster"), ("background", "Background")],
                        max_length=16,
                    ),
                ),
                ("image_url", models.CharField(max_length=500)),
                ("language", models.CharField(blank=True, max_length=16, null=True)),
                ("width", models.PositiveIntegerField(blank=True, null=True)),
                ("height", models.PositiveIntegerField(blank=True, null=True)),
                ("score", models.FloatField(blank=True, null=True)),
                ("remote_id", models.CharField(blank=True, max_length=64, null=True)),
                ("is_default", models.BooleanField(default=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ("-is_default", "-score", "id"),
                "indexes": [
                    models.Index(
                        fields=["provider", "media_type", "external_id", "kind"],
                        name="catart_lookup_idx",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=[
                            "provider",
                            "media_type",
                            "external_id",
                            "kind",
                            "image_url",
                        ],
                        name="catalog_media_artwork_identity_uniq",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="UserMediaArtworkPreference",
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
                ("provider", models.CharField(max_length=16)),
                (
                    "media_type",
                    models.CharField(
                        choices=[("movie", "Movie"), ("tv", "TV")],
                        max_length=8,
                    ),
                ),
                ("external_id", models.CharField(max_length=32)),
                ("language", models.CharField(blank=True, max_length=16, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "background_artwork",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="background_preferences",
                        to="catalog.mediaartwork",
                    ),
                ),
                (
                    "poster_artwork",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="poster_preferences",
                        to="catalog.mediaartwork",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="media_artwork_preferences",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["user", "media_type", "provider", "external_id"],
                        name="catuserart_lookup_idx",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=["user", "provider", "media_type", "external_id"],
                        name="catalog_user_media_artwork_pref_uniq",
                    )
                ],
            },
        ),
    ]
