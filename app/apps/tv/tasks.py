from huey.contrib.djhuey import db_task

from apps.tv import services as tv_services


@db_task()
def hydrate_show_translations(show_id: int):
    return tv_services.hydrate_show_translations_sync(show_id)
