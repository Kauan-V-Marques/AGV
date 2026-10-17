"""RPLIDAR (Slamtec A1/A2/A3): conexao serial, varredura 360 graus e estado do laser.

Usa so o protocolo serial publicado pela Slamtec (pyserial, sem SDK):
  pedido  = A5 <cmd> [<tamanho> <dados> <checksum>]
  resposta = A5 5A <tamanho 30 bits + modo 2 bits> <tipo> <dados>
Na varredura padrao (SCAN, 0x20) cada amostra tem 5 bytes:
  b0 = qualidade << 2 | !S << 1 | S   (S = 1 na primeira amostra de cada volta)
  b1 = angulo_q6[6:0] << 1 | 1        (o bit 0 e sempre 1: serve de verificacao)
  b2 = angulo_q6[14:7]
  b3, b4 = distancia_q2               (mm * 4; 0 = sem leitura)

O motor do A1 liga pelo DTR do adaptador USB (DTR baixo = girando); o A2/A3 pelo
comando de PWM. Os dois sao enviados, entao o mesmo codigo serve aos tres.

A thread agv-lidar junta as amostras de cada volta completa e publica em
estado.lidar_state os pontos no referencial do AGV e as folgas por setor.
"""

import os
import time
from typing import Optional

import numpy as np

from pc_agv.config import (
    AGV_FRENTE_M,
    AGV_LARGURA_M,
    AGV_TRASEIRA_M,
    LIDAR_BAUD,
    LIDAR_ENABLED,
    LIDAR_FRENTE_GRAUS,
    LIDAR_MASCARA,
    LIDAR_MAX_M,
    LIDAR_MIN_M,
    LIDAR_MOTOR_PWM,
    LIDAR_PORT,
    LIDAR_VELHO_S,
    LIDAR_X_M,
    LIDAR_Y_M,
    log,
)
from pc_agv import estado
from pc_agv import util
from pc_agv.visao import lidar as geometria


SYNC = 0xA5
CMD_STOP = 0x25
CMD_RESET = 0x40
CMD_SCAN = 0x20
CMD_INFO = 0x50
CMD_SAUDE = 0x52
CMD_PWM = 0xF0

PORTA_UDEV = "/dev/rplidar"            # regra scripts/99-agv-serial.rules
USB_CP210X = (0x10C4, 0xEA60)          # adaptador USB-serial que acompanha o RPLIDAR
MIN_PONTOS_VOLTA = 20                  # volta com menos pontos que isso e descartada
MAX_AMOSTRAS_VOLTA = 4000              # sem marca de inicio de volta: publica assim mesmo
SEM_DADOS_S = 2.0                      # sem bytes por mais que isso: reconecta
GIRO_INICIAL_S = 1.0                   # o motor precisa estabilizar antes da varredura
SAUDE = {0: "ok", 1: "aviso", 2: "erro"}

MONTAGEM = geometria.Montagem(
    frente_graus=LIDAR_FRENTE_GRAUS,
    x_m=LIDAR_X_M,
    y_m=LIDAR_Y_M,
    min_m=LIDAR_MIN_M,
    max_m=LIDAR_MAX_M,
    mascara=geometria.ler_mascara(LIDAR_MASCARA),
)
CORPO = geometria.Corpo(frente_m=AGV_FRENTE_M, tras_m=AGV_TRASEIRA_M, largura_m=AGV_LARGURA_M)


# ── Protocolo (funcoes puras) ───────────────────────────────────────
def comando(cmd: int, dados: bytes = b"") -> bytes:
    if not dados:
        return bytes([SYNC, cmd])
    pacote = bytes([SYNC, cmd, len(dados)]) + bytes(dados)
    verificacao = 0
    for byte in pacote:
        verificacao ^= byte
    return pacote + bytes([verificacao])


def comando_pwm(pwm: int) -> bytes:
    pwm = util.clamp(int(pwm), 0, 1023)
    return comando(CMD_PWM, bytes([pwm & 0xFF, pwm >> 8]))


def decodificar(dados: bytes) -> tuple[list[tuple[bool, int, float, float]], bytes]:
    """Amostras (inicio_de_volta, qualidade, angulo em graus, distancia em m) e o resto.

    Um byte que nao passa nas verificacoes e descartado ate o fluxo voltar a alinhar.
    """
    amostras = []
    i, fim = 0, len(dados)
    while fim - i >= 5:
        b0, b1, b2, b3, b4 = dados[i:i + 5]
        inicio = b0 & 0x01
        if inicio == (b0 >> 1) & 0x01 or not b1 & 0x01:
            i += 1
            continue
        angulo = ((b2 << 7) | (b1 >> 1)) / 64.0
        if angulo >= 360.0:
            i += 1
            continue
        amostras.append((bool(inicio), b0 >> 2, angulo, ((b4 << 8) | b3) / 4000.0))
        i += 5
    return amostras, bytes(dados[i:])


def ler_info(dados: bytes) -> Optional[dict]:
    """Resposta do GET_INFO (20 bytes de dados)."""
    if len(dados) < 20:
        return None
    return {
        "modelo": dados[0],
        "firmware": f"{dados[2]}.{dados[1]:02d}",
        "hardware": dados[3],
        "serie": dados[4:20].hex().upper(),
    }


# ── Porta ───────────────────────────────────────────────────────────
def descobrir_porta() -> Optional[str]:
    """AGV_LIDAR_PORT, depois /dev/rplidar (udev) e por fim a primeira porta CP210x."""
    if LIDAR_PORT:
        return LIDAR_PORT
    if os.path.exists(PORTA_UDEV):
        return PORTA_UDEV
    try:
        from serial.tools import list_ports  # type: ignore[import-not-found]
    except Exception:
        return None
    try:
        for info in sorted(list_ports.comports(), key=lambda item: str(item.device)):
            if (getattr(info, "vid", None), getattr(info, "pid", None)) == USB_CP210X:
                return str(info.device)
    except Exception:
        pass
    return None


def porta_reservada() -> Optional[str]:
    """Caminho real da porta do LIDAR, para o Arduino nao tentar abri-la."""
    if not LIDAR_ENABLED:
        return None
    with estado.lidar_lock:
        aberta = estado.lidar_state["porta"]
    porta = aberta or descobrir_porta()
    return os.path.realpath(porta) if porta else None


# ── Serial ──────────────────────────────────────────────────────────
def _resposta(ser, tamanho: int) -> Optional[bytes]:
    """Le o descritor (7 bytes) e os dados de uma resposta unica."""
    descritor = ser.read(7)
    if len(descritor) != 7 or descritor[0] != SYNC or descritor[1] != 0x5A:
        return None
    dados = ser.read(tamanho)
    return dados if len(dados) == tamanho else None


def _parar(ser) -> None:
    ser.write(comando(CMD_STOP))
    ser.flush()
    time.sleep(0.05)
    ser.reset_input_buffer()


def _motor(ser, ligar: bool) -> None:
    try:
        ser.setDTR(not ligar)
    except Exception:
        pass
    ser.write(comando_pwm(LIDAR_MOTOR_PWM if ligar else 0))
    ser.flush()


def _abrir(porta: str):
    import serial  # type: ignore[import-not-found]

    ser = serial.Serial(port=porta, baudrate=LIDAR_BAUD, timeout=0.25, write_timeout=0.5)
    try:
        _parar(ser)
        ser.write(comando(CMD_INFO))
        info = ler_info(_resposta(ser, 20) or b"")
        if info is None:
            raise RuntimeError(f"sem resposta do RPLIDAR em {porta} a {LIDAR_BAUD} baud")

        ser.write(comando(CMD_SAUDE))
        saude = _resposta(ser, 3)
        if saude is not None and saude[0] == 2:
            log.warning("RPLIDAR em estado de erro (codigo %d): reiniciando", saude[1] | saude[2] << 8)
            ser.write(comando(CMD_RESET))
            time.sleep(2.0)
            ser.reset_input_buffer()
        info["saude"] = SAUDE.get(saude[0], "?") if saude is not None else "?"

        _motor(ser, True)
        time.sleep(GIRO_INICIAL_S)
        ser.reset_input_buffer()
        ser.write(comando(CMD_SCAN))
        descritor = ser.read(7)
        if len(descritor) != 7 or descritor[:2] != bytes([SYNC, 0x5A]) or descritor[-1] != 0x81:
            raise RuntimeError("RPLIDAR nao confirmou a varredura")
        return ser, info
    except Exception:
        _fechar(ser)
        raise


def _fechar(ser) -> None:
    if ser is None:
        return
    try:
        ser.write(comando(CMD_STOP))
        _motor(ser, False)
    except Exception:
        pass
    try:
        ser.close()
    except Exception:
        pass


# ── Publicacao ──────────────────────────────────────────────────────
def publicar(angulos: list, distancias: list, agora: float, hz: float) -> None:
    pontos, _ = geometria.para_agv(angulos, distancias, MONTAGEM)
    folgas = geometria.folgas(pontos, CORPO)
    with estado.lidar_lock:
        estado.lidar_state.update(
            ok=True, erro=None, at=agora, hz=round(hz, 1), pontos=pontos, folgas=folgas,
            voltas=estado.lidar_state["voltas"] + 1,
        )


def leitura_recente(agora: Optional[float] = None) -> Optional[dict]:
    """Pontos e folgas da ultima volta, ou None se o LIDAR esta fora ou a volta e velha."""
    agora = util.now() if agora is None else agora
    with estado.lidar_lock:
        if not estado.lidar_state["ok"] or estado.lidar_state["pontos"] is None:
            return None
        if agora - float(estado.lidar_state["at"]) > LIDAR_VELHO_S:
            return None
        return {
            "pontos": estado.lidar_state["pontos"],
            "folgas": dict(estado.lidar_state["folgas"]),
            "at": float(estado.lidar_state["at"]),
        }


def status_snapshot() -> dict:
    with estado.lidar_lock:
        at = float(estado.lidar_state["at"])
        pontos = estado.lidar_state["pontos"]
        return {
            "habilitado": LIDAR_ENABLED,
            "online": bool(estado.lidar_state["ok"]),
            "erro": estado.lidar_state["erro"],
            "porta": estado.lidar_state["porta"],
            "info": estado.lidar_state["info"],
            "hz": estado.lidar_state["hz"],
            "voltas": estado.lidar_state["voltas"],
            "pontos": 0 if pontos is None else int(len(pontos)),
            "idade_s": None if at <= 0 else round(util.now() - at, 2),
            "folgas": dict(estado.lidar_state["folgas"]),
        }


def varredura_publica(max_pontos: int = 400) -> dict:
    """Pontos (reduzidos) para o radar do painel, mais o tamanho do AGV para desenha-lo."""
    resumo = status_snapshot()
    with estado.lidar_lock:
        pontos = estado.lidar_state["pontos"]
    lista = []
    if pontos is not None and len(pontos):
        passo = max(1, int(np.ceil(len(pontos) / max(1, max_pontos))))
        lista = np.round(pontos[::passo], 3).tolist()
    return {
        **resumo,
        "lista": lista,
        "corpo": CORPO._asdict(),
        "montagem": {"x_m": MONTAGEM.x_m, "y_m": MONTAGEM.y_m, "frente_graus": MONTAGEM.frente_graus},
    }


def _marcar_erro(mensagem: str, porta: Optional[str] = None) -> None:
    with estado.lidar_lock:
        estado.lidar_state.update(ok=False, erro=mensagem, porta=porta, hz=0.0)


# ── Thread ──────────────────────────────────────────────────────────
def _ler_voltas(ser) -> None:
    angulos: list[float] = []
    distancias: list[float] = []
    resto = b""
    ultimo_dado = util.now()
    ultima_volta = 0.0
    hz = 0.0
    while not estado.stop_event.is_set():
        bloco = ser.read(max(250, int(getattr(ser, "in_waiting", 0) or 0)))
        agora = util.now()
        if not bloco:
            if agora - ultimo_dado > SEM_DADOS_S:
                raise RuntimeError("RPLIDAR parou de enviar dados")
            continue
        ultimo_dado = agora
        amostras, resto = decodificar(resto + bloco)
        for inicio, qualidade, angulo, distancia in amostras:
            if (inicio and len(angulos) >= MIN_PONTOS_VOLTA) or len(angulos) >= MAX_AMOSTRAS_VOLTA:
                if ultima_volta > 0:
                    periodo = max(1e-3, agora - ultima_volta)
                    hz = 1.0 / periodo if hz <= 0 else 0.8 * hz + 0.2 / periodo
                ultima_volta = agora
                publicar(angulos, distancias, agora, hz)
                angulos, distancias = [], []
            elif inicio:
                angulos, distancias = [], []
            if qualidade > 0 and distancia > 0:
                angulos.append(angulo)
                distancias.append(distancia)


def lidar_loop() -> None:
    if not LIDAR_ENABLED:
        _marcar_erro("LIDAR desativado por ambiente (AGV_LIDAR_ENABLED=0)")
        return
    ultimo_aviso = ""
    while not estado.stop_event.is_set():
        porta = descobrir_porta()
        if porta is None:
            _marcar_erro("nenhum RPLIDAR encontrado (conecte o USB ou defina AGV_LIDAR_PORT)")
            estado.stop_event.wait(3.0)
            continue
        ser = None
        try:
            ser, info = _abrir(porta)
            with estado.lidar_lock:
                estado.lidar_state.update(porta=os.path.realpath(porta), info=info, erro=None)
            log.info("RPLIDAR conectado em %s (modelo 0x%02X, firmware %s, hardware %s, saude %s)",
                     os.path.realpath(porta), info["modelo"], info["firmware"], info["hardware"], info["saude"])
            ultimo_aviso = ""
            _ler_voltas(ser)
        except Exception as exc:
            mensagem = str(exc) or exc.__class__.__name__
            if "Permission denied" in mensagem or "Errno 13" in mensagem:
                mensagem = f"sem permissao para abrir {porta}; adicione o usuario ao grupo dialout"
            _marcar_erro(mensagem, porta=None)
            if mensagem != ultimo_aviso:
                log.warning("RPLIDAR: %s", mensagem)
                ultimo_aviso = mensagem
        finally:
            _fechar(ser)
        estado.stop_event.wait(2.0)
    with estado.lidar_lock:
        estado.lidar_state["ok"] = False


def portas_candidatas_ignoradas(portas: list) -> list:
    """Remove da lista as portas que sao do LIDAR (usado na busca do Arduino)."""
    reservada = porta_reservada()
    if not reservada:
        return list(portas)
    return [porta for porta in portas if os.path.realpath(porta) != reservada]

