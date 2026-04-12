#!/usr/bin/env python3
"""
AGV — Launcher de Produção
Inicia o servidor Kinect e abre o painel no browser.
"""

import os
import sys
import time
import webbrowser
import subprocess
import urllib.request
from pathlib import Path


def _is_server_online(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/api/status", timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


def _cleanup_stale_server_processes() -> None:
    # Evita conflito de porta/Kinect quando existe servidor antigo em segundo plano.
    patterns = ["iniciar_sistema.py", "/iniciar_sistema"]
    for pattern in patterns:
        subprocess.run(
            ["pkill", "-f", pattern],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    time.sleep(0.4)

ROOT = Path(__file__).parent if not getattr(sys, "frozen", False) else Path(sys.executable).parent
URL  = "http://127.0.0.1:5000"

print("=" * 60)
print("  AGV — KINECT v1  |  RGB + Depth + Motor")
print("=" * 60)

# Remove driver gspca_kinect se estiver bloqueando o freenect (sem pedir senha)
os.system("sudo -n rmmod gspca_kinect >/dev/null 2>&1 || true")

env = os.environ.copy()
env["AGV_HOST"] = "0.0.0.0"
env["AGV_PORT"] = "5000"

if _is_server_online(URL):
    print("\nServidor já estava online em 5000. Reutilizando processo existente.")
    print(f"Abrindo: {URL}")
    webbrowser.open(URL)
    sys.exit(0)

_cleanup_stale_server_processes()

# ─── Seleciona como iniciar o servidor ───────────────────────────────────────
# Modo executável (frozen): procura iniciar_sistema na mesma pasta
# Modo script: usa python3 com o caminho do .py
_SERVER_EXE = ROOT / "iniciar_sistema"         # executável gerado pelo PyInstaller
_SERVER_PY  = ROOT / "pc_agv" / "iniciar_sistema.py"

if getattr(sys, "frozen", False) and _SERVER_EXE.is_file():
    _cmd = [str(_SERVER_EXE)]
    _cwd = str(ROOT)
elif _SERVER_PY.is_file():
    _cmd = [sys.executable, str(_SERVER_PY)]
    _cwd = str(ROOT / "pc_agv")
else:
    print(f"ERRO: servidor não encontrado em {_SERVER_EXE} nem {_SERVER_PY}")
    sys.exit(1)

print("\nIniciando servidor...")
proc = subprocess.Popen(_cmd, env=env, cwd=_cwd)

print("Aguardando servidor responder", end="", flush=True)
for i in range(25):
    time.sleep(1)
    print(".", end="", flush=True)
    if proc.poll() is not None:
        print("\nServidor encerrou inesperadamente!")
        sys.exit(1)
    try:
        urllib.request.urlopen(URL + "/api/status", timeout=1)
        break
    except Exception:
        pass
else:
    print("\nServidor não respondeu em 25s — verifique o Kinect.")
    proc.terminate()
    sys.exit(1)

print("\nServidor online!")
print(f"Abrindo: {URL}")
webbrowser.open(URL)

print("\nPressione Ctrl+C para parar.\n")
try:
    while True:
        time.sleep(1)
        if proc.poll() is not None:
            print("Servidor encerrou!")
            sys.exit(1)
except KeyboardInterrupt:
    print("\nEncerrando...")
    proc.terminate()
    proc.wait(timeout=5)
    print("Finalizado.")
