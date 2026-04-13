#!/usr/bin/env python3
import os
import json
import glob
import numpy as np
import cv2
import face_recognition

AFONSO_DIR = "/home/kauan-linux/Downloads/Detecta_rosto/fotos afonso"
TEST_IMG = "/home/kauan-linux/Downloads/Detecta_rosto/teste.jpg"
DB_PATHS = [
    "/home/kauan-linux/Área de trabalho/agv/logs/faces_db.json",
    "/home/kauan-linux/Área de trabalho/agv/EXECUTAVEIS/agv_pc/logs/faces_db.json",
    "/home/kauan-linux/Área de trabalho/agv/EXECUTAVEIS/teste_local_pc/logs/faces_db.json",
]
THRESH_MULTI = 0.40
THRESH_SEL = 0.38


def extract_enc(path):
    img = cv2.imread(path)
    if img is None:
        return None
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    locs = face_recognition.face_locations(rgb, model="hog")
    if not locs:
        return None
    best = max(locs, key=lambda l: (l[2] - l[0]) * (l[1] - l[3]))
    encs = face_recognition.face_encodings(rgb, [best], num_jitters=2, model="large")
    if not encs:
        return None
    enc = np.asarray(encs[0], dtype=np.float32)
    return enc if enc.size == 128 else None


def main():
    files = sorted(glob.glob(os.path.join(AFONSO_DIR, "*")))
    encs = []
    failed = []

    for f in files:
        e = extract_enc(f)
        if e is None:
            failed.append(os.path.basename(f))
        else:
            encs.append(e)

    print(f"Fotos encontradas: {len(files)}")
    print(f"Encodings validos: {len(encs)}")
    if failed:
        print("Sem face detectada:", ", ".join(failed))

    if not encs:
        raise SystemExit("ERRO: nenhuma foto gerou encoding")

    payload = {
        "enabled": True,
        "selected": "afonso",
        "people": {"afonso": [e.tolist() for e in encs]},
    }

    for p in DB_PATHS:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=True)
        os.replace(p + ".tmp", p)
        print(f"DB atualizado: {p}")

    if len(encs) >= 2:
        same_d = []
        for i, probe in enumerate(encs):
            refs = [encs[j] for j in range(len(encs)) if j != i]
            same_d.append(min(float(np.linalg.norm(probe - r)) for r in refs))
        print(
            "Afonso->Afonso media(min_dist)=",
            round(float(np.mean(same_d)), 4),
            "max=",
            round(float(np.max(same_d)), 4),
        )

    if os.path.exists(TEST_IMG):
        t = extract_enc(TEST_IMG)
        if t is None:
            print("Teste externo: sem rosto detectado em teste.jpg")
        else:
            best = min(float(np.linalg.norm(t - r)) for r in encs)
            thr = min(THRESH_MULTI, THRESH_SEL)
            verdict = "DESCONHECIDO" if best > thr else "RECONHECIDO"
            print(f"Teste externo best_dist={best:.4f} threshold={thr:.2f} => {verdict}")


if __name__ == "__main__":
    main()
