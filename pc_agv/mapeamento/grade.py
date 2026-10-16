"""Mapa de ocupacao 2D em log-odds.

Cada celula guarda o quanto o AGV acredita que ela esta ocupada. Cada raio da
varredura diz "livre" nas celulas que atravessa e "ocupado" onde bate; somar essas
evidencias em log-odds e o jeito padrao de juntar muitas leituras com ruido
(Thrun, Burgard e Fox, Probabilistic Robotics, 2005, cap. 9).

Celulas: linha 0 no topo (y maior), coluna 0 a esquerda (x menor). A origem do mapa
(onde o mapeamento comecou) fica no centro da grade.
"""

import io
from typing import Optional

import numpy as np

from pc_agv.mapeamento.geometria import Pose, transformar


LIVRE = -0.4
OCUPADO = 0.85
LIMITE = 4.0          # satura para o mapa continuar mudando se o ambiente mudar
CONHECIDO = 0.3       # |log-odds| acima disso: a celula ja foi observada (uma leitura basta)

COR_LIVRE = (148, 163, 184)     # cinza-azulado
COR_OCUPADO = (96, 165, 250)    # azul claro


class Grade:
    def __init__(self, tamanho_m: float = 30.0, resolucao_m: float = 0.05, logodds: Optional[np.ndarray] = None):
        self.resolucao = float(resolucao_m)
        if logodds is None:
            lado = int(round(tamanho_m / resolucao_m))
            logodds = np.zeros((lado, lado), np.float32)
        self.logodds = np.ascontiguousarray(logodds, dtype=np.float32)
        self.lado = self.logodds.shape[0]
        self.centro = self.lado // 2
        self.versao = 0

    # ── Coordenadas ─────────────────────────────────────────────────
    def celulas(self, xy: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Linha, coluna e se esta dentro da grade, para pontos (N, 2) em metros."""
        coluna = np.floor(xy[:, 0] / self.resolucao).astype(np.int64) + self.centro
        linha = self.centro - 1 - np.floor(xy[:, 1] / self.resolucao).astype(np.int64)
        dentro = (linha >= 0) & (linha < self.lado) & (coluna >= 0) & (coluna < self.lado)
        return linha, coluna, dentro

    # ── Atualizacao ─────────────────────────────────────────────────
    def integrar(self, pose: Pose, pontos: np.ndarray) -> None:
        """Soma uma varredura (pontos no referencial do AGV) vista da pose dada."""
        if len(pontos) == 0:
            return
        mundo = transformar(pontos, pose)
        origem = np.array([pose.x, pose.y])
        vetores = mundo - origem
        comprimento = np.hypot(vetores[:, 0], vetores[:, 1])

        # Amostras ao longo de cada raio, a cada meia celula, parando uma celula antes
        # do obstaculo (para o "livre" nao apagar a borda do que foi visto).
        passos = max(2, int(np.ceil(comprimento.max() / (self.resolucao * 0.5))))
        t = np.linspace(0.0, 1.0, passos, endpoint=False)
        amostras = origem + t[:, None, None] * vetores[None, :, :]
        antes_do_fim = (1.0 - t)[:, None] * comprimento[None, :] > self.resolucao
        linha, coluna, dentro = self.celulas(amostras[antes_do_fim])
        livres = np.unique(linha[dentro] * self.lado + coluna[dentro])

        linha, coluna, dentro = self.celulas(mundo)
        ocupadas = np.unique(linha[dentro] * self.lado + coluna[dentro])
        livres = np.setdiff1d(livres, ocupadas, assume_unique=True)

        plano = self.logodds.reshape(-1)
        plano[livres] += LIVRE
        plano[ocupadas] += OCUPADO
        np.clip(self.logodds, -LIMITE, LIMITE, out=self.logodds)
        self.versao += 1

    # ── Leitura ─────────────────────────────────────────────────────
    def area_conhecida_m2(self) -> float:
        return float((np.abs(self.logodds) > CONHECIDO).sum()) * self.resolucao ** 2

    def recorte(self, margem_m: float = 1.0) -> Optional[dict]:
        """Retangulo que contem tudo o que ja foi observado (com margem)."""
        conhecidas = np.abs(self.logodds) > CONHECIDO
        if not conhecidas.any():
            return None
        linhas = np.flatnonzero(conhecidas.any(axis=1))
        colunas = np.flatnonzero(conhecidas.any(axis=0))
        margem = int(round(margem_m / self.resolucao))
        l0, l1 = max(0, linhas[0] - margem), min(self.lado, linhas[-1] + 1 + margem)
        c0, c1 = max(0, colunas[0] - margem), min(self.lado, colunas[-1] + 1 + margem)
        return {
            "linha0": int(l0), "linha1": int(l1), "coluna0": int(c0), "coluna1": int(c1),
            "x_min": round(float(c0 - self.centro) * self.resolucao, 3),
            "y_max": round(float(self.centro - l0) * self.resolucao, 3),
            "largura_m": round(float(c1 - c0) * self.resolucao, 3),
            "altura_m": round(float(l1 - l0) * self.resolucao, 3),
            "resolucao": self.resolucao,
        }

    def imagem_rgba(self, recorte: dict) -> np.ndarray:
        """Imagem do recorte: desconhecido transparente, livre cinza, ocupado azul."""
        trecho = self.logodds[recorte["linha0"]:recorte["linha1"], recorte["coluna0"]:recorte["coluna1"]]
        imagem = np.zeros(trecho.shape + (4,), np.uint8)
        livre = trecho < -CONHECIDO
        ocupado = trecho > CONHECIDO
        imagem[livre, :3] = COR_LIVRE
        # quanto mais certeza, mais opaco
        imagem[livre, 3] = np.clip(40 + (-trecho[livre] / LIMITE) * 60, 0, 255).astype(np.uint8)
        imagem[ocupado, :3] = COR_OCUPADO
        imagem[ocupado, 3] = np.clip(140 + (trecho[ocupado] / LIMITE) * 115, 0, 255).astype(np.uint8)
        return imagem

    # ── Persistencia ────────────────────────────────────────────────
    def para_bytes(self) -> bytes:
        buffer = io.BytesIO()
        np.savez_compressed(buffer, logodds=self.logodds.astype(np.float16), resolucao=np.array([self.resolucao]))
        return buffer.getvalue()

    @classmethod
    def de_bytes(cls, dados: bytes) -> "Grade":
        try:
            with np.load(io.BytesIO(dados), allow_pickle=False) as arquivo:
                logodds = arquivo["logodds"].astype(np.float32)
                resolucao = float(arquivo["resolucao"][0])
        except Exception as exc:
            raise ValueError(f"mapa ilegivel: {exc}") from exc
        if logodds.ndim != 2 or logodds.shape[0] != logodds.shape[1]:
            raise ValueError("mapa com formato inesperado")
        return cls(resolucao_m=resolucao, logodds=logodds)
