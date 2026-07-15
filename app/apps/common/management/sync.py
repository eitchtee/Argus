from django.core.management.base import CommandError
from huey.contrib.djhuey import HUEY
from huey.exceptions import TaskException


def run_sync_command(command, dispatch_task, *, label: str, force_all: bool, wait: bool):
    result = dispatch_task(force_all=force_all)
    if not wait:
        command.stdout.write(
            f"Queued {label} synchronization ({'all' if force_all else 'stale tracked'})."
        )
        return

    try:
        item_task_ids = result.get(blocking=True)
    except TaskException as exc:
        raise CommandError(f"{label} synchronization dispatch failed: {exc}") from exc

    failures = []
    for item_task_id in item_task_ids:
        try:
            item_result = HUEY.result(item_task_id, blocking=True)
        except TaskException as exc:
            failures.append(f"{item_task_id}: {exc}")
            continue

        if not isinstance(item_result, dict):
            continue

        translation_task_id = item_result.get("translation_task_id")
        if not translation_task_id:
            continue

        try:
            HUEY.result(translation_task_id, blocking=True)
        except TaskException as exc:
            item_id = item_result.get("item_id", item_task_id)
            failures.append(f"{item_id}: {exc}")

    if failures:
        for failure in failures:
            command.stderr.write(f"Failed to sync {label} item {failure}")
        raise CommandError(
            f"{len(failures)} {label} synchronization task(s) failed."
        )

    command.stdout.write(f"Synchronized {len(item_task_ids)} {label} item(s).")
