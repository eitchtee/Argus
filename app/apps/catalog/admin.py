from django.contrib import admin

from apps.catalog.models import Genre


@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    list_display = ("name", "provider", "external_id")
    list_filter = ("provider",)
    search_fields = ("name", "external_id")
