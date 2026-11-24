# Pedidos Veloz

MVP da plataforma de pedidos da Loja Veloz em microsserviços, com ambiente local em Docker Compose, deploy em Kubernetes, pipeline no GitHub Actions, observabilidade com OpenTelemetry/Prometheus/Jaeger e infraestrutura em Terraform.

Vídeo de apresentação: LINK_DO_VIDEO

## Arquitetura

```
                 +-----------+
  cliente ---->  |  gateway  |  :8080
                 +-----+-----+
                       |
           +-----------+-----------+
           |                       |
     +-----v-----+           +-----v-----+
     |  pedidos  +---------->+  estoque  |
     +-----+-----+           +-----+-----+
           |                       |
     +-----v------+          +-----v------+
     | pagamentos |          | PostgreSQL |  (bancos pedidos e estoque)
     +------------+          +------------+

  todos os serviços -> OTel Collector -> Jaeger (traces)
  Prometheus -> /metrics de cada serviço -> Grafana
```

| Serviço | Responsabilidade | Banco |
|---|---|---|
| gateway | ponto único de entrada HTTP, repassa para os serviços internos | - |
| pedidos | cria e consulta pedidos, orquestra reserva e pagamento | `pedidos` |
| estoque | catálogo, reserva, confirmação (baixa) e cancelamento | `estoque` |
| pagamentos | autorização junto ao provedor externo (simulado quando `PROVEDOR_URL` está vazio) | - |

Fluxo de um pedido: `pedidos` grava o pedido como `PENDENTE`, reserva os itens no `estoque`, pede autorização em `pagamentos` e, conforme a resposta, confirma ou cancela a reserva. Status finais: `CONFIRMADO`, `RECUSADO`, `REJEITADO` (sem estoque) e `FALHA`.

Stack: Python 3.12, FastAPI, PostgreSQL 16, OpenTelemetry, Prometheus, Jaeger, Grafana.

## Estrutura

```
services/            código, testes e Dockerfile de cada microsserviço
deploy/compose/      configs usadas pelo Compose (init do Postgres, collector, Prometheus, Grafana)
k8s/base/            manifests comuns (Deployments, Services, HPA, PDB, NetworkPolicy, Ingress, collector)
k8s/overlays/        staging e prod (Kustomize)
k8s/extras/          VPA em modo recomendação (precisa do VPA instalado no cluster)
infra/terraform/     módulo da plataforma (VPC, EKS, RDS) e ambientes staging/prod
scripts/deploy.sh    deploy com rollout status, rollback automático e smoke test
.github/workflows/   pipeline de CI/CD
```

## Rodando localmente

Requisitos: Docker com Compose v2.

```bash
docker compose up -d --build
```

Sobem os 4 serviços, o Postgres e a stack de observabilidade. As senhas têm valores padrão para desenvolvimento; para trocar, copie `.env.example` para `.env` antes de subir.

| Endereço | O que é |
|---|---|
| http://localhost:8080/api/produtos | API (gateway) |
| http://localhost:8080/docs | Swagger do gateway |
| http://localhost:16686 | Jaeger (traces) |
| http://localhost:9090 | Prometheus |
| http://localhost:3000 | Grafana, dashboard "Pedidos Veloz - Visao geral" |

Criando um pedido:

```bash
curl -X POST localhost:8080/api/pedidos \
  -H 'content-type: application/json' \
  -d '{"cliente_id":"cli-1","itens":[{"sku":"TEN-002","quantidade":1},{"sku":"FON-005","quantidade":2}]}'
```

Para ver os outros cenários: pedido acima de R$ 5.000 é recusado no pagamento (ex.: 25 unidades de `REL-004`) e quantidade maior que o disponível retorna 409.

Para derrubar tudo e apagar os volumes: `docker compose down -v`.

Redes do Compose: `frontend` (só o gateway publica porta), `backend` (comunicação entre serviços), `dados` (interna, só `pedidos`, `estoque` e Postgres) e `observabilidade`.

## Testes e lint

```bash
pip install ruff -r services/pedidos/requirements-dev.txt
ruff check . && ruff format --check .

cd services/pedidos && python -m pytest
```

Os testes do `estoque` rodam contra um Postgres real (inclusive um teste de reservas concorrentes). Localmente:

```bash
docker run -d --name pg-test -e POSTGRES_PASSWORD=teste -p 5433:5432 postgres:16-alpine
cd services/estoque
TEST_DATABASE_URL=postgresql://postgres:teste@localhost:5433/postgres python -m pytest
```

## Imagens

- Build multi-stage: dependências instaladas num venv no estágio de build, imagem final só com o venv e o código.
- Usuário não-root (UID 10001), sem cache do pip, `.dockerignore` tirando testes da imagem.
- Tags geradas no pipeline: `sha-<commit>` em todo push, `latest-main` na main e a versão semântica (`1.2.0`) quando há tag `v1.2.0`. Os manifests nunca usam `latest`.
- Registry: GitHub Container Registry (`ghcr.io/<owner>/pedidos-veloz-<serviço>`).

## Kubernetes

```bash
cp k8s/overlays/staging/secrets.env.example k8s/overlays/staging/secrets.env
cp k8s/overlays/staging/postgres.env.example k8s/overlays/staging/postgres.env
# editar as senhas
kubectl apply -k k8s/overlays/staging
```

O que está nos manifests:

- Namespace com Pod Security Admission em `restricted` (enforce, audit e warn).
- Pods sem root, `readOnlyRootFilesystem`, sem privilege escalation, capabilities removidas, seccomp `RuntimeDefault` e sem token de service account montado.
- `startupProbe`, `readinessProbe` (`/readyz`, verifica o banco) e `livenessProbe` (`/healthz`).
- Requests/limits definidos, `preStop` de 5s para o pod sair do Service antes de parar.
- ConfigMap gerado pelo Kustomize (com hash no nome, então mudar config gera rollout) e Secret com as URLs de banco e o token do provedor de pagamento.
- NetworkPolicy com bloqueio padrão de entrada e liberação só do necessário (gateway <- ingress, pedidos <- gateway, estoque <- gateway/pedidos, pagamentos <- pedidos, Postgres <- pedidos/estoque).
- HPA por CPU (70%) em todos os serviços e PodDisruptionBudget com `minAvailable: 1`.
- Rolling update com `maxUnavailable: 0` e `maxSurge: 1`.

Diferenças entre overlays: staging sobe Postgres e Jaeger dentro do cluster, 1 réplica mínima e 100% de amostragem de traces. Prod usa RDS, mínimo de 3 réplicas em gateway e pedidos, TLS no Ingress e amostragem por cauda (todos os erros, tudo acima de 800 ms e 10% do restante).

## CI/CD

Arquivo: `.github/workflows/ci-cd.yml`.

| Job | Quando | O que faz |
|---|---|---|
| lint | sempre | ruff (lint e formatação) e hadolint nos Dockerfiles |
| test | sempre | pytest de cada serviço (matriz), com Postgres como service container |
| manifests | sempre | `kustomize build` dos overlays e validação com kubeconform |
| terraform | sempre | `fmt -check` e `validate` |
| build | push na main ou tag | build, scan com Trivy (falha em HIGH/CRITICAL corrigível), push no GHCR com SBOM e provenance |
| deploy-staging | push na main | `scripts/deploy.sh staging sha-<commit>` |
| deploy-prod | tag `v*.*.*` | mesmo script no overlay prod, com aprovação manual do environment `production` |

Secrets configurados por environment no GitHub (Settings > Environments):

- `KUBECONFIG_B64`, `PEDIDOS_DATABASE_URL`, `ESTOQUE_DATABASE_URL`, `PROVEDOR_TOKEN` (staging e production)
- `POSTGRES_PASSWORD`, `PEDIDOS_DB_PASSWORD`, `ESTOQUE_DB_PASSWORD` (só staging)

Os jobs de deploy só rodam quando a variável de repositório `DEPLOY_ENABLED` está como `true` (Settings > Secrets and variables > Actions > Variables), para o pipeline não quebrar enquanto não existe cluster. O push para o GHCR usa o `GITHUB_TOKEN` do próprio workflow. No environment `production` é preciso marcar "Required reviewers" para ter o gate manual.

## Observabilidade

- Métricas: cada serviço expõe `/metrics` (requisições, latência em histograma, `pedidos_total` por status, `pagamentos_total`, `estoque_reservas_total`). Alertas de exemplo em `deploy/compose/prometheus/alertas.yml` (taxa de 5xx, p95 e serviço fora).
- Logs: JSON no stdout com `trace_id` e `span_id`, então dá para sair de uma linha de log para o trace no Jaeger.
- Traces: instrumentação OpenTelemetry de FastAPI, httpx e psycopg, com spans manuais em `reservar_estoque` e `autorizar_pagamento`. O contexto é propagado pelo header `traceparent` (W3C), e o trace mostra o caminho completo gateway > pedidos > estoque/pagamentos > SQL.

## Terraform

```bash
cd infra/terraform/envs/staging
terraform init
terraform plan
```

O módulo `plataforma` cria VPC com subnets privadas, públicas e de banco, cluster EKS com node group gerenciado e add-ons (incluindo metrics-server, necessário para o HPA, e network policy no VPC CNI), e RDS PostgreSQL criptografado com senha gerenciada no Secrets Manager. O state fica no S3 com lock. Staging usa NAT único e nodes spot; prod usa NAT por zona, RDS multi-AZ e proteção contra exclusão.

## Referência

A organização do projeto segue a linha do [OpenTelemetry Demo](https://github.com/open-telemetry/opentelemetry-demo) (Astronomy Shop) e do [Online Boutique](https://github.com/GoogleCloudPlatform/microservices-demo) do Google: e-commerce em microsserviços, Compose para desenvolvimento, manifests para Kubernetes e telemetria passando por um OpenTelemetry Collector.
