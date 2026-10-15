"""Publica as ultimas linhas de um log como anotacao de erro do GitHub Actions.

As anotacoes aparecem no resumo do CI e na API publica do GitHub, mesmo sem login,
ao contrario do log completo. Uso: python3 anotar_erro.py <arquivo-de-log> <titulo>
"""
import sys

caminho, titulo = sys.argv[1], sys.argv[2]
with open(caminho, encoding="utf-8", errors="replace") as arquivo:
    linhas = arquivo.read().splitlines()[-40:]

texto = "\n".join(linhas).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
print(f"::error title={titulo}::{texto}")
