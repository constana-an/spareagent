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

# Week 3: git (repository checkout) and the Docker CLI only (no daemon) for driving
# sandbox containers. The sandbox itself never receives the Docker socket.
ARG DOCKER_CLI_VERSION=27.5.1
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && python -c "import platform,urllib.request,tarfile,io; a={'x86_64':'x86_64','aarch64':'aarch64','arm64':'aarch64'}[platform.machine()]; d=urllib.request.urlopen(f'https://download.docker.com/linux/static/stable/{a}/docker-${DOCKER_CLI_VERSION}.tgz', timeout=120).read(); t=tarfile.open(fileobj=io.BytesIO(d)); m=t.getmember('docker/docker'); m.name='docker'; t.extract(m, '/usr/local/bin', filter='data')" \
 && chmod 0755 /usr/local/bin/docker && docker --version

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app \
 && mkdir -p /var/lib/spareagent/artifacts /var/lib/spareagent-worker /certs /fixtures \
 && chown -R app:app /var/lib/spareagent /var/lib/spareagent-worker /certs /fixtures /app

COPY --chown=app:app . /app

USER app
EXPOSE 8000

CMD ["uvicorn", "--factory", "apps.api.main:create_app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
