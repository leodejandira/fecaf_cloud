import json
import logging
import os
import sys
import time

from fastapi import FastAPI, Request, Response
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

IGNORADAS = {"/metrics", "/healthz", "/readyz"}

REQUESTS = Counter(
    "http_requests_total",
    "Requisicoes HTTP recebidas",
    ["service", "method", "route", "status"],
)
LATENCY = Histogram(
    "http_request_duration_seconds",
    "Latencia das requisicoes HTTP",
    ["service", "method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)

_configurado = False


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        ctx = trace.get_current_span().get_span_context()
        if ctx.is_valid:
            data["trace_id"] = format(ctx.trace_id, "032x")
            data["span_id"] = format(ctx.span_id, "016x")
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False)


def _configurar(service: str) -> None:
    global _configurado
    if _configurado:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(os.getenv("LOG_LEVEL", "INFO"))
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("httpx").setLevel(logging.WARNING)

    resource = Resource.create(
        {
            "service.name": service,
            "service.version": os.getenv("APP_VERSION", "dev"),
            "deployment.environment": os.getenv("APP_ENV", "local"),
        }
    )
    provider = TracerProvider(resource=resource)
    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    HTTPXClientInstrumentor().instrument()
    _configurado = True


def instrumentar(app: FastAPI, service: str) -> None:
    _configurar(service)
    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz,metrics", exclude_spans=["receive", "send"])

    @app.middleware("http")
    async def coletar_metricas(request: Request, call_next):
        inicio = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            path = getattr(route, "path", "nao_mapeada")
            if path not in IGNORADAS:
                REQUESTS.labels(service, request.method, path, str(status)).inc()
                LATENCY.labels(service, request.method, path).observe(time.perf_counter() - inicio)

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
