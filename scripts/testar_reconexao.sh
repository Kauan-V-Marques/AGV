#!/usr/bin/env bash
# Teste de campo: simula tirar e recolocar o cabo USB de cada equipamento com o servidor
# rodando e mede em quanto tempo o painel acusa a falha e o sistema volta sozinho.
#
# Uso (servidor rodando, AGV parado):  sudo -v && bash scripts/testar_reconexao.sh
# O Arduino so recebe 0,0 (modo manual + watchdog) durante o teste.
set -uo pipefail

API="${AGV_API:-http://127.0.0.1:5000}/api/status"

online() {
  curl -s -m 2 "$API" | python3 -c "import json,sys; d=json.load(sys.stdin); print(bool($1))" 2>/dev/null
}

porta_usb() {  # caminho em /sys/bus/usb/devices do primeiro dispositivo com este vendor:product
  for d in /sys/bus/usb/devices/*; do
    [[ -f "$d/idVendor" ]] || continue
    [[ "$(cat "$d/idVendor"):$(cat "$d/idProduct")" == "$1" ]] && { basename "$d"; return; }
  done
}

testar() {  # nome, vendor:product, expressao sobre o status d
  local nome=$1 id=$2 expr=$3 dev t0
  dev=$(porta_usb "$id")
  if [[ -z "$dev" ]]; then echo "$nome: nao conectado ($id)"; return; fi
  if [[ "$(online "$expr")" != "True" ]]; then echo "$nome: offline antes do teste, pulando"; return; fi
  echo "$dev" | sudo tee /sys/bus/usb/drivers/usb/unbind >/dev/null
  t0=$(date +%s.%N)
  for _ in $(seq 1 40); do [[ "$(online "$expr")" == "False" ]] && break; sleep 0.25; done
  local caiu; caiu=$(echo "$(date +%s.%N) - $t0" | bc)
  sleep 3
  echo "$dev" | sudo tee /sys/bus/usb/drivers/usb/bind >/dev/null 2>&1
  t0=$(date +%s.%N); local voltou=nao
  for _ in $(seq 1 160); do [[ "$(online "$expr")" == "True" ]] && { voltou=sim; break; }; sleep 0.25; done
  printf "%-8s painel acusou em %4.1f s | voltou: %s em %4.1f s\n" "$nome" "$caiu" "$voltou" "$(echo "$(date +%s.%N) - $t0" | bc)"
}

testar "RPLIDAR" "10c4:ea60" "d['lidar']['online']"
testar "Arduino" "0403:6001" "d['arduino']['connected']"
testar "Arduino" "2341:0043" "d['arduino']['connected']"
testar "Webcam"  "0bda:58c2" "d['zed']['online']"
testar "Kinect"  "045e:02c2" "d['state']['kinect_ok'] and (d['state']['frame_age_s'] or 9) < 1"
