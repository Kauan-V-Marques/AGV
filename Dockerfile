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
#   freenect         – driver do Kinect v1
#   libusb           – acesso USB (Kinect + Arduino)
#   cmake/g++/etc.    – necessarios para compilar dlib (face-recognition)
# ─────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-freenect \
        libfreenect-dev \
        libfreenect-bin \
        libusb-1.0-0 \
        udev \
        build-essential \
        cmake \
        # necessário para pyserial encontrar portas seriais
        && rm -rf /var/lib/apt/lists/*

# ─────────────────────────────────────────────
# Dependências Python (sem freenect — já é de sistema)
# face-recognition puxa o dlib, que compila do fonte (demora na 1a build).
# ─────────────────────────────────────────────
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# ─────────────────────────────────────────────
# Código do projeto
# ─────────────────────────────────────────────
COPY pc_agv/ ./pc_agv/
COPY launch_production.py ./

EXPOSE 5000

# Ponto de entrada
CMD ["python", "launch_production.py"]
