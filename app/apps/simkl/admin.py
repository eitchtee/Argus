from django.contrib import admin

from apps.simkl.models import (
    SimklAccount,
    SimklLibraryItem,
    SimklMediaInfo,
    SimklSyncIntent,
)


@admin.register(SimklAccount)
class SimklAccountAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "simkl_username",
        "account_type",
        "sync_status",
        "initial_sync_complete",
        "last_synced_at",
    )
    list_filter = ("sync_status", "initial_sync_complete", "account_type")
    search_fields = ("user__email", "simkl_username")
    readonly_fields = (
        "created_at",
        "updated_at",
        "last_synced_at",
        "activities_cursor",
        "last_activities",
    )
    exclude = ("access_token",)


@admin.register(SimklLibraryItem)
class SimklLibraryItemAdmin(admin.ModelAdmin):
    list_display = (
        "account",
        "media_type",
        "simkl_id",
        "title",
        "status",
        "user_rating",
        "updated_at",
    )
    list_filter = ("media_type", "status")
    search_fields = ("account__user__email", "title", "simkl_id")
    readonly_fields = ("created_at", "updated_at")


@admin.register(SimklSyncIntent)
class SimklSyncIntentAdmin(admin.ModelAdmin):
    list_display = ("user", "kind", "identity_key", "desired", "updated_at")
    list_filter = ("kind", "desired")
    search_fields = ("user__email", "identity_key")
    readonly_fields = ("created_at", "updated_at")


@admin.register(SimklMediaInfo)
class SimklMediaInfoAdmin(admin.ModelAdmin):
    list_display = (
        "media_type",
        "simkl_id",
        "slug",
        "simkl_rating",
        "imdb_rating",
        "status",
        "fetched_at",
    )
    list_filter = ("media_type", "status")
    search_fields = ("slug", "simkl_id")
    readonly_fields = ("created_at", "updated_at", "fetched_at")
