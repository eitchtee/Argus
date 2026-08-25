from django.contrib import admin

from apps.catalog.models import Genre, MediaRating


@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    list_display = ("name", "provider", "external_id")
    list_filter = ("provider",)
    search_fields = ("name", "external_id")


@admin.register(MediaRating)
class MediaRatingAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "media_type",
        "content_type",
        "object_id",
        "score",
        "updated_at",
    )
    list_filter = ("media_type", "content_type")
    search_fields = ("user__email",)
    ordering = ("-updated_at",)
