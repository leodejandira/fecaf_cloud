# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

FROM python:3.12-slim
ARG VERSION=dev
LABEL org.opencontainers.image.title="pedidos-veloz-pagamentos" \
      org.opencontainers.image.version="${VERSION}"
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_VERSION=${VERSION}
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
WORKDIR /srv
COPY --from=build /opt/venv /opt/venv
COPY app ./app
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
