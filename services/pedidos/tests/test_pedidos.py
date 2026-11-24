from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app


class RepoMemoria:
    def __init__(self):
        self.dados = {}

    def migrar(self):
        pass

    def ping(self):
        return True

    def close(self):
        pass

    def inserir(self, pedido_id, cliente_id, itens):
        agora = datetime.now(UTC)
        self.dados[pedido_id] = {
            "id": pedido_id,
            "cliente_id": cliente_id,
            "itens": itens,
            "status": "PENDENTE",
            "valor_total": None,
            "motivo": None,
            "criado_em": agora,
            "atualizado_em": agora,
        }
        return self.dados[pedido_id]

    def atualizar(self, pedido_id, status, valor_total=None, motivo=None):
        p = self.dados[pedido_id]
        p.update(status=status, motivo=motivo, atualizado_em=datetime.now(UTC))
        if valor_total is not None:
            p["valor_total"] = valor_total
        return p

    def buscar(self, pedido_id):
        return self.dados.get(pedido_id)


class Servicos:
    def __init__(self, reserva=201, pagamento="APROVADO", pagamentos_fora=False):
        self.reserva = reserva
        self.pagamento = pagamento
        self.pagamentos_fora = pagamentos_fora
        self.chamadas = []

    def __call__(self, request: httpx.Request):
        path = request.url.path
        self.chamadas.append(path)
        if path == "/reservas":
            if self.reserva == 201:
                return httpx.Response(201, json={"valor_total": "119.80", "itens": []})
            return httpx.Response(self.reserva, json={"detail": "estoque insuficiente para CAM-001"})
        if path == "/pagamentos":
            if self.pagamentos_fora:
                raise httpx.ConnectError("fora", request=request)
            return httpx.Response(200, json={"status": self.pagamento, "motivo": "limite excedido"})
        if path.endswith("/confirmar") or path.endswith("/cancelar"):
            return httpx.Response(200, json={})
        return httpx.Response(404)


PEDIDO = {"cliente_id": "cli-1", "itens": [{"sku": "CAM-001", "quantidade": 2}]}


@pytest.fixture
def repo():
    return RepoMemoria()


def cliente(repo, servicos):
    return TestClient(create_app(repo=repo, transport=httpx.MockTransport(servicos)))


def test_pedido_aprovado_confirma_reserva(repo):
    servicos = Servicos()
    with cliente(repo, servicos) as c:
        r = c.post("/pedidos", json=PEDIDO)
    assert r.status_code == 201
    assert r.json()["status"] == "CONFIRMADO"
    assert r.json()["valor_total"] == "119.80"
    assert servicos.chamadas[-1].endswith("/confirmar")


def test_pagamento_recusado_cancela_reserva(repo):
    servicos = Servicos(pagamento="RECUSADO")
    with cliente(repo, servicos) as c:
        r = c.post("/pedidos", json=PEDIDO)
    assert r.status_code == 402
    assert r.json()["status"] == "RECUSADO"
    assert servicos.chamadas[-1].endswith("/cancelar")


def test_pagamentos_fora_do_ar_libera_estoque(repo):
    servicos = Servicos(pagamentos_fora=True)
    with cliente(repo, servicos) as c:
        r = c.post("/pedidos", json=PEDIDO)
    assert r.json()["status"] == "RECUSADO"
    assert r.json()["motivo"] == "pagamentos indisponivel"
    assert servicos.chamadas[-1].endswith("/cancelar")


def test_sem_estoque_rejeita_sem_cobrar(repo):
    servicos = Servicos(reserva=409)
    with cliente(repo, servicos) as c:
        r = c.post("/pedidos", json=PEDIDO)
    assert r.status_code == 409
    assert r.json()["status"] == "REJEITADO"
    assert "/pagamentos" not in servicos.chamadas


def test_estoque_fora_do_ar(repo):
    def handler(request):
        raise httpx.ConnectError("fora", request=request)

    with TestClient(create_app(repo=repo, transport=httpx.MockTransport(handler))) as c:
        r = c.post("/pedidos", json=PEDIDO)
    assert r.status_code == 503
    assert r.json()["status"] == "FALHA"


def test_consulta_pedido(repo):
    with cliente(repo, Servicos()) as c:
        pedido_id = c.post("/pedidos", json=PEDIDO).json()["id"]
        r = c.get(f"/pedidos/{pedido_id}")
        assert r.status_code == 200
        assert r.json()["itens"] == PEDIDO["itens"]
        assert c.get("/pedidos/nao-existe").status_code == 404


def test_validacao_de_entrada(repo):
    with cliente(repo, Servicos()) as c:
        assert c.post("/pedidos", json={"cliente_id": "x", "itens": []}).status_code == 422
        assert c.post("/pedidos", json={"cliente_id": "x", "itens": [{"sku": "A", "quantidade": 0}]}).status_code == 422
