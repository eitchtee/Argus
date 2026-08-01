from datetime import timedelta

from django.utils import timezone
from procrastinate.contrib.django import app

from apps.imports.models import ImportJob
from apps.imports.services import import_trakt_export


_STALLED_IMPORT_TIMEOUT = timedelta(hours=6)
_STALLED_IMPORT_ERROR = "The import worker stopped before completing this job."


def _delete_source_file(job, source_name=None):
    source_name = source_name or job.source_file.name
    if not source_name:
        return
    job.source_file.storage.delete(source_name)
    ImportJob.objects.filter(id=job.id, source_file=source_name).update(
        source_file="",
        updated_at=timezone.now(),
    )


@app.task(name="import_trakt_export")
def import_trakt_job(import_job_id: int):
    job = ImportJob.objects.select_related("user").get(id=import_job_id)
    started_at = timezone.now()
    claimed = ImportJob.objects.filter(
        id=job.id,
        status=ImportJob.Status.QUEUED,
    ).update(
        status=ImportJob.Status.PROCESSING,
        started_at=started_at,
        error_message="",
        updated_at=started_at,
    )
    if not claimed:
        return None

    source_name = job.source_file.name

    try:
        with job.source_file.open("rb") as source:
            report = import_trakt_export(job.user, source)
    except Exception as exc:
        finished_at = timezone.now()
        ImportJob.objects.filter(id=job.id).update(
            status=ImportJob.Status.FAILED,
            error_message=str(exc),
            finished_at=finished_at,
            updated_at=finished_at,
        )
        raise
    else:
        finished_at = timezone.now()
        ImportJob.objects.filter(id=job.id).update(
            status=ImportJob.Status.SUCCEEDED,
            movies_imported=report.movies_imported,
            shows_imported=report.shows_imported,
            episodes_marked=report.episodes_marked,
            warning_messages=report.warnings,
            finished_at=finished_at,
            updated_at=finished_at,
        )
        return report
    finally:
        _delete_source_file(job, source_name)


def recover_stalled_imports():
    cutoff = timezone.now() - _STALLED_IMPORT_TIMEOUT
    stalled_jobs = list(
        ImportJob.objects.filter(
            status=ImportJob.Status.PROCESSING,
            started_at__lt=cutoff,
        ).only("id", "source_file")
    )
    recovered = 0
    for job in stalled_jobs:
        finished_at = timezone.now()
        updated = ImportJob.objects.filter(
            id=job.id,
            status=ImportJob.Status.PROCESSING,
            started_at__lt=cutoff,
        ).update(
            status=ImportJob.Status.FAILED,
            error_message=_STALLED_IMPORT_ERROR,
            finished_at=finished_at,
            updated_at=finished_at,
        )
        if updated:
            _delete_source_file(job)
            recovered += 1
    return recovered


@app.periodic(cron="*/15 * * * *")
@app.task(name="recover_stalled_imports")
def recover_stalled_imports_task(timestamp: int | None = None):
    return recover_stalled_imports()
