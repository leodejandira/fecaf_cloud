import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.db import Database
from app.main import create_app

URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL nao definida")


@pytest.fixture
def db():
    database = Database(URL, max_size=5)
    with database.pool.connection() as conn:
        conn.execute("DROP TABLE IF EXISTS reservas, produtos")
    yield database
    database.close()


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setenv("SEED_DATA", "true")
    with TestClient(create_app(db)) as c:
        yield c


def disponivel(client, sku):
    return client.get(f"/produtos/{sku}").json()["disponivel"]


def test_lista_produtos_iniciais(client):
    produtos = client.get("/produtos").json()
    assert len(produtos) == 5
    assert produtos[0]["sku"] == "CAM-001"


def test_produto_inexistente(client):
    assert client.get("/produtos/XXX").status_code == 404


def test_reserva_calcula_total_e_bloqueia_quantidade(client):
    r = client.post(
        "/reservas",
        json={"pedido_id": "p1", "itens": [{"sku": "CAM-001", "quantidade": 2}, {"sku": "FON-005", "quantidade": 1}]},
    )
    assert r.status_code == 201
    assert r.json()["valor_total"] == "249.70"
    assert disponivel(client, "CAM-001") == 198


def test_reserva_repetida_e_idempotente(client):
    corpo = {"pedido_id": "p1", "itens": [{"sku": "MOC-003", "quantidade": 5}]}
    client.post("/reservas", json=corpo)
    client.post("/reservas", json=corpo)
    assert disponivel(client, "MOC-003") == 45


def test_sem_estoque_retorna_409_e_nao_reserva_nada(client):
    r = client.post(
        "/reservas",
        json={"pedido_id": "p2", "itens": [{"sku": "CAM-001", "quantidade": 1}, {"sku": "REL-004", "quantidade": 31}]},
    )
    assert r.status_code == 409
    assert disponivel(client, "CAM-001") == 200


def test_confirmar_baixa_estoque(client, db):
    client.post("/reservas", json={"pedido_id": "p3", "itens": [{"sku": "TEN-002", "quantidade": 3}]})
    assert client.post("/reservas/p3/confirmar").status_code == 200
    with db.pool.connection() as conn:
        linha = conn.execute("SELECT quantidade, reservado FROM produtos WHERE sku = 'TEN-002'").fetchone()
    assert linha == {"quantidade": 77, "reservado": 0}


def test_cancelar_devolve_reserva(client):
    client.post("/reservas", json={"pedido_id": "p4", "itens": [{"sku": "TEN-002", "quantidade": 3}]})
    client.post("/reservas/p4/cancelar")
    assert disponivel(client, "TEN-002") == 80


def test_cancelar_reserva_inexistente(client):
    assert client.post("/reservas/nao-existe/cancelar").status_code == 404


def test_reservas_concorrentes_nao_vendem_alem_do_estoque(client):
    def reservar(i):
        return client.post(
            "/reservas", json={"pedido_id": f"c{i}", "itens": [{"sku": "REL-004", "quantidade": 4}]}
        ).status_code

    with ThreadPoolExecutor(max_workers=5) as ex:
        status = list(ex.map(reservar, range(10)))

    assert status.count(201) == 7
    assert status.count(409) == 3
    assert disponivel(client, "REL-004") == 2
