"""Fase 2: recompensa das acoes da IA e peso de cada amostra no treino (funcoes puras).

Sem encoders, o AGV nao sabe quanto andou; a recompensa usa o que ele sabe:
o comando que deu, a distancia a frente logo depois e se o operador precisou
intervir. O treino e uma regressao ponderada pela recompensa (Peters e Schaal,
2007): acoes que deram certo pesam mais, as que deram errado quase somem.
"""

import numpy as np


GAMA = 0.8                 # desconto por passo (5 amostras/s -> horizonte de ~1 s)
INTERVALO_MAX_S = 1.0      # amostras mais afastadas que isso nao sao "o passo seguinte"
PERIGO_M = 0.35
ALERTA_M = 0.60
PREMIO_AVANCO = 0.5        # andar para frente vale ate +0,5 por passo
PENALIDADE_PERIGO = 2.0
PENALIDADE_ALERTA = 0.6
PENALIDADE_CORRECAO = 1.0  # o operador teve de corrigir logo em seguida
PENALIDADE_PARADA = 3.0    # o operador apertou a parada de emergencia
PENALIDADE_TRANCO = 0.1    # por mudanca brusca de direcao (por 100% de variacao)
LIMITE_Z = 3.0
BETA = 1.0


def _continua(sessao, tempo, i: int) -> bool:
    """A amostra i+1 e o passo seguinte de i (mesma sessao e logo depois)?"""
    return (
        i + 1 < len(sessao)
        and sessao[i + 1] == sessao[i]
        and 0.0 <= tempo[i + 1] - tempo[i] <= INTERVALO_MAX_S
    )


def recompensas(sessao, tempo, origem, velocidade, direcao, frente, evento) -> np.ndarray:
    """Recompensa de cada passo da IA (NaN nas amostras que nao sao da IA)."""
    n = len(origem)
    saida = np.full(n, np.nan)
    for i in range(n):
        if origem[i] != "ia":
            continue
        seguinte = i + 1 if _continua(sessao, tempo, i) else None
        r = PREMIO_AVANCO * max(float(velocidade[i]), 0.0) / 100.0

        distancia = frente[seguinte] if seguinte is not None else frente[i]
        if np.isfinite(distancia):
            if distancia < PERIGO_M:
                r -= PENALIDADE_PERIGO
            elif distancia < ALERTA_M:
                r -= PENALIDADE_ALERTA

        if seguinte is not None:
            if origem[seguinte] == "correcao":
                r -= PENALIDADE_CORRECAO
            elif origem[seguinte] == "ia":
                r -= PENALIDADE_TRANCO * abs(float(direcao[seguinte]) - float(direcao[i])) / 100.0
        if evento[i] == "parada":
            r -= PENALIDADE_PARADA
        saida[i] = r
    return saida


def retornos(sessao, tempo, origem, recompensa) -> np.ndarray:
    """Retorno descontado de cada passo da IA, somando os passos seguintes da IA."""
    n = len(origem)
    saida = np.full(n, np.nan)
    acumulado = 0.0
    for i in range(n - 1, -1, -1):
        if origem[i] != "ia":
            acumulado = 0.0
            continue
        continua = _continua(sessao, tempo, i) and origem[i + 1] == "ia"
        acumulado = recompensa[i] + (GAMA * acumulado if continua else 0.0)
        saida[i] = acumulado
    return saida


def pesos_por_retorno(retorno: np.ndarray) -> np.ndarray:
    """exp(beta * retorno padronizado), com media 1."""
    if retorno.size == 0:
        return retorno.copy()
    desvio = float(retorno.std())
    z = (retorno - float(retorno.mean())) / (desvio if desvio > 1e-6 else 1.0)
    pesos = np.exp(np.clip(BETA * z, -LIMITE_Z, LIMITE_Z))
    return pesos / pesos.mean()
