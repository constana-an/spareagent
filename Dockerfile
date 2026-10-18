FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
# Dev tools are included so `make test` / `make lint` run inside the same image.
RUN pip install -r requirements-dev.txt

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app \
 && mkdir -p /var/lib/spareagent/artifacts \
 && chown -R app:app /var/lib/spareagent /app

COPY --chown=app:app . /app

USER app
EXPOSE 8000

CMD ["uvicorn", "--factory", "apps.api.main:create_app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
