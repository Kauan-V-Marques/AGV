# Changelog

Todas as mudanças relevantes do projeto. Os marcos `v1.0.x` foram registrados nas mensagens de commit.

## [Não lançado]

### Documentação

- README reescrito no modelo da disciplina UPX — Realidade Aumentada (`dr-ohata/upx-modelo`).
- Pasta `docs/` com diagramas (`arquitetura.svg`, `janelas-profundidade.svg`), capturas do painel e do HUD e a síntese do artigo científico.
- Registro das limitações conhecidas, incluindo a análise da escolha do ponto nas janelas de profundidade de 11 bits.
- Registro da troca do computador embarcado: a Raspberry Pi 5 original queimou e o AGV passou a usar um notebook Linux.

## 2026-08-31 — Organização e HUD

### Adicionado

- Visualizador **HUD em tela cheia** para o RGB do Kinect: chips de estado, distâncias, comando ativo, FPS, miniatura da profundidade e joystick no celular (PR #1).
- `docker-compose.windows.yml` para ver o painel no Docker Desktop sem hardware.
- Licença MIT.

### Alterado

- Repositório organizado: `requirements.txt` na raiz, `.gitignore` para builds, caches e `logs/`, remoção de binários e dados de rosto da árvore atual (PR #2).
- Build do Docker corrigida.
- README no padrão universal (começando, pré-requisitos, instalação, construído com, versão, autores).

## [1.0.4] — 2026-07-09

### Alterado

- Nome do projeto padronizado em minúsculas (`agv`).
- Sketch do Arduino movido para `arduino/sketch_may13a/`.
- Suporte a **ré**: o servidor envia aceleração negativa e o firmware inverte o sentido dos motores com proteção anti-reversão; teste de payload da ré adicionado.

## [1.0.3] — 2026-05-01 a 2026-05-06

### Adicionado

- Vários rostos cadastrados e seleção da pessoa procurada na câmera ZED (`/api/face/select`).
- Testes de tilt e estabilização do Kinect.
- `launch_production.py` passa a usar automaticamente a `.venv` do projeto, se ela existir.

## [1.0.2] — 2026-04-13

### Adicionado

- Expansão do servidor `pc_agv/iniciar_sistema.py` com reconhecimento facial.

## 2026-04-12 a 2026-04-23 — Início

### Adicionado

- Upload inicial do projeto AGV: servidor web, controle e firmware.
- Testes automatizados da serial do Arduino e da liberação USB do Kinect.
