#!/usr/bin/env python3
"""
PC Controle remoto do AGV.
- Aguarda servidor AGV responder
- Abre painel web para assistir/controlar
- Opcionalmente envia comandos por teclado (WASD + espaço)
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
import webbrowser


def _base_url() -> str:
    agv_ip = os.environ.get("AGV_IP", "127.0.0.1").strip()
    agv_port = os.environ.get("AGV_PORT", "5000").strip()
    return f"http://{agv_ip}:{agv_port}"


def _wait_server(url: str, retries: int = 25, delay: float = 1.0) -> bool:
    print(f"Aguardando AGV em {url}/api/status ...")
    for i in range(1, retries + 1):
        try:
            with urllib.request.urlopen(url + "/api/status", timeout=2) as resp:
                if resp.status == 200:
                    print("AGV online.")
                    return True
        except Exception:
            pass
        print(f"  tentativa {i}/{retries}")
        time.sleep(delay)
    return False


def _post_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=2) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _keyboard_loop(base_url: str) -> None:
    print("\nControle por teclado (opcional):")
    print("  w = frente | s = re | a = esquerda | d = direita")
    print("  x = parar | m = manual | u = auto | q = sair")
    print("Digite e pressione Enter.")

    speed = 0
    steering = 0
    mode = "manual"

    while True:
        cmd = input("> ").strip().lower()
        if not cmd:
            continue
        if cmd == "q":
            break
        if cmd == "w":
            speed = min(100, speed + 20)
        elif cmd == "s":
            speed = max(-100, speed - 20)
        elif cmd == "a":
            steering = max(-100, steering - 20)
        elif cmd == "d":
            steering = min(100, steering + 20)
        elif cmd == "x":
            speed = 0
            steering = 0
        elif cmd == "m":
            mode = "manual"
        elif cmd == "u":
            mode = "auto"
        else:
            print("Comando invalido.")
            continue

        try:
            out = _post_json(
                base_url + "/api/control",
                {
                    "mode": mode,
                    "speed": speed,
                    "steering": steering,
                    "source": "pc-controle",
                },
            )
            agv = out.get("agv", {})
            print(
                f"OK mode={agv.get('mode')} speed={agv.get('speed')} steering={agv.get('steering')}"
            )
        except urllib.error.URLError as e:
            print(f"Falha ao enviar comando: {e}")


def main() -> int:
    base_url = _base_url()
    print("=" * 56)
    print("PC CONTROLE AGV (REMOTO)")
    print("=" * 56)
    print(f"Destino AGV: {base_url}")

    if not _wait_server(base_url):
        print("Falha: AGV nao respondeu.")
        print("Defina AGV_IP e AGV_PORT corretamente no PC remoto.")
        return 1

    print(f"Abrindo painel: {base_url}")
    webbrowser.open(base_url)

    use_keyboard = os.environ.get("AGV_KEYBOARD", "1").strip() == "1"
    if use_keyboard:
        _keyboard_loop(base_url)

    print("Encerrado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
