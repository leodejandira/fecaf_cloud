import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.telemetry import instrumentar

SERVICE = "gateway"
log = logging.getLogger(SERVICE)

PEDIDOS_URL = os.getenv("PEDIDOS_URL", "http://pedidos:8000")
ESTOQUE_URL = os.getenv("ESTOQUE_URL", "http://estoque:8000")
TIMEOUT = float(os.getenv("HTTP_TIMEOUT_SECONDS", "5"))

HEADERS_REPASSADOS = {"content-type", "x-request-id"}


def create_app(transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.http = httpx.AsyncClient(timeout=TIMEOUT, transport=transport)
        yield
        await app.state.http.aclose()

    app = FastAPI(title="Pedidos Veloz - API Gateway", lifespan=lifespan)
    instrumentar(app, SERVICE)

    async def repassar(request: Request, url: str) -> Response:
        headers = {k: v for k, v in request.headers.items() if k.lower() in HEADERS_REPASSADOS}
        try:
            upstream = await request.app.state.http.request(
                request.method,
                url,
                params=request.query_params,
                content=await request.body(),
                headers=headers,
            )
        except httpx.TimeoutException:
            log.warning("timeout chamando %s", url)
            return JSONResponse({"erro": "servico indisponivel (timeout)"}, status_code=504)
        except httpx.HTTPError as exc:
            log.error("falha chamando %s: %s", url, exc)
            return JSONResponse({"erro": "servico indisponivel"}, status_code=502)
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type"),
        )

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        return {"status": "ok"}

    @app.post("/api/pedidos")
    async def criar_pedido(request: Request):
        return await repassar(request, f"{PEDIDOS_URL}/pedidos")

    @app.get("/api/pedidos/{pedido_id}")
    async def consultar_pedido(pedido_id: str, request: Request):
        return await repassar(request, f"{PEDIDOS_URL}/pedidos/{pedido_id}")

    @app.get("/api/produtos")
    async def listar_produtos(request: Request):
        return await repassar(request, f"{ESTOQUE_URL}/produtos")

    @app.get("/api/produtos/{sku}")
    async def consultar_produto(sku: str, request: Request):
        return await repassar(request, f"{ESTOQUE_URL}/produtos/{sku}")

    return app


app = create_app()
