import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path


def stream_output(proc: subprocess.Popen) -> None:
    assert proc.stdout is not None
    for line in proc.stdout:
        print(f"[agv] {line.rstrip()}")


def wait_server(url: str, timeout_seconds: float = 20.0) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def stop_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        proc.terminate()
    else:
        proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=4)
    except subprocess.TimeoutExpired:
        proc.kill()


def cleanup_stale_agv_processes() -> None:
    patterns = ["iniciar_sistema.py", "/iniciar_sistema"]
    for pattern in patterns:
        subprocess.run(
            ["pkill", "-f", pattern],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    time.sleep(0.4)


def find_available_port(host: str, preferred_port: int) -> int:
    for port in range(preferred_port, preferred_port + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if sock.connect_ex((host, port)) != 0:
                return port
    raise RuntimeError("Nao encontrei porta livre para abrir o site de teste")


def detect_kinect_v4l2_device() -> str:
    sys_video = Path("/sys/class/video4linux")
    if not sys_video.exists():
        return ""

    candidates = []
    for entry in sys_video.glob("video*"):
        name_file = entry / "name"
        if not name_file.exists():
            continue
        try:
            name = name_file.read_text(encoding="utf-8", errors="ignore").strip().lower()
        except Exception:
            continue

        if "integrated_webcam" in name or "dummy video device" in name:
            continue

        score = 0
        if "xbox nui" in name:
            score += 10
        if "kinect" in name:
            score += 8
        if "gspca" in name:
            score += 6
        if score > 0:
            candidates.append((score, entry.name))

    if not candidates:
        return ""

    candidates.sort(reverse=True)
    return f"/dev/{candidates[0][1]}"


def build_runtime_env(host: str, port: int) -> dict[str, str]:
    env = os.environ.copy()
    detected_camera = detect_kinect_v4l2_device()

    env["AGV_HOST"] = host
    env["AGV_PORT"] = str(port)
    env["AGV_KINECT_FUSION"] = "1"
    env["AGV_KINECT_PRIMARY_CAMERA"] = "1"
    env["AGV_KINECT_AUX_IR"] = "0"
    env["AGV_KINECT_STABILIZATION"] = "0"

    if detected_camera:
        env["AGV_CAMERA_DEVICE"] = detected_camera
    else:
        env.pop("AGV_CAMERA_DEVICE", None)

    return env


def main() -> int:
    frozen = getattr(sys, "frozen", False)
    root_dir = Path(sys.executable).resolve().parent if frozen else Path(__file__).resolve().parent
    agv_dir = root_dir / "pc_agv"

    host = "127.0.0.1"
    port = find_available_port(host, 5000)
    base_url = f"http://{host}:{port}"
    status_url = f"{base_url}/api/status"

    env = build_runtime_env(host, port)

    print("========================================")
    print("TESTE VISUAL LOCAL DO AGV")
    print("========================================")
    print("Este modo simula os dois PCs no mesmo computador.")
    print("O site vai abrir sozinho no navegador.")
    print("Para encerrar tudo, pressione Ctrl+C aqui neste terminal.")
    print()
    print(f"Camera selecionada: {env.get('AGV_CAMERA_DEVICE', 'nenhuma V4L2 do Kinect encontrada')}")
    print("Fusao Kinect: forcada para ligada")
    print("Modo validado: RGB continuo + painel IR vindo do sensor depth")
    print("Estabilizacao: desligada neste launcher para garantir streams estaveis")
    print()

    cleanup_stale_agv_processes()

    server_exe = root_dir / "iniciar_sistema"
    server_py_in_pc_agv = agv_dir / "iniciar_sistema.py"
    server_py_local = root_dir / "iniciar_sistema.py"

    if frozen and server_exe.is_file():
        cmd = [str(server_exe)]
        cwd = str(root_dir)
    elif server_py_in_pc_agv.is_file():
        cmd = [sys.executable, str(server_py_in_pc_agv)]
        cwd = str(agv_dir)
    elif server_py_local.is_file():
        cmd = [sys.executable, str(server_py_local)]
        cwd = str(root_dir)
    else:
        print("FALHA: nao encontrei servidor AGV para iniciar.")
        print("Esperado em um destes caminhos:")
        print(f"- {server_exe}")
        print(f"- {server_py_in_pc_agv}")
        print(f"- {server_py_local}")
        print("Dica: copie o executavel 'iniciar_sistema' para a mesma pasta deste launcher.")
        return 1

    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    log_thread = threading.Thread(target=stream_output, args=(proc,), daemon=True)
    log_thread.start()

    try:
        print("Aguardando servidor subir...")
        if not wait_server(status_url):
            print("FALHA: servidor nao respondeu em tempo.")
            return 1

        print(f"Servidor ativo em: {base_url}")
        print("Abrindo navegador...")
        opened = webbrowser.open(base_url)
        if not opened:
            print("Nao consegui abrir o navegador automaticamente.")
            print(f"Abra manualmente: {base_url}")
        else:
            print("Navegador aberto.")

        print()
        print("Agora voce pode ver o site em tempo real.")
        print("Se a camera estiver funcionando, o video aparece na pagina.")
        print("Ctrl+C aqui fecha o servidor de teste.")

        while True:
            time.sleep(1)
            if proc.poll() is not None:
                print("O servidor foi encerrado.")
                return proc.returncode or 0
    except KeyboardInterrupt:
        print("\nEncerrando teste visual...")
        return 0
    finally:
        stop_process(proc)


if __name__ == "__main__":
    raise SystemExit(main())
