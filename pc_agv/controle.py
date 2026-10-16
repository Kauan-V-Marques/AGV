"""Decisao do comando enviado ao Arduino: manual, autonomo por regras, IA e watchdog."""

from pc_agv.config import (
    AUTO_LOOP_SECONDS,
    CONTROL_LOOP_SECONDS,
    IA_CORRECAO_JANELA_S,
    WATCHDOG_TIMEOUT_S,
)
from pc_agv import estado
from pc_agv import util
from pc_agv.aprendizado import servico as aprendizado
from pc_agv.hardware import arduino



def compute_autopilot_command() -> tuple[int, int, str]:
    with estado.state_lock:
        kinect_ok = bool(estado.state["kinect_ok"])
        left = estado.state["left_clearance_m"]
        center = estado.state["center_clearance_m"]
        right = estado.state["right_clearance_m"]
        distance = estado.state["distance_m"]

    if not kinect_ok:
        return 0, 0, "sem-kinect"
    if center is None and distance is None:
        return 0, 0, "sem-depth"

    center_distance = center if center is not None else distance
    if center_distance is None:
        return 0, 0, "sem-distancia"

    left_value = left if left is not None else center_distance
    right_value = right if right is not None else center_distance

    if center_distance < 0.35:
        turn = 70 if right_value >= left_value else -70
        return 0, turn, "obstaculo-muito-perto"
    if center_distance < 0.60:
        turn = 65 if right_value >= left_value else -65
        return 18, turn, "desvio-curto"
    if center_distance < 0.95:
        turn = 45 if right_value >= left_value else -45
        return 32, turn, "desvio-suave"

    steering = 0
    if left is not None and right is not None:
        diff = right - left
        steering = util.clamp(int(diff * 70.0), -35, 35)
        if abs(steering) < 8:
            steering = 0
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
