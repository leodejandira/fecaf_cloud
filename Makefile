SERVICOS := gateway pedidos pagamentos estoque

.PHONY: up down logs test lint pedido

up:
	docker compose up -d --build

down:
	docker compose down -v

logs:
	docker compose logs -f gateway pedidos pagamentos estoque

lint:
	ruff check .
	ruff format --check .

test:
	@for s in $(SERVICOS); do (cd services/$$s && python -m pytest -q) || exit 1; done

pedido:
	curl -s -X POST localhost:8080/api/pedidos \
	  -H 'content-type: application/json' \
	  -d '{"cliente_id":"cli-1","itens":[{"sku":"TEN-002","quantidade":1},{"sku":"FON-005","quantidade":2}]}'
	@echo
