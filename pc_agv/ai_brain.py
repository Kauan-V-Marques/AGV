#!/usr/bin/env python3
"""
AGV Brain: rede neural simples + banco de experiencias (SQLite).

Objetivo:
- Aprender a sugerir (speed, steering) com base no estado atual.
- Registrar experiencia em banco para evoluir com o tempo.
- Treino incremental em background, sem bloquear o servidor.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Dict, Tuple, Optional, List

import numpy as np


@dataclass
class BrainMetrics:
    samples: int
    train_steps: int
    last_loss: float
    model_version: int
    updated_at: float


class AGVBrain:
    def __init__(self, db_path: str, lr: float = 0.003, hidden: int = 24) -> None:
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

        # Rede neural: 8 -> hidden -> 2 (speed_norm, steering_norm)
        self.in_dim = 8
        self.hid_dim = hidden
        self.out_dim = 2
        self.lr = lr

        rng = np.random.default_rng(42)
        self.W1 = rng.normal(0.0, 0.2, size=(self.in_dim, self.hid_dim)).astype(np.float32)
        self.b1 = np.zeros((self.hid_dim,), dtype=np.float32)
        self.W2 = rng.normal(0.0, 0.2, size=(self.hid_dim, self.out_dim)).astype(np.float32)
        self.b2 = np.zeros((self.out_dim,), dtype=np.float32)

        self.metrics = BrainMetrics(
            samples=0,
            train_steps=0,
            last_loss=0.0,
            model_version=1,
            updated_at=time.time(),
        )

        self.last_action: Tuple[int, int] = (0, 0)
        self.last_features: Optional[np.ndarray] = None

        self._load_or_init_metrics()

    def _init_db(self) -> None:
        cur = self._conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS experiences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL NOT NULL,
                distance_m REAL,
                fps_rgb REAL,
                fps_depth REAL,
                tilt_deg REAL,
                accel_x REAL,
                accel_y REAL,
                accel_z REAL,
                mode TEXT,
                speed INTEGER,
                steering INTEGER,
                reward REAL DEFAULT 0.0,
                source TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS brain_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def _load_or_init_metrics(self) -> None:
        cur = self._conn.cursor()
        cur.execute("SELECT value FROM brain_meta WHERE key='train_steps'")
        row = cur.fetchone()
        if row:
            self.metrics.train_steps = int(row[0])

        cur.execute("SELECT COUNT(*) FROM experiences")
        self.metrics.samples = int(cur.fetchone()[0])

    def _set_meta(self, key: str, value: str) -> None:
        cur = self._conn.cursor()
        cur.execute(
            "INSERT INTO brain_meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self._conn.commit()

    @staticmethod
    def _safe_float(v: Optional[float], default: float = 0.0) -> float:
        if v is None:
            return default
        try:
            x = float(v)
            if np.isnan(x) or np.isinf(x):
                return default
            return x
        except Exception:
            return default

    def _features_from_state(self, state: Dict) -> np.ndarray:
        # Features normalizadas para facilitar treino estável.
        distance = self._safe_float(state.get("distance_m"), 2.0)
        fps_rgb = self._safe_float(state.get("fps_rgb"), 0.0)
        fps_depth = self._safe_float(state.get("fps_depth"), 0.0)
        tilt = self._safe_float(state.get("tilt_deg"), 0.0)
        accel = state.get("accel") or [0.0, 0.0, 0.0]
        ax = self._safe_float(accel[0] if len(accel) > 0 else 0.0)
        ay = self._safe_float(accel[1] if len(accel) > 1 else 0.0)
        az = self._safe_float(accel[2] if len(accel) > 2 else 0.0)

        mode = str((state.get("agv") or {}).get("mode", "manual")).lower()
        mode_auto = 1.0 if mode == "auto" else 0.0

        x = np.array(
            [
                np.clip(distance / 4.0, 0.0, 1.5),
                np.clip(fps_rgb / 30.0, 0.0, 2.0),
                np.clip(fps_depth / 30.0, 0.0, 2.0),
                np.clip(tilt / 30.0, -1.5, 1.5),
                np.clip(ax / 12.0, -2.0, 2.0),
                np.clip(ay / 12.0, -2.0, 2.0),
                np.clip(az / 12.0, -2.0, 2.0),
                mode_auto,
            ],
            dtype=np.float32,
        )
        return x

    def _forward(self, x: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        z1 = x @ self.W1 + self.b1
        h1 = np.tanh(z1)
        z2 = h1 @ self.W2 + self.b2
        y = np.tanh(z2)  # faixa -1..1
        return z1, h1, y

    def recommend(self, state: Dict) -> Dict:
        with self._lock:
            x = self._features_from_state(state)
            _, _, y = self._forward(x)
            speed = int(np.clip(y[0] * 100.0, -100, 100))
            steering = int(np.clip(y[1] * 100.0, -100, 100))
            self.last_features = x
            self.last_action = (speed, steering)

            # Regras de seguranca em cima da rede
            distance = self._safe_float(state.get("distance_m"), 2.0)
            if distance < 0.35:
                speed = min(speed, 0)
                steering = 50 if steering >= 0 else -50
            elif distance < 0.60 and speed > 40:
                speed = 30

            return {
                "speed": speed,
                "steering": steering,
                "confidence": float(np.clip(1.0 - abs(distance - 1.2) / 2.0, 0.05, 0.99)),
                "model_version": self.metrics.model_version,
            }

    def record(self, state: Dict, speed: int, steering: int, source: str, reward: float = 0.0) -> None:
        accel = state.get("accel") or [0.0, 0.0, 0.0]
        agv = state.get("agv") or {}

        cur = self._conn.cursor()
        cur.execute(
            """
            INSERT INTO experiences(
                created_at, distance_m, fps_rgb, fps_depth, tilt_deg,
                accel_x, accel_y, accel_z, mode, speed, steering, reward, source
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                time.time(),
                self._safe_float(state.get("distance_m"), None),
                self._safe_float(state.get("fps_rgb"), 0.0),
                self._safe_float(state.get("fps_depth"), 0.0),
                self._safe_float(state.get("tilt_deg"), 0.0),
                self._safe_float(accel[0] if len(accel) > 0 else 0.0),
                self._safe_float(accel[1] if len(accel) > 1 else 0.0),
                self._safe_float(accel[2] if len(accel) > 2 else 0.0),
                str(agv.get("mode", "manual")),
                int(np.clip(speed, -100, 100)),
                int(np.clip(steering, -100, 100)),
                float(np.clip(reward, -2.0, 2.0)),
                str(source),
            ),
        )
        self._conn.commit()
        self.metrics.samples += 1
        self.metrics.updated_at = time.time()

    def apply_feedback(self, reward: float) -> None:
        reward = float(np.clip(reward, -2.0, 2.0))
        cur = self._conn.cursor()
        cur.execute("SELECT id FROM experiences ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE experiences SET reward=? WHERE id=?", (reward, row[0]))
            self._conn.commit()

    def _load_batch(self, batch_size: int = 64) -> Tuple[np.ndarray, np.ndarray]:
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT distance_m, fps_rgb, fps_depth, tilt_deg,
                   accel_x, accel_y, accel_z, mode,
                   speed, steering, reward
            FROM experiences
            ORDER BY id DESC
            LIMIT ?
            """,
            (batch_size,),
        )
        rows = cur.fetchall()
        if not rows:
            return np.empty((0, self.in_dim), dtype=np.float32), np.empty((0, self.out_dim), dtype=np.float32)

        xs: List[np.ndarray] = []
        ys: List[np.ndarray] = []

        for r in rows:
            mode_auto = 1.0 if str(r[7]).lower() == "auto" else 0.0
            x = np.array(
                [
                    np.clip(self._safe_float(r[0], 2.0) / 4.0, 0.0, 1.5),
                    np.clip(self._safe_float(r[1], 0.0) / 30.0, 0.0, 2.0),
                    np.clip(self._safe_float(r[2], 0.0) / 30.0, 0.0, 2.0),
                    np.clip(self._safe_float(r[3], 0.0) / 30.0, -1.5, 1.5),
                    np.clip(self._safe_float(r[4], 0.0) / 12.0, -2.0, 2.0),
                    np.clip(self._safe_float(r[5], 0.0) / 12.0, -2.0, 2.0),
                    np.clip(self._safe_float(r[6], 0.0) / 12.0, -2.0, 2.0),
                    mode_auto,
                ],
                dtype=np.float32,
            )

            speed_n = np.clip(self._safe_float(r[8], 0.0) / 100.0, -1.0, 1.0)
            steering_n = np.clip(self._safe_float(r[9], 0.0) / 100.0, -1.0, 1.0)
            reward = self._safe_float(r[10], 0.0)

            # Peso por recompensa para aprender com amostras melhores.
            gain = np.clip(1.0 + reward * 0.35, 0.25, 1.75)
            y = np.array([speed_n * gain, steering_n * gain], dtype=np.float32)
            y = np.clip(y, -1.0, 1.0)

            xs.append(x)
            ys.append(y)

        return np.vstack(xs), np.vstack(ys)

    def train_step(self, batch_size: int = 64) -> Dict:
        with self._lock:
            x, y_true = self._load_batch(batch_size=batch_size)
            if x.shape[0] < 8:
                return {
                    "ok": False,
                    "reason": "samples_insufficient",
                    "samples": int(x.shape[0]),
                }

            # Forward batch
            z1 = x @ self.W1 + self.b1
            h1 = np.tanh(z1)
            z2 = h1 @ self.W2 + self.b2
            y_pred = np.tanh(z2)

            # MSE
            err = y_pred - y_true
            loss = float(np.mean(err * err))

            # Backprop (tanh')
            n = float(x.shape[0])
            d_z2 = (2.0 / n) * err * (1.0 - y_pred * y_pred)
            dW2 = h1.T @ d_z2
            db2 = np.sum(d_z2, axis=0)

            d_h1 = d_z2 @ self.W2.T
            d_z1 = d_h1 * (1.0 - h1 * h1)
            dW1 = x.T @ d_z1
            db1 = np.sum(d_z1, axis=0)

            # Update
            self.W2 -= self.lr * dW2
            self.b2 -= self.lr * db2
            self.W1 -= self.lr * dW1
            self.b1 -= self.lr * db1

            self.metrics.train_steps += 1
            self.metrics.last_loss = loss
            self.metrics.updated_at = time.time()
            if self.metrics.train_steps % 50 == 0:
                self.metrics.model_version += 1

            self._set_meta("train_steps", str(self.metrics.train_steps))
            self._set_meta("last_loss", f"{self.metrics.last_loss:.8f}")
            self._set_meta("model_version", str(self.metrics.model_version))

            return {
                "ok": True,
                "loss": loss,
                "train_steps": self.metrics.train_steps,
                "model_version": self.metrics.model_version,
                "samples_used": int(x.shape[0]),
            }

    def status(self) -> Dict:
        return {
            "samples": self.metrics.samples,
            "train_steps": self.metrics.train_steps,
            "last_loss": round(float(self.metrics.last_loss), 7),
            "model_version": self.metrics.model_version,
            "updated_at": self.metrics.updated_at,
            "last_action": {
                "speed": int(self.last_action[0]),
                "steering": int(self.last_action[1]),
            },
        }

    def close(self) -> None:
        with self._lock:
            self._conn.commit()
            self._conn.close()
