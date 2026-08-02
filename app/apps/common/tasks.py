from procrastinate import builtin_tasks
from procrastinate.contrib.django import app


@app.periodic(cron="0 4 * * *")
@app.task(
    name="cleanup_old_jobs",
    pass_context=True,
    queueing_lock="cleanup_old_jobs",
)
async def cleanup_old_jobs(context, timestamp: int | None = None):
    return await builtin_tasks.remove_old_jobs(
        context,
        max_hours=30 * 24,
        remove_failed=True,
        remove_cancelled=True,
        remove_aborted=True,
    )
