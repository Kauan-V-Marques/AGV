"""Servico do auto-mapeamento: odometria visual e mapa de ocupacao em tempo real.

Enquanto o mapeamento esta ligado, a thread agv-mapeamento transforma cada quadro de
profundidade numa varredura 2D, estima o movimento do AGV (odometria visual, sem
encoders) e soma a varredura ao mapa. Os mapas podem ser salvos em logs/mapas.db e
abertos depois para consulta.
"""

import math
import os
import threading
from typing import Any, Optional

import cv2
import numpy as np

from pc_agv.config import (
    KINECT_AVANCO_M,
    MAPA_DB_FILE,
    MAPA_HZ,
    MAPA_RESOLUCAO_M,
    MAPA_TAMANHO_M,
    log,
)
from pc_agv import estado
from pc_agv import util
from pc_agv.mapeamento import banco as banco_mapas
from pc_agv.mapeamento import varredura
from pc_agv.mapeamento.banco import BancoMapas
from pc_agv.mapeamento.geometria import Pose, transformar
from pc_agv.mapeamento.grade import Grade
from pc_agv.mapeamento.odometria import Odometria


PROFUNDIDADE_VELHA_S = 0.6
TRAJETORIA_PASSO_M = 0.05     # guarda um ponto da trajetoria a cada 5 cm
TRAJETORIA_MAX = 20000
TRAJETORIA_EXIBIDA = 600      # pontos mandados ao painel (a trajetoria e reduzida)
VARREDURA_EXIBIDA = 90        # pontos da ultima varredura mandados ao painel
NOME_MAX = 60
AVANCO_MAX_CM = 100

_lock = threading.Lock()
_banco: Optional[BancoMapas] = None
_erro_banco: Optional[str] = None
_avanco_m = KINECT_AVANCO_M
_grade = Grade(MAPA_TAMANHO_M, MAPA_RESOLUCAO_M)
_odometria = Odometria(avanco_m=_avanco_m)
_trajetoria: list[tuple[float, float]] = []
_varredura = np.zeros((0, 2))
_estado = {"rodando": False, "somente_leitura": False, "mapa_id": None, "nome": None,
           "leituras": 0, "falhas": 0, "salvo": True}
_png: tuple[int, Optional[bytes]] = (-1, None)


# ── Ciclo de vida ───────────────────────────────────────────────────
def iniciar(caminho: Optional[str] = None) -> bool:
    """Abre o banco dos mapas. Sem banco o mapeamento funciona, so nao salva."""
    global _banco, _erro_banco, _avanco_m
    caminho = caminho or MAPA_DB_FILE
    with _lock:
        if _banco is not None:
            return True
    try:
        os.makedirs(os.path.dirname(os.path.abspath(caminho)), exist_ok=True)
        banco = BancoMapas(caminho)
        avanco_cm = banco.ler_preferencia("avanco_cm", round(KINECT_AVANCO_M * 100))
    except Exception as exc:
        log.error("Banco de mapas indisponivel (%s); mapas nao serao salvos, arquivo preservado: %s", exc, caminho)
        with _lock:
            _erro_banco = str(exc)
        return False
    with _lock:
        _banco, _erro_banco = banco, None
        _avanco_m = float(avanco_cm) / 100.0
        _odometria.avanco_m = _avanco_m
    return True


def encerrar() -> None:
    global _banco
    with _lock:
        banco, _banco = _banco, None
        _estado["rodando"] = False
    if banco is not None:
        banco.fechar()


# ── Mapeamento ──────────────────────────────────────────────────────
def _registrar_trajetoria(pose: Pose) -> None:
    if not _trajetoria or math.hypot(pose.x - _trajetoria[-1][0], pose.y - _trajetoria[-1][1]) >= TRAJETORIA_PASSO_M:
        _trajetoria.append((round(pose.x, 3), round(pose.y, 3)))
        if len(_trajetoria) > TRAJETORIA_MAX:
            del _trajetoria[: len(_trajetoria) - TRAJETORIA_MAX]


def passo(agora: Optional[float] = None) -> bool:
    """Um quadro do mapeamento. Devolve True se a pose foi atualizada pela visao."""
    global _varredura
    agora = util.now() if agora is None else agora
    with _lock:
        if not _estado["rodando"]:
            return False
    with estado.state_lock:
        kinect_ok = bool(estado.state["kinect_ok"])
        depth = estado.state["depth"]
        is_mm = bool(estado.state["depth_mm"])
        depth_at = float(estado.state["depth_at"])
    with estado.control_lock:
        em_movimento = estado.control_state["active_speed"] != 0 or estado.control_state["active_steering"] != 0
    if not kinect_ok or depth is None or agora - depth_at > PROFUNDIDADE_VELHA_S:
        return False

    pontos = varredura.varredura(depth, is_mm)
    with _lock:
        if not _estado["rodando"]:
            return False
        ok = _odometria.atualizar(pontos, em_movimento)
        _estado["leituras"] += 1
        if ok:
            _grade.integrar(_odometria.pose, pontos)
            _registrar_trajetoria(_odometria.pose)
            _estado["salvo"] = False
        else:
            _estado["falhas"] += 1
        passo_exibicao = max(1, len(pontos) // VARREDURA_EXIBIDA)
        _varredura = transformar(pontos[::passo_exibicao], _odometria.pose) if len(pontos) else np.zeros((0, 2))
    return ok


def mapeamento_loop() -> None:
    periodo = 1.0 / max(0.5, MAPA_HZ)
    ultimo_aviso = 0.0
    while not estado.stop_event.is_set():
        agora = util.now()
        try:
            passo(agora)
        except Exception as exc:
            if agora - ultimo_aviso > 30.0:  # nao enche o log se o erro se repetir
                log.warning("Falha no mapeamento: %s", exc)
                ultimo_aviso = agora
        estado.stop_event.wait(periodo)


# ── Comandos ────────────────────────────────────────────────────────
def ligar() -> tuple[bool, str]:
    with _lock:
        if _estado["somente_leitura"]:
            return False, "mapa_somente_leitura"
        _estado["rodando"] = True
    return True, "ligado"


def pausar() -> tuple[bool, str]:
    with _lock:
        _estado["rodando"] = False
    return True, "pausado"


def _limpar() -> None:
    """Comeca um mapa vazio (chamar com _lock)."""
    global _grade, _odometria, _varredura
    _grade = Grade(MAPA_TAMANHO_M, MAPA_RESOLUCAO_M)
    _odometria = Odometria(avanco_m=_avanco_m)
    _trajetoria.clear()
    _varredura = np.zeros((0, 2))
    _estado.update(rodando=False, somente_leitura=False, mapa_id=None, nome=None, leituras=0, falhas=0, salvo=True)


def novo() -> tuple[bool, str]:
    with _lock:
        _limpar()
    return True, "novo"


def salvar(nome: str) -> tuple[bool, Any]:
    nome = str(nome or "").strip()[:NOME_MAX]
    if not nome:
        return False, "nome_obrigatorio"
    with _lock:
        banco = _banco
        if banco is None:
            return False, "banco_indisponivel"
        if _grade.recorte() is None:
            return False, "mapa_vazio"
        dados = {
            "grade": _grade.para_bytes(),
            "trajetoria": banco_mapas.trajetoria_para_bytes(_trajetoria),
            "resolucao": _grade.resolucao,
            "distancia_m": round(_odometria.distancia_m, 2),
            "area_m2": round(_grade.area_conhecida_m2(), 2),
        }
        mapa_id = _estado["mapa_id"]
    novo_id = banco.salvar(nome, mapa_id=mapa_id, **dados)
    with _lock:
        _estado.update(mapa_id=novo_id, nome=nome, salvo=True)
    return True, novo_id


def listar() -> list[dict]:
    with _lock:
        banco = _banco
    return banco.listar() if banco is not None else []


def abrir(mapa_id: int) -> tuple[bool, Any]:
    """Abre um mapa salvo para consulta. Para mapear de novo, comece um mapa novo:
    sem encoders o AGV nao sabe onde esta num mapa antigo."""
    global _grade, _odometria, _varredura
    with _lock:
        banco = _banco
    if banco is None:
        return False, "banco_indisponivel"
    mapa = banco.carregar(mapa_id)
    if mapa is None:
        return False, "mapa_inexistente"
    try:
        grade = Grade.de_bytes(mapa["grade"])
        trajetoria = banco_mapas.trajetoria_de_bytes(mapa["trajetoria"])
    except ValueError as exc:
        log.error("Mapa %s ilegivel: %s", mapa_id, exc)
        return False, "mapa_ilegivel"
    with _lock:
        _grade = grade
        _trajetoria[:] = [(float(x), float(y)) for x, y in trajetoria]
        ultimo = _trajetoria[-1] if _trajetoria else (0.0, 0.0)
        _odometria = Odometria(pose=Pose(ultimo[0], ultimo[1], 0.0), avanco_m=_avanco_m)
        _odometria.distancia_m = float(mapa["distancia_m"])
        _varredura = np.zeros((0, 2))
        _estado.update(rodando=False, somente_leitura=True, mapa_id=int(mapa["id"]), nome=mapa["nome"],
                       leituras=0, falhas=0, salvo=True)
    return True, int(mapa["id"])


def apagar(mapa_id: int) -> tuple[bool, Any]:
    with _lock:
        banco = _banco
    if banco is None:
        return False, "banco_indisponivel"
    if not banco.apagar(mapa_id):
        return False, "mapa_inexistente"
    with _lock:
        if _estado["mapa_id"] == int(mapa_id):
            # O mapa aberto continua na tela, mas deixa de estar salvo.
            _estado.update(mapa_id=None, salvo=False)
    return True, int(mapa_id)


def configurar(payload: dict) -> tuple[bool, Any]:
    global _avanco_m
    if "avanco_cm" not in payload:
        return False, "valor_invalido"
    valor = payload["avanco_cm"]
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor):
        return False, "valor_invalido"
    avanco_cm = max(0.0, min(float(AVANCO_MAX_CM), float(valor)))
    with _lock:
        banco = _banco
        _avanco_m = avanco_cm / 100.0
        _odometria.avanco_m = _avanco_m
    if banco is not None:
        banco.gravar_preferencia("avanco_cm", avanco_cm)
    return True, {"avanco_cm": avanco_cm}


# ── Painel e API ────────────────────────────────────────────────────
def imagem_png() -> Optional[bytes]:
    """PNG do recorte observado do mapa (transparente onde nao se sabe nada)."""
    global _png
    with _lock:
        versao = _grade.versao
        if _png[0] == versao:
            return _png[1]
        recorte = _grade.recorte()
        imagem = _grade.imagem_rgba(recorte) if recorte else None
    dados = None
    if imagem is not None:
        ok, buffer = cv2.imencode(".png", imagem[..., [2, 1, 0, 3]])  # RGBA -> BGRA (OpenCV)
        dados = buffer.tobytes() if ok else None
    with _lock:
        _png = (versao, dados)
    return dados


def _pose_publica(pose: Pose) -> dict:
    return {"x": round(pose.x, 3), "y": round(pose.y, 3), "theta_graus": round(math.degrees(pose.theta), 1)}


def resumo() -> dict:
    """Parte pequena, mandada em todo /api/status."""
    with _lock:
        return {
            "rodando": _estado["rodando"],
            "somente_leitura": _estado["somente_leitura"],
            "area_m2": round(_grade.area_conhecida_m2(), 2),
            "distancia_m": round(_odometria.distancia_m, 2),
            "pose": _pose_publica(_odometria.pose),
        }


def status_detalhado() -> dict:
    """Tudo o que a pagina do mapa desenha (/api/mapa/status)."""
    with _lock:
        ultimo = _odometria.ultimo
        passo_trajetoria = max(1, len(_trajetoria) // TRAJETORIA_EXIBIDA)
        trajetoria = _trajetoria[::passo_trajetoria]
        if _trajetoria and (not trajetoria or trajetoria[-1] != _trajetoria[-1]):
            trajetoria = trajetoria + [_trajetoria[-1]]
        return {
            **{k: _estado[k] for k in ("rodando", "somente_leitura", "mapa_id", "nome", "leituras", "falhas", "salvo")},
            "disponivel": _banco is not None,
            "erro": _erro_banco,
            "versao": _grade.versao,
            "recorte": _grade.recorte(),
            "resolucao": _grade.resolucao,
            "area_m2": round(_grade.area_conhecida_m2(), 2),
            "distancia_m": round(_odometria.distancia_m, 2),
            "pose": _pose_publica(_odometria.pose),
            "prevendo": _odometria.prevendo,
            "casamento": None if ultimo is None or not ultimo.ok else {
                "erro_cm": round(ultimo.erro_m * 100, 2),
                "casados": round(ultimo.casados, 3),
            },
            "trajetoria": [[x, y] for x, y in trajetoria],
            "varredura": np.round(_varredura, 3).tolist(),
            "avanco_cm": round(_avanco_m * 100, 1),
        }
