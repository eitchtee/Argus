from django.db import models


class Tier(models.TextChoices):
    S = "S", "S"
    A = "A", "A"
    B = "B", "B"
    C = "C", "C"
    D = "D", "D"
    E = "E", "E"
    F = "F", "F"


class SyncStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    OK = "ok", "OK"
    ERROR = "error", "Error"


class ProviderBackedModel(models.Model):
    provider = models.CharField(max_length=16)
    external_id = models.CharField(max_length=32)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    sync_status = models.CharField(
        max_length=16,
        choices=SyncStatus.choices,
        default=SyncStatus.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "external_id"],
                name="%(app_label)s_%(class)s_provider_external_id_uniq",
            )
        ]


class Genre(models.Model):
    provider = models.CharField(max_length=16)
    external_id = models.CharField(max_length=32)
    name = models.CharField(max_length=120)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "external_id"],
                name="catalog_genre_provider_external_id_uniq",
            )
        ]
        ordering = ("name",)

    def __str__(self):
        return self.name
