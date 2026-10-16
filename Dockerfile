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
#   libfreenect-dev  – headers C do driver do Kinect v1
#   cython3          – necessário para compilar bindings Python do freenect
#   libusb           – acesso USB (Kinect + Arduino)
#   cmake/g++/etc.   – necessários para compilar dlib (face-recognition) e freenect
# ─────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        libfreenect-dev \
        libfreenect-bin \
        cython3 \
        libusb-1.0-0 \
        udev \
        build-essential \
        cmake \
        && rm -rf /var/lib/apt/lists/*

# ─────────────────────────────────────────────
# Dependências Python
# face-recognition puxa o dlib, que compila do fonte.
# freenect compila bindings C contra libfreenect-dev.
# ─────────────────────────────────────────────
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt freenect

# ─────────────────────────────────────────────
# Código do projeto
# ─────────────────────────────────────────────
COPY pc_agv/ ./pc_agv/
COPY launch_production.py ./

EXPOSE 5000

# Ponto de entrada
CMD ["python", "launch_production.py"]
