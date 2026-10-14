"""Camera ZED: descoberta do dispositivo e captura por OpenCV (V4L2) ou FFmpeg."""

import glob
import os
import select
import subprocess
import time
from typing import Optional

import cv2
import numpy as np

from pc_agv.config import (
    ZED_DEVICE,
    ZED_FPS,
    ZED_HEIGHT,
    ZED_NAME_HINT,
    ZED_STRICT_DEVICE,
    ZED_WIDTH,
    log,
)
from pc_agv import estado
from pc_agv import util
from pc_agv.visao import rostos


def _zed_source_value(source_raw: str):
    value = str(source_raw or "").strip()
    if value.isdigit():
        return int(value)
    return value or 0


def _zed_nodes_from_sysfs() -> list[str]:
    """Detecta nodes ZED pelo sysfs. Tenta varios nomes: zed, stereolabs, usb video, ou qualquer device USB."""
    nodes = []
    for name_path in sorted(glob.glob("/sys/class/video4linux/video*/name")):
        try:
            card_name = open(name_path, "r", encoding="utf-8", errors="ignore").read().strip().lower()
        except Exception:
            continue
        
        # Tenta match por ZED_NAME_HINT ou por pattern STEREOLABS ou USB generico
        is_match = (
            (ZED_NAME_HINT and ZED_NAME_HINT in card_name) or
            "stereolabs" in card_name or
            "stereo" in card_name or
            ("usb" in card_name and "video" in card_name)
        )
        if not is_match:
            continue

        video_node = os.path.basename(os.path.dirname(name_path))
        node_path = f"/dev/{video_node}"
        if os.path.exists(node_path):
            nodes.append(node_path)
            log.info("ZED device detectado via sysfs: %s (%s)", node_path, card_name)
    return nodes


def _zed_candidate_sources() -> list:
    candidates = []
    preferred_raw = str(ZED_DEVICE or "").strip()
    preferred = _zed_source_value(preferred_raw)
    if preferred_raw:
        candidates.append(preferred)

    for node in _zed_nodes_from_sysfs():
        if node not in candidates:
            candidates.append(node)

    if not ZED_STRICT_DEVICE:
        for path in sorted(glob.glob("/dev/video*")):
            if path not in candidates:
                candidates.append(path)

    dedup = []
    seen = set()
    for source in candidates:
        key = str(source)
        if key in seen:
            continue
        seen.add(key)
        dedup.append(source)
    return dedup


def _open_zed_capture(source) -> tuple[Optional[subprocess.Popen], Optional[str]]:
    """Abre stream de video ZED via FFmpeg (OpenCV nao consegue abrir device v4l2)."""
    if not isinstance(source, str) or not source.startswith("/dev/video"):
        return None, "source invalido"
    
    try:
        if not os.path.exists(source):
            return None, f"device {source} nao existe"
        
        base_prefix = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-probesize", "32",
            "-analyzeduration", "0",
            "-f", "v4l2",
            "-thread_queue_size", "32",
            "-video_size", f"{ZED_WIDTH}x{ZED_HEIGHT}",
            "-framerate", str(ZED_FPS),
        ]

        variants = [
            {
                "name": "mjpeg-copy",
                "cmd": base_prefix
                + [
                    "-input_format", "mjpeg",
                    "-i", source,
                    "-an", "-sn", "-dn",
                    "-fflags", "flush_packets",
                    "-f", "mjpeg",
                    "-c:v", "copy",
                    "-",
                ],
            },
            {
                "name": "yuyv-encode",
                "cmd": base_prefix
                + [
                    "-input_format", "yuyv422",
                    "-i", source,
                    "-an", "-sn", "-dn",
                    "-fflags", "flush_packets",
                    "-vsync", "drop",
                    "-f", "image2pipe",
                    "-c:v", "mjpeg",
                    "-q:v", "3",
                    "-",
                ],
            },
        ]

        last_err = ""
        for variant in variants:
            proc = subprocess.Popen(
                variant["cmd"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )

            time.sleep(0.25)
            if proc.poll() is None:
                log.info(
                    "FFmpeg stream aberto: %s (%dx%d @ %d fps, %s)",
                    source,
                    ZED_WIDTH,
                    ZED_HEIGHT,
                    ZED_FPS,
                    variant["name"],
                )
                return proc, None

            try:
                proc.communicate(timeout=0.4)
            except Exception:
                pass
            last_err = f"variant {variant['name']} falhou"

        return None, f"ffmpeg falhou: {last_err or 'unknown'}"
    except Exception as exc:
        return None, str(exc)


def _open_zed_cv_capture(source) -> tuple[Optional[cv2.VideoCapture], Optional[str]]:
    if not isinstance(source, str) or not source.startswith("/dev/video"):
        return None, "source invalido"
    if not os.path.exists(source):
        return None, f"device {source} nao existe"

    try:
        cap = cv2.VideoCapture(source, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap.release()
            return None, "falha ao abrir com OpenCV/V4L2"

        fourcc = cv2.VideoWriter_fourcc(*"YUYV")
        cap.set(cv2.CAP_PROP_FOURCC, fourcc)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(ZED_WIDTH))
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(ZED_HEIGHT))
        cap.set(cv2.CAP_PROP_FPS, float(ZED_FPS))
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        actual_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        log.info(
            "OpenCV stream aberto: %s (%dx%d @ %.1f fps)",
            source,
            actual_w,
            actual_h,
            actual_fps,
        )
        return cap, None
    except Exception as exc:
        return None, str(exc)


def _read_frame_from_ffmpeg(
    proc: subprocess.Popen,
    carry: bytes,
    timeout_s: float = 0.35,
) -> tuple[Optional[np.ndarray], bytes]:
    """Le o frame mais recente do pipe FFmpeg, descartando backlog para reduzir latencia."""
    if not proc or proc.poll() is not None:
        return None, b""

    stream = proc.stdout
    if stream is None:
        return None, b""
    
    frame_data = carry
    deadline = util.now() + timeout_s
    
    try:
        while util.now() < deadline:
            wait = max(0.0, deadline - util.now())
            readable, _, _ = select.select([stream], [], [], wait)
            if not readable:
                break

            chunk = stream.read(65536)
            if not chunk:
                return None, frame_data
            
            frame_data += chunk

            # Mantem apenas uma janela recente para evitar crescimento sem limite.
            if len(frame_data) > 4_000_000:
                frame_data = frame_data[-4_000_000:]
            
            if b"\xff\xd9" in frame_data:
                end_idx = frame_data.rfind(b"\xff\xd9") + 2
                start_idx = frame_data.rfind(b"\xff\xd8", 0, end_idx)
                
                if start_idx >= 0:
                    jpeg_bytes = frame_data[start_idx:end_idx]
                    remaining = frame_data[end_idx:]
                    frame = cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8), cv2.IMREAD_COLOR)
                    if frame is not None:
                        return frame, remaining
                    frame_data = remaining
    except Exception:
        pass
    
    return None, frame_data


def capture_loop() -> None:
    fps_counter = 0
    fps_mark = util.now()
    failed_until: dict[str, float] = {}

    while not estado.stop_event.is_set():
        proc = None
        cap = None
        pipe_carry = b""
        try:
            selected_source = None
            last_error = None

            for candidate in _zed_candidate_sources():
                candidate_key = str(candidate)
                if float(failed_until.get(candidate_key, 0.0)) > util.now():
                    continue

                cap, open_error = _open_zed_cv_capture(candidate)
                if cap is not None:
                    selected_source = candidate
                    break

                proc, open_error = _open_zed_capture(candidate)
                if proc is not None:
                    selected_source = candidate
                    break

                last_error = open_error
                failed_until[candidate_key] = util.now() + 20.0

            if cap is None and proc is None:
                with estado.state_lock:
                    estado.state["zed_ok"] = False
                    estado.state["zed_error"] = f"nao abriu webcam ZED ({last_error or 'sem detalhe'})"
                estado.stop_event.wait(2.0)
                continue

            with estado.state_lock:
                estado.state["zed_ok"] = True
                estado.state["zed_error"] = None
                estado.state["zed_device"] = str(selected_source)

            while not estado.stop_event.is_set():
                if cap is not None:
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        frame = None
                else:
                    frame, pipe_carry = _read_frame_from_ffmpeg(proc, pipe_carry)

                if frame is None:
                    with estado.state_lock:
                        estado.state["zed_ok"] = False
                        estado.state["zed_error"] = f"falha de leitura da webcam ZED em {selected_source}"
                    failed_until[str(selected_source)] = util.now() + 20.0
                    break

                now = util.now()
                known, label, bbox = rostos.offer_frame(frame, now)

                annotated_frame = frame
                if known and bbox is not None:
                    x, y, bw, bh = bbox
                    cv2.rectangle(annotated_frame, (x, y), (x + bw, y + bh), (80, 230, 120), 2)
                    cv2.putText(
                        annotated_frame,
                        label,
                        (x, max(18, y - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.65,
                        (80, 230, 120),
                        2,
                    )

                with estado.state_lock:
                    if isinstance(frame, np.ndarray) and frame.size > 0:
                        estado.state["zed"] = annotated_frame
                        estado.state["zed_ok"] = True
                        estado.state["zed_error"] = None
                        estado.state["zed_last_frame_at"] = util.now()

                fps_counter += 1
                now = util.now()
                elapsed = now - fps_mark
                if elapsed >= 1.0:
                    with estado.state_lock:
                        estado.state["fps_zed"] = round(fps_counter / elapsed, 1)
                    fps_counter = 0
                    fps_mark = now

                estado.stop_event.wait(max(0.001, 1.0 / max(1, ZED_FPS)))
        except Exception as exc:
            with estado.state_lock:
                estado.state["zed_ok"] = False
                estado.state["zed_error"] = f"erro na webcam ZED: {exc}"
            estado.stop_event.wait(2.0)
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
            if proc is not None:
                try:
                    proc.terminate()
                    proc.wait(timeout=1)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
