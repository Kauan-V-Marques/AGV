"""Servico do auto-aprendizado: grava o que acontece, treina e dirige no modo IA.

A thread agv-aprendizado (aprendizado_loop) grava, algumas vezes por segundo, o
comando em vigor junto com a profundidade daquele momento:
- modo manual: o operador dirigindo (fase 1, imitacao);
- modo IA: a acao da propria IA ou a correcao do operador (fase 2, recompensa).
Com amostras novas suficientes e o AGV parado, treina um modelo novo em segundo
plano. Tudo fica no banco SQLite: reiniciar o sistema nao perde o que foi aprendido.
"""

import os
import threading
from typing import Any, Optional

import numpy as np

from pc_agv.config import (
    IA_AMOSTRAS_HZ,
    IA_DB_FILE,
    IA_MAX_AMOSTRAS,
    IA_MIN_AMOSTRAS,
    IA_TREINO_AUTOMATICO,
    LIDAR_MAX_M,
    log,
)
from pc_agv import estado
from pc_agv import util
from pc_agv.aprendizado import entrada, politica, treino
from pc_agv.aprendizado.banco import BancoAprendizado
from pc_agv.aprendizado.rede import RedeNeural
from pc_agv.hardware import rplidar


PADROES = {"gravando": True, "explorar": False, "velocidade_max": 50}
RUIDO_EXPLORACAO = 0.15       # desvio do ruido somado a saida da rede (escala -1..1)
PROFUNDIDADE_VELHA_S = 0.6    # quadro mais velho que isso nao serve para decidir nem gravar
JANELA_PARADA_S = 2.0         # apos soltar o comando, ainda grava a decisao de ficar parado
NOVA_SESSAO_APOS_S = 5.0
REVISAO_S = 10.0              # de quanto em quanto tempo olha se precisa treinar/podar
OCIOSO_PARA_TREINAR_S = 5.0   # o treino automatico espera o AGV ficar parado esse tempo
FONTES_IGNORADAS = ("watchdog", "stop", "boot")

_lock = threading.Lock()
_banco: Optional[BancoAprendizado] = None
_erro_banco: Optional[str] = None
_rede: Optional[RedeNeural] = None
_modelo: Optional[dict] = None
_prefs = dict(PADROES)
_contagem = {"operador": 0, "correcao": 0, "ia": 0, "total": 0}
_ultimo_id = 0
_treino = {"rodando": False, "progresso": 0.0, "erro": None, "automatico": False, "iniciado_em": None}
_coleta = {"sessao_id": None, "sessao_modo": None, "ultima_em": 0.0, "amostra_id": None, "origem": None}
_ultimo_movimento = 0.0
_rng = np.random.default_rng()


# ── Ciclo de vida ───────────────────────────────────────────────────
def _carregar_rede(ativo) -> tuple[Optional[RedeNeural], Optional[dict]]:
    if ativo is None:
        return None, None
    resumo, pesos = ativo
    if resumo["versao_entrada"] != entrada.VERSAO_ENTRADA:
        log.warning("Modelo %s usa outra versao da entrada; treine de novo", resumo["id"])
        return None, None
    try:
        rede = RedeNeural.de_bytes(pesos)
    except Exception as exc:
        log.error("Modelo %s ilegivel: %s", resumo["id"], exc)
        return None, None
    if rede.camadas[0] != entrada.TAMANHO_ENTRADA:
        return None, None
    return rede, resumo


def iniciar(caminho: Optional[str] = None) -> bool:
    """Abre o banco e carrega o modelo ativo. Sem banco, o resto do AGV segue normal."""
    global _banco, _erro_banco, _rede, _modelo, _prefs, _contagem, _ultimo_id
    caminho = caminho or IA_DB_FILE
    with _lock:
        if _banco is not None:
            return True
    try:
        os.makedirs(os.path.dirname(os.path.abspath(caminho)), exist_ok=True)
        banco = BancoAprendizado(caminho)
        prefs = {chave: banco.ler_preferencia(chave, padrao) for chave, padrao in PADROES.items()}
        contagem = banco.contagem()
        ultimo_id = banco.ultimo_id()
        rede, modelo = _carregar_rede(banco.modelo_ativo())
    except Exception as exc:
        # Nunca apaga o arquivo: um banco ilegivel pode ter horas de demonstracoes.
        log.error("Banco do aprendizado indisponivel (%s); aprendizado desligado, arquivo preservado: %s", exc, caminho)
        with _lock:
            _erro_banco = str(exc)
        return False

    with _lock:
        _banco, _erro_banco = banco, None
        _prefs, _contagem, _ultimo_id = prefs, contagem, ultimo_id
        _rede, _modelo = rede, modelo
        _coleta.update(sessao_id=None, sessao_modo=None, ultima_em=0.0, amostra_id=None, origem=None)
    log.info(
        "Aprendizado: %d amostras, modelo %s",
        contagem["total"], f"#{modelo['id']} ({modelo['fase']})" if modelo else "nenhum",
    )
    return True


def encerrar() -> None:
    global _banco, _rede, _modelo
    with _lock:
        banco, _banco = _banco, None
        _rede, _modelo = None, None
    if banco is not None:
        banco.fechar()


def tem_modelo() -> bool:
    with _lock:
        return _rede is not None


# ── Decisao no modo IA ──────────────────────────────────────────────
def decidir(agora: Optional[float] = None) -> tuple[int, int, str]:
    """Comando da IA para o momento atual, ja passado pela camada de seguranca."""
    agora = util.now() if agora is None else agora
    with _lock:
        rede = _rede
        explorar = bool(_prefs["explorar"])
        velocidade_max = int(_prefs["velocidade_max"])
    if rede is None:
        return 0, 0, "ia-sem-modelo"
    with estado.state_lock:
        kinect_ok = bool(estado.state["kinect_ok"])
        depth = estado.state["depth"]
        is_mm = bool(estado.state["depth_mm"])
        depth_at = float(estado.state["depth_at"])
        centro = estado.state["center_clearance_m"]
        distancia = estado.state["distance_m"]
    if not kinect_ok:
        return 0, 0, "sem-kinect"
    if depth is None or agora - depth_at > PROFUNDIDADE_VELHA_S:
        return 0, 0, "sem-depth"

    saida = rede.prever(entrada.extrair_entrada(entrada.miniatura(depth, is_mm)))[0]
    motivo = "ia"
    if explorar:
        saida = saida + _rng.normal(0.0, RUIDO_EXPLORACAO, 2)
        motivo = "ia-explorando"
    velocidade, direcao = treino.para_comando(saida)
    frente = centro if centro is not None else distancia
    tras = None
    pode_girar = True
    leitura = rplidar.leitura_recente(agora)
    if leitura is not None:
        # O LIDAR cobre a zona cega do Kinect (abaixo de ~0,5 m) e a traseira.
        folgas = leitura["folgas"]
        frente_lidar = folgas.get("frente")
        frente_lidar = LIDAR_MAX_M if frente_lidar is None else frente_lidar
        frente = frente_lidar if frente is None else min(frente, frente_lidar)
        # so o corredor atras: num corredor as paredes laterais aparecem nas diagonais
        tras = folgas.get("tras")
        tras = LIDAR_MAX_M if tras is None else tras
        pode_girar = rplidar.pode_girar(leitura["pontos"])
    return politica.aplicar_seguranca(int(round(velocidade)), int(round(direcao)), frente, velocidade_max, motivo,
                                      tras_m=tras, pode_girar=pode_girar)


# ── Gravacao ────────────────────────────────────────────────────────
def coletar(agora: Optional[float] = None) -> Optional[str]:
    """Um passo da gravacao. Devolve a origem da amostra gravada, ou None."""
    global _ultimo_movimento, _ultimo_id
    agora = util.now() if agora is None else agora
    with estado.control_lock:
        modo = estado.control_state["mode"]
        velocidade = int(estado.control_state["active_speed"])
        direcao = int(estado.control_state["active_steering"])
        fonte = str(estado.control_state["active_source"])
    with estado.state_lock:
        kinect_ok = bool(estado.state["kinect_ok"])
        depth = estado.state["depth"]
        is_mm = bool(estado.state["depth_mm"])
        depth_at = float(estado.state["depth_at"])

    with _lock:
        if velocidade != 0 or direcao != 0:
            _ultimo_movimento = agora
        banco = _banco
        gravando = bool(_prefs["gravando"])
        ultimo_movimento = _ultimo_movimento
    if banco is None or not gravando:
        return None

    if modo == "manual":
        if fonte in FONTES_IGNORADAS:
            return None
        if velocidade == 0 and direcao == 0 and agora - ultimo_movimento > JANELA_PARADA_S:
            return None  # parado sem motivo: so ensinaria a IA a ficar parada
        origem = "operador"
    elif modo == "ia":
        origem = "correcao" if fonte == "correcao" else "ia"
    else:
        return None  # o modo autonomo por regras nao e professor da IA

    if not kinect_ok or depth is None or agora - depth_at > PROFUNDIDADE_VELHA_S:
        return None
    mini = entrada.miniatura(depth, is_mm)
    if not mini.any():
        return None  # quadro sem nenhuma leitura valida

    with _lock:
        nova = (
            _coleta["sessao_id"] is None
            or _coleta["sessao_modo"] != modo
            or agora - _coleta["ultima_em"] > NOVA_SESSAO_APOS_S
        )
        sessao_id = _coleta["sessao_id"]
    if nova:
        sessao_id = banco.nova_sessao(modo, agora)
    amostra_id = banco.gravar_amostra(sessao_id, origem, velocidade, direcao, mini, agora)
    with _lock:
        _coleta.update(sessao_id=sessao_id, sessao_modo=modo, ultima_em=agora, amostra_id=amostra_id, origem=origem)
        _contagem[origem] += 1
        _contagem["total"] += 1
        _ultimo_id = max(_ultimo_id, amostra_id)
    return origem


def registrar_parada(agora: Optional[float] = None) -> bool:
    """Parada de emergencia no modo IA: a ultima acao da IA leva a penalidade."""
    agora = util.now() if agora is None else agora
    with _lock:
        banco = _banco
        amostra_id = _coleta["amostra_id"]
        recente = _coleta["origem"] == "ia" and agora - _coleta["ultima_em"] <= JANELA_PARADA_S
    if banco is None or amostra_id is None or not recente:
        return False
    banco.marcar_evento(amostra_id, "parada")
    return True


# ── Treino ──────────────────────────────────────────────────────────
def _novas_desde_o_modelo() -> int:
    """Amostras gravadas depois do treino do modelo ativo (chamar com _lock)."""
    base = int(((_modelo or {}).get("info") or {}).get("ultimo_id", 0))
    return max(0, _ultimo_id - base)


def _progresso(epoca: int, total: int) -> None:
    with _lock:
        _treino["progresso"] = round(epoca / max(1, total), 3)


def _treinar(banco: BancoAprendizado) -> None:
    global _rede, _modelo
    try:
        dados = banco.carregar_amostras()
        rede, info = treino.treinar(dados, IA_MIN_AMOSTRAS, semente=len(dados["id"]), progresso=_progresso)
        info["ultimo_id"] = int(dados["id"].max())
        modelo_id = banco.salvar_modelo(info["fase"], info["versao_entrada"], info, rede.para_bytes())
        resumo = {
            "id": modelo_id,
            "criado_em": util.now(),
            "fase": info["fase"],
            "versao_entrada": info["versao_entrada"],
            "ativo": True,
            "info": info,
        }
        with _lock:
            _rede, _modelo = rede, resumo
            _treino.update(progresso=1.0, erro=None)
        log.info(
            "Aprendizado: modelo #%d (%s) treinado em %.1f s; acerto de direcao %.0f%%",
            modelo_id, info["fase"], info["duracao_s"], 100 * info["validacao"].get("acerto_direcao", 0),
        )
    except treino.DadosInsuficientes:
        with _lock:
            _treino["erro"] = "amostras_insuficientes"
    except Exception as exc:
        log.exception("Falha no treino do aprendizado")
        with _lock:
            _treino["erro"] = str(exc) or "falha_no_treino"
    finally:
        with _lock:
            _treino["rodando"] = False


def iniciar_treino(automatico: bool = False, em_segundo_plano: bool = True) -> tuple[bool, str]:
    with _lock:
        banco = _banco
        if banco is None:
            return False, "banco_indisponivel"
        if _treino["rodando"]:
            return False, "treino_em_andamento"
        if _contagem["operador"] + _contagem["correcao"] < IA_MIN_AMOSTRAS:
            return False, "amostras_insuficientes"
        _treino.update(rodando=True, progresso=0.0, erro=None, automatico=automatico, iniciado_em=util.now())
    if em_segundo_plano:
        threading.Thread(target=_treinar, args=(banco,), daemon=True, name="agv-treino").start()
    else:
        _treinar(banco)
    return True, "iniciado"


def _manutencao(agora: float) -> None:
    """Poda o banco e dispara o treino automatico quando faz sentido."""
    global _contagem
    with _lock:
        banco = _banco
    if banco is None:
        return
    if banco.limitar_amostras(IA_MAX_AMOSTRAS):
        contagem = banco.contagem()
        with _lock:
            _contagem = contagem

    if IA_TREINO_AUTOMATICO <= 0:
        return
    with estado.control_lock:
        modo = estado.control_state["mode"]
    with _lock:
        pronto = (
            not _treino["rodando"]
            and _novas_desde_o_modelo() >= IA_TREINO_AUTOMATICO
            and agora - _ultimo_movimento >= OCIOSO_PARA_TREINAR_S
        )
    if pronto and modo != "ia":
        iniciar_treino(automatico=True)


def aprendizado_loop() -> None:
    periodo = 1.0 / max(0.5, IA_AMOSTRAS_HZ)
    proxima_revisao = 0.0
    ultimo_aviso = 0.0
    while not estado.stop_event.is_set():
        agora = util.now()
        try:
            coletar(agora)
            if agora >= proxima_revisao:
                proxima_revisao = agora + REVISAO_S
                _manutencao(agora)
        except Exception as exc:
            if agora - ultimo_aviso > 30.0:  # nao enche o log se o erro se repetir
                log.warning("Falha na gravacao do aprendizado: %s", exc)
                ultimo_aviso = agora
        estado.stop_event.wait(periodo)


# ── Painel e API ────────────────────────────────────────────────────
def _resumo_modelo(modelo: Optional[dict], com_historico: bool = False) -> Optional[dict]:
    if modelo is None:
        return None
    info = dict(modelo.get("info") or {})
    if not com_historico:
        info.pop("historico", None)
    return {**modelo, "info": info}


def status_snapshot() -> dict:
    with _lock:
        return {
            "disponivel": _banco is not None,
            "erro": _erro_banco,
            "tem_modelo": _rede is not None,
            "modelo": _resumo_modelo(_modelo),
            "amostras": dict(_contagem),
            "novas": _novas_desde_o_modelo(),
            "preferencias": dict(_prefs),
            "treino": dict(_treino),
            "minimo_amostras": IA_MIN_AMOSTRAS,
            "treino_automatico_a_cada": IA_TREINO_AUTOMATICO,
        }


def configurar(payload: dict) -> tuple[bool, Any]:
    novas = {}
    for chave in ("gravando", "explorar"):
        if chave in payload:
            if not isinstance(payload[chave], bool):
                return False, "valor_invalido"
            novas[chave] = payload[chave]
    if "velocidade_max" in payload:
        valor = payload["velocidade_max"]
        if isinstance(valor, bool) or not isinstance(valor, (int, float)):
            return False, "valor_invalido"
        novas["velocidade_max"] = util.clamp(int(valor), 0, 100)
    with _lock:
        banco = _banco
    if banco is None:
        return False, "banco_indisponivel"
    for chave, valor in novas.items():
        banco.gravar_preferencia(chave, valor)
    with _lock:
        _prefs.update(novas)
        return True, dict(_prefs)


def listar_modelos() -> list[dict]:
    with _lock:
        banco = _banco
    if banco is None:
        return []
    return banco.listar_modelos()


def ativar_modelo(modelo_id: int) -> tuple[bool, Any]:
    global _rede, _modelo
    with _lock:
        banco = _banco
    if banco is None:
        return False, "banco_indisponivel"
    ativo = banco.ativar_modelo(modelo_id)
    if ativo is None:
        return False, "modelo_inexistente"
    rede, resumo = _carregar_rede(ativo)
    if rede is None:
        return False, "modelo_incompativel"
    with _lock:
        _rede, _modelo = rede, resumo
    return True, _resumo_modelo(resumo)


def apagar(alvo: str) -> tuple[bool, Any]:
    global _rede, _modelo, _contagem
    if alvo not in ("amostras", "tudo"):
        return False, "alvo_invalido"
    with _lock:
        banco = _banco
        if banco is None:
            return False, "banco_indisponivel"
        if _treino["rodando"]:
            return False, "treino_em_andamento"
    with _lock:
        # A proxima amostra abre sessao nova (a atual vai ser apagada).
        _coleta.update(sessao_id=None, sessao_modo=None, amostra_id=None, origem=None)
    if alvo == "tudo":
        banco.apagar_tudo()
    else:
        banco.apagar_amostras()
    contagem = banco.contagem()
    with _lock:
        _contagem = contagem
        if alvo == "tudo":
            _rede, _modelo = None, None
    return True, alvo
