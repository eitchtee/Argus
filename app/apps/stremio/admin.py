from django.contrib import admin

from apps.stremio.models import StremioAccount, StremioSyncIntent


@admin.register(StremioAccount)
class StremioAccountAdmin(admin.ModelAdmin):
    list_display = ("user", "stremio_username", "sync_status", "last_synced_at")
    exclude = ("auth_key",)


@admin.register(StremioSyncIntent)
class StremioSyncIntentAdmin(admin.ModelAdmin):
    list_display = ("user", "kind", "identity_key", "desired", "updated_at")
    list_filter = ("kind", "desired")
