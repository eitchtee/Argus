from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.common.decorators.user import htmx_login_required

from .forms import TraktImportForm
from .models import ImportJob
from .tasks import import_trakt_job


_ACTIVE_STATUSES = (
    ImportJob.Status.QUEUED,
    ImportJob.Status.PROCESSING,
)


def _latest_job(user):
    return (
        ImportJob.objects.filter(user=user, service=ImportJob.Service.TRAKT)
        .order_by("-queued_at", "-id")
        .first()
    )


def _active_job(user):
    return (
        ImportJob.objects.filter(
            user=user,
            service=ImportJob.Service.TRAKT,
            status__in=_ACTIVE_STATUSES,
        )
        .order_by("-id")
        .first()
    )


def _render_trakt(request, *, form=None, import_job=None):
    return render(
        request,
        "imports/fragments/trakt.html",
        {
            "form": form or TraktImportForm(),
            "import_job": import_job,
            "import_in_progress": import_job is not None
            and import_job.status in _ACTIVE_STATUSES,
        },
    )


@htmx_login_required
@require_GET
def trakt_panel(request):
    return _render_trakt(request, import_job=_latest_job(request.user))


@htmx_login_required
@require_POST
def trakt_upload(request):
    form = TraktImportForm(request.POST, request.FILES)
    if not form.is_valid():
        return _render_trakt(request, form=form, import_job=_latest_job(request.user))

    try:
        with transaction.atomic():
            locked_user = (
                get_user_model().objects.select_for_update().get(pk=request.user.pk)
            )
            active_job = _active_job(locked_user)
            if active_job is None:
                job = ImportJob(
                    user=locked_user,
                    service=ImportJob.Service.TRAKT,
                    source_file=form.cleaned_data["archive"],
                )
                try:
                    job.save(force_insert=True)
                except IntegrityError:
                    if job.source_file._committed:
                        job.source_file.delete(save=False)
                    raise
    except IntegrityError:
        active_job = _active_job(request.user)
        if active_job is None:
            raise

    if active_job is not None:
        form.add_error(None, "A Trakt import is already in progress.")
        return _render_trakt(request, form=form, import_job=active_job)

    try:
        task_id = import_trakt_job.defer(import_job_id=job.id)
    except Exception as exc:
        finished_at = timezone.now()
        job.source_file.delete(save=False)
        job.status = ImportJob.Status.FAILED
        job.error_message = str(exc)
        job.finished_at = finished_at
        job.save(
            update_fields=[
                "source_file",
                "status",
                "error_message",
                "finished_at",
                "updated_at",
            ]
        )
    else:
        if isinstance(task_id, int):
            job.task_id = task_id
            job.save(update_fields=["task_id", "updated_at"])

    return _render_trakt(request, import_job=job)


@htmx_login_required
@require_GET
def trakt_status(request, job_id):
    job = get_object_or_404(
        ImportJob,
        id=job_id,
        user=request.user,
        service=ImportJob.Service.TRAKT,
    )
    return _render_trakt(request, import_job=job)
