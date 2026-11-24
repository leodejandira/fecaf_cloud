import httpx
from fastapi.testclient import TestClient

from app.main import create_app


def cliente(handler):
    app = create_app(transport=httpx.MockTransport(handler))
    return TestClient(app)


def test_healthz():
    with cliente(lambda r: httpx.Response(200)) as c:
        assert c.get("/healthz").json() == {"status": "ok"}


def test_repassa_criacao_de_pedido():
    recebido = {}

    def handler(request: httpx.Request):
        recebido["url"] = str(request.url)
        recebido["body"] = request.content
        return httpx.Response(201, json={"id": "abc", "status": "CONFIRMADO"})

    with cliente(handler) as c:
        r = c.post("/api/pedidos", json={"cliente_id": "c1", "itens": [{"sku": "A", "quantidade": 1}]})

    assert r.status_code == 201
    assert r.json()["status"] == "CONFIRMADO"
    assert recebido["url"].endswith("/pedidos")
    assert b'"cliente_id"' in recebido["body"]


def test_mantem_status_de_erro_do_servico():
    with cliente(lambda r: httpx.Response(404, json={"detail": "nao encontrado"})) as c:
        r = c.get("/api/pedidos/inexistente")
    assert r.status_code == 404


def test_retorna_502_quando_servico_cai():
    def handler(request):
        raise httpx.ConnectError("recusado", request=request)

    with cliente(handler) as c:
        r = c.get("/api/produtos")
    assert r.status_code == 502


def test_retorna_504_em_timeout():
    def handler(request):
        raise httpx.ReadTimeout("lento", request=request)

    with cliente(handler) as c:
        r = c.get("/api/produtos/CAM-001")
    assert r.status_code == 504


def test_expoe_metricas():
    with cliente(lambda r: httpx.Response(200, json=[])) as c:
        c.get("/api/produtos")
        body = c.get("/metrics").text
    assert 'route="/api/produtos"' in body
