"""Configuracao do agv: logging e variaveis de ambiente AGV_*."""

import logging
import os
import sys


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("agv")
logging.getLogger("werkzeug").setLevel(logging.WARNING)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_HOST = os.environ.get("AGV_HOST", "0.0.0.0").strip() or "0.0.0.0"
DEFAULT_PORT = int(os.environ.get("AGV_PORT", "5000"))
CONTROL_LOOP_SECONDS = float(os.environ.get("AGV_CONTROL_LOOP_SECONDS", "0.12"))
AUTO_LOOP_SECONDS = float(os.environ.get("AGV_AUTO_LOOP_SECONDS", "0.18"))
# Watchdog: para o agv se nenhum comando manual chegar neste intervalo (0 = desativado)
WATCHDOG_TIMEOUT_S = float(os.environ.get("AGV_WATCHDOG_TIMEOUT_S", "2.0"))
# FPS dos streams MJPEG — reduzido para funcionar bem com sinal fraco.
VIDEO_FPS = int(os.environ.get("AGV_VIDEO_FPS", "6"))
DEPTH_FPS = int(os.environ.get("AGV_DEPTH_FPS", "4"))
ZED_FPS = int(os.environ.get("AGV_ZED_FPS", "60"))
ZED_WIDTH = int(os.environ.get("AGV_ZED_WIDTH", "640"))
ZED_HEIGHT = int(os.environ.get("AGV_ZED_HEIGHT", "360"))
ZED_STREAM_MAX_WIDTH = int(os.environ.get("AGV_ZED_STREAM_MAX_WIDTH", "960"))
ZED_JPEG_QUALITY = int(os.environ.get("AGV_ZED_JPEG_QUALITY", "60"))
ZED_DEVICE = os.environ.get("AGV_ZED_DEVICE", "/dev/video0").strip() or "/dev/video0"
ZED_NAME_HINT = os.environ.get("AGV_ZED_NAME_HINT", "zed").strip().lower() or "zed"
ZED_STRICT_DEVICE = os.environ.get("AGV_ZED_STRICT_DEVICE", "1").strip() != "0"
# Sem ZED conectada, usa qualquer outra webcam (ex.: a integrada do notebook) no lugar dela.
ZED_WEBCAM_GENERICA = os.environ.get("AGV_ZED_WEBCAM_GENERICA", "1").strip() != "0"
KINECT_RELEASE_UVCVIDEO = os.environ.get("AGV_KINECT_RELEASE_UVCVIDEO", "0").strip() == "1"
MIN_FORWARD_PWM = int(os.environ.get("AGV_MIN_FORWARD_PWM", "170"))
MIN_TURN_PWM = int(os.environ.get("AGV_MIN_TURN_PWM", "140"))
KINECT_STABILIZATION_ENABLED = os.environ.get("AGV_KINECT_STABILIZATION", "1").strip() == "1"
KINECT_TILT_MIN = float(os.environ.get("AGV_KINECT_TILT_MIN", "-18"))
KINECT_TILT_MAX = float(os.environ.get("AGV_KINECT_TILT_MAX", "18"))
KINECT_TILT_INTERVAL = float(os.environ.get("AGV_KINECT_TILT_INTERVAL", "0.5"))
KINECT_TILT_DEADBAND = float(os.environ.get("AGV_KINECT_TILT_DEADBAND", "0.6"))
KINECT_TILT_NEUTRAL = float(os.environ.get("AGV_KINECT_TILT_NEUTRAL", "0"))
KINECT_TILT_GAIN = float(os.environ.get("AGV_KINECT_TILT_GAIN", "1.20"))
KINECT_TILT_SMOOTHING = float(os.environ.get("AGV_KINECT_TILT_SMOOTHING", "0.45"))
KINECT_TILT_MANUAL_HOLD = float(os.environ.get("AGV_KINECT_TILT_MANUAL_HOLD", "2.5"))
KINECT_TILT_PITCH_DEADBAND = float(os.environ.get("AGV_KINECT_TILT_PITCH_DEADBAND", "0.8"))
KINECT_TILT_MAX_STEP = float(os.environ.get("AGV_KINECT_TILT_MAX_STEP", "2.5"))
FACE_SCAN_INTERVAL = float(os.environ.get("AGV_FACE_SCAN_INTERVAL", "0.7"))
FACE_MATCH_THRESHOLD = float(os.environ.get("AGV_FACE_MATCH_THRESHOLD", "0.665"))
FACE_IMPOSTOR_MARGIN = float(os.environ.get("AGV_FACE_IMPOSTOR_MARGIN", "0.05"))
FACE_SELECTED_MAX_DISTANCE = float(os.environ.get("AGV_FACE_SELECTED_MAX_DISTANCE", "0.52"))
FACE_DETECT_MODEL = os.environ.get("AGV_FACE_DETECT_MODEL", "hog").strip().lower() or "hog"
FACE_SCAN_MAX_WIDTH = int(os.environ.get("AGV_FACE_SCAN_MAX_WIDTH", "640"))


def _pasta_de_dados() -> str:
    # O executavel do PyInstaller (onefile) roda numa pasta temporaria que e apagada ao
    # fechar: nele os dados ficam em logs/ ao lado do executavel.
    definida = os.environ.get("AGV_DADOS_DIR", "").strip()
    if definida:
        return os.path.abspath(definida)
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "logs")
    return os.path.join(PROJECT_ROOT, "logs")


# Dados gerados em execucao (rostos e aprendizado). Fora do Git.
DADOS_DIR = _pasta_de_dados()
FACE_DB_DIR = DADOS_DIR
FACE_UPLOADS_DIR = os.path.join(FACE_DB_DIR, "faces")
ENCODINGS_FILE = os.path.join(FACE_DB_DIR, "encodings.pkl")
# Auto-aprendizado (pc_agv/aprendizado): banco, ritmo da gravacao e treino automatico.
IA_DB_FILE = os.path.join(DADOS_DIR, "aprendizado.db")
IA_AMOSTRAS_HZ = float(os.environ.get("AGV_IA_AMOSTRAS_HZ", "5"))
IA_MAX_AMOSTRAS = int(os.environ.get("AGV_IA_MAX_AMOSTRAS", "150000"))
IA_MIN_AMOSTRAS = int(os.environ.get("AGV_IA_MIN_AMOSTRAS", "150"))
# Treina sozinho a cada N amostras novas, com o AGV parado (0 = so pelo botao).
IA_TREINO_AUTOMATICO = int(os.environ.get("AGV_IA_TREINO_AUTOMATICO", "400"))
# No modo IA, um comando do operador vale como correcao por este tempo.
IA_CORRECAO_JANELA_S = float(os.environ.get("AGV_IA_CORRECAO_JANELA_S", "1.0"))
# Auto-mapeamento (pc_agv/mapeamento). Parametros intrinsecos da camera de profundidade
# do Kinect v1 em 640x480 (calibracao de referencia de N. Burrus); ajuste se calibrar o seu.
KINECT_DEPTH_FX = float(os.environ.get("AGV_KINECT_DEPTH_FX", "594.21"))
KINECT_DEPTH_CX = float(os.environ.get("AGV_KINECT_DEPTH_CX", "339.31"))
# Distancia (m) do eixo das rodas ate o Kinect, para a frente. A odometria usa para saber
# que, girando no lugar, quem fica parado e o eixo e nao o Kinect.
KINECT_AVANCO_M = float(os.environ.get("AGV_KINECT_AVANCO_M", "0"))
MAPA_DB_FILE = os.path.join(DADOS_DIR, "mapas.db")
MAPA_HZ = float(os.environ.get("AGV_MAPA_HZ", "4"))
MAPA_RESOLUCAO_M = float(os.environ.get("AGV_MAPA_RESOLUCAO_M", "0.05"))
MAPA_TAMANHO_M = float(os.environ.get("AGV_MAPA_TAMANHO_M", "30"))
# Tamanho maximo de upload (foto de rosto); acima disso a API responde 413.
MAX_UPLOAD_MB = float(os.environ.get("AGV_MAX_UPLOAD_MB", "10"))
