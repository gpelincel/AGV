# Arquitetura Interna — `pc_agv`

Este diretório contém todo o backend em Python, os templates HTML e os módulos JavaScript do frontend web do **AGV**.

---

## 📁 Estrutura de Diretórios

```text
pc_agv/
├── __init__.py              # Exportações públicas do pacote (create_server, app, etc.)
├── iniciar_sistema.py       # Núcleo do backend (drivers, threads, lógica e rotas Flask)
├── README.md                # Este documento de referência técnica
├── templates/
│   └── index.html           # Interface web única (dashboard, controles e feeds de vídeo)
└── static/
    ├── style.css            # Estilização visual (tema escuro/claro, layout responsivo)
    └── scripts/
        ├── api.js           # Funções utilitárias de requisição HTTP (fetch/JSON)
        ├── face.js          # Cadastro e seleção de identidades faciais
        ├── joystick.js      # Controle por stick virtual para dispositivos móveis
        ├── main.js          # Orquestração principal do frontend
        └── state.js         # Estado reativo da interface no navegador
```

---

## 🧵 Modelo de Concorrência (Threads)

Para garantir baixa latência na resposta aos comandos do usuário sem travar os fluxos de vídeo ou comunicação serial, o backend executa **5 threads paralelas de longa duração** gerenciadas por `start_runtime()`:

| Thread | Função | Descrição |
|---|---|---|
| `agv-kinect` | `_capture_loop()` | Executa o laço de eventos da `libfreenect`. Obtém frames RGB, matrizes de profundidade e leituras do acelerômetro, além de aplicar o algoritmo de nivelamento do motor de tilt. |
| `agv-zed` | `_zed_capture_loop()` | Captura o fluxo de vídeo da câmera frontal ZED via V4L2 (`cv2.VideoCapture`) ou decodificação por pipe do `FFmpeg`. |
| `agv-zed-face` | `_zed_face_worker_loop()` | Processa o reconhecimento facial (`dlib` / `face-recognition`) em segundo plano, liberando o GIL e evitando travamentos no streaming de vídeo. |
| `agv-autopilot` | `_autopilot_loop()` | Avalia as três zonas do mapa de profundidade (esquerda, centro, direita) e decide manobras autônomas de desvio de obstáculos. |
| `agv-control` | `_control_loop()` | Malha periódica de controle (`0.12s`). Envia comandos para o Arduino (`aceleracao,direcao`) e aciona o **watchdog de segurança** caso o operador perca a conexão. |

---

## 🔒 Gerenciamento de Estado e Sincronização

Todos os estados compartilhados entre as threads são protegidos por primitivas `threading.Lock`:

- `_state` / `_state_lock`: Armazena os frames RGB e Depth mais recentes, status de disponibilidade das câmeras, métricas de distância e ângulos do acelerômetro.
- `_control_state` / `_control_lock`: Mantém o modo ativo (`manual` ou `auto`), velocidade solicitada, esterçamento e timestamp do último comando.
- `_arduino_state` / `_arduino_lock`: Gerencia a conexão com a porta serial (ex.: `/dev/ttyACM0` ou `COM3`), status de recepção de feedback e histórico de erros.
- `_face_runtime` / `_face_lock`: Controla o banco de identidades cadastradas e o resultado da última face identificada.
- `_stop_event`: Instância de `threading.Event()` utilizada para sinalizar o encerramento gracioso e coordenado de todas as threads.

---

## 📡 Protocolo Serial com o Arduino

Os comandos para o Arduino Uno seguem o formato ASCII delimitado por quebra de linha:
```text
<aceleracao>,<direcao>\n
```
- **Aceleração**: `-255` a `255` (valores positivos para frente, negativos para ré).
- **Direção**: `-254` a `254` (valores negativos para esquerda, positivos para direita).

Exemplo: `140,0\n` (anda em linha reta) ou `0,-120\n` (gira à esquerda parado).

---

## 🛠️ Boas Práticas para Manutenção

1. **Adicionar Novas Rotas:**
   - Adicione funções decoradas com `@app.route` na seção de rotas em `iniciar_sistema.py`.
   - Sempre utilize headers `"Connection": "close"` em requisições de controle para não reter slots na piscina HTTP do navegador.
2. **Tratamento de Ausência de Hardware:**
   - Todo acesso ao Kinect (`freenect`) ou à serial (`serial`) deve ser protegido por blocos `try/except` com mensagens de fallback legíveis gravadas nos dicionários de estado.
   - Nunca bloqueie a inicialização do servidor caso um dispositivo físico não seja encontrado.
