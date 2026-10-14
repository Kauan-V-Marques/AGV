"""Arduino Uno: conexao serial, protocolo `aceleracao,direcao` e estado do link."""

import glob
import os
import threading
import time
from typing import Optional

from pc_agv.config import (
    MIN_FORWARD_PWM,
    MIN_TURN_PWM,
    log,
)
from pc_agv import estado
from pc_agv import util


_arduino_lock = threading.Lock()

_arduino_serial = None

_arduino_state = {
    "enabled": os.environ.get("AGV_ARDUINO_ENABLED", "1").strip() == "1",
    "connected": False,
    "port": None,
    "baud": int(os.environ.get("AGV_ARDUINO_BAUD", "9600")),
    "protocol": str(os.environ.get("AGV_ARDUINO_PROTOCOL", "csv")).strip().lower() or "csv",
    "last_command": None,
    "last_error": None,
    "last_tx_at": 0.0,
}

_arduino_log_state = {
    "payload": None,
    "mode": None,
    "source": None,
    "at": 0.0,
}

_arduino_rx_state = {
    "last_line": None,
    "at": 0.0,
}



def _candidate_arduino_ports():
    preferred = os.environ.get("AGV_ARDUINO_PORT", "").strip()
    ports = []
    if preferred:
        ports.append(preferred)

    try:
        from serial.tools import list_ports  # type: ignore[import-not-found]
    except Exception:
        list_ports = None

    if list_ports is not None:
        try:
            discovered = []
            for info in list_ports.comports():
                device = str(getattr(info, "device", "") or "").strip()
                if not device:
                    continue
                discovered.append(device)
            ports.extend(sorted(discovered))
        except Exception:
            pass

    ports.extend(sorted(glob.glob("/dev/serial/by-id/*")))
    ports.extend(sorted(glob.glob("/dev/ttyACM*")))
    ports.extend(sorted(glob.glob("/dev/ttyUSB*")))
    ports.extend(sorted(glob.glob("/dev/ttyAMA*")))
    ports.extend(sorted(glob.glob("/dev/rfcomm*")))

    dedup = []
    seen = set()
    for port in ports:
        if not port:
            continue
        key = os.path.realpath(port)
        if key in seen:
            continue
        seen.add(key)
        dedup.append(port)
    return dedup


def _close_arduino_locked() -> None:
    global _arduino_serial

    if _arduino_serial is None:
        return
    try:
        _arduino_serial.close()
    except Exception:
        pass
    _arduino_serial = None


def _drain_arduino_input_locked(ser, max_lines: int = 12) -> list[str]:
    lines = []
    prev_line = _arduino_rx_state["last_line"]
    prev_at = float(_arduino_rx_state["at"])

    try:
        waiting = int(getattr(ser, "in_waiting", 0) or 0)
    except Exception:
        waiting = 0

    while waiting > 0 and len(lines) < max_lines:
        try:
            raw = ser.readline()
        except Exception as exc:
            _arduino_state["last_error"] = f"falha ao ler serial: {exc}"
            break

        if not raw:
            break

        line = raw.decode("utf-8", errors="ignore").strip()
        if line:
            lines.append(line)
            _arduino_rx_state["last_line"] = line
            _arduino_rx_state["at"] = util.now()

            if "ERROR" in line or "TIMEOUT" in line:
                _arduino_state["last_error"] = line
            elif (
                line.startswith("ARDUINO:OK")
                or line.startswith("ARDUINO:READY")
                or line.startswith("ARDUINO:PONG")
                or line.startswith("STATUS:")
            ):
                _arduino_state["last_error"] = None

        try:
            waiting = int(getattr(ser, "in_waiting", 0) or 0)
        except Exception:
            break

    if lines:
        last_line = lines[-1]
        should_log = (
            prev_line != last_line
            or (util.now() - prev_at) >= 2.0
            or "ERROR" in last_line
            or "TIMEOUT" in last_line
        )
        if should_log:
            log.info("Arduino RX: %s", last_line)

    return lines


def _probe_arduino_locked(ser) -> bool:
    lines = _drain_arduino_input_locked(ser)
    if any(("READY" in line or "PONG" in line or "OK" in line or line.startswith("STATUS:")) for line in lines):
        return True

    try:
        ser.write(b"PING\n")
        ser.flush()
        time.sleep(0.25)
    except Exception:
        return False

    lines = _drain_arduino_input_locked(ser)
    return any(("READY" in line or "PONG" in line or "OK" in line or line.startswith("STATUS:")) for line in lines)


def _connect_arduino() -> bool:
    global _arduino_serial

    if not _arduino_state["enabled"]:
        with _arduino_lock:
            _arduino_state["last_error"] = "serial desativada por ambiente"
        return False

    try:
        import serial  # type: ignore[import-not-found]
    except Exception as exc:
        with _arduino_lock:
            _arduino_state["connected"] = False
            _arduino_state["last_error"] = f"pyserial ausente: {exc}"
        return False

    ports = _candidate_arduino_ports()
    if not ports:
        with _arduino_lock:
            _arduino_state["connected"] = False
            _arduino_state["last_error"] = "nenhuma porta serial encontrada"
        return False

    for port in ports:
        try:
            ser = serial.Serial(
                port=port,
                baudrate=int(_arduino_state["baud"]),
                timeout=0.1,
                write_timeout=0.1,
                dsrdtr=False,
                rtscts=False,
            )
            try:
                ser.setDTR(False)
                ser.setRTS(False)
            except Exception:
                pass

            # O Arduino Uno costuma reiniciar ao abrir a serial USB.
            # Espera curta demais faz os primeiros comandos se perderem.
            time.sleep(1.8)
            try:
                ser.reset_output_buffer()
            except Exception:
                pass

            with _arduino_lock:
                _arduino_serial = ser
                _arduino_state["connected"] = True
                _arduino_state["port"] = os.path.realpath(port)
                _arduino_state["last_error"] = None
                identified = _probe_arduino_locked(ser)
                if not identified:
                    _arduino_state["last_error"] = "porta abriu, mas sem resposta do firmware"
            log.info("Arduino conectado em %s @ %s", os.path.realpath(port), _arduino_state["baud"])
            return True
        except Exception as exc:
            detail = str(exc)
            if isinstance(exc, PermissionError) or "Permission denied" in detail or "Errno 13" in detail:
                detail = f"sem permissao para abrir {port}; entre novamente na sessao ou adicione o usuario ao grupo dialout"
            with _arduino_lock:
                _arduino_state["connected"] = False
                _arduino_state["last_error"] = f"falha ao abrir {port}: {detail}"

    return False


def speed_steering_to_payload(speed: int, steering: int) -> tuple[int, int]:
    speed_cmd = util.clamp(speed, -100, 100)
    steering_cmd = util.clamp(steering, -100, 100)

    abs_speed_pct = abs(speed_cmd)
    accel = abs_speed_pct * 255 // 100

    if abs_speed_pct > 0:
        accel = max(util.clamp(MIN_FORWARD_PWM, 0, 255), accel)

    if speed_cmd < 0:
        accel = -accel

    if accel == 0 and abs(steering_cmd) >= 20:
        accel = util.clamp(MIN_TURN_PWM, 0, 255)

    steering_gain = 0.70 if accel != 0 else 1.00
    dir_val = int(steering_cmd * 254 * steering_gain / 100)
    if abs(dir_val) < 8:
        dir_val = 0

    return accel, dir_val


def send_to_arduino(speed: int, steering: int, mode: str, source: str) -> bool:
    global _arduino_serial

    if not _arduino_state["enabled"]:
        with _arduino_lock:
            _arduino_state["last_error"] = "serial desativada por ambiente"
        return False

    with _arduino_lock:
        ser = _arduino_serial

    if ser is None and not _connect_arduino():
        return False

    with _arduino_lock:
        ser = _arduino_serial
    if ser is None:
        return False

    accel, dir_val = speed_steering_to_payload(speed, steering)

    # Speed limit: escala linear em 0-255 para que o usuario sinta diferenca real.
    # A 50% o PWM maximo e 127 (visivelmente mais lento que 255).
    with estado.settings_lock:
        limit_pct = estado.settings["speed_limit_pct"]
    if limit_pct < 100 and accel != 0:
        max_pwm = int(255 * limit_pct / 100)
        accel = max(-max_pwm, min(max_pwm, accel))

    payload = f"{accel},{dir_val}"

    try:
        with _arduino_lock:
            _drain_arduino_input_locked(ser)
        ser.write((payload + "\n").encode("ascii", errors="ignore"))
        try:
            ser.flush()
        except Exception:
            pass
        with _arduino_lock:
            _arduino_state["connected"] = True
            _arduino_state["last_command"] = payload
            _arduino_state["last_error"] = None
            _arduino_state["last_tx_at"] = util.now()
            _drain_arduino_input_locked(ser)
        log_changed = (
            _arduino_log_state["payload"] != payload
            or _arduino_log_state["mode"] != mode
            or _arduino_log_state["source"] != source
            or (util.now() - float(_arduino_log_state["at"])) >= 2.0
        )
        if log_changed:
            log.info("Arduino TX: %s (mode=%s source=%s)", payload, mode, source)
            _arduino_log_state["payload"] = payload
            _arduino_log_state["mode"] = mode
            _arduino_log_state["source"] = source
            _arduino_log_state["at"] = util.now()
        return True
    except Exception as exc:
        with _arduino_lock:
            _arduino_state["connected"] = False
            _arduino_state["last_error"] = str(exc)
            _close_arduino_locked()
        return False



def status_snapshot() -> dict:
    with _arduino_lock:
        return dict(_arduino_state)


def last_error() -> Optional[str]:
    with _arduino_lock:
        return _arduino_state.get("last_error")


def close() -> None:
    with _arduino_lock:
        _close_arduino_locked()
        _arduino_state["connected"] = False
