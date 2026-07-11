from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.catalog.models import Genre, ProviderBackedModel, Tier


class Show(ProviderBackedModel):
    provider = models.CharField(max_length=16, default="tvdb")
    name = models.CharField(max_length=255)
    overview = models.TextField(blank=True)
    poster_path = models.CharField(max_length=255, null=True, blank=True)
    backdrop_path = models.CharField(max_length=255, null=True, blank=True)
    cast = models.JSONField(default=list, blank=True)
    trailer_url = models.CharField(max_length=255, null=True, blank=True)
    imdb_id = models.CharField(max_length=32, null=True, blank=True)
    tmdb_id = models.CharField(max_length=32, null=True, blank=True)
    average_runtime = models.PositiveIntegerField(null=True, blank=True)
    next_air_date = models.DateField(null=True, blank=True)
    last_air_date = models.DateField(null=True, blank=True)
    airs_time = models.TimeField(null=True, blank=True)
    first_aired = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=64, blank=True)
    network = models.CharField(max_length=255, null=True, blank=True)
    genres = models.ManyToManyField(Genre, blank=True, related_name="shows")
    aired_episode_count = models.PositiveIntegerField(default=0)

    class Meta(ProviderBackedModel.Meta):
        ordering = ("name",)

    @property
    def tvdb_id(self):
        return self.external_id

    @property
    def poster_url(self) -> str | None:
        return self.poster_path or None

    @property
    def backdrop_url(self) -> str | None:
        return self.backdrop_path or None

    def __str__(self):
        return self.name


class Season(models.Model):
    show = models.ForeignKey(
        Show,
        on_delete=models.CASCADE,
        related_name="seasons",
    )
    season_number = models.PositiveIntegerField()
    name = models.CharField(max_length=255, blank=True)
    overview = models.TextField(blank=True)
    poster_path = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        ordering = ("show__name", "season_number")
        constraints = [
            models.UniqueConstraint(
                fields=["show", "season_number"],
                name="tv_season_show_number_uniq",
            )
        ]

    def __str__(self):
        return self.name or f"{self.show} season {self.season_number}"


class Episode(models.Model):
    show = models.ForeignKey(
        Show,
        on_delete=models.CASCADE,
        related_name="episodes",
    )
    season = models.ForeignKey(
        Season,
        on_delete=models.CASCADE,
        related_name="episodes",
    )
    season_number = models.PositiveIntegerField()
    episode_number = models.PositiveIntegerField()
    absolute_number = models.PositiveIntegerField(null=True, blank=True)
    name = models.CharField(max_length=255, blank=True)
    overview = models.TextField(blank=True)
    still_path = models.CharField(max_length=255, null=True, blank=True)
    air_date = models.DateField(null=True, blank=True)
    runtime = models.PositiveIntegerField(null=True, blank=True)
    finale_type = models.CharField(
        max_length=16,
        null=True,
        blank=True,
        choices=[
            ("season", "Season"),
            ("midseason", "Midseason"),
            ("series", "Series"),
        ],
    )

    class Meta:
        ordering = ("show__name", "season_number", "episode_number")
        constraints = [
            models.UniqueConstraint(
                fields=["show", "season_number", "episode_number"],
                name="tv_episode_show_season_episode_uniq",
            )
        ]

    def __str__(self):
        label = f"S{self.season_number:02d}E{self.episode_number:02d}"
        return f"{self.show} {label} {self.name}".strip()


class UserShow(models.Model):
    class Status(models.TextChoices):
        TRACKED = "tracked", "Tracked"
        PAUSED = "paused", "Paused"
        DROPPED = "dropped", "Dropped"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shows",
    )
    show = models.ForeignKey(
        Show,
        on_delete=models.CASCADE,
        related_name="user_states",
    )
    status = models.CharField(
        max_length=8,
        choices=Status.choices,
        default=Status.TRACKED,
    )
    tracking_started_at = models.DateTimeField(default=timezone.now)
    tier = models.CharField(
        max_length=1,
        choices=Tier.choices,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "show"],
                name="tv_usershow_user_show_uniq",
            )
        ]

    def __str__(self):
        return f"{self.user} - {self.show}"


class UserEpisode(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="seen_episodes",
    )
    episode = models.ForeignKey(
        Episode,
        on_delete=models.CASCADE,
        related_name="user_states",
    )
    seen_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "episode"],
                name="tv_userepisode_user_episode_uniq",
            )
        ]

    def __str__(self):
        return f"{self.user} - {self.episode}"
