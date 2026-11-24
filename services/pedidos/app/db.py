from typing import Protocol

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

SCHEMA = """
CREATE TABLE IF NOT EXISTS pedidos (
    id            VARCHAR(64) PRIMARY KEY,
    cliente_id    VARCHAR(64) NOT NULL,
    itens         JSONB NOT NULL,
    status        VARCHAR(20) NOT NULL,
    valor_total   NUMERIC(12, 2),
    motivo        VARCHAR(200),
    criado_em     TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_pedidos_cliente ON pedidos (cliente_id);
"""


class Repositorio(Protocol):
    def migrar(self) -> None: ...
    def ping(self) -> bool: ...
    def inserir(self, pedido_id: str, cliente_id: str, itens: list[dict]) -> dict: ...
    def atualizar(self, pedido_id: str, status: str, valor_total=None, motivo=None) -> dict: ...
    def buscar(self, pedido_id: str) -> dict | None: ...
    def close(self) -> None: ...


class Database:
    def __init__(self, url: str, max_size: int = 10):
        self.pool = ConnectionPool(url, min_size=1, max_size=max_size, kwargs={"row_factory": dict_row})

    def close(self) -> None:
        self.pool.close()

    def ping(self) -> bool:
        with self.pool.connection(timeout=2) as conn:
            conn.execute("SELECT 1")
        return True

    def migrar(self) -> None:
        with self.pool.connection() as conn:
            conn.execute(SCHEMA)

    def inserir(self, pedido_id: str, cliente_id: str, itens: list[dict]) -> dict:
        with self.pool.connection() as conn:
            return conn.execute(
                "INSERT INTO pedidos (id, cliente_id, itens, status) VALUES (%s, %s, %s, 'PENDENTE') "
                "RETURNING id, cliente_id, itens, status, valor_total, motivo, criado_em, atualizado_em",
                (pedido_id, cliente_id, Jsonb(itens)),
            ).fetchone()

    def atualizar(self, pedido_id: str, status: str, valor_total=None, motivo=None) -> dict:
        with self.pool.connection() as conn:
            return conn.execute(
                "UPDATE pedidos SET status = %s, valor_total = COALESCE(%s, valor_total), motivo = %s, "
                "atualizado_em = now() WHERE id = %s "
                "RETURNING id, cliente_id, itens, status, valor_total, motivo, criado_em, atualizado_em",
                (status, valor_total, motivo, pedido_id),
            ).fetchone()

    def buscar(self, pedido_id: str) -> dict | None:
        with self.pool.connection() as conn:
            return conn.execute(
                "SELECT id, cliente_id, itens, status, valor_total, motivo, criado_em, atualizado_em "
                "FROM pedidos WHERE id = %s",
                (pedido_id,),
            ).fetchone()
