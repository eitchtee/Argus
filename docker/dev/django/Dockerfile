FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ARG VERSION=dev
ENV APP_VERSION=$VERSION

WORKDIR /usr/src/app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

RUN apt-get update && \
    apt-get install --no-install-recommends -y gettext supervisor && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN uv sync --no-install-project --no-dev

COPY ./docker/dev/django/start /start
COPY ./docker/dev/huey/start /start-huey
COPY ./docker/dev/supervisord/supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY ./docker/dev/supervisord/supervisord.conf /etc/supervisord.conf
COPY ./docker/dev/supervisord/start /start-supervisor

RUN sed -i 's/\r$//g' /start && \
    chmod +x /start && \
    sed -i 's/\r$//g' /start-huey && \
    chmod +x /start-huey && \
    sed -i 's/\r$//g' /start-supervisor && \
    chmod +x /start-supervisor

ENV PATH="/opt/venv/bin:$PATH"

COPY ./app .

CMD ["/start-supervisor"]
