"""Banco SQLite do aprendizado (logs/aprendizado.db): amostras, modelos e preferencias.

Tudo o que a IA aprendeu fica aqui; parar e abrir o sistema de novo nao perde nada.
Uma unica conexao, protegida por lock, atende todas as threads.
"""

import json
import sqlite3
import threading
import time
from typing import Any, Optional

import numpy as np

from pc_agv.aprendizado import entrada


VERSAO_ESQUEMA = 1
MODELOS_GUARDADOS = 10

ESQUEMA = """
CREATE TABLE IF NOT EXISTS meta (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    iniciada_em REAL NOT NULL,
    modo TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS amostras (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sessao_id INTEGER NOT NULL REFERENCES sessoes(id) ON DELETE CASCADE,
    criada_em REAL NOT NULL,
    origem TEXT NOT NULL CHECK (origem IN ('operador', 'correcao', 'ia')),
    velocidade INTEGER NOT NULL,
    direcao INTEGER NOT NULL,
    profundidade BLOB NOT NULL,
    evento TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS amostras_por_sessao ON amostras(sessao_id, id);
CREATE TABLE IF NOT EXISTS modelos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    criado_em REAL NOT NULL,
    fase TEXT NOT NULL,
    versao_entrada INTEGER NOT NULL,
    info TEXT NOT NULL,
    pesos BLOB NOT NULL,
    ativo INTEGER NOT NULL DEFAULT 0
);
"""


class BancoAprendizado:
    def __init__(self, caminho: str):
        self.caminho = caminho
        self._lock = threading.Lock()
        self._con = sqlite3.connect(caminho, check_same_thread=False, timeout=10.0)
        self._con.row_factory = sqlite3.Row
        with self._lock:
            # Confere a integridade antes de escrever qualquer coisa: um arquivo
            # corrompido gera erro aqui e o servico desliga o aprendizado sem apaga-lo.
            self._con.execute("PRAGMA schema_version").fetchone()
            try:
                self._con.execute("PRAGMA journal_mode=WAL")
                # Com WAL, NORMAL nunca corrompe o banco; numa queda de energia perde no
                # maximo as ultimas amostras, e poupa uma escrita no disco por amostra.
                self._con.execute("PRAGMA synchronous=NORMAL")
            except sqlite3.DatabaseError:
                pass  # sistema de arquivos sem suporte a WAL: segue no modo padrao
            self._con.execute("PRAGMA foreign_keys=ON")
            with self._con:
                self._con.executescript(ESQUEMA)
                self._con.execute(
                    "INSERT OR IGNORE INTO meta (chave, valor) VALUES ('versao_esquema', ?)",
                    (json.dumps(VERSAO_ESQUEMA),),
                )

    def fechar(self) -> None:
        with self._lock:
            self._con.close()

    # ── Preferencias ────────────────────────────────────────────────
    def ler_preferencia(self, chave: str, padrao: Any) -> Any:
        with self._lock:
            linha = self._con.execute("SELECT valor FROM meta WHERE chave = ?", (f"pref.{chave}",)).fetchone()
        if linha is None:
            return padrao
        try:
            return json.loads(linha["valor"])
        except ValueError:
            return padrao

    def gravar_preferencia(self, chave: str, valor: Any) -> None:
        with self._lock, self._con:
            self._con.execute(
                "INSERT INTO meta (chave, valor) VALUES (?, ?) "
                "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
                (f"pref.{chave}", json.dumps(valor)),
            )

    # ── Amostras ────────────────────────────────────────────────────
    def nova_sessao(self, modo: str, iniciada_em: Optional[float] = None) -> int:
        with self._lock, self._con:
            cursor = self._con.execute(
                "INSERT INTO sessoes (iniciada_em, modo) VALUES (?, ?)",
                (time.time() if iniciada_em is None else iniciada_em, modo),
            )
            return int(cursor.lastrowid)

    def gravar_amostra(
        self,
        sessao_id: int,
        origem: str,
        velocidade: int,
        direcao: int,
        miniatura: np.ndarray,
        criada_em: float,
    ) -> int:
        dados = np.ascontiguousarray(miniatura, dtype=np.uint8).tobytes()
        if len(dados) != entrada.BYTES_MINIATURA:
            raise ValueError("miniatura com tamanho errado")
        with self._lock, self._con:
            cursor = self._con.execute(
                "INSERT INTO amostras (sessao_id, criada_em, origem, velocidade, direcao, profundidade) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sessao_id, criada_em, origem, int(velocidade), int(direcao), sqlite3.Binary(dados)),
            )
            return int(cursor.lastrowid)

    def marcar_evento(self, amostra_id: int, evento: str) -> None:
        with self._lock, self._con:
            self._con.execute("UPDATE amostras SET evento = ? WHERE id = ?", (evento, amostra_id))

    def contagem(self) -> dict:
        with self._lock:
            linhas = self._con.execute("SELECT origem, COUNT(*) AS n FROM amostras GROUP BY origem").fetchall()
        contagem = {"operador": 0, "correcao": 0, "ia": 0}
        for linha in linhas:
            contagem[linha["origem"]] = int(linha["n"])
        contagem["total"] = sum(contagem.values())
        return contagem

    def carregar_amostras(self) -> dict:
        """Todas as amostras em ordem de gravacao, como arrays para o treino."""
        with self._lock:
            linhas = self._con.execute(
                "SELECT id, sessao_id, criada_em, origem, velocidade, direcao, profundidade, evento "
                "FROM amostras ORDER BY id"
            ).fetchall()
        validas = []
        minis = []
        for linha in linhas:
            mini = entrada.miniatura_de_bytes(bytes(linha["profundidade"]))
            if mini is None:
                continue
            validas.append(linha)
            minis.append(mini)
        return {
            "id": np.array([linha["id"] for linha in validas], dtype=np.int64),
            "sessao": np.array([linha["sessao_id"] for linha in validas], dtype=np.int64),
            "tempo": np.array([linha["criada_em"] for linha in validas], dtype=np.float64),
            "origem": np.array([linha["origem"] for linha in validas], dtype=object),
            "velocidade": np.array([linha["velocidade"] for linha in validas], dtype=np.float64),
            "direcao": np.array([linha["direcao"] for linha in validas], dtype=np.float64),
            "evento": np.array([linha["evento"] for linha in validas], dtype=object),
            "minis": np.stack(minis) if minis else np.zeros((0, entrada.LINHAS, entrada.COLUNAS), dtype=np.uint8),
        }

    def ultimo_id(self) -> int:
        """Id da amostra mais recente ja gravada (cresce sempre, mesmo apos apagar)."""
        with self._lock:
            linha = self._con.execute("SELECT seq FROM sqlite_sequence WHERE name = 'amostras'").fetchone()
        return int(linha[0]) if linha else 0

    def limitar_amostras(self, maximo: int) -> int:
        """Apaga as amostras mais antigas acima do limite. Devolve quantas apagou."""
        with self._lock, self._con:
            total = self._con.execute("SELECT COUNT(*) FROM amostras").fetchone()[0]
            excesso = int(total) - int(maximo)
            if excesso <= 0:
                return 0
            self._con.execute(
                "DELETE FROM amostras WHERE id IN (SELECT id FROM amostras ORDER BY id LIMIT ?)", (excesso,),
            )
            self._con.execute("DELETE FROM sessoes WHERE id NOT IN (SELECT DISTINCT sessao_id FROM amostras)")
            return excesso

    def apagar_amostras(self) -> None:
        with self._lock, self._con:
            self._con.execute("DELETE FROM amostras")
            self._con.execute("DELETE FROM sessoes")

    # ── Modelos ─────────────────────────────────────────────────────
    def salvar_modelo(self, fase: str, versao_entrada: int, info: dict, pesos: bytes) -> int:
        """Grava o modelo como o ativo e mantem so os ultimos MODELOS_GUARDADOS."""
        with self._lock, self._con:
            self._con.execute("UPDATE modelos SET ativo = 0")
            cursor = self._con.execute(
                "INSERT INTO modelos (criado_em, fase, versao_entrada, info, pesos, ativo) VALUES (?, ?, ?, ?, ?, 1)",
                (time.time(), fase, int(versao_entrada), json.dumps(info), sqlite3.Binary(pesos)),
            )
            self._con.execute(
                "DELETE FROM modelos WHERE ativo = 0 AND id NOT IN "
                "(SELECT id FROM modelos ORDER BY id DESC LIMIT ?)",
                (MODELOS_GUARDADOS,),
            )
            return int(cursor.lastrowid)

    @staticmethod
    def _resumo(linha) -> dict:
        try:
            info = json.loads(linha["info"])
        except ValueError:
            info = {}
        return {
            "id": int(linha["id"]),
            "criado_em": float(linha["criado_em"]),
            "fase": linha["fase"],
            "versao_entrada": int(linha["versao_entrada"]),
            "ativo": bool(linha["ativo"]),
            "info": info,
        }

    def modelo_ativo(self) -> Optional[tuple[dict, bytes]]:
        with self._lock:
            linha = self._con.execute("SELECT * FROM modelos WHERE ativo = 1 ORDER BY id DESC LIMIT 1").fetchone()
        if linha is None:
            return None
        return self._resumo(linha), bytes(linha["pesos"])

    def listar_modelos(self) -> list[dict]:
        with self._lock:
            linhas = self._con.execute(
                "SELECT id, criado_em, fase, versao_entrada, info, ativo FROM modelos ORDER BY id DESC"
            ).fetchall()
        return [self._resumo(linha) for linha in linhas]

    def ativar_modelo(self, modelo_id: int) -> Optional[tuple[dict, bytes]]:
        with self._lock, self._con:
            linha = self._con.execute("SELECT * FROM modelos WHERE id = ?", (int(modelo_id),)).fetchone()
            if linha is None:
                return None
            self._con.execute("UPDATE modelos SET ativo = CASE WHEN id = ? THEN 1 ELSE 0 END", (int(modelo_id),))
        resumo = self._resumo(linha)
        resumo["ativo"] = True
        return resumo, bytes(linha["pesos"])

    def apagar_tudo(self) -> None:
        with self._lock, self._con:
            self._con.execute("DELETE FROM amostras")
            self._con.execute("DELETE FROM sessoes")
            self._con.execute("DELETE FROM modelos")
