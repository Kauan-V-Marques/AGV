#!/usr/bin/env python3
"""Launcher principal do AGV.

Sobe o servidor web no mesmo processo e abre o painel no navegador.
Assim sobra um unico executavel principal para o projeto.
"""

import os
import atexit
import fcntl
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser

from pc_agv.iniciar_sistema import _release_kinect_usb_claims, create_server


HOST = os.environ.get("AGV_HOST", "0.0.0.0").strip() or "0.0.0.0"
PORT = int(os.environ.get("AGV_PORT", "5000"))
URL = f"http://127.0.0.1:{PORT}"
_LOCK_HANDLE = None
_RUNNING = True
_SERVER = None


def _acquire_single_instance_lock() -> bool:
    global _LOCK_HANDLE

    lock_path = "/tmp/agv_launch_production.lock"
    _LOCK_HANDLE = open(lock_path, "w", encoding="utf-8")
    try:
        fcntl.flock(_LOCK_HANDLE.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _LOCK_HANDLE.write(str(os.getpid()))
        _LOCK_HANDLE.flush()
        return True
    except OSError:
        return False


def _release_single_instance_lock() -> None:
    global _LOCK_HANDLE

    if _LOCK_HANDLE is None:
        return
    try:
        fcntl.flock(_LOCK_HANDLE.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        _LOCK_HANDLE.close()
    except Exception:
        pass
    _LOCK_HANDLE = None


def _handle_stop_signal(signum, _frame) -> None:
    global _RUNNING

    _RUNNING = False
    print(f"\nSinal {signum} recebido. Encerrando AGV...")


atexit.register(_release_single_instance_lock)


def _ensure_visible_terminal() -> None:
    # Quando iniciado por clique duplo no Linux, pode rodar sem terminal visivel.
    # Nesse caso, relanca o proprio executavel dentro de um emulador de terminal.
    if os.environ.get("AGV_TERMINAL_RELAUNCHED") == "1":
        return
    if sys.stdout.isatty() and sys.stdin.isatty():
        return
    if not os.environ.get("DISPLAY"):
        return

    argv = [os.path.abspath(sys.argv[0])] + sys.argv[1:]
    if not getattr(sys, "frozen", False):
        argv = [sys.executable] + argv

    env = os.environ.copy()
    env["AGV_TERMINAL_RELAUNCHED"] = "1"

    candidates = [
        ["x-terminal-emulator", "-e"] + argv,
        ["gnome-terminal", "--"] + argv,
        ["konsole", "-e"] + argv,
        ["xfce4-terminal", "-e", " ".join(argv)],
        ["xterm", "-hold", "-e"] + argv,
    ]

    for cmd in candidates:
        binary = cmd[0]
        if shutil.which(binary) is None:
            continue
        try:
            proc = subprocess.Popen(cmd, env=env,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            time.sleep(0.9)
            if proc.poll() is not None and proc.returncode != 0:
                # terminal crashou antes de abrir (ex: gnome-terminal com snap quebrado)
                continue
            print("Abrindo terminal para mostrar os logs do AGV...")
            raise SystemExit(0)
        except Exception:
            continue

    # Nenhum terminal funcionou: redirecionar logs para arquivo e continuar no processo atual
    log_path = "/tmp/agv_production.log"
    try:
        log_file = open(log_path, "a", buffering=1, encoding="utf-8")
        sys.stdout = log_file
        sys.stderr = log_file
        print(f"\n=== AGV sem terminal — logs em {log_path} ===")
    except Exception:
        pass
    os.environ["AGV_TERMINAL_RELAUNCHED"] = "1"


def _is_server_online(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/api/status", timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


_ensure_visible_terminal()

if not _acquire_single_instance_lock():
    print("Outro AGV ja esta em execucao neste PC.")
    if _is_server_online(URL):
        print(f"Abrindo painel existente: {URL}")
        webbrowser.open(URL)
        raise SystemExit(0)
    print("Use Ctrl+C na janela ja aberta para parar o projeto antes de abrir outro.")
    raise SystemExit(1)

signal.signal(signal.SIGINT, _handle_stop_signal)
signal.signal(signal.SIGTERM, _handle_stop_signal)

print("=" * 60)
print("  AGV | Site + Kinect + Arduino")
print("=" * 60)

# Remove drivers de kernel que bloqueiam o Kinect via libusb (LIBUSB_ERROR_BUSY)
_release_kinect_usb_claims()

if _is_server_online(URL):
    print("\nServidor ja estava online. Reutilizando processo existente.")
    print(f"Abrindo: {URL}")
    webbrowser.open(URL)
    sys.exit(0)

print("\nIniciando servidor...")
_SERVER = create_server(host=HOST, port=PORT)
_SERVER.start()

print("Aguardando servidor responder", end="", flush=True)
for _ in range(25):
    time.sleep(1)
    print(".", end="", flush=True)
    try:
        urllib.request.urlopen(URL + "/api/status", timeout=1)
        break
    except Exception:
        pass
else:
    print("\nServidor nao respondeu em 25s. Verifique o Kinect e a serial.")
    _SERVER.stop()
    sys.exit(1)

print("\nServidor online!")
print(f"Abrindo: {URL}")
webbrowser.open(URL)

print("\nPressione Ctrl+C para parar.\n")
try:
    while _RUNNING:
        time.sleep(1)
except KeyboardInterrupt:
    _RUNNING = False
finally:
    print("\nEncerrando...")
    if _SERVER is not None:
        _SERVER.stop()
    print("Finalizado.")
