from django.contrib import admin

from apps.mdblist.models import (
    MdblistAccount,
    MdblistLibraryItem,
    MdblistMediaInfo,
    MdblistSyncIntent,
)


@admin.register(MdblistAccount)
class MdblistAccountAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "mdblist_username",
        "auth_method",
        "plan",
        "sync_status",
        "initial_sync_complete",
        "last_synced_at",
    )
    list_filter = ("sync_status", "initial_sync_complete", "auth_method", "plan")
    search_fields = ("user__email", "mdblist_username")
    readonly_fields = (
        "created_at",
        "updated_at",
        "last_synced_at",
        "token_expires_at",
        "activities_cursor",
        "last_activities",
    )
    exclude = ("access_token", "refresh_token")


@admin.register(MdblistLibraryItem)
class MdblistLibraryItemAdmin(admin.ModelAdmin):
    list_display = (
        "account",
        "media_type",
        "tmdb_id",
        "title",
        "watched_at",
        "on_watchlist",
        "dropped",
        "user_rating",
        "updated_at",
    )
    list_filter = ("media_type", "on_watchlist", "dropped")
    search_fields = ("account__user__email", "title", "tmdb_id")
    readonly_fields = ("created_at", "updated_at")


@admin.register(MdblistSyncIntent)
class MdblistSyncIntentAdmin(admin.ModelAdmin):
    list_display = ("user", "kind", "identity_key", "desired", "updated_at")
    list_filter = ("kind", "desired")
    search_fields = ("user__email", "identity_key")
    readonly_fields = ("created_at", "updated_at")


@admin.register(MdblistMediaInfo)
class MdblistMediaInfoAdmin(admin.ModelAdmin):
    list_display = ("media_type", "mdblist_id", "imdb_id", "score", "status", "fetched_at")
    list_filter = ("media_type", "status")
    search_fields = ("mdblist_id", "imdb_id")
    readonly_fields = ("created_at", "updated_at", "fetched_at")
