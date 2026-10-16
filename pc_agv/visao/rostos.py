"""Reconhecimento facial na camera ZED: banco de rostos, busca da pessoa selecionada e cadastro."""

import os
import pickle
import threading
from typing import Optional

import cv2
import face_recognition
import numpy as np
from werkzeug.utils import secure_filename

from pc_agv.config import (
    ENCODINGS_FILE,
    FACE_DB_DIR,
    FACE_DETECT_MODEL,
    FACE_IMPOSTOR_MARGIN,
    FACE_MATCH_THRESHOLD,
    FACE_SCAN_INTERVAL,
    FACE_SCAN_MAX_WIDTH,
    FACE_SELECTED_MAX_DISTANCE,
    FACE_UPLOADS_DIR,
    log,
)
from pc_agv import estado
from pc_agv import util


_face_known_encodings = []

_face_known_names = []

_face_known_image_paths = []

_face_selected_name = ""

_face_lock = threading.Lock()

_face_runtime = {
    "known": False,
    "label": "",
    "distance": None,
    "last_seen_at": 0.0,
    "last_announce_at": 0.0,
    "bbox": None,
    "next_scan_at": 0.0,
    "template_loaded": False,
    "selected_name": "",
}

_zed_face_lock = threading.Lock()

_zed_face_event = threading.Event()

_zed_face_state = {
    "pending": False,
    "frame": None,
    "next_scan_at": 0.0,
    "known": False,
    "label": "",
    "distance": None,
    "bbox": None,
}



def _normalize_face_name(name: str) -> str:
    normalized = " ".join(str(name or "").strip().split())
    return normalized[:60]


def _face_name_slug(name: str) -> str:
    slug = secure_filename(name).strip("._").lower()
    return slug or "rosto"


def _load_face_database() -> tuple[list[np.ndarray], list[str], list[Optional[str]]]:
    if not os.path.exists(ENCODINGS_FILE):
        raise FileNotFoundError(ENCODINGS_FILE)

    with open(ENCODINGS_FILE, "rb") as f:
        payload = pickle.load(f)

    if not isinstance(payload, tuple):
        raise ValueError("Formato invalido do encodings.pkl")

    if len(payload) == 2:
        known_encodings, known_names = payload
        known_image_paths = [None] * len(known_names)
    elif len(payload) == 3:
        known_encodings, known_names, known_image_paths = payload
    else:
        raise ValueError("Formato invalido do encodings.pkl")

    if not isinstance(known_encodings, list) or not isinstance(known_names, list) or not isinstance(known_image_paths, list):
        raise ValueError("Formato invalido do encodings.pkl")
    if len(known_encodings) != len(known_names) or len(known_names) != len(known_image_paths):
        raise ValueError("Banco de rostos inconsistente")

    return (
        [np.array(enc, dtype=np.float32) for enc in known_encodings],
        [_normalize_face_name(name) for name in known_names],
        [path if path else None for path in known_image_paths],
    )


def _save_face_database(encodings: list[np.ndarray], names: list[str], image_paths: list[Optional[str]]) -> None:
    os.makedirs(FACE_DB_DIR, exist_ok=True)
    with open(ENCODINGS_FILE, "wb") as f:
        pickle.dump((encodings, names, image_paths), f)


def load_known_faces() -> None:
    global _face_known_encodings, _face_known_names, _face_known_image_paths, _face_selected_name

    try:
        known_encodings, known_names, known_image_paths = _load_face_database()
        _face_known_encodings = known_encodings
        _face_known_names = known_names
        _face_known_image_paths = known_image_paths

        with _face_lock:
            if _face_selected_name and _face_selected_name not in _face_known_names:
                _face_selected_name = ""
            _face_runtime["template_loaded"] = bool(_face_known_names)
            _face_runtime["selected_name"] = _face_selected_name

        log.info("Encodings carregados com sucesso (%d faces)", len(_face_known_encodings))
    except FileNotFoundError:
        _face_known_encodings = []
        _face_known_names = []
        _face_known_image_paths = []
        with _face_lock:
            _face_runtime["template_loaded"] = False
            _face_runtime["selected_name"] = ""
        log.warning("Arquivo de encodings nao encontrado: %s", ENCODINGS_FILE)
    except Exception as exc:
        _face_known_encodings = []
        _face_known_names = []
        _face_known_image_paths = []
        with _face_lock:
            _face_runtime["template_loaded"] = False
            _face_runtime["selected_name"] = ""
        log.warning("Falha ao carregar encodings: %s", exc)


def set_selected_face(name: str) -> tuple[bool, str]:
    global _face_selected_name

    selected_name = _normalize_face_name(name)
    with _face_lock:
        if not selected_name:
            _face_selected_name = ""
            _face_runtime["known"] = False
            _face_runtime["label"] = ""
            _face_runtime["distance"] = None
            _face_runtime["bbox"] = None
            _face_runtime["next_scan_at"] = 0.0
            _face_runtime["selected_name"] = ""
            return True, "nenhum rosto selecionado"

        if selected_name not in _face_known_names:
            return False, "rosto nao encontrado"

        _face_selected_name = selected_name
        _face_runtime["known"] = False
        _face_runtime["label"] = ""
        _face_runtime["distance"] = None
        _face_runtime["bbox"] = None
        _face_runtime["next_scan_at"] = 0.0
        _face_runtime["selected_name"] = selected_name
        return True, "rosto selecionado"


def _selected_face_encoding() -> tuple[str, Optional[np.ndarray]]:
    with _face_lock:
        selected_name = str(_face_selected_name)
        known_names = list(_face_known_names)
        known_encodings = list(_face_known_encodings)

    if not selected_name:
        return "", None

    try:
        index = known_names.index(selected_name)
    except ValueError:
        return selected_name, None

    return selected_name, np.array(known_encodings[index], dtype=np.float32)


def face_status_snapshot() -> dict:
    with _face_lock:
        return {
            "known": bool(_face_runtime["known"]),
            "label": str(_face_runtime["label"]),
            "distance": _face_runtime["distance"],
            "last_seen_at": float(_face_runtime["last_seen_at"]),
            "template_loaded": bool(_face_runtime["template_loaded"]),
            "selected_name": str(_face_runtime["selected_name"]),
            "names": list(_face_known_names),
        }


def _detect_selected_face_on_frame(
    frame_bgr: np.ndarray,
    selected_name: str,
    known_names: list[str],
    known_encodings: list[np.ndarray],
) -> tuple[bool, str, Optional[float], Optional[tuple[int, int, int, int]]]:
    known = False
    label = ""
    distance = None
    bbox = None

    h, w = frame_bgr.shape[:2]
    scan_frame = frame_bgr[:, : w // 2] if w > 700 else frame_bgr

    scan_h, scan_w = scan_frame.shape[:2]
    if scan_w > max(64, FACE_SCAN_MAX_WIDTH):
        scale = float(FACE_SCAN_MAX_WIDTH) / float(scan_w)
        resized_w = int(scan_w * scale)
        resized_h = int(scan_h * scale)
        frame_for_face = cv2.resize(scan_frame, (resized_w, resized_h), interpolation=cv2.INTER_AREA)
        scale_x = float(scan_w) / float(resized_w)
        scale_y = float(scan_h) / float(resized_h)
    else:
        frame_for_face = scan_frame
        scale_x = 1.0
        scale_y = 1.0

    rgb_small = cv2.cvtColor(frame_for_face, cv2.COLOR_BGR2RGB)
    model = "cnn" if FACE_DETECT_MODEL == "cnn" else "hog"
    face_locations = face_recognition.face_locations(rgb_small, model=model)
    face_encodings = face_recognition.face_encodings(rgb_small, face_locations)

    try:
        selected_idx = known_names.index(selected_name)
    except ValueError:
        return False, "", None, None

    strict_threshold = min(FACE_MATCH_THRESHOLD, FACE_SELECTED_MAX_DISTANCE)

    best_selected_distance = None
    best_bbox = None

    for (top, right, bottom, left), encoding in zip(face_locations, face_encodings):
        probe = np.array(encoding, dtype=np.float32)
        all_distances = [float(np.linalg.norm(probe - np.array(ref, dtype=np.float32))) for ref in known_encodings]
        if not all_distances:
            continue

        selected_distance = all_distances[selected_idx]
        others = [dist for idx, dist in enumerate(all_distances) if idx != selected_idx]
        best_other_distance = min(others) if others else 999.0
        best_distance = min(all_distances)

        is_selected_best = selected_distance <= (best_distance + 1e-9)
        has_margin = (best_other_distance - selected_distance) >= FACE_IMPOSTOR_MARGIN
        is_valid = (
            selected_distance <= strict_threshold
            and is_selected_best
            and has_margin
        )

        if not is_valid:
            continue

        if best_selected_distance is None or selected_distance < best_selected_distance:
            best_selected_distance = selected_distance
            left_px = int(left * scale_x)
            top_px = int(top * scale_y)
            right_px = int(right * scale_x)
            bottom_px = int(bottom * scale_y)
            best_bbox = (
                left_px,
                top_px,
                max(1, right_px - left_px),
                max(1, bottom_px - top_px),
            )

    if best_selected_distance is not None and best_bbox is not None:
        distance = best_selected_distance
        known = True
        label = selected_name
        bbox = best_bbox

    return known, label, distance, bbox


def face_worker_loop() -> None:
    while not estado.stop_event.is_set():
        _zed_face_event.wait(timeout=0.15)
        _zed_face_event.clear()

        with _zed_face_lock:
            frame = _zed_face_state["frame"]
            pending = bool(_zed_face_state["pending"])
            _zed_face_state["frame"] = None

        if not pending or frame is None:
            continue

        now = util.now()
        selected_name, selected_encoding = _selected_face_encoding()
        with _face_lock:
            known_names = list(_face_known_names)
            known_encodings = [np.array(enc, dtype=np.float32) for enc in _face_known_encodings]

        known = False
        label = ""
        distance = None
        bbox = None

        if selected_name and selected_encoding is not None and known_names and known_encodings:
            try:
                known, label, distance, bbox = _detect_selected_face_on_frame(
                    frame,
                    selected_name,
                    known_names,
                    known_encodings,
                )
            except Exception as exc:
                log.warning("Falha na deteccao facial da ZED: %s", exc)

        with _face_lock:
            _face_runtime["known"] = known
            _face_runtime["label"] = label
            _face_runtime["distance"] = distance
            _face_runtime["bbox"] = bbox if known else None
            _face_runtime["next_scan_at"] = now + FACE_SCAN_INTERVAL
            _face_runtime["template_loaded"] = bool(_face_known_names)
            _face_runtime["selected_name"] = selected_name
            if bbox is not None:
                _face_runtime["last_seen_at"] = now
            if known and now - float(_face_runtime["last_announce_at"]) >= 4.0:
                log.info("Face reconhecida na ZED: %s", selected_name)
                _face_runtime["last_announce_at"] = now

        with _zed_face_lock:
            _zed_face_state["pending"] = False
            _zed_face_state["known"] = known
            _zed_face_state["label"] = label
            _zed_face_state["distance"] = distance
            _zed_face_state["bbox"] = bbox if known else None
            _zed_face_state["next_scan_at"] = now + FACE_SCAN_INTERVAL



def offer_frame(frame, now: float):
    """Oferece um quadro da ZED ao reconhecimento e devolve a ultima marcacao.

    Retorna (known, label, bbox) para desenhar sobre o quadro.
    """
    with _zed_face_lock:
        should_queue = (not bool(_zed_face_state["pending"])) and (now >= float(_zed_face_state["next_scan_at"]))
        if should_queue:
            _zed_face_state["pending"] = True
            _zed_face_state["frame"] = frame.copy()
            _zed_face_event.set()
        known = bool(_zed_face_state["known"])
        label = str(_zed_face_state["label"])
        bbox = _zed_face_state["bbox"]
    return known, label, bbox



def register_face(raw_name: str, image_file) -> tuple[dict, int]:
    """Cadastra (ou atualiza) um rosto a partir de uma foto; retorna (payload, status HTTP)."""
    face_name = _normalize_face_name(raw_name)

    if not face_name:
        return {"ok": False, "error": "name_required"}, 400
    if image_file is None or not image_file.filename:
        return {"ok": False, "error": "image_required"}, 400

    image_bytes = image_file.read()
    if not image_bytes:
        return {"ok": False, "error": "empty_image"}, 400

    frame = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return {"ok": False, "error": "invalid_image"}, 400

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    encodings = face_recognition.face_encodings(rgb)
    if len(encodings) == 0:
        return {"ok": False, "error": "no_face_detected"}, 400

    descriptor = np.array(encodings[0], dtype=np.float32)
    extension = os.path.splitext(image_file.filename or "")[1].lower()
    if extension not in (".jpg", ".jpeg", ".png", ".bmp", ".webp"):
        extension = ".jpg"

    os.makedirs(FACE_UPLOADS_DIR, exist_ok=True)
    stored_path = os.path.join(FACE_UPLOADS_DIR, f"{_face_name_slug(face_name)}{extension}")
    if not cv2.imwrite(stored_path, frame):
        return {"ok": False, "error": "failed_to_save_image"}, 500

    try:
        known_encodings, known_names, known_image_paths = _load_face_database()
    except FileNotFoundError:
        known_encodings, known_names, known_image_paths = [], [], []
    except Exception as exc:
        # Nao sobrescreve um banco que existe mas nao pode ser lido: perderia todos os rostos.
        log.error("Banco de rostos ilegivel (%s): cadastro recusado para nao apagar %s", exc, ENCODINGS_FILE)
        return {"ok": False, "error": "face_database_unreadable"}, 500

    target_index = None
    face_name_lower = face_name.lower()
    for index, existing_name in enumerate(known_names):
        if str(existing_name).lower() == face_name_lower:
            target_index = index
            break

    # Caminho relativo a pasta que contem logs/ (no executavel ela fica fora do projeto).
    project_image_path = os.path.relpath(stored_path, os.path.dirname(FACE_DB_DIR))
    if target_index is None:
        known_encodings.append(descriptor)
        known_names.append(face_name)
        known_image_paths.append(project_image_path)
    else:
        known_encodings[target_index] = descriptor
        known_names[target_index] = face_name
        known_image_paths[target_index] = project_image_path

    _save_face_database(known_encodings, known_names, known_image_paths)
    load_known_faces()
    set_selected_face(face_name)

    return {
        "ok": True,
        "name": face_name,
        "image_path": project_image_path,
        "face": face_status_snapshot(),
    }, 200
