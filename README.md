# agv

Sistema de controle web para o AGV: video RGB e mapa de profundidade ao vivo do
Kinect v1 (e webcam ZED), reconhecimento facial, controle manual por teclado ou
joystick virtual, modo autonomo simples por profundidade e envio serial para o
Arduino. Um unico executavel principal (`launch_production`) sobe o backend e
abre o navegador no painel.

## 🚀 Começando

Estas instrucoes colocam uma copia do projeto rodando na sua maquina. Veja
**Pre-requisitos** e **Instalacao** abaixo. O jeito mais simples e via Docker —
funciona tanto no PC do AGV (Linux/Ubuntu, com Kinect e Arduino conectados)
quanto so para visualizar o painel web (Windows/Mac, sem hardware).

### 📋 Pré-requisitos

**Para rodar com Docker (recomendado):**

- [Docker](https://docs.docker.com/engine/install/) e
  [Docker Compose](https://docs.docker.com/compose/install/) instalados
- No Windows/Mac: [Docker Desktop](https://www.docker.com/products/docker-desktop/)
  (com o backend WSL2 no Windows)

**Para rodar sem Docker:**

- **No Windows:**
  - Python 3.10+ instalado
  - Opcional: Arduino conectado em porta serial USB (ex.: `COM3`). O Kinect v1 no Windows opera em modo de simulação/fallback (a interface abre e funciona normalmente sem o hardware).
- **No Linux/Ubuntu (PC do AGV com Kinect e Arduino):**
  - Drivers e ferramentas de build do Kinect e USB:
    ```bash
    sudo apt update
    sudo apt install -y python3 python3-pip python3-venv libfreenect-dev libfreenect-bin cython3 build-essential cmake libusb-1.0-0
    ```
    > **Nota sobre o Kinect:** Distribuições modernas (Debian 12 Bookworm, Ubuntu 22.04+) não possuem o pacote `python3-freenect` no APT. Os headers de desenvolvimento (`libfreenect-dev`) e `cython3` são instalados pelo sistema e o módulo Python é instalado via `pip install freenect`.

### 🔧 Instalação e Execução

Passo a passo para ter o ambiente rodando.

**1. Clone o repositório**

```bash
git clone https://github.com/Kauan-V-Marques/AGV.git
cd AGV
```

**2. Executando com Docker**

- **No Windows / Mac (teste do painel web, com ou sem hardware):**
  Utilize a variante `docker-compose.windows.yml`, que expõe a porta `5000:5000` e desativa o passthrough direto de `/dev/...`:
  ```bash
  docker compose -f docker-compose.windows.yml up --build
  ```

- **No PC do AGV (Linux/Ubuntu, com Kinect e Arduino conectados via USB):**
  O arquivo padrão `docker-compose.yml` mapeia o Arduino (`/dev/ttyACM0`), o barramento USB do Kinect (`/dev/bus/usb`) e usa `network_mode: host`:
  ```bash
  docker compose up --build
  ```

Para rodar em segundo plano, adicione `-d`. Para parar, execute `docker compose down`. Para acompanhar os logs em tempo real: `docker compose logs -f`.

**3. Executando localmente sem Docker**

- **No Windows:**
  1. Crie e ative um ambiente virtual:
     ```powershell
     python -m venv .venv
     .\.venv\Scripts\activate
     ```
  2. Instale as dependências:
     ```powershell
     pip install -r requirements.txt
     ```
  3. Inicie o sistema:
     ```powershell
     python launch_production.py
     ```
  *Comportamento sem hardware:* A aplicação inicializa o servidor web e abre o navegador automaticamente em `http://127.0.0.1:5000`. O Kinect é tratado como opcional: sem o hardware, os streams exibem telas informativas ("Aguardando Kinect...") e todas as rotas e controles continuam operacionais. Caso haja um Arduino conectado (ex.: `COM3`), a porta é detectada e conectada automaticamente; se não houver, o modo serial entra em espera sem travar o sistema.

- **No Linux/Ubuntu (PC do AGV):**
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -r requirements.txt freenect
  python launch_production.py
  ```

**4. Acesse no navegador**

```text
http://localhost:5000
```

## 🛠️ Construído com

- [Python 3.11](https://www.python.org/) — linguagem principal do backend
- [Flask](https://flask.palletsprojects.com/) — servidor web e API
- [OpenCV](https://opencv.org/) — video, mapa de profundidade e deteccao de rosto
- [face-recognition](https://github.com/ageitgey/face_recognition) / dlib — reconhecimento facial
- [libfreenect](https://github.com/OpenKinect/libfreenect) — driver do Kinect v1
- [PySerial](https://pyserial.readthedocs.io/) — comunicacao serial com o Arduino
- [Docker](https://www.docker.com/) — empacotamento e execucao
- [PyInstaller](https://pyinstaller.org/) — gera o executavel unico
- Firmware Arduino (C++) — controle dos motores

## 📌 Versão

Ainda sem tags formais de versao no repositorio — o historico de commits usa
marcos como `v1.0.2`, `v1.0.3`, `v1.0.4` (ultima: reorganizacao do sketch do
Arduino e padronizacao dos nomes para minusculas). Veja o
[historico de commits](https://github.com/Kauan-V-Marques/AGV/commits/main)
para o que mudou em cada marco.

## ✒️ Autores

- **Kauan V. Marques** — [Kauan-V-Marques](https://github.com/Kauan-V-Marques)

---

## Estrutura do projeto

```text
agv/
├── arduino/
│   └── sketch_may13a/
│       └── sketch_may13a.ino
├── pc_agv/
│   ├── __init__.py
│   └── iniciar_sistema.py
├── tests/
│   ├── test_arduino_serial.py
│   └── test_kinect_release.py
├── logs/                      # fotos e encodings gerados em runtime (git-ignored)
├── launch_production.py
├── launch_production.spec
├── requirements.txt
├── Dockerfile
├── docker-compose.yml         # deploy no PC do AGV (Linux/Ubuntu)
├── docker-compose.windows.yml # so o painel web, sem hardware (Windows)
└── README.md
```

## API basica

| Metodo | Rota | Uso |
|---|---|---|
| GET | `/` | painel web |
| GET | `/video` | stream RGB |
| GET | `/depth_map` | stream depth |
| GET | `/api/status` | status do AGV |
| POST | `/api/control` | atualiza modo e comando |
| POST | `/api/stop` | parada total |
| POST | `/api/kinect/reconnect` | tenta reiniciar captura |

Exemplo de comando manual:

```bash
curl -X POST http://127.0.0.1:5000/api/control \
  -H 'Content-Type: application/json' \
  -d '{"mode":"manual","speed":55,"steering":0,"source":"curl"}'
```

## Gerar o executavel unico

```bash
pip3 install pyinstaller --break-system-packages
pyinstaller launch_production.spec
```

Saida esperada: `dist/launch_production` — ja inclui o launcher e o backend no
mesmo processo.

## Variaveis uteis

| Variavel | Padrao | Uso |
|---|---|---|
| `AGV_HOST` | `0.0.0.0` | interface do servidor |
| `AGV_PORT` | `5000` | porta HTTP |
| `AGV_ARDUINO_PORT` | auto | porta serial fixa |
| `AGV_ARDUINO_BAUD` | `9600` | baud do Arduino |
| `AGV_ARDUINO_ENABLED` | `1` | desliga serial se `0` |

## Protocolo serial

O servidor envia para o Arduino o formato `aceleracao,direcao\n`. Exemplo:

```text
140,0
100,-177
```

## Observações

- O modo autonomo atual e simples: usa apenas profundidade para seguir em
  frente e desviar do lado com mais espaco.
- Se o Kinect nao estiver respondendo, o modo autonomo para o AGV.
- O firmware do Arduino foi mantido porque ja estava servindo para o fluxo
  basico de motores e seguranca.

## 📄 Licença

Este projeto esta sob a licenca MIT — veja o arquivo [LICENSE](LICENSE) para
detalhes.
