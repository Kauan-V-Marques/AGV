#!/usr/bin/env python3
"""
Teste ao vivo de reconhecimento facial.

USO:
  /home/kauan-linux/Downloads/Detecta_rosto/.venv/bin/python3 testar_reconhecimento.py

Funcionamento:
  - Abre a webcam (ou Kinect via V4L2)
  - Cadastra rostos em tempo real pressionando teclas
  - Mostra no frame: nome identificado, distância, threshold usado
  - 'q' sai | 'c' limpa o banco | 'r' recadastra com nome
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "pc_agv"))

# Garante que está usando o venv com face_recognition
VENV_PYTHON = "/home/kauan-linux/Downloads/Detecta_rosto/.venv/bin/python3"
if sys.executable != VENV_PYTHON and os.path.exists(VENV_PYTHON):
    os.execv(VENV_PYTHON, [VENV_PYTHON] + sys.argv)

import cv2
import json
import numpy as np
import time
try:
    import face_recognition
    FR_OK = True
except ImportError:
    print("ERRO: face_recognition nao instalado no ambiente atual.")
    print(f"Use: {VENV_PYTHON} testar_reconhecimento.py")
    sys.exit(1)

# ─── Configuracao ─────────────────────────────────────────────────────────────
THRESH_MULTI   = 0.40   # >= 3 amostras
THRESH_SINGLE  = 0.32   # 1-2 amostras (muito restritivo)
THRESH_SEL_ONLY = 0.38  # modo "selected only"
AMBIGUOUS_GAP  = 0.06
ENCODING_DIM   = 128

DB_PATH = "EXECUTAVEIS/agv_pc/logs/faces_db.json"
# ──────────────────────────────────────────────────────────────────────────────

people_db: dict[str, list] = {}   # {nome: [encoding, ...]}


def load_db():
    global people_db
    if not os.path.exists(DB_PATH):
        return
    try:
        with open(DB_PATH) as f:
            data = json.load(f)
        for name, samples in data.get("people", {}).items():
            valid = [np.array(s, dtype=np.float32) for s in samples
                     if isinstance(s, list) and len(s) == ENCODING_DIM]
            if valid:
                people_db[name] = valid
    except Exception as e:
        print(f"Erro ao carregar DB: {e}")


def save_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    data = {
        "enabled": True,
        "selected": next(iter(people_db), ""),
        "people": {n: [s.tolist() for s in slist] for n, slist in people_db.items()},
    }
    with open(DB_PATH + ".tmp", "w") as f:
        json.dump(data, f)
    os.replace(DB_PATH + ".tmp", DB_PATH)


def get_encoding(bgr_frame) -> tuple[np.ndarray | None, tuple | None]:
    rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
    locs = face_recognition.face_locations(rgb, model="hog")
    if not locs:
        locs = face_recognition.face_locations(rgb, model="cnn")
    if not locs:
        return None, None
    best = max(locs, key=lambda l: (l[2]-l[0]) * (l[1]-l[3]))
    encs = face_recognition.face_encodings(rgb, [best], num_jitters=2, model="large")
    if not encs:
        return None, None
    enc = np.array(encs[0], dtype=np.float32)
    top, right, bottom, left = best
    return enc, (left, top, right-left, bottom-top)


def register_person(name: str, frame):
    """Cadastra uma pessoa com augmentacao (ate 5 amostras de 1 foto)."""
    h, w = frame.shape[:2]
    variants = [
        frame,
        np.clip(frame.astype(np.float32) * 0.75, 0, 255).astype(np.uint8),
        np.clip(frame.astype(np.float32) * 1.25, 0, 255).astype(np.uint8),
        cv2.resize(frame[int(h*0.05):int(h*0.95), :], (w, h)),
        cv2.resize(frame[:, int(w*0.05):int(w*0.95)], (w, h)),
    ]
    sigs = []
    for v in variants:
        sig, _ = get_encoding(v)
        if sig is not None:
            sigs.append(sig)
    if not sigs:
        print(f"[CADASTRO] Nenhum rosto detectado para '{name}'")
        return False
    people_db[name] = sigs
    save_db()
    print(f"[CADASTRO] '{name}' salvo com {len(sigs)} amostras.")
    return True


def match(signature) -> tuple[str, float, bool]:
    if not people_db:
        return "Desconhecido", float("inf"), False

    best_name = "Desconhecido"
    best_dist = float("inf")
    second_dist = float("inf")
    best_score = float("inf")
    best_count = 0

    print("\n  --- distâncias ---")
    for name, samples in people_db.items():
        dists = []
        for s in samples:
            if s.shape != signature.shape:
                continue
            dists.append(float(np.linalg.norm(signature - s)))
        if not dists:
            print(f"  {name}: SEM amostras compatíveis")
            continue
        dists.sort()
        top_k = dists[:min(3, len(dists))]
        score = float(np.mean(top_k))
        best_d = dists[0]
        print(f"  {name}: melhor={best_d:.4f}  score_top3={score:.4f}  ({len(dists)} amostras)")
        if score < best_score:
            second_dist = best_dist
            best_score = score
            best_dist = best_d
            best_name = name
            best_count = len(dists)
        elif best_d < second_dist:
            second_dist = best_d

    if best_dist == float("inf"):
        return "Desconhecido", float("inf"), False

    thresh = THRESH_MULTI
    if best_count <= 2:
        thresh = min(thresh, THRESH_SINGLE)
    if len(people_db) == 1:
        thresh = min(thresh, THRESH_SEL_ONLY)
    print(f"  threshold={thresh:.3f}  best_dist={best_dist:.4f}  2nd={second_dist:.4f}")

    known = (best_dist <= thresh) and (best_score <= thresh + 0.04)
    if known and second_dist < float("inf"):
        if (second_dist - best_dist) < AMBIGUOUS_GAP:
            known = False
            print("  → AMBÍGUO, rejeitado")
    return (best_name if known else "Desconhecido"), best_dist, known


def main():
    load_db()
    print(f"Banco carregado: {list(people_db.keys())}")
    print()
    print("Teclas:")
    print("  'r' → registrar rosto atual (pedirá nome no terminal)")
    print("  'c' → limpar banco de dados")
    print("  'q' → sair")
    print()

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("ERRO: nao foi possivel abrir a webcam")
        sys.exit(1)

    last_result = ("--", None, False)
    last_scan = 0.0
    SCAN_INTERVAL = 0.5

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        now = time.time()
        if now - last_scan >= SCAN_INTERVAL:
            last_scan = now
            sig, bbox = get_encoding(frame)
            if sig is not None:
                label, dist, known = match(sig)
                last_result = (label, dist, known, bbox)
            else:
                last_result = ("Sem rosto", None, False, None)

        if len(last_result) == 4:
            label, dist, known, bbox = last_result
        else:
            label, dist, known, bbox = "--", None, False, None

        if bbox:
            x, y, w2, h2 = bbox
            color = (80, 240, 140) if known else (75, 120, 255)
            cv2.rectangle(frame, (x, y), (x+w2, y+h2), color, 2)
            dist_txt = f" d={dist:.3f}" if dist is not None else ""
            cv2.putText(frame, label + dist_txt,
                        (x, max(22, y - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)

        info = f"Pessoas: {list(people_db.keys())}  |  [r]cadastrar [c]limpar [q]sair"
        cv2.putText(frame, info, (10, frame.shape[0]-10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1)

        cv2.imshow("Teste Reconhecimento Facial", frame)
        key = cv2.waitKey(1) & 0xFF

        if key == ord('q'):
            break
        elif key == ord('c'):
            people_db.clear()
            save_db()
            print("[CMD] banco limpo")
        elif key == ord('r'):
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret2, frame2 = cap.read()
            if ret2:
                nome = input("Nome para cadastrar: ").strip()
                if nome:
                    register_person(nome, frame2)
                else:
                    print("Nome vazio, cancelado.")

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
