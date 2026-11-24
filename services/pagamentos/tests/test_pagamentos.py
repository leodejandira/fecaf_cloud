import httpx
from fastapi.testclient import TestClient

from app.main import Config, create_app


def config(**kw):
    c = Config()
    c.latencia_ms = 0
    c.limite = 1000
    c.provedor_url = ""
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def test_aprova_dentro_do_limite():
    with TestClient(create_app(config())) as c:
        r = c.post("/pagamentos", json={"pedido_id": "p1", "valor": "199.90"})
    assert r.status_code == 200
    assert r.json()["status"] == "APROVADO"
    assert r.json()["autorizacao"]


def test_recusa_acima_do_limite():
    with TestClient(create_app(config())) as c:
        r = c.post("/pagamentos", json={"pedido_id": "p2", "valor": "1500.00"})
    assert r.json()["status"] == "RECUSADO"


def test_valida_valor():
    with TestClient(create_app(config())) as c:
        assert c.post("/pagamentos", json={"pedido_id": "p3", "valor": "-1"}).status_code == 422
        assert c.post("/pagamentos", json={"pedido_id": "", "valor": "10"}).status_code == 422


def test_usa_provedor_externo_com_token():
    vistos = {}

    def handler(request: httpx.Request):
        vistos["auth"] = request.headers["authorization"]
        return httpx.Response(200, json={"aprovado": True, "codigo": "XYZ"})

    cfg = config(provedor_url="https://psp.exemplo", provedor_token="segredo")
    with TestClient(create_app(cfg, transport=httpx.MockTransport(handler))) as c:
        r = c.post("/pagamentos", json={"pedido_id": "p4", "valor": "50"})
    assert r.json()["autorizacao"] == "XYZ"
    assert vistos["auth"] == "Bearer segredo"


def test_provedor_fora_do_ar_retorna_502():
    def handler(request):
        raise httpx.ConnectError("fora", request=request)

    cfg = config(provedor_url="https://psp.exemplo")
    with TestClient(create_app(cfg, transport=httpx.MockTransport(handler))) as c:
        r = c.post("/pagamentos", json={"pedido_id": "p5", "valor": "50"})
    assert r.status_code == 502
