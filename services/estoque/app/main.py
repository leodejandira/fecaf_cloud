import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
from prometheus_client import Counter
from pydantic import BaseModel, Field

from app.db import Database, EstoqueInsuficiente, ProdutoInexistente, ReservaInexistente
from app.telemetry import instrumentar

SERVICE = "estoque"
log = logging.getLogger(SERVICE)

RESERVAS = Counter("estoque_reservas_total", "Reservas de estoque por resultado", ["resultado"])


class Item(BaseModel):
    sku: str = Field(min_length=1, max_length=40)
    quantidade: int = Field(gt=0, le=100)


class Reserva(BaseModel):
    pedido_id: str = Field(min_length=1, max_length=64)
    itens: list[Item] = Field(min_length=1, max_length=50)


def create_app(database: Database | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if database is None:
            PsycopgInstrumentor().instrument(enable_commenter=False)
            app.state.db = Database(os.environ["DATABASE_URL"], max_size=int(os.getenv("DB_POOL_MAX", "10")))
        else:
            app.state.db = database
        app.state.db.migrar(seed=os.getenv("SEED_DATA", "false").lower() == "true")
        yield
        if database is None:
            app.state.db.close()

    app = FastAPI(title="Pedidos Veloz - Estoque", lifespan=lifespan)
    instrumentar(app, SERVICE)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        try:
            app.state.db.ping()
        except Exception:
            return JSONResponse({"status": "banco indisponivel"}, status_code=503)
        return {"status": "ok"}

    @app.get("/produtos")
    def listar():
        return app.state.db.listar()

    @app.get("/produtos/{sku}")
    def buscar(sku: str):
        produto = app.state.db.buscar(sku)
        if produto is None:
            raise HTTPException(status_code=404, detail="produto nao encontrado")
        return produto

    @app.post("/reservas", status_code=201)
    def reservar(reserva: Reserva):
        try:
            resultado = app.state.db.reservar(reserva.pedido_id, [(i.sku, i.quantidade) for i in reserva.itens])
        except ProdutoInexistente as exc:
            RESERVAS.labels("produto_inexistente").inc()
            raise HTTPException(status_code=404, detail=f"produto {exc.sku} nao encontrado")
        except EstoqueInsuficiente as exc:
            RESERVAS.labels("sem_estoque").inc()
            log.info("estoque insuficiente para %s no pedido %s", exc.sku, reserva.pedido_id)
            raise HTTPException(status_code=409, detail=f"estoque insuficiente para {exc.sku}")
        RESERVAS.labels("ok").inc()
        return resultado

    @app.post("/reservas/{pedido_id}/confirmar")
    def confirmar(pedido_id: str):
        try:
            app.state.db.confirmar(pedido_id)
        except ReservaInexistente:
            raise HTTPException(status_code=404, detail="reserva nao encontrada")
        return {"pedido_id": pedido_id, "status": "CONFIRMADO"}

    @app.post("/reservas/{pedido_id}/cancelar")
    def cancelar(pedido_id: str):
        try:
            app.state.db.cancelar(pedido_id)
        except ReservaInexistente:
            raise HTTPException(status_code=404, detail="reserva nao encontrada")
        return {"pedido_id": pedido_id, "status": "CANCELADO"}

    return app


app = create_app()
