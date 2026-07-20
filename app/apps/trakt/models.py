from django.conf import settings
from django.db import models

from apps.trakt.fields import EncryptedTextField


class TraktAccount(models.Model):
    class SyncStatus(models.TextChoices):
        OK = "ok", "OK"
        ERROR = "error", "Error"
        REAUTHORIZE = "reauthorize", "Reauthorize"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="trakt_account",
    )
    trakt_username = models.CharField(max_length=255, blank=True)
    access_token = EncryptedTextField(default="")
    refresh_token = EncryptedTextField(default="")
    token_expires_at = models.DateTimeField(null=True, blank=True)
    initial_sync_complete = models.BooleanField(default=False)
    episode_history_synced_at = models.DateTimeField(null=True, blank=True)
    sync_status = models.CharField(
        max_length=16,
        choices=SyncStatus.choices,
        default=SyncStatus.OK,
    )
    last_error = models.TextField(blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.trakt_username or str(self.user)


class TraktWatchedEpisode(models.Model):
    account = models.ForeignKey(
        TraktAccount,
        on_delete=models.CASCADE,
        related_name="watched_episode_cache",
    )
    identity_key = models.CharField(max_length=512)
    show_data = models.JSONField(default=dict)
    episode_data = models.JSONField(default=dict)
    season_number = models.PositiveIntegerField()
    episode_number = models.PositiveIntegerField()
    watched_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("account", "identity_key"),
                name="trakt_watched_episode_account_identity_uniq",
            )
        ]
        indexes = [
            models.Index(fields=("account", "watched_at")),
        ]

    def __str__(self):
        return f"{self.account} - {self.identity_key}"


class TraktSyncIntent(models.Model):
    class Kind(models.TextChoices):
        MOVIE_WATCHLIST = "movie_watchlist", "Movie watchlist"
        SHOW_WATCHLIST = "show_watchlist", "Show watchlist"
        MOVIE_HISTORY = "movie_history", "Movie history"
        EPISODE_HISTORY = "episode_history", "Episode history"
        SHOW_DROPPED = "show_dropped", "Dropped show"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="trakt_sync_intents",
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
                name="trakt_intent_user_kind_identity_uniq",
            )
        ]

    def __str__(self):
        return f"{self.user} - {self.kind} - {self.identity_key}"
