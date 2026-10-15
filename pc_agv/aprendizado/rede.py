"""Rede neural pequena (perceptron multicamadas) em NumPy puro.

Entrada: caracteristicas da profundidade. Saida: [velocidade, direcao] em -1..1
(tanh), que viram -100..100 no controle. Treino com Adam, erro quadratico ponderado
por amostra e parada antecipada pela validacao.
"""

import io
import zipfile
from typing import Callable, Optional

import numpy as np


class RedeNeural:
    def __init__(self, n_entrada: int, ocultas: tuple[int, ...] = (32, 16), semente: int = 0):
        rng = np.random.default_rng(semente)
        self.camadas = [int(n_entrada), *(int(n) for n in ocultas), 2]
        self.pesos = [
            rng.normal(0.0, np.sqrt(1.0 / entrada), (entrada, saida))
            for entrada, saida in zip(self.camadas[:-1], self.camadas[1:])
        ]
        self.vieses = [np.zeros(saida) for saida in self.camadas[1:]]
        self.media = np.zeros(n_entrada)
        self.desvio = np.ones(n_entrada)

    # ── Uso ─────────────────────────────────────────────────────────
    def ajustar_normalizacao(self, x: np.ndarray) -> None:
        self.media = x.mean(axis=0)
        self.desvio = np.maximum(x.std(axis=0), 1e-3)

    def _ativacoes(self, x: np.ndarray) -> list[np.ndarray]:
        ativacao = (np.atleast_2d(x) - self.media) / self.desvio
        ativacoes = [ativacao]
        for peso, vies in zip(self.pesos, self.vieses):
            ativacao = np.tanh(ativacao @ peso + vies)
            ativacoes.append(ativacao)
        return ativacoes

    def prever(self, x: np.ndarray) -> np.ndarray:
        return self._ativacoes(x)[-1]

    # ── Treino ──────────────────────────────────────────────────────
    def perda(self, x: np.ndarray, y: np.ndarray, w: np.ndarray) -> float:
        erro = self.prever(x) - y
        return float((w[:, None] * erro ** 2).sum() / (2.0 * w.sum()))

    def gradientes(self, x, y, w, l2: float = 0.0):
        """Perda = soma(w * erro^2) / (2 * soma(w)) + l2/2 * soma(pesos^2)."""
        ativacoes = self._ativacoes(x)
        saida = ativacoes[-1]
        erro = saida - y
        perda = float((w[:, None] * erro ** 2).sum() / (2.0 * w.sum()))
        perda += 0.5 * l2 * sum(float((p ** 2).sum()) for p in self.pesos)

        delta = (w[:, None] * erro / w.sum()) * (1.0 - saida ** 2)
        grad_pesos = [None] * len(self.pesos)
        grad_vieses = [None] * len(self.vieses)
        for i in range(len(self.pesos) - 1, -1, -1):
            grad_pesos[i] = ativacoes[i].T @ delta + l2 * self.pesos[i]
            grad_vieses[i] = delta.sum(axis=0)
            if i > 0:
                delta = (delta @ self.pesos[i].T) * (1.0 - ativacoes[i] ** 2)
        return perda, grad_pesos, grad_vieses

    def treinar(
        self,
        x: np.ndarray,
        y: np.ndarray,
        w: np.ndarray,
        x_val: np.ndarray,
        y_val: np.ndarray,
        w_val: np.ndarray,
        max_epocas: int = 200,
        lote: int = 128,
        taxa: float = 3e-3,
        l2: float = 1e-4,
        paciencia: int = 15,
        semente: int = 0,
        progresso: Optional[Callable[[int, int], None]] = None,
    ) -> list[dict]:
        """Adam com parada antecipada. Fica com os pesos da melhor epoca na validacao."""
        rng = np.random.default_rng(semente)
        self.ajustar_normalizacao(x)
        parametros = self.pesos + self.vieses
        m = [np.zeros_like(p) for p in parametros]
        v = [np.zeros_like(p) for p in parametros]
        b1, b2, eps = 0.9, 0.999, 1e-8
        passo = 0

        historico = []
        melhor = (np.inf, None)
        sem_melhora = 0
        n = x.shape[0]
        for epoca in range(1, max_epocas + 1):
            ordem = rng.permutation(n)
            for inicio in range(0, n, lote):
                idx = ordem[inicio:inicio + lote]
                _, gp, gb = self.gradientes(x[idx], y[idx], w[idx], l2)
                passo += 1
                for k, (param, grad) in enumerate(zip(parametros, gp + gb)):
                    m[k] = b1 * m[k] + (1 - b1) * grad
                    v[k] = b2 * v[k] + (1 - b2) * grad ** 2
                    m_hat = m[k] / (1 - b1 ** passo)
                    v_hat = v[k] / (1 - b2 ** passo)
                    param -= taxa * m_hat / (np.sqrt(v_hat) + eps)

            perda_treino = self.perda(x, y, w)
            perda_val = self.perda(x_val, y_val, w_val)
            historico.append({"epoca": epoca, "treino": round(perda_treino, 5), "validacao": round(perda_val, 5)})
            if perda_val < melhor[0] - 1e-6:
                melhor = (perda_val, [p.copy() for p in parametros])
                sem_melhora = 0
            else:
                sem_melhora += 1
            if progresso is not None:
                progresso(epoca, max_epocas)
            if sem_melhora >= paciencia:
                break

        if melhor[1] is not None:
            for param, guardado in zip(parametros, melhor[1]):
                param[...] = guardado
        return historico

    # ── Persistencia ────────────────────────────────────────────────
    def para_bytes(self) -> bytes:
        arrays = {"media": self.media, "desvio": self.desvio}
        for i, (peso, vies) in enumerate(zip(self.pesos, self.vieses)):
            arrays[f"peso{i}"] = peso
            arrays[f"vies{i}"] = vies
        arrays["camadas"] = np.array(self.camadas, dtype=np.int64)
        buffer = io.BytesIO()
        np.savez_compressed(buffer, **arrays)
        return buffer.getvalue()

    @classmethod
    def de_bytes(cls, dados: bytes) -> "RedeNeural":
        """Reconstroi a rede salva por para_bytes. Pesos ilegiveis levantam ValueError."""
        try:
            # allow_pickle=False: o arquivo so pode conter numeros, nunca codigo.
            with np.load(io.BytesIO(dados), allow_pickle=False) as arquivo:
                camadas = [int(n) for n in arquivo["camadas"]]
                rede = cls(camadas[0], tuple(camadas[1:-1]))
                rede.pesos = [arquivo[f"peso{i}"].astype(np.float64) for i in range(len(camadas) - 1)]
                rede.vieses = [arquivo[f"vies{i}"].astype(np.float64) for i in range(len(camadas) - 1)]
                rede.media = arquivo["media"].astype(np.float64)
                rede.desvio = arquivo["desvio"].astype(np.float64)
        except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
            raise ValueError(f"modelo ilegivel: {exc}") from exc
        if camadas[-1] != 2:
            raise ValueError(f"saida inesperada: {camadas[-1]}")
        for i, peso in enumerate(rede.pesos):
            if peso.shape != (camadas[i], camadas[i + 1]):
                raise ValueError("pesos com formato inconsistente")
        return rede
