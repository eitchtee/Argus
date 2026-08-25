from django.conf import settings
from django.db import models
from django.db.models import Q


class ImportJob(models.Model):
    class Service(models.TextChoices):
        TRAKT = "trakt", "Trakt"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Processing"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="import_jobs",
    )
    service = models.CharField(
        max_length=16,
        choices=Service.choices,
        default=Service.TRAKT,
    )
    source_file = models.FileField(upload_to="imports/%Y/%m/%d/")
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.QUEUED,
    )
    task_id = models.BigIntegerField(null=True, blank=True, unique=True)
    queued_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    movies_imported = models.PositiveIntegerField(default=0)
    shows_imported = models.PositiveIntegerField(default=0)
    episodes_marked = models.PositiveIntegerField(default=0)
    ratings_applied = models.PositiveIntegerField(default=0)
    warning_messages = models.JSONField(default=list, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-queued_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("user", "service"),
                condition=Q(
                    status__in=("queued", "processing"),
                ),
                name="imports_active_user_service_uniq",
            )
        ]

    def __str__(self):
        return f"{self.get_service_display()} import for {self.user}"
