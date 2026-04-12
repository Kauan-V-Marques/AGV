#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
chmod +x ./ver_site_teste || true

if [[ ! -f ./iniciar_sistema ]]; then
	echo "ERRO: faltou o arquivo 'iniciar_sistema' nesta pasta."
	echo "Copie de: ../agv_pc/iniciar_sistema"
	exit 1
fi

chmod +x ./iniciar_sistema || true
./ver_site_teste
