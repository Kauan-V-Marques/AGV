"""Banco SQLite dos mapas (logs/mapas.db): mapas salvos e preferencias do mapeamento."""

import threading
import time
from typing import Any, Optional

import numpy as np

from pc_agv import sqlite_util


ESQUEMA = sqlite_util.ESQUEMA_META + """
CREATE TABLE IF NOT EXISTS mapas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    criado_em REAL NOT NULL,
    atualizado_em REAL NOT NULL,
    resolucao REAL NOT NULL,
    grade BLOB NOT NULL,
    trajetoria BLOB NOT NULL,
    distancia_m REAL NOT NULL,
    area_m2 REAL NOT NULL
);
"""

COLUNAS_RESUMO = "id, nome, criado_em, atualizado_em, resolucao, distancia_m, area_m2"


def trajetoria_para_bytes(pontos) -> bytes:
    return np.asarray(pontos, dtype=np.float32).reshape(-1, 2).tobytes()


def trajetoria_de_bytes(dados: bytes) -> np.ndarray:
    return np.frombuffer(dados, dtype=np.float32).reshape(-1, 2).astype(np.float64)


class BancoMapas:
    def __init__(self, caminho: str):
        self.caminho = caminho
        self._lock = threading.Lock()
        # Arquivo corrompido gera erro aqui e o servico segue sem salvar mapas (sem apagar nada).
        self._con = sqlite_util.conectar(caminho)
        with self._lock, self._con:
            self._con.executescript(ESQUEMA)

    def fechar(self) -> None:
        with self._lock:
            self._con.close()

    def ler_preferencia(self, chave: str, padrao: Any) -> Any:
        with self._lock:
            return sqlite_util.ler_preferencia(self._con, chave, padrao)

    def gravar_preferencia(self, chave: str, valor: Any) -> None:
        with self._lock:
            sqlite_util.gravar_preferencia(self._con, chave, valor)

    def salvar(self, nome: str, grade: bytes, trajetoria: bytes, resolucao: float, distancia_m: float,
               area_m2: float, mapa_id: Optional[int] = None) -> int:
        """Grava um mapa novo, ou atualiza o de `mapa_id` se ele ainda existir."""
        agora = time.time()
        with self._lock, self._con:
            if mapa_id is not None:
                cursor = self._con.execute(
                    "UPDATE mapas SET nome = ?, atualizado_em = ?, resolucao = ?, grade = ?, trajetoria = ?, "
                    "distancia_m = ?, area_m2 = ? WHERE id = ?",
                    (nome, agora, resolucao, grade, trajetoria, distancia_m, area_m2, int(mapa_id)),
                )
                if cursor.rowcount:
                    return int(mapa_id)
            cursor = self._con.execute(
                "INSERT INTO mapas (nome, criado_em, atualizado_em, resolucao, grade, trajetoria, distancia_m, area_m2) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (nome, agora, agora, resolucao, grade, trajetoria, distancia_m, area_m2),
            )
            return int(cursor.lastrowid)

    def listar(self) -> list[dict]:
        with self._lock:
            linhas = self._con.execute(f"SELECT {COLUNAS_RESUMO} FROM mapas ORDER BY atualizado_em DESC").fetchall()
        return [dict(linha) for linha in linhas]

    def carregar(self, mapa_id: int) -> Optional[dict]:
        with self._lock:
            linha = self._con.execute("SELECT * FROM mapas WHERE id = ?", (int(mapa_id),)).fetchone()
        if linha is None:
            return None
        mapa = dict(linha)
        mapa["grade"] = bytes(mapa["grade"])
        mapa["trajetoria"] = bytes(mapa["trajetoria"])
        return mapa

    def apagar(self, mapa_id: int) -> bool:
        with self._lock, self._con:
            return self._con.execute("DELETE FROM mapas WHERE id = ?", (int(mapa_id),)).rowcount > 0
