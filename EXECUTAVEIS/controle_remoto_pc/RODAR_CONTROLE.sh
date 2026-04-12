#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
chmod +x ./iniciar_controle || true

if [[ -z "${AGV_IP:-}" ]]; then
  echo "Defina o IP do AGV antes de rodar. Exemplo:"
  echo "  AGV_IP=192.168.0.50 AGV_PORT=5000 ./RODAR_CONTROLE.sh"
  exit 1
fi

AGV_PORT="${AGV_PORT:-5000}" ./iniciar_controle
