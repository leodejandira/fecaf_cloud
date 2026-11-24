from decimal import Decimal

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

SCHEMA = """
CREATE TABLE IF NOT EXISTS produtos (
    sku         VARCHAR(40) PRIMARY KEY,
    nome        VARCHAR(120) NOT NULL,
    preco       NUMERIC(10, 2) NOT NULL CHECK (preco >= 0),
    quantidade  INTEGER NOT NULL CHECK (quantidade >= 0),
    reservado   INTEGER NOT NULL DEFAULT 0 CHECK (reservado >= 0)
);

CREATE TABLE IF NOT EXISTS reservas (
    pedido_id   VARCHAR(64) NOT NULL,
    sku         VARCHAR(40) NOT NULL REFERENCES produtos (sku),
    quantidade  INTEGER NOT NULL CHECK (quantidade > 0),
    preco       NUMERIC(10, 2) NOT NULL,
    status      VARCHAR(20) NOT NULL DEFAULT 'RESERVADO',
    criado_em   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (pedido_id, sku)
);
"""

PRODUTOS_INICIAIS = [
    ("CAM-001", "Camiseta basica", Decimal("59.90"), 200),
    ("TEN-002", "Tenis corrida", Decimal("349.90"), 80),
    ("MOC-003", "Mochila notebook", Decimal("189.00"), 50),
    ("REL-004", "Relogio digital", Decimal("249.00"), 30),
    ("FON-005", "Fone bluetooth", Decimal("129.90"), 120),
]


class EstoqueInsuficiente(Exception):
    def __init__(self, sku: str):
        super().__init__(sku)
        self.sku = sku


class ProdutoInexistente(Exception):
    def __init__(self, sku: str):
        super().__init__(sku)
        self.sku = sku


class ReservaInexistente(Exception):
    pass


class Database:
    def __init__(self, url: str, min_size: int = 1, max_size: int = 10):
        self.pool = ConnectionPool(
            url, min_size=min_size, max_size=max_size, kwargs={"row_factory": dict_row}, open=True
        )

    def close(self) -> None:
        self.pool.close()

    def ping(self) -> bool:
        with self.pool.connection(timeout=2) as conn:
            conn.execute("SELECT 1")
        return True

    def migrar(self, seed: bool = False) -> None:
        with self.pool.connection() as conn:
            conn.execute(SCHEMA)
            if seed:
                vazio = conn.execute("SELECT count(*) AS n FROM produtos").fetchone()["n"] == 0
                if vazio:
                    conn.cursor().executemany(
                        "INSERT INTO produtos (sku, nome, preco, quantidade) VALUES (%s, %s, %s, %s)",
                        PRODUTOS_INICIAIS,
                    )

    def listar(self) -> list[dict]:
        with self.pool.connection() as conn:
            return conn.execute(
                "SELECT sku, nome, preco, quantidade - reservado AS disponivel FROM produtos ORDER BY sku"
            ).fetchall()

    def buscar(self, sku: str) -> dict | None:
        with self.pool.connection() as conn:
            return conn.execute(
                "SELECT sku, nome, preco, quantidade - reservado AS disponivel FROM produtos WHERE sku = %s",
                (sku,),
            ).fetchone()

    def reservar(self, pedido_id: str, itens: list[tuple[str, int]]) -> dict:
        agrupado: dict[str, int] = {}
        for sku, qtd in itens:
            agrupado[sku] = agrupado.get(sku, 0) + qtd

        with self.pool.connection() as conn, conn.transaction():
            existentes = conn.execute(
                "SELECT sku, quantidade, preco FROM reservas WHERE pedido_id = %s AND status = 'RESERVADO'",
                (pedido_id,),
            ).fetchall()
            if existentes:
                return self._resumo(pedido_id, existentes)

            reservados = []
            for sku in sorted(agrupado):
                qtd = agrupado[sku]
                produto = conn.execute(
                    "SELECT preco, quantidade - reservado AS disponivel FROM produtos WHERE sku = %s FOR UPDATE",
                    (sku,),
                ).fetchone()
                if produto is None:
                    raise ProdutoInexistente(sku)
                if produto["disponivel"] < qtd:
                    raise EstoqueInsuficiente(sku)
                conn.execute("UPDATE produtos SET reservado = reservado + %s WHERE sku = %s", (qtd, sku))
                conn.execute(
                    "INSERT INTO reservas (pedido_id, sku, quantidade, preco) VALUES (%s, %s, %s, %s)",
                    (pedido_id, sku, qtd, produto["preco"]),
                )
                reservados.append({"sku": sku, "quantidade": qtd, "preco": produto["preco"]})
            return self._resumo(pedido_id, reservados)

    def confirmar(self, pedido_id: str) -> None:
        self._finalizar(pedido_id, "CONFIRMADO", baixar=True)

    def cancelar(self, pedido_id: str) -> None:
        self._finalizar(pedido_id, "CANCELADO", baixar=False)

    def _finalizar(self, pedido_id: str, novo_status: str, baixar: bool) -> None:
        with self.pool.connection() as conn, conn.transaction():
            reservas = conn.execute(
                "SELECT sku, quantidade FROM reservas WHERE pedido_id = %s AND status = 'RESERVADO' "
                "ORDER BY sku FOR UPDATE",
                (pedido_id,),
            ).fetchall()
            if not reservas:
                total = conn.execute(
                    "SELECT count(*) AS n FROM reservas WHERE pedido_id = %s", (pedido_id,)
                ).fetchone()["n"]
                if total == 0:
                    raise ReservaInexistente(pedido_id)
                return
            for r in reservas:
                if baixar:
                    conn.execute(
                        "UPDATE produtos SET quantidade = quantidade - %(q)s, reservado = reservado - %(q)s "
                        "WHERE sku = %(sku)s",
                        {"q": r["quantidade"], "sku": r["sku"]},
                    )
                else:
                    conn.execute(
                        "UPDATE produtos SET reservado = reservado - %(q)s WHERE sku = %(sku)s",
                        {"q": r["quantidade"], "sku": r["sku"]},
                    )
            conn.execute(
                "UPDATE reservas SET status = %s WHERE pedido_id = %s AND status = 'RESERVADO'",
                (novo_status, pedido_id),
            )

    @staticmethod
    def _resumo(pedido_id: str, itens: list[dict]) -> dict:
        total = sum(Decimal(i["preco"]) * i["quantidade"] for i in itens)
        return {
            "pedido_id": pedido_id,
            "itens": [{"sku": i["sku"], "quantidade": i["quantidade"], "preco": str(i["preco"])} for i in itens],
            "valor_total": str(total.quantize(Decimal("0.01"))),
        }
