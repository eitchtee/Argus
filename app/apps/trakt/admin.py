from django.contrib import admin

from apps.trakt.models import TraktAccount, TraktSyncIntent


@admin.register(TraktAccount)
class TraktAccountAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "trakt_username",
        "sync_status",
        "initial_sync_complete",
        "last_synced_at",
    )
    list_filter = ("sync_status", "initial_sync_complete")
    search_fields = ("user__email", "trakt_username")
    readonly_fields = (
        "created_at",
        "updated_at",
        "last_synced_at",
        "token_expires_at",
    )
    exclude = ("access_token", "refresh_token")


@admin.register(TraktSyncIntent)
class TraktSyncIntentAdmin(admin.ModelAdmin):
    list_display = ("user", "kind", "identity_key", "desired", "updated_at")
    list_filter = ("kind", "desired")
    search_fields = ("user__email", "identity_key")
    readonly_fields = ("created_at", "updated_at")
