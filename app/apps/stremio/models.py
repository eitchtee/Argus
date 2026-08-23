from django.conf import settings
from django.db import models

from apps.trakt.fields import EncryptedTextField


class StremioAccount(models.Model):
    class SyncStatus(models.TextChoices):
        OK = "ok", "OK"
        ERROR = "error", "Error"
        REAUTHORIZE = "reauthorize", "Reauthorize"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="stremio_account",
    )
    stremio_user_id = models.CharField(max_length=255, blank=True)
    stremio_username = models.CharField(max_length=255, blank=True)
    auth_key = EncryptedTextField(default="")
    initial_sync_complete = models.BooleanField(default=False)
    library_synced_at = models.DateTimeField(null=True, blank=True)
    sync_status = models.CharField(
        max_length=16,
        choices=SyncStatus.choices,
        default=SyncStatus.OK,
    )
    last_error = models.TextField(blank=True)
    last_warning = models.TextField(blank=True)
    deferred_content_ids = models.JSONField(default=list, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.stremio_username or self.stremio_user_id or str(self.user)


class StremioSyncIntent(models.Model):
    class Kind(models.TextChoices):
        MOVIE_WATCHLIST = "movie_watchlist", "Movie watchlist"
        SHOW_WATCHLIST = "show_watchlist", "Show watchlist"
        MOVIE_HISTORY = "movie_history", "Movie history"
        EPISODE_HISTORY = "episode_history", "Episode history"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="stremio_sync_intents",
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    identity_key = models.CharField(max_length=512)
    payload = models.JSONField(default=dict)
    desired = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("kind", "identity_key")
        constraints = [
            models.UniqueConstraint(
                fields=("user", "kind", "identity_key"),
                name="stremio_intent_user_kind_identity_uniq",
            )
        ]

    def __str__(self):
        return f"{self.user} - {self.kind} - {self.identity_key}"

