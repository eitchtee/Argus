import time

from django.core.management.base import CommandError
from procrastinate import jobs
from procrastinate.contrib.django import app


DEFAULT_WAIT_TIMEOUT = 300
TERMINAL_STATUSES = {
    jobs.Status.SUCCEEDED,
    jobs.Status.FAILED,
    jobs.Status.CANCELLED,
    jobs.Status.ABORTED,
}


def wait_for_jobs(job_ids, *, timeout):
    deadline = time.monotonic() + timeout
    pending = set(job_ids)
    statuses = {}

    while pending:
        for job_id in tuple(pending):
            status = app.job_manager.get_job_status(job_id)
            statuses[job_id] = status
            if status in TERMINAL_STATUSES:
                pending.remove(job_id)

        if pending:
            if time.monotonic() >= deadline:
                raise CommandError("Timed out waiting for Procrastinate jobs.")
            time.sleep(0.25)

    return list(statuses.items())


def run_sync_command(
    command,
    dispatch_task,
    *,
    label: str,
    force_all: bool,
    wait: bool,
    wait_timeout: int = DEFAULT_WAIT_TIMEOUT,
):
    if wait and wait_timeout <= 0:
        raise CommandError("--wait-timeout must be greater than zero.")

    if not wait:
        dispatch_job_id = dispatch_task.defer(force_all=force_all)
        command.stdout.write(
            f"Queued {label} synchronization ({'all' if force_all else 'stale tracked'}) "
            f"as task {dispatch_job_id}."
        )
        return

    item_task_ids = dispatch_task.func(force_all=force_all)
    results = wait_for_jobs(item_task_ids, timeout=wait_timeout)

    failures = [
        f"{job_id}: {status.value}"
        for job_id, status in results
        if status != jobs.Status.SUCCEEDED
    ]
    if failures:
        for failure in failures:
            command.stderr.write(f"Failed to sync {label} item {failure}")
        raise CommandError(
            f"{len(failures)} {label} synchronization task(s) failed."
        )

    command.stdout.write(f"Synchronized {len(item_task_ids)} {label} item(s).")
