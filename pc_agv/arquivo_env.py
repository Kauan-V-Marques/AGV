"""Arquivo agv.env: configuracao local do AGV sem precisar exportar variaveis.

Cada linha `NOME=valor` vira uma variavel de ambiente, salvo se ela ja estiver
definida (o ambiente sempre tem prioridade). Linhas vazias e comentarios (#) sao
ignorados. So o launcher carrega o arquivo, antes de importar o restante do
pc_agv: os testes nunca dependem da configuracao de uma maquina.
"""

import os
from typing import Optional


def ler(caminho: str) -> dict[str, str]:
    valores: dict[str, str] = {}
    with open(caminho, "r", encoding="utf-8") as arquivo:
        for linha in arquivo:
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            nome, valor = linha.split("=", 1)
            nome = nome.strip()
            if nome.startswith("export "):
                nome = nome[len("export "):].strip()
            valor = valor.strip()
            if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
                valor = valor[1:-1]
            if nome:
                valores[nome] = valor
    return valores


def carregar(caminho: Optional[str] = None) -> list[str]:
    """Aplica o agv.env da raiz do projeto (ou AGV_ENV_ARQUIVO). Devolve os nomes aplicados."""
    if caminho is None:
        raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        caminho = os.environ.get("AGV_ENV_ARQUIVO", "").strip() or os.path.join(raiz, "agv.env")
    if not os.path.isfile(caminho):
        return []
    aplicados = []
    for nome, valor in ler(caminho).items():
        if nome not in os.environ:
            os.environ[nome] = valor
            aplicados.append(nome)
    return aplicados
