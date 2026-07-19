from django.conf import settings
from django.db import models

from apps.catalog.models import Genre, ProviderBackedModel, Tier
from apps.catalog.providers.tmdb import build_backdrop_url, build_poster_url


class Movie(ProviderBackedModel):
    provider = models.CharField(max_length=16, default="tmdb")
    imdb_id = models.CharField(max_length=32, null=True, blank=True)
    tmdb_id = models.CharField(max_length=32, null=True, blank=True)
    tvdb_id = models.CharField(max_length=32, null=True, blank=True)
    title = models.CharField(max_length=255)
    original_title = models.CharField(max_length=255, blank=True)
    overview = models.TextField(blank=True)
    tagline = models.CharField(max_length=255, blank=True)
    translations = models.JSONField(default=dict, blank=True)
    poster_path = models.CharField(max_length=255, null=True, blank=True)
    backdrop_path = models.CharField(max_length=255, null=True, blank=True)
    cast = models.JSONField(default=list, blank=True)
    director = models.CharField(max_length=255, blank=True)
    trailer_url = models.CharField(max_length=255, null=True, blank=True)
    release_date = models.DateField(null=True, blank=True)
    runtime = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=64, blank=True)
    vote_average = models.FloatField(null=True, blank=True)
    vote_count = models.PositiveIntegerField(null=True, blank=True)
    genres = models.ManyToManyField(Genre, blank=True, related_name="movies")

    class Meta(ProviderBackedModel.Meta):
        ordering = ("title",)

    def save(self, *args, **kwargs):
        if self.provider == "tmdb" and not self.tmdb_id:
            self.tmdb_id = self.external_id
        if self.provider == "tvdb" and not self.tvdb_id:
            self.tvdb_id = self.external_id
        super().save(*args, **kwargs)

    @property
    def poster_url(self) -> str | None:
        return build_poster_url(self.poster_path)

    @property
    def backdrop_url(self) -> str | None:
        return build_backdrop_url(self.backdrop_path)

    def __str__(self):
        return self.title


class UserMovie(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="movies",
    )
    movie = models.ForeignKey(
        Movie,
        on_delete=models.CASCADE,
        related_name="user_states",
    )
    on_watchlist = models.BooleanField(default=False)
    watchlist_added_at = models.DateTimeField(null=True, blank=True)
    is_seen = models.BooleanField(default=False)
    seen_at = models.DateTimeField(null=True, blank=True)
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
                fields=["user", "movie"],
                name="movies_usermovie_user_movie_uniq",
            )
        ]

    def __str__(self):
        return f"{self.user} - {self.movie}"
