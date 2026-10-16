"""Abertura padrao e preferencias dos bancos SQLite do agv (aprendizado e mapas)."""

import json
import sqlite3
from typing import Any


def conectar(caminho: str) -> sqlite3.Connection:
    """Abre o banco para uso por varias threads (cada modulo protege com seu lock).

    Confere a integridade antes de escrever qualquer coisa: um arquivo corrompido gera
    sqlite3.DatabaseError aqui, e quem chamou desliga o recurso sem apagar o arquivo.
    """
    conexao = sqlite3.connect(caminho, check_same_thread=False, timeout=10.0)
    try:
        conexao.row_factory = sqlite3.Row
        conexao.execute("PRAGMA schema_version").fetchone()
        try:
            conexao.execute("PRAGMA journal_mode=WAL")
            # Com WAL, NORMAL nunca corrompe o banco; numa queda de energia perde no
            # maximo as ultimas escritas, e poupa uma escrita no disco por insercao.
            conexao.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.DatabaseError:
            pass  # sistema de arquivos sem suporte a WAL: segue no modo padrao
        conexao.execute("PRAGMA foreign_keys=ON")
    except Exception:
        conexao.close()
        raise
    return conexao


# Preferencias guardadas na tabela meta (chave -> JSON), com o prefixo "pref.".
ESQUEMA_META = """
CREATE TABLE IF NOT EXISTS meta (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);
"""


def ler_preferencia(conexao: sqlite3.Connection, chave: str, padrao: Any) -> Any:
    linha = conexao.execute("SELECT valor FROM meta WHERE chave = ?", (f"pref.{chave}",)).fetchone()
    if linha is None:
        return padrao
    try:
        return json.loads(linha[0])
    except ValueError:
        return padrao


def gravar_preferencia(conexao: sqlite3.Connection, chave: str, valor: Any) -> None:
    with conexao:
        conexao.execute(
            "INSERT INTO meta (chave, valor) VALUES (?, ?) ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
            (f"pref.{chave}", json.dumps(valor)),
        )
