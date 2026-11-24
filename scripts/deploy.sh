#!/usr/bin/env bash
set -euo pipefail

OVERLAY="${1:?uso: deploy.sh <staging|prod> <tag>}"
TAG="${2:?informe a tag da imagem}"
REGISTRY="${REGISTRY:?defina REGISTRY, ex: ghcr.io/usuario}"
NS=pedidos-veloz
SERVICOS=(gateway pedidos pagamentos estoque)

reverter() {
  echo "::error::$1 - voltando para a revisao anterior"
  for s in "${SERVICOS[@]}"; do
    kubectl -n "$NS" rollout undo "deployment/$s" || true
  done
  exit 1
}

smoke_test() {
  kubectl -n "$NS" port-forward svc/gateway 18080:8000 >/dev/null 2>&1 &
  local pf=$!
  sleep 3
  local ok=0
  curl -fsS --retry 5 --retry-delay 2 http://127.0.0.1:18080/api/produtos >/dev/null || ok=1
  kill "$pf" 2>/dev/null || true
  return $ok
}

cd "$(dirname "$0")/../k8s/overlays/$OVERLAY"

for s in "${SERVICOS[@]}"; do
  kustomize edit set image "pedidos-veloz/$s=$REGISTRY/pedidos-veloz-$s:$TAG"
done

kustomize build . | kubectl apply -f -

for s in "${SERVICOS[@]}"; do
  kubectl -n "$NS" rollout status "deployment/$s" --timeout=180s || reverter "rollout de $s nao concluiu"
done

smoke_test || reverter "smoke test falhou"
echo "deploy de $TAG em $OVERLAY concluido"
