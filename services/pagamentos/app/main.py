import logging
import os
import random
import time
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal

import httpx
from fastapi import FastAPI, HTTPException
from prometheus_client import Counter
from pydantic import BaseModel, Field

from app.telemetry import instrumentar

SERVICE = "pagamentos"
log = logging.getLogger(SERVICE)

PAGAMENTOS = Counter("pagamentos_total", "Pagamentos processados por resultado", ["status"])


class Config:
    def __init__(self):
        self.provedor_url = os.getenv("PROVEDOR_URL", "")
        self.provedor_token = os.getenv("PROVEDOR_TOKEN", "")
        self.limite = Decimal(os.getenv("LIMITE_APROVACAO", "5000"))
        self.latencia_ms = int(os.getenv("LATENCIA_SIMULADA_MS", "120"))
        self.timeout = float(os.getenv("HTTP_TIMEOUT_SECONDS", "3"))


class Pagamento(BaseModel):
    pedido_id: str = Field(min_length=1, max_length=64)
    valor: Decimal = Field(gt=0, max_digits=12, decimal_places=2)


def create_app(config: Config | None = None, transport: httpx.BaseTransport | None = None) -> FastAPI:
    cfg = config or Config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.http = httpx.Client(timeout=cfg.timeout, transport=transport)
        yield
        app.state.http.close()

    app = FastAPI(title="Pedidos Veloz - Pagamentos", lifespan=lifespan)
    instrumentar(app, SERVICE)

    def simular(p: Pagamento) -> dict:
        if cfg.latencia_ms:
            time.sleep(random.uniform(0.5, 1.5) * cfg.latencia_ms / 1000)
        if p.valor > cfg.limite:
            return {"status": "RECUSADO", "motivo": "limite excedido"}
        return {"status": "APROVADO", "autorizacao": uuid.uuid4().hex[:12]}

    def chamar_provedor(http: httpx.Client, p: Pagamento) -> dict:
        r = http.post(
            f"{cfg.provedor_url}/autorizacoes",
            json={"referencia": p.pedido_id, "valor": str(p.valor)},
            headers={"Authorization": f"Bearer {cfg.provedor_token}"},
        )
        r.raise_for_status()
        dados = r.json()
        if dados.get("aprovado"):
            return {"status": "APROVADO", "autorizacao": dados.get("codigo")}
        return {"status": "RECUSADO", "motivo": dados.get("motivo", "recusado pelo provedor")}

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        return {"status": "ok"}

    @app.post("/pagamentos")
    def autorizar(p: Pagamento):
        try:
            resultado = chamar_provedor(app.state.http, p) if cfg.provedor_url else simular(p)
        except httpx.HTTPError as exc:
            PAGAMENTOS.labels("erro").inc()
            log.error("provedor de pagamento falhou para pedido %s: %s", p.pedido_id, exc)
            raise HTTPException(status_code=502, detail="provedor de pagamento indisponivel")
        PAGAMENTOS.labels(resultado["status"].lower()).inc()
        log.info("pagamento do pedido %s: %s", p.pedido_id, resultado["status"])
        return {"pedido_id": p.pedido_id, "valor": str(p.valor), **resultado}

    return app


app = create_app()
