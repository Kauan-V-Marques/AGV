# ─────────────────────────────────────────────
# Imagem base: Python 3.11 slim (Debian Bookworm)
# ─────────────────────────────────────────────
FROM python:3.11-slim-bookworm

# Metadados
LABEL description="AGV – servidor web de controle"
LABEL maintainer="kauan"

# Variáveis de ambiente
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AGV_HOST=0.0.0.0 \
    AGV_PORT=5000

WORKDIR /app

# ─────────────────────────────────────────────
# Dependências do sistema
#   libfreenect-dev  – biblioteca C do Kinect v1 (o python3-freenect nao existe no Debian)
#   libusb           – acesso USB (Kinect + Arduino)
#   build-essential/cmake – compilar o dlib (face-recognition) e o driver Python do Kinect
#   curl/ca-certificates  – baixar o codigo-fonte do driver
# ─────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        libfreenect-dev \
        libfreenect-bin \
        libusb-1.0-0-dev \
        udev \
        build-essential \
        cmake \
        curl \
        ca-certificates \
        && rm -rf /var/lib/apt/lists/*

# ─────────────────────────────────────────────
# Dependências Python
# face-recognition puxa o dlib, que compila do fonte (demora na 1a build).
# ─────────────────────────────────────────────
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Driver Python do Kinect, compilado contra a libfreenect do sistema
COPY scripts/instalar_freenect_python.sh ./scripts/
RUN PYTHON=python PIP_ARGS=--no-cache-dir bash scripts/instalar_freenect_python.sh

# ─────────────────────────────────────────────
# Código do projeto
# ─────────────────────────────────────────────
COPY pc_agv/ ./pc_agv/
COPY launch_production.py ./

EXPOSE 5000

# Ponto de entrada
CMD ["python", "launch_production.py"]
