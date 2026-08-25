from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _


class SyncStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    OK = "ok", "OK"
    ERROR = "error", "Error"


class ProviderBackedModel(models.Model):
    provider = models.CharField(max_length=16)
    external_id = models.CharField(max_length=32)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    sync_status = models.CharField(
        max_length=16,
        choices=SyncStatus.choices,
        default=SyncStatus.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "external_id"],
                name="%(app_label)s_%(class)s_provider_external_id_uniq",
            )
        ]


class Genre(models.Model):
    provider = models.CharField(max_length=16)
    external_id = models.CharField(max_length=32)
    name = models.CharField(max_length=120)
    translations = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "external_id"],
                name="catalog_genre_provider_external_id_uniq",
            )
        ]
        ordering = ("name",)

    def __str__(self):
        return self.name


class MediaArtwork(models.Model):
    class MediaType(models.TextChoices):
        MOVIE = "movie", "Movie"
        TV = "tv", "TV"

    class Kind(models.TextChoices):
        POSTER = "poster", "Poster"
        BACKGROUND = "background", "Background"

    provider = models.CharField(max_length=16)
    media_type = models.CharField(max_length=8, choices=MediaType.choices)
    external_id = models.CharField(max_length=32)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    image_url = models.CharField(max_length=500)
    language = models.CharField(max_length=16, null=True, blank=True)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    score = models.FloatField(null=True, blank=True)
    remote_id = models.CharField(max_length=64, null=True, blank=True)
    is_default = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
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
        ]
        indexes = [
            models.Index(
                fields=["provider", "media_type", "external_id", "kind"],
                name="catart_lookup_idx",
            ),
        ]
        ordering = ("-is_default", "-score", "id")

    def __str__(self):
        return f"{self.provider}:{self.media_type}:{self.external_id} {self.kind}"


class MediaRating(models.Model):
    class MediaType(models.TextChoices):
        MOVIE = "movie", "Movie"
        SHOW = "show", "TV Show"
        EPISODE = "episode", "Episode"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="media_ratings",
    )
    media_type = models.CharField(max_length=8, choices=MediaType.choices)
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        related_name="media_ratings",
    )
    object_id = models.PositiveBigIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")
    score = models.DecimalField(max_digits=3, decimal_places=1)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "content_type", "object_id"],
                name="catalog_mediarating_user_content_uniq",
            )
        ]
        indexes = [
            models.Index(
                fields=["content_type", "object_id"],
                name="catrating_content_idx",
            ),
        ]
        ordering = ("-updated_at",)

    def __str__(self):
        return f"{self.user} - {self.media_type}:{self.object_id} {self.score}"


class UserMediaArtworkPreference(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="media_artwork_preferences",
    )
    provider = models.CharField(max_length=16)
    media_type = models.CharField(max_length=8, choices=MediaArtwork.MediaType.choices)
    external_id = models.CharField(max_length=32)
    language = models.CharField(max_length=16, null=True, blank=True)
    use_original_title = models.BooleanField(
        default=False,
        verbose_name=_("Use original title"),
    )
    poster_artwork = models.ForeignKey(
        MediaArtwork,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="poster_preferences",
    )
    background_artwork = models.ForeignKey(
        MediaArtwork,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="background_preferences",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "provider", "media_type", "external_id"],
                name="catalog_user_media_artwork_pref_uniq",
            )
        ]
        indexes = [
            models.Index(
                fields=["user", "media_type", "provider", "external_id"],
                name="catuserart_lookup_idx",
            ),
        ]

    def __str__(self):
        return f"{self.user} - {self.provider}:{self.media_type}:{self.external_id}"
