"""Decisao do comando enviado ao Arduino: manual, autonomo por regras, IA e watchdog."""

from pc_agv.config import (
    AUTO_LOOP_SECONDS,
    CONTROL_LOOP_SECONDS,
    IA_CORRECAO_JANELA_S,
    LIDAR_MAX_M,
    WATCHDOG_TIMEOUT_S,
)
from pc_agv import estado
from pc_agv import util
from pc_agv.aprendizado import servico as aprendizado
from pc_agv.hardware import arduino, rplidar



PERIGO_M = 0.35
ALERTA_M = 0.60
ATENCAO_M = 0.95
LATERAL_MIN_M = 0.12   # folga minima ao lado do chassi para girar para aquele lado
RE_LIVRE_M = 0.40      # folga atras para recuar quando a frente e os dois lados fecham
RE_VELOCIDADE = 25


def _menor(*valores):
    validos = [valor for valor in valores if valor is not None]
    return min(validos) if validos else None


def _lidar_ou_livre(folgas: dict, setor: str) -> float:
    # O LIDAR ve 360 graus: setor sem ponto nenhum esta livre ate o alcance maximo.
    valor = folgas.get(setor)
    return LIDAR_MAX_M if valor is None else float(valor)


def _lado_mais_livre(esquerda: float, direita: float, lado_esq, lado_dir) -> int:
    """+1 = direita, -1 = esquerda. Nao escolhe um lado com o chassi colado em algo."""
    if lado_dir is not None and lado_dir < LATERAL_MIN_M and (lado_esq is None or lado_esq >= LATERAL_MIN_M):
        return -1
    if lado_esq is not None and lado_esq < LATERAL_MIN_M and (lado_dir is None or lado_dir >= LATERAL_MIN_M):
        return 1
    return 1 if direita >= esquerda else -1


def compute_autopilot_command() -> tuple[int, int, str]:
    """Regras do autonomo com Kinect + RPLIDAR.

    O Kinect ve a frente em 3D (inclusive o que esta acima ou abaixo do plano do laser),
    mas nao mede abaixo de ~0,5 m. O RPLIDAR ve 360 graus num plano, de 0,15 m em
    diante: cobre a zona cega do Kinect, as laterais e a traseira. A frente e as
    diagonais usam a menor das duas leituras; os lados e a traseira, so o LIDAR.
    Sem LIDAR (ou com a volta velha) as regras sao as do Kinect sozinho.
    """
    with estado.state_lock:
        kinect_ok = bool(estado.state["kinect_ok"])
        left = estado.state["left_clearance_m"]
        center = estado.state["center_clearance_m"]
        right = estado.state["right_clearance_m"]
        distance = estado.state["distance_m"]
    leitura = rplidar.leitura_recente()

    # O LIDAR so ve um plano (nao ve mesa, degrau, objeto baixo): sem Kinect, para.
    if not kinect_ok:
        return 0, 0, "sem-kinect"
    if leitura is None and center is None and distance is None:
        return 0, 0, "sem-depth"

    lado_esq = lado_dir = tras = None
    if leitura is not None:
        folgas = leitura["folgas"]
        center = _menor(center if center is not None else distance, _lidar_ou_livre(folgas, "frente"))
        left = _menor(left, _lidar_ou_livre(folgas, "frente_esq"))
        right = _menor(right, _lidar_ou_livre(folgas, "frente_dir"))
        lado_esq = _lidar_ou_livre(folgas, "esquerda")
        lado_dir = _lidar_ou_livre(folgas, "direita")
        tras = _menor(_lidar_ou_livre(folgas, "tras"), _lidar_ou_livre(folgas, "tras_esq"),
                      _lidar_ou_livre(folgas, "tras_dir"))

    center_distance = center if center is not None else distance
    if center_distance is None:
        return 0, 0, "sem-distancia"

    left_value = left if left is not None else center_distance
    right_value = right if right is not None else center_distance
    lado = _lado_mais_livre(left_value, right_value, lado_esq, lado_dir)

    if center_distance < PERIGO_M:
        encurralado = left_value < PERIGO_M and right_value < PERIGO_M
        if encurralado and tras is not None and tras >= RE_LIVRE_M:
            return -RE_VELOCIDADE, 0, "re-encurralado"
        return 0, 70 * lado, "obstaculo-muito-perto"
    if center_distance < ALERTA_M:
        return 18, 65 * lado, "desvio-curto"
    if center_distance < ATENCAO_M:
        return 32, 45 * lado, "desvio-suave"

    steering = 0
    if left is not None and right is not None:
        diff = right - left
        steering = util.clamp(int(diff * 70.0), -35, 35)
        if abs(steering) < 8:
            steering = 0
    # Chassi raspando numa parede lateral: afasta-se dela.
    if lado_esq is not None and lado_esq < LATERAL_MIN_M:
        return 40, max(steering, 25), "afastando-da-esquerda"
    if lado_dir is not None and lado_dir < LATERAL_MIN_M:
        return 40, min(steering, -25), "afastando-da-direita"
    return 48, steering, "livre"


def autopilot_loop() -> None:
    """Calcula o comando dos modos automaticos: regras (auto) ou rede treinada (ia)."""
    while not estado.stop_event.is_set():
        with estado.control_lock:
            mode = estado.control_state["mode"]

        if mode in ("auto", "ia"):
            if mode == "auto":
                speed, steering, reason = compute_autopilot_command()
            else:
                speed, steering, reason = aprendizado.decidir()
            with estado.autopilot_lock:
                estado.autopilot_state["running"] = True
                estado.autopilot_state["target_speed"] = speed
                estado.autopilot_state["target_steering"] = steering
                estado.autopilot_state["last_reason"] = reason
                estado.autopilot_state["last_tick"] = util.now()
        else:
            with estado.autopilot_lock:
                estado.autopilot_state["running"] = False
                estado.autopilot_state["target_speed"] = 0
                estado.autopilot_state["target_steering"] = 0
                estado.autopilot_state["last_reason"] = "manual"
                estado.autopilot_state["last_tick"] = util.now()

        estado.stop_event.wait(AUTO_LOOP_SECONDS)


def control_loop() -> None:
    while not estado.stop_event.is_set():
        with estado.control_lock:
            mode = estado.control_state["mode"]
            manual_speed = estado.control_state["manual_speed"]
            manual_steering = estado.control_state["manual_steering"]
            source = estado.control_state["last_source"]
            updated_at = estado.control_state["updated_at"]

        correcao = (
            mode == "ia"
            and (manual_speed != 0 or manual_steering != 0)
            and (util.now() - updated_at) <= IA_CORRECAO_JANELA_S
        )
        if correcao:
            # O operador corrigiu a IA: o comando dele vale e vira exemplo para o treino.
            speed = manual_speed
            steering = manual_steering
            command_source = "correcao"
        elif mode in ("auto", "ia"):
            with estado.autopilot_lock:
                speed = int(estado.autopilot_state["target_speed"])
                steering = int(estado.autopilot_state["target_steering"])
            command_source = "autopilot" if mode == "auto" else "ia"
        else:
            # Watchdog: se nenhum novo comando chegou dentro do timeout, para o AGV.
            if WATCHDOG_TIMEOUT_S > 0 and (util.now() - updated_at) > WATCHDOG_TIMEOUT_S:
                speed = 0
                steering = 0
                command_source = "watchdog"
            else:
                speed = manual_speed
                steering = manual_steering
                command_source = source

        sent = arduino.send_to_arduino(speed, steering, mode, command_source)
        error = None
        if not sent:
            error = arduino.last_error()

        with estado.control_lock:
            estado.control_state["active_speed"] = speed
            estado.control_state["active_steering"] = steering
            estado.control_state["active_source"] = command_source
            estado.control_state["arduino_sent"] = bool(sent)
            estado.control_state["arduino_error"] = error

        estado.stop_event.wait(CONTROL_LOOP_SECONDS)
