"""Treino do modelo a partir das amostras do banco (sem estado global).

Fase 1 (imitacao): so ha amostras do operador; a rede aprende a repetir o comando
dele para cada leitura de profundidade.
Fase 2 (recompensa): entram tambem as acoes da propria IA, pesadas pela recompensa,
e as correcoes do operador, com peso dobrado.
"""

import time
from typing import Callable, Optional

import numpy as np

from pc_agv.aprendizado import entrada, recompensa
from pc_agv.aprendizado.rede import RedeNeural


# A saida da rede e tanh: o alvo fica em +-0,95 para nao exigir pesos infinitos.
ESCALA_SAIDA = 0.95
PESO_CORRECAO = 2.0
FRACAO_VALIDACAO = 0.2
ZONA_MORTA = 15  # |comando| abaixo disso conta como "parado"/"reto" nas metricas


class DadosInsuficientes(Exception):
    pass


def para_alvo(comando: np.ndarray) -> np.ndarray:
    return np.clip(np.asarray(comando, dtype=np.float64) / 100.0, -1.0, 1.0) * ESCALA_SAIDA


def para_comando(saida: np.ndarray) -> np.ndarray:
    return np.clip(np.asarray(saida) / ESCALA_SAIDA, -1.0, 1.0) * 100.0


def montar_conjunto(dados: dict) -> dict:
    """Entradas, alvos e pesos de todas as amostras usaveis."""
    origem = dados["origem"]
    if len(origem):
        x = entrada.extrair_entradas(dados["minis"])
        y = np.stack([para_alvo(dados["velocidade"]), para_alvo(dados["direcao"])], axis=1)
        frente = entrada.distancia_frente(dados["minis"])
    else:
        x = np.zeros((0, entrada.TAMANHO_ENTRADA), dtype=np.float32)
        y = np.zeros((0, 2))
        frente = np.zeros(0)
    r = recompensa.recompensas(
        dados["sessao"], dados["tempo"], origem, dados["velocidade"], dados["direcao"], frente, dados["evento"],
    )
    g = recompensa.retornos(dados["sessao"], dados["tempo"], origem, r)

    pesos = np.zeros(len(origem))
    pesos[origem == "operador"] = 1.0
    pesos[origem == "correcao"] = PESO_CORRECAO
    da_ia = origem == "ia"
    if da_ia.any():
        pesos[da_ia] = recompensa.pesos_por_retorno(g[da_ia])

    humanas = int((origem == "operador").sum() + (origem == "correcao").sum())
    return {
        "x": x,
        "y": y,
        "w": pesos,
        "humana": ~da_ia,
        "fase": "recompensa" if da_ia.any() else "imitacao",
        "contagem": {
            "operador": int((origem == "operador").sum()),
            "correcao": int((origem == "correcao").sum()),
            "ia": int(da_ia.sum()),
            "humanas": humanas,
        },
        "recompensa_media": float(np.nanmean(r)) if da_ia.any() else None,
    }


def separar_validacao(n: int) -> tuple[np.ndarray, np.ndarray]:
    """Validacao = ultimos 20% em ordem de gravacao (amostras vizinhas sao quase iguais,
    entao sortear misturaria treino e validacao)."""
    corte = max(1, int(round(n * (1.0 - FRACAO_VALIDACAO))))
    corte = min(corte, n - 1)
    indices = np.arange(n)
    return indices[:corte], indices[corte:]


def _classe(comando: np.ndarray) -> np.ndarray:
    return np.where(comando > ZONA_MORTA, 1, np.where(comando < -ZONA_MORTA, -1, 0))


def avaliar(rede: RedeNeural, x: np.ndarray, y: np.ndarray) -> dict:
    """Quanto a rede concorda com o operador, em termos que o painel pode mostrar."""
    if x.shape[0] == 0:
        return {"amostras": 0}
    previsto = para_comando(rede.prever(x))
    real = para_comando(y)
    return {
        "amostras": int(x.shape[0]),
        "erro_velocidade": round(float(np.abs(previsto[:, 0] - real[:, 0]).mean()), 1),
        "erro_direcao": round(float(np.abs(previsto[:, 1] - real[:, 1]).mean()), 1),
        "acerto_movimento": round(float((_classe(previsto[:, 0]) == _classe(real[:, 0])).mean()), 3),
        "acerto_direcao": round(float((_classe(previsto[:, 1]) == _classe(real[:, 1])).mean()), 3),
    }


def treinar(
    dados: dict,
    minimo_humanas: int,
    semente: int = 0,
    max_epocas: int = 200,
    progresso: Optional[Callable[[int, int], None]] = None,
) -> tuple[RedeNeural, dict]:
    inicio = time.monotonic()
    conjunto = montar_conjunto(dados)
    contagem = conjunto["contagem"]
    if contagem["humanas"] < minimo_humanas:
        raise DadosInsuficientes(
            f"{contagem['humanas']} amostras do operador; sao necessarias {minimo_humanas}"
        )

    x, y, w = conjunto["x"], conjunto["y"], conjunto["w"]
    usar = w > 0
    x, y, w, humana = x[usar], y[usar], w[usar], conjunto["humana"][usar]
    treino, validacao = separar_validacao(x.shape[0])

    rede = RedeNeural(entrada.TAMANHO_ENTRADA, semente=semente)
    historico = rede.treinar(
        x[treino], y[treino], w[treino], x[validacao], y[validacao], w[validacao],
        max_epocas=max_epocas, semente=semente, progresso=progresso,
    )

    # O acerto mostrado ao usuario compara com o que o operador fez (nao com a propria IA).
    val_humana = validacao[humana[validacao]]
    if val_humana.size == 0:
        val_humana = validacao
    info = {
        "fase": conjunto["fase"],
        "versao_entrada": entrada.VERSAO_ENTRADA,
        "amostras": contagem,
        "treino": int(treino.size),
        "validacao": avaliar(rede, x[val_humana], y[val_humana]),
        "epocas": len(historico),
        "historico": historico,
        "recompensa_media": conjunto["recompensa_media"],
        "duracao_s": round(time.monotonic() - inicio, 2),
    }
    return rede, info
