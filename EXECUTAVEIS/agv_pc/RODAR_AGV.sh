#!/usr/bin/env bash
# ─── AGV — Script de Inicialização ───────────────────────────────────────────
# Sempre mata processos antigos e sempre inicia o código-fonte atualizado.
# Não usa o executável congelado (frozen) para evitar comportamento desatualizado.

set -e

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
SERVER_PY="$ROOT_DIR/pc_agv/iniciar_sistema.py"
VENV_SP="/home/kauan-linux/Downloads/Detecta_rosto/.venv/lib/python3.12/site-packages"
LOG_FILE="/tmp/agv_server.log"

# ── Verifica que o código-fonte existe ────────────────────────────────────────
if [[ ! -f "$SERVER_PY" ]]; then
    echo "ERRO: não encontrei $SERVER_PY"
    exit 1
fi

# ── Mata qualquer servidor AGV antigo (frozen ou source) ─────────────────────
echo "[AGV] Encerrando processos antigos..."
pkill -9 -f "iniciar_sistema" 2>/dev/null || true
sleep 1

# ── Variáveis de ambiente ─────────────────────────────────────────────────────
export PYTHONPATH="$VENV_SP"
export AGV_HOST="0.0.0.0"
export AGV_PORT="5000"
export AGV_FACE_MATCH_THRESHOLD="0.40"
export AGV_FACE_MATCH_THRESHOLD_SINGLE_SAMPLE="0.32"
export AGV_FACE_MATCH_THRESHOLD_SELECTED_ONLY="0.50"
export AGV_FACE_AMBIGUOUS_MARGIN="0.06"
export AGV_KINECT_STALE_TIMEOUT="4.0"
export AGV_KINECT_RESTART_COOLDOWN="6.0"

# Tenta remover drivers do kernel que costumam bloquear o Kinect v1.
# Se sudo sem senha não estiver liberado, o servidor ainda sobe, mas o Kinect pode ficar indisponível.
sudo -n modprobe -r uvcvideo gspca_kinect gspca_main snd_usb_audio 2>/dev/null || true

# ── Inicia o servidor em background ──────────────────────────────────────────
echo "[AGV] Iniciando servidor (código-fonte)..."
cd "$ROOT_DIR"
python3 "$SERVER_PY" >> "$LOG_FILE" 2>&1 &
SERVER_PID=$!
echo "[AGV] PID=$SERVER_PID  log=$LOG_FILE"

# ── Aguarda o servidor responder ─────────────────────────────────────────────
echo -n "[AGV] Aguardando servidor"
for i in $(seq 1 30); do
    sleep 1
    echo -n "."
    if ! kill -0 $SERVER_PID 2>/dev/null; then
        echo ""
        echo "ERRO: servidor encerrou inesperadamente. Veja: tail -30 $LOG_FILE"
        exit 1
    fi
    if python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/api/status', timeout=1)" 2>/dev/null; then
        echo ""
        echo "[AGV] Servidor online! http://127.0.0.1:5000"
        # Abre o navegador se disponível
        xdg-open "http://127.0.0.1:5000" 2>/dev/null || true
        exit 0
    fi
done

echo ""
echo "AVISO: servidor não respondeu em 30s. Verifique: tail -50 $LOG_FILE"

