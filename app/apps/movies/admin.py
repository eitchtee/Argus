from django.contrib import admin

from apps.movies.models import Movie, UserMovie


@admin.register(Movie)
class MovieAdmin(admin.ModelAdmin):
    list_display = ("title", "provider", "external_id", "sync_status", "last_synced_at")
    list_filter = ("provider", "sync_status", "status", "genres")
    search_fields = ("title", "original_title", "external_id", "imdb_id")


@admin.register(UserMovie)
class UserMovieAdmin(admin.ModelAdmin):
    list_display = ("user", "movie", "on_watchlist", "is_seen", "tier")
    list_filter = ("on_watchlist", "is_seen", "tier")
    search_fields = ("user__email", "movie__title")
