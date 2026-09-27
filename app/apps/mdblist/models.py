from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from apps.sync.fields import EncryptedTextField


MDBLIST_WEB_URL = "https://mdblist.com"


class MdblistAccount(models.Model):
    class AuthMethod(models.TextChoices):
        OAUTH = "oauth", "OAuth"
        API_KEY = "apikey", "API key"

    class SyncStatus(models.TextChoices):
        OK = "ok", "OK"
        ERROR = "error", "Error"
        REAUTHORIZE = "reauthorize", "Reauthorize"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mdblist_account",
    )
    mdblist_username = models.CharField(max_length=255, blank=True)
    mdblist_user_id = models.CharField(max_length=32, blank=True)
    plan = models.CharField(max_length=32, blank=True)
    is_supporter = models.BooleanField(default=False)
    auth_method = models.CharField(
        max_length=8,
        choices=AuthMethod.choices,
        default=AuthMethod.OAUTH,
    )
    # The OAuth bearer token, or the personal API key for ``apikey`` accounts.
    access_token = EncryptedTextField(default="")
    refresh_token = EncryptedTextField(default="", blank=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    initial_sync_complete = models.BooleanField(default=False)
    # ``server_time`` of the last ``/sync/last_activities`` read, the ``since``
    # of the next journal replay. MDBList asks clients to echo its own clock
    # rather than theirs.
    activities_cursor = models.CharField(max_length=40, blank=True)
    last_activities = models.JSONField(default=dict, blank=True)
    # Writes MDBList has not echoed back yet (or could not match), keyed by
    # identity. State-based pushes skip them so an unmatched title does not
    # cost one write per run; a manual sync clears the map.
    pending_pushes = models.JSONField(default=dict, blank=True)
    sync_status = models.CharField(
        max_length=16,
        choices=SyncStatus.choices,
        default=SyncStatus.OK,
    )
    last_error = models.TextField(blank=True)
    last_warning = models.TextField(blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    # Last time the user used Argus. Idle accounts are skipped by the periodic
    # sync so they do not burn the daily request quota.
    last_seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.mdblist_username or str(self.user)


class MdblistLibraryItem(models.Model):
    """Local mirror of one movie or show in the MDBList library of a user.

    After the first pull MDBList only reports what changed (the sync journal
    for watched and rated state, activity stamps for the watchlist and
    dropped shows), so this cache is what the next change is compared with.
    Every MDBList sync row carries a TMDB id, which is why it is the key.
    """

    class MediaType(models.TextChoices):
        MOVIE = "movie", "Movie"
        SHOW = "show", "Show"

    account = models.ForeignKey(
        MdblistAccount,
        on_delete=models.CASCADE,
        related_name="library_items",
    )
    media_type = models.CharField(max_length=8, choices=MediaType.choices)
    tmdb_id = models.PositiveBigIntegerField()
    ids = models.JSONField(default=dict, blank=True)
    title = models.CharField(max_length=255, blank=True)
    watched_at = models.DateTimeField(null=True, blank=True)
    on_watchlist = models.BooleanField(default=False)
    dropped = models.BooleanField(default=False)
    user_rating = models.PositiveSmallIntegerField(null=True, blank=True)
    # ``{"<season>:<episode>": "<iso watched_at>"}`` in TMDB coordinates.
    watched_episodes = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("account", "media_type", "tmdb_id"),
                name="mdblist_library_item_account_media_uniq",
            )
        ]

    def __str__(self):
        return f"{self.account} - {self.media_type}:{self.tmdb_id} {self.title}"

    @property
    def is_empty(self) -> bool:
        return not (
            self.watched_at
            or self.on_watchlist
            or self.dropped
            or self.user_rating
            or self.watched_episodes
        )


class MdblistSyncIntent(models.Model):
    class Kind(models.TextChoices):
        MOVIE_WATCHLIST = "movie_watchlist", "Movie watchlist"
        SHOW_WATCHLIST = "show_watchlist", "Show watchlist"
        MOVIE_HISTORY = "movie_history", "Movie history"
        EPISODE_HISTORY = "episode_history", "Episode history"
        SHOW_DROPPED = "show_dropped", "Dropped show"
        MOVIE_RATING = "movie_rating", "Movie rating"
        SHOW_RATING = "show_rating", "Show rating"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mdblist_sync_intents",
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
                name="mdblist_intent_user_kind_identity_uniq",
            )
        ]

    def __str__(self):
        return f"{self.user} - {self.kind} - {self.identity_key}"


class MdblistMediaInfo(models.Model):
    """Ratings MDBList aggregates for a movie or show (IMDb, Rotten Tomatoes,
    Metacritic, Letterboxd...), plus where to stream it.

    Shared by every user, like the rest of the catalog metadata.
    """

    class Status(models.TextChoices):
        OK = "ok", "OK"
        NOT_FOUND = "not_found", "Not found"
        ERROR = "error", "Error"

    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        related_name="mdblist_media_info",
    )
    object_id = models.PositiveBigIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")
    media_type = models.CharField(max_length=8, blank=True)
    mdblist_id = models.CharField(max_length=32, blank=True)
    slug = models.CharField(max_length=255, blank=True)
    imdb_id = models.CharField(max_length=32, blank=True)
    score = models.PositiveSmallIntegerField(null=True, blank=True)
    # ``[{"source", "value", "score", "votes", "url"}]`` as MDBList sends it,
    # minus sources without a value.
    ratings = models.JSONField(default=list, blank=True)
    certification = models.CharField(max_length=32, blank=True)
    trailer_url = models.CharField(max_length=255, blank=True)
    streams = models.JSONField(default=list, blank=True)
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
                name="mdblist_media_info_content_uniq",
            )
        ]

    def __str__(self):
        return f"{self.media_type}:{self.mdblist_id} ({self.content_type_id}:{self.object_id})"

    @property
    def mdblist_url(self) -> str | None:
        return build_mdblist_url(self.media_type, self.mdblist_id, self.slug)


def build_mdblist_url(media_type: str | None, mdblist_id: str | None, slug: str | None = "") -> str | None:
    """Per-item MDBList page, ``/movie/<mdblist id>-<slug>`` like MDBList's own links."""
    if not mdblist_id:
        return None
    section = "movie" if media_type == "movie" else "show"
    return f"{MDBLIST_WEB_URL}/{section}/{mdblist_id}{'-' + slug if slug else ''}"
