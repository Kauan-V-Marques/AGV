"""O que a rede "ve": miniatura da profundidade e vetor de caracteristicas (funcoes puras).

A miniatura (24 x 32, uint8) e o que fica gravado no banco. As caracteristicas sao
calculadas a partir dela na hora de treinar, entao mudar a forma de extrair
(VERSAO_ENTRADA) nao invalida as amostras ja gravadas.
"""

from typing import Optional

import numpy as np

from pc_agv.visao import profundidade


LINHAS = 24
COLUNAS = 32
DIST_MIN_M = 0.15
DIST_MAX_M = 5.0
# Distancia em que a caracteristica satura (1.0 = "longe o bastante").
ALCANCE_M = 4.0
# Faixas de altura da imagem: alto, meio e baixo. As duas ultimas linhas ficam de
# fora porque mostram o chao colado no AGV.
FAIXAS_LINHAS = ((0, 8), (8, 16), (16, 22))
FAIXAS_COLUNAS = 8
VERSAO_ENTRADA = 1
TAMANHO_ENTRADA = len(FAIXAS_LINHAS) * FAIXAS_COLUNAS * 2
BYTES_MINIATURA = LINHAS * COLUNAS


def miniatura(depth: np.ndarray, is_mm: bool) -> np.ndarray:
    """Reduz o quadro de profundidade a 24 x 32 blocos.

    Cada bloco guarda o percentil 20 das distancias validas (perto, mas sem ser
    enganado por um pixel isolado de ruido), codificado em 1..255; 0 = sem leitura.
    """
    depth = np.asarray(depth)
    if depth.shape[0] >= LINHAS * 4 and depth.shape[1] >= COLUNAS * 4:
        depth = depth[::2, ::2]  # 1 pixel a cada 2 basta e deixa o calculo 4x mais leve
    metros = profundidade.depth_to_meters(depth, is_mm)
    altura = metros.shape[0] // LINHAS
    largura = metros.shape[1] // COLUNAS
    if altura == 0 or largura == 0:
        return np.zeros((LINHAS, COLUNAS), dtype=np.uint8)

    blocos = (
        metros[: altura * LINHAS, : largura * COLUNAS]
        .reshape(LINHAS, altura, COLUNAS, largura)
        .transpose(0, 2, 1, 3)
        .reshape(LINHAS, COLUNAS, altura * largura)
    )
    quantidade = np.isfinite(blocos).sum(axis=2)
    ordenados = np.sort(blocos, axis=2)  # NaN vai para o fim
    indice = np.maximum(((quantidade - 1) * 0.2).astype(np.int64), 0)
    perto = np.take_along_axis(ordenados, indice[..., None], axis=2)[..., 0]

    suficiente = quantidade >= max(1, int(0.15 * altura * largura))
    escala = (np.clip(perto, DIST_MIN_M, DIST_MAX_M) - DIST_MIN_M) / (DIST_MAX_M - DIST_MIN_M)
    codigo = np.where(suficiente, 1.0 + np.round(np.nan_to_num(escala) * 254.0), 0.0)
    return codigo.astype(np.uint8)


def metros_da_miniatura(minis: np.ndarray) -> np.ndarray:
    """Decodifica uma ou varias miniaturas para metros; 0 vira NaN."""
    minis = np.asarray(minis)
    metros = DIST_MIN_M + (minis.astype(np.float32) - 1.0) / 254.0 * (DIST_MAX_M - DIST_MIN_M)
    return np.where(minis > 0, metros, np.nan).astype(np.float32)


def extrair_entradas(minis: np.ndarray) -> np.ndarray:
    """Caracteristicas de N miniaturas (N, 24, 32) -> (N, TAMANHO_ENTRADA).

    Para cada faixa de altura e cada uma das 8 colunas: a distancia mais proxima
    (0..1, saturando em ALCANCE_M) e a fracao de blocos com leitura.
    """
    metros = metros_da_miniatura(minis)
    if metros.ndim == 2:
        metros = metros[None]
    total = metros.shape[0]
    largura = COLUNAS // FAIXAS_COLUNAS
    partes = []
    for inicio, fim in FAIXAS_LINHAS:
        faixa = (
            metros[:, inicio:fim, :]
            .reshape(total, fim - inicio, FAIXAS_COLUNAS, largura)
            .transpose(0, 2, 1, 3)
            .reshape(total, FAIXAS_COLUNAS, -1)
        )
        validos = np.isfinite(faixa)
        perto = np.where(validos, faixa, np.inf).min(axis=2)
        perto = np.where(np.isfinite(perto), np.minimum(perto, ALCANCE_M) / ALCANCE_M, 1.0)
        partes.append(perto)
        partes.append(validos.mean(axis=2))
    return np.concatenate(partes, axis=1).astype(np.float32)


def extrair_entrada(mini: np.ndarray) -> np.ndarray:
    return extrair_entradas(mini[None])[0]


def distancia_frente(minis: np.ndarray) -> np.ndarray:
    """Distancia mais proxima no centro da imagem (faixa do meio, colunas centrais).

    Usada pela recompensa. NaN quando nao ha leitura.
    """
    metros = metros_da_miniatura(minis)
    if metros.ndim == 2:
        metros = metros[None]
    centro = metros[:, 8:16, 12:20].reshape(metros.shape[0], -1)
    validos = np.isfinite(centro)
    perto = np.where(validos, centro, np.inf).min(axis=1)
    return np.where(np.isfinite(perto), perto, np.nan)


def miniatura_de_bytes(dados: bytes) -> Optional[np.ndarray]:
    if len(dados) != BYTES_MINIATURA:
        return None
    return np.frombuffer(dados, dtype=np.uint8).reshape(LINHAS, COLUNAS)
