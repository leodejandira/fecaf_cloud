import logging
import os
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from opentelemetry import trace
from prometheus_client import Counter, Histogram
from pydantic import BaseModel, Field

from app.db import Database, Repositorio
from app.telemetry import instrumentar

SERVICE = "pedidos"
log = logging.getLogger(SERVICE)
tracer = trace.get_tracer(SERVICE)

PEDIDOS = Counter("pedidos_total", "Pedidos finalizados por status", ["status"])
VALOR = Histogram(
    "pedidos_valor_reais",
    "Valor dos pedidos confirmados",
    buckets=(50, 100, 250, 500, 1000, 2500, 5000),
)


class Item(BaseModel):
    sku: str = Field(min_length=1, max_length=40)
    quantidade: int = Field(gt=0, le=100)


class NovoPedido(BaseModel):
    cliente_id: str = Field(min_length=1, max_length=64)
    itens: list[Item] = Field(min_length=1, max_length=50)


class Config:
    def __init__(self):
        self.estoque_url = os.getenv("ESTOQUE_URL", "http://estoque:8000")
        self.pagamentos_url = os.getenv("PAGAMENTOS_URL", "http://pagamentos:8000")
        self.timeout = float(os.getenv("HTTP_TIMEOUT_SECONDS", "3"))


def create_app(
    repo: Repositorio | None = None,
    transport: httpx.BaseTransport | None = None,
    config: Config | None = None,
) -> FastAPI:
    cfg = config or Config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if repo is None:
            from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor

            PsycopgInstrumentor().instrument(enable_commenter=False)
            app.state.repo = Database(os.environ["DATABASE_URL"], max_size=int(os.getenv("DB_POOL_MAX", "10")))
        else:
            app.state.repo = repo
        app.state.repo.migrar()
        app.state.http = httpx.Client(timeout=cfg.timeout, transport=transport)
        yield
        app.state.http.close()
        app.state.repo.close()

    app = FastAPI(title="Pedidos Veloz - Pedidos", lifespan=lifespan)
    instrumentar(app, SERVICE)

    def finalizar(pedido_id: str, status: str, codigo: int, valor=None, motivo=None):
        pedido = app.state.repo.atualizar(pedido_id, status, valor, motivo)
        PEDIDOS.labels(status.lower()).inc()
        trace.get_current_span().set_attribute("pedido.status", status)
        log.info("pedido %s finalizado com status %s", pedido_id, status)
        return JSONResponse(serializar(pedido), status_code=codigo)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        try:
            app.state.repo.ping()
        except Exception:
            return JSONResponse({"status": "banco indisponivel"}, status_code=503)
        return {"status": "ok"}

    @app.post("/pedidos")
    def criar(novo: NovoPedido):
        http: httpx.Client = app.state.http
        pedido_id = str(uuid.uuid4())
        itens = [i.model_dump() for i in novo.itens]
        app.state.repo.inserir(pedido_id, novo.cliente_id, itens)
        trace.get_current_span().set_attribute("pedido.id", pedido_id)

        try:
            with tracer.start_as_current_span("reservar_estoque"):
                reserva = http.post(f"{cfg.estoque_url}/reservas", json={"pedido_id": pedido_id, "itens": itens})
        except httpx.HTTPError as exc:
            log.error("estoque indisponivel no pedido %s: %s", pedido_id, exc)
            return finalizar(pedido_id, "FALHA", 503, motivo="estoque indisponivel")

        if reserva.status_code in (404, 409):
            return finalizar(pedido_id, "REJEITADO", 409, motivo=reserva.json().get("detail"))
        if reserva.status_code != 201:
            return finalizar(pedido_id, "FALHA", 502, motivo="erro no servico de estoque")

        valor = reserva.json()["valor_total"]
        aprovado, motivo = False, None
        try:
            with tracer.start_as_current_span("autorizar_pagamento"):
                pagamento = http.post(f"{cfg.pagamentos_url}/pagamentos", json={"pedido_id": pedido_id, "valor": valor})
            if pagamento.status_code == 200:
                corpo = pagamento.json()
                aprovado = corpo["status"] == "APROVADO"
                motivo = corpo.get("motivo")
            else:
                motivo = "falha no pagamento"
        except httpx.HTTPError as exc:
            log.error("pagamentos indisponivel no pedido %s: %s", pedido_id, exc)
            motivo = "pagamentos indisponivel"

        acao = "confirmar" if aprovado else "cancelar"
        try:
            http.post(f"{cfg.estoque_url}/reservas/{pedido_id}/{acao}").raise_for_status()
        except httpx.HTTPError as exc:
            log.error("falha ao %s reserva do pedido %s: %s", acao, pedido_id, exc)

        if aprovado:
            VALOR.observe(float(valor))
            return finalizar(pedido_id, "CONFIRMADO", 201, valor=valor)
        return finalizar(pedido_id, "RECUSADO", 402, valor=valor, motivo=motivo)

    @app.get("/pedidos/{pedido_id}")
    def consultar(pedido_id: str):
        pedido = app.state.repo.buscar(pedido_id)
        if pedido is None:
            raise HTTPException(status_code=404, detail="pedido nao encontrado")
        return serializar(pedido)

    return app


def serializar(p: dict) -> dict:
    return {
        "id": p["id"],
        "cliente_id": p["cliente_id"],
        "itens": p["itens"],
        "status": p["status"],
        "valor_total": str(p["valor_total"]) if p.get("valor_total") is not None else None,
        "motivo": p.get("motivo"),
        "criado_em": p["criado_em"].isoformat(),
        "atualizado_em": p["atualizado_em"].isoformat(),
    }


app = create_app()
