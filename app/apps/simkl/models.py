from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from apps.trakt.fields import EncryptedTextField


SIMKL_WEB_URL = "https://simkl.com"


class SimklAccount(models.Model):
    class SyncStatus(models.TextChoices):
        OK = "ok", "OK"
        ERROR = "error", "Error"
        REAUTHORIZE = "reauthorize", "Reauthorize"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="simkl_account",
    )
    simkl_username = models.CharField(max_length=255, blank=True)
    simkl_user_id = models.CharField(max_length=32, blank=True)
    account_type = models.CharField(max_length=16, blank=True)
    access_token = EncryptedTextField(default="")
    initial_sync_complete = models.BooleanField(default=False)
    # ``/sync/activities`` ``all`` timestamp, stored verbatim: SIMKL wants it
    # echoed back unchanged as ``date_from`` on the next delta pull.
    activities_cursor = models.CharField(max_length=40, blank=True)
    last_activities = models.JSONField(default=dict, blank=True)
    # Writes SIMKL has not echoed back yet (or could not match), keyed by
    # identity. State-based pushes skip them so a title SIMKL ignores does
    # not cost one write per run; a manual sync clears the map.
    pending_pushes = models.JSONField(default=dict, blank=True)
    sync_status = models.CharField(
        max_length=16,
        choices=SyncStatus.choices,
        default=SyncStatus.OK,
    )
    last_error = models.TextField(blank=True)
    last_warning = models.TextField(blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    # Last time the user used Argus. SIMKL forbids polling without user
    # interaction, so idle accounts are skipped by the periodic sync.
    last_seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.simkl_username or str(self.user)


class SimklLibraryItem(models.Model):
    """Local mirror of one entry in the SIMKL library of a user.

    SIMKL only ever sends deltas after the first pull, so this cache is what
    lets a sync tell "these episodes were unmarked on SIMKL" or "this title
    was removed from the library" apart from "nothing changed".
    """

    class MediaType(models.TextChoices):
        MOVIE = "movie", "Movie"
        SHOW = "show", "Show"
        ANIME = "anime", "Anime"

    account = models.ForeignKey(
        SimklAccount,
        on_delete=models.CASCADE,
        related_name="library_items",
    )
    media_type = models.CharField(max_length=8, choices=MediaType.choices)
    simkl_id = models.PositiveBigIntegerField()
    ids = models.JSONField(default=dict, blank=True)
    title = models.CharField(max_length=255, blank=True)
    year = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=16, blank=True)
    last_watched_at = models.DateTimeField(null=True, blank=True)
    user_rating = models.PositiveSmallIntegerField(null=True, blank=True)
    # ``{"<season>:<episode>": "<iso watched_at>"}`` in TVDB coordinates.
    watched_episodes = models.JSONField(default=dict, blank=True)
    watched_episodes_count = models.PositiveIntegerField(default=0)
    total_episodes_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("account", "media_type", "simkl_id"),
                name="simkl_library_item_account_media_uniq",
            )
        ]

    def __str__(self):
        return f"{self.account} - {self.media_type}:{self.simkl_id} {self.title}"


class SimklSyncIntent(models.Model):
    class Kind(models.TextChoices):
        MOVIE_WATCHLIST = "movie_watchlist", "Movie watchlist"
        SHOW_WATCHLIST = "show_watchlist", "Show watchlist"
        MOVIE_HISTORY = "movie_history", "Movie history"
        EPISODE_HISTORY = "episode_history", "Episode history"
        SHOW_DROPPED = "show_dropped", "Dropped show"
        SHOW_PAUSED = "show_paused", "Paused show"
        MOVIE_RATING = "movie_rating", "Movie rating"
        SHOW_RATING = "show_rating", "Show rating"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="simkl_sync_intents",
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
                name="simkl_intent_user_kind_identity_uniq",
            )
        ]

    def __str__(self):
        return f"{self.user} - {self.kind} - {self.identity_key}"


class SimklMediaInfo(models.Model):
    """Community data SIMKL knows about a movie or show that TMDB/TVDB lack.

    Shared by every user, like the rest of the catalog metadata.
    """

    class Status(models.TextChoices):
        OK = "ok", "OK"
        NOT_FOUND = "not_found", "Not found"
        ERROR = "error", "Error"

    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        related_name="simkl_media_info",
    )
    object_id = models.PositiveBigIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")
    media_type = models.CharField(max_length=8, blank=True)
    simkl_id = models.PositiveBigIntegerField(null=True, blank=True)
    slug = models.CharField(max_length=255, blank=True)
    simkl_rating = models.FloatField(null=True, blank=True)
    simkl_votes = models.PositiveIntegerField(null=True, blank=True)
    imdb_rating = models.FloatField(null=True, blank=True)
    imdb_votes = models.PositiveIntegerField(null=True, blank=True)
    rank = models.PositiveIntegerField(null=True, blank=True)
    drop_rate = models.CharField(max_length=16, blank=True)
    certification = models.CharField(max_length=32, blank=True)
    trailer_url = models.CharField(max_length=255, blank=True)
    recommendations = models.JSONField(default=list, blank=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.OK,
    )
    last_error = models.TextField(blank=True)
    fetched_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("content_type", "object_id"),
                name="simkl_media_info_content_uniq",
            )
        ]

    def __str__(self):
        return f"{self.media_type}:{self.simkl_id} ({self.content_type_id}:{self.object_id})"

    @property
    def simkl_url(self) -> str | None:
        return build_simkl_url(self.media_type, self.simkl_id, self.slug)


def build_simkl_url(media_type: str | None, simkl_id, slug: str | None = "") -> str | None:
    """Per-item SIMKL page, the deep link the SIMKL API rules ask apps to show."""
    if not simkl_id:
        return None
    section = {"movie": "movies", "anime": "anime"}.get(
        str(media_type or "").lower(), "tv"
    )
    return f"{SIMKL_WEB_URL}/{section}/{simkl_id}/{slug or ''}"
