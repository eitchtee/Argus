from django.contrib import admin

from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow


class SeasonInline(admin.TabularInline):
    model = Season
    extra = 0
    fields = ("season_number", "name")


class EpisodeInline(admin.TabularInline):
    model = Episode
    extra = 0
    fields = ("season_number", "episode_number", "name", "air_date")


@admin.register(Show)
class ShowAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "provider",
        "external_id",
        "status",
        "aired_episode_count",
        "sync_status",
        "last_synced_at",
    )
    list_filter = ("provider", "sync_status", "status", "genres")
    search_fields = ("name", "external_id", "network")
    inlines = (SeasonInline,)


@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    list_display = ("show", "season_number", "name")
    list_filter = ("show",)
    search_fields = ("show__name", "name")


@admin.register(Episode)
class EpisodeAdmin(admin.ModelAdmin):
    list_display = ("show", "season_number", "episode_number", "name", "air_date")
    list_filter = ("show", "season_number", "air_date")
    search_fields = ("show__name", "name")


@admin.register(UserShow)
class UserShowAdmin(admin.ModelAdmin):
    list_display = ("user", "show", "is_tracking", "tier", "tracking_started_at")
    list_filter = ("is_tracking", "tier")
    search_fields = ("user__email", "show__name")


@admin.register(UserEpisode)
class UserEpisodeAdmin(admin.ModelAdmin):
    list_display = ("user", "episode", "seen_at")
    search_fields = ("user__email", "episode__name", "episode__show__name")
