"""Apoio aos testes: parada controlada dos loops, serial falsa e isolamento do estado global."""

import contextlib
import os
import tempfile
from unittest.mock import patch

import numpy as np

from pc_agv import estado
from pc_agv.aprendizado import servico
from pc_agv.hardware import arduino
from pc_agv.mapeamento import servico as servico_mapa
from pc_agv.mapeamento.grade import Grade
from pc_agv.mapeamento.odometria import Odometria
from pc_agv.visao import rostos


class EventoDeParada:
    """Substitui estado.stop_event: o loop roda `voltas` vezes e depois para.

    Cada chamada a is_set() consome uma volta; wait() volta na hora, sem dormir.
    """

    def __init__(self, voltas: int = 1):
        self._restantes = voltas

    def is_set(self) -> bool:
        if self._restantes > 0:
            self._restantes -= 1
            return False
        return True

    def wait(self, timeout=None) -> bool:
        return False


class SerialFalsa:
    """Porta serial em memoria: registra o que foi escrito e devolve linhas programadas."""

    def __init__(self, linhas=None, falhar_escrita: bool = False):
        self.linhas = list(linhas or [])
        self.escrito = []
        self.falhar_escrita = falhar_escrita
        self.fechada = False

    @property
    def in_waiting(self) -> int:
        return sum(len(item) for item in self.linhas)

    def readline(self) -> bytes:
        return self.linhas.pop(0) if self.linhas else b""

    def write(self, data: bytes) -> int:
        if self.falhar_escrita:
            raise OSError("cabo desconectado")
        self.escrito.append(data)
        return len(data)

    def flush(self) -> None:
        pass

    def reset_output_buffer(self) -> None:
        pass

    def setDTR(self, value) -> None:  # noqa: N802 - mesmo nome da API do pyserial
        pass

    def setRTS(self, value) -> None:  # noqa: N802 - mesmo nome da API do pyserial
        pass

    def close(self) -> None:
        self.fechada = True


@contextlib.contextmanager
def estado_isolado():
    """Restaura os dicionarios de estado compartilhado ao sair do bloco."""
    with contextlib.ExitStack() as pilha:
        for dicionario in (estado.state, estado.control_state, estado.autopilot_state, estado.settings,
                           estado.lidar_state):
            pilha.enter_context(patch.dict(dicionario))
        yield


@contextlib.contextmanager
def arduino_isolado():
    """Restaura o estado do link serial (porta aberta e dicionario de estado)."""
    with patch.dict(arduino._arduino_state), patch.dict(arduino._arduino_rx_state), \
            patch.dict(arduino._arduino_log_state), patch.object(arduino, "_arduino_serial", None):
        yield


@contextlib.contextmanager
def rostos_isolado():
    """Restaura o banco de rostos em memoria e o estado do reconhecimento."""
    with patch.dict(rostos._face_runtime), patch.dict(rostos._zed_face_state), \
            patch.object(rostos, "_face_known_encodings", []), \
            patch.object(rostos, "_face_known_names", []), \
            patch.object(rostos, "_face_known_image_paths", []), \
            patch.object(rostos, "_face_selected_name", ""):
        yield


@contextlib.contextmanager
def aprendizado_isolado(abrir: bool = True):
    """Aprendizado com banco proprio numa pasta temporaria; o estado do modulo volta ao sair.

    Devolve o caminho do banco. Com abrir=False o banco ainda nao foi aberto.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as pasta, contextlib.ExitStack() as pilha:
        valores = {
            "_banco": None,
            "_erro_banco": None,
            "_rede": None,
            "_modelo": None,
            "_prefs": dict(servico.PADROES),
            "_contagem": {"operador": 0, "correcao": 0, "ia": 0, "total": 0},
            "_ultimo_id": 0,
            "_ultimo_movimento": 0.0,
        }
        for nome, valor in valores.items():
            pilha.enter_context(patch.object(servico, nome, valor))
        pilha.enter_context(patch.dict(servico._treino))
        pilha.enter_context(patch.dict(servico._coleta))
        caminho = os.path.join(pasta, "aprendizado.db")
        if abrir:
            servico.iniciar(caminho)
        try:
            yield caminho
        finally:
            servico.encerrar()


@contextlib.contextmanager
def mapeamento_isolado(abrir: bool = True, tamanho_m: float = 12.0):
    """Mapeamento com banco proprio numa pasta temporaria e mapa vazio; tudo volta ao sair."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as pasta, contextlib.ExitStack() as pilha:
        valores = {
            "_banco": None,
            "_erro_banco": None,
            "_avanco_m": 0.0,
            "_grade": Grade(tamanho_m, 0.05),
            "_odometria": Odometria(avanco_m=0.0),
            "_trajetoria": [],
            "_varredura": np.zeros((0, 2)),
            "_estado": {"rodando": False, "somente_leitura": False, "mapa_id": None, "nome": None,
                        "leituras": 0, "falhas": 0, "salvo": True, "fonte": None},
            "_png": (-1, None),
            "_ultima_volta_lidar": 0.0,
        }
        for nome, valor in valores.items():
            pilha.enter_context(patch.object(servico_mapa, nome, valor))
        pilha.enter_context(patch.object(servico_mapa, "MAPA_TAMANHO_M", tamanho_m))
        caminho = os.path.join(pasta, "mapas.db")
        if abrir:
            servico_mapa.iniciar(caminho)
        try:
            yield caminho
        finally:
            servico_mapa.encerrar()
