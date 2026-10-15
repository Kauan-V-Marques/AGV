#!/usr/bin/env bash
# Compila e instala o driver Python do Kinect v1 (modulo `freenect`).
#
# Ubuntu e Debian empacotam so a biblioteca C (libfreenect-dev); o pacote
# python3-freenect nao existe. O wrapper Python e compilado a partir do
# codigo-fonte do OpenKinect/libfreenect e ligado a biblioteca do sistema.
#
# Pre-requisitos (apt): libfreenect-dev libusb-1.0-0-dev build-essential curl
# e o numpy ja instalado no mesmo Python.
#
# Variaveis opcionais:
#   PYTHON              interpretador alvo            (padrao: python3)
#   PIP_ARGS            argumentos extras do pip      (ex.: --break-system-packages)
#   LIBFREENECT_VERSAO  tag do OpenKinect/libfreenect (padrao: v0.7.5, compativel com Cython 3)
set -euo pipefail

PYTHON="${PYTHON:-python3}"
VERSAO="${LIBFREENECT_VERSAO:-v0.7.5}"
read -r -a PIP_EXTRA <<< "${PIP_ARGS:-}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo ">> baixando OpenKinect/libfreenect ${VERSAO}"
curl -fsSL "https://github.com/OpenKinect/libfreenect/archive/refs/tags/${VERSAO}.tar.gz" | tar -xz -C "$TMP"

echo ">> compilando o wrapper Python com ${PYTHON}"
"$PYTHON" -m pip install "${PIP_EXTRA[@]}" "cython>=3" "setuptools<81"
cd "$TMP/libfreenect-${VERSAO#v}/wrappers/python"
"$PYTHON" -m pip install "${PIP_EXTRA[@]}" --no-build-isolation .

echo ">> verificando"
cd /
"$PYTHON" -c "import freenect; print('freenect instalado em', freenect.__file__)"
