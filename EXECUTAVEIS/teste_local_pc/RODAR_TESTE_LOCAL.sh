#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

ROOT_DIR="$(cd ../.. && pwd)"
VENV_PY="/home/kauan-linux/Downloads/Detecta_rosto/.venv/bin/python3"

if [[ -f "$ROOT_DIR/ver_site_teste.py" ]]; then
	echo "Iniciando teste local pelo codigo-fonte atual: $ROOT_DIR/ver_site_teste.py"
	if [[ -x "$VENV_PY" ]]; then
		exec "$VENV_PY" "$ROOT_DIR/ver_site_teste.py"
	fi
	exec python3 "$ROOT_DIR/ver_site_teste.py"
fi

chmod +x ./ver_site_teste || true

if [[ ! -f ./iniciar_sistema ]]; then
	echo "ERRO: faltou o arquivo 'iniciar_sistema' nesta pasta."
	echo "Copie de: ../agv_pc/iniciar_sistema"
	exit 1
fi

chmod +x ./iniciar_sistema || true
exec ./ver_site_teste
