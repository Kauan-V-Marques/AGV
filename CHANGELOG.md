# Changelog

Todas as mudanças relevantes do projeto. Cada versão tem uma tag no GitHub (`v1.0.2`, `v1.0.3`, `v1.0.4` e `v1.1.0`).

## [Não lançado]

### Documentação

- Síntese da pesquisa (`docs/documents/pesquisa-artigo.md`) atualizada para a versão 2 do artigo: condições comparadas, desenho experimental intrassujeitos com 20 operadores, definições operacionais, Comitê de Ética e avaliação técnica das sobreposições com critérios de aceitação.
- README alinhado ao artigo V2 e à apresentação do projeto: etapas da pesquisa, objetivos técnicos (zona não medida e avaliação técnica), RF39, cronograma com os meses previstos, papéis da equipe unificados, Prof. Dr. Daniel Ohata e alcance do Kinect v1 (Wasenmüller e Stricker, 2017).
- Corrigido o Problema 20, que aparecia como título no GitHub por falta de uma linha em branco antes do separador.

## [1.1.0] — 2026-10-16 — Entrega da documentação técnica

### Adicionado

- **Painel novo**, no estilo sala de controle e organizado por um menu lateral (Painel, Câmeras, Telemetria, Aprendizado, Mapa, Configurações), sem perder nenhuma função anterior (vídeos, HUD, WASD/joystick, rostos, tilt, velocidade, link). Novidades: foto do vídeo, troca de câmera pela miniatura, setores de proximidade, gráfico dos últimos 60 s, telemetria geral, avisos na tela, tema claro/escuro e layout para celular. O JavaScript virou módulos ES por assunto (`web/static/js/`).
- **Auto-aprendizado** (`pc_agv/aprendizado/`): grava exemplos do operador (profundidade 24×32 + comando), treina uma rede neural em NumPy e dirige no novo modo **IA**, sempre através de uma camada de segurança. No modo IA, as teclas e o joystick valem como correção e viram exemplos com peso dobrado; o retreino pesa as ações da IA pela recompensa (fase 2). Treino pelo botão ou automático; os 10 últimos modelos ficam guardados.
- **Auto-mapeamento** (`pc_agv/mapeamento/`): odometria visual pelo Kinect, sem encoders (varredura 2D + ICP com várias partidas, refinamento ponto a reta e restrição de tração diferencial), mapa de ocupação ao vivo, mapas salvos e calibração do avanço do Kinect. Em simulação com o ruído do Kinect v1: 0,1% a 1,9% de erro do caminho.
- Bancos SQLite em `logs/` (`aprendizado.db`, `mapas.db`): o que a IA aprendeu e os mapas continuam lá depois de desligar o sistema; um banco ilegível desliga só aquele recurso, sem apagar o arquivo.
- API: `/api/ia/*` e `/api/mapa/*`; `/api/control` aceita `mode=ia`; `/api/status` ganha as seções `ia` e `mapa`.

### Qualidade

- Testes de 19 para 225, todos sem hardware; cobertura do `pc_agv` de 28% para 90% (`controle.py` 99%, `aprendizado/` e `mapeamento/` acima de 92%).
- Simulador de Kinect (`tests/simulador_kinect.py`) para testar a odometria numa sala virtual com ruído realista.
- Testes funcionais do painel em navegador *headless*: 61 verificações no painel e 26 na página do mapa, sem erro de JavaScript.
- CI no GitHub Actions: lint, sintaxe dos módulos JavaScript, testes com cobertura mínima de 80%, build da imagem Docker e do executável (PyInstaller) a cada push.
- `requirements.txt` com faixas testadas e `setuptools<81` (o `face_recognition_models` depende do `pkg_resources`).
- Lint com Ruff (`ruff.toml`), sem avisos.

### Corrigido (encontrado pelos testes)

- Cadastro de rosto apagava o banco de rostos inteiro quando `logs/encodings.pkl` estava ilegível.
- `POST /api/kinect/tilt` com ângulo inválido dava erro 500; `nan`/`inf` chegavam ao motor. Agora responde 400.
- Upload sem limite de tamanho: novo `AGV_MAX_UPLOAD_MB` (10 MB), acima disso 413.
- README: a tabela do protocolo dizia `140,-177` para girar parado; o valor enviado é `140,-124`.
- Instalação do zero (Docker e passo B) falhava: o pacote `python3-freenect` não existe no Ubuntu/Debian. Novo `scripts/instalar_freenect_python.sh` compila o driver do Kinect a partir do código-fonte; o painel passa a dizer "driver freenect nao instalado".
- Executável do PyInstaller não abria: faltavam os modelos `.dat` do reconhecimento facial no pacote.
- Executável perdia os dados a cada execução: o *onefile* roda numa pasta temporária apagada ao sair. Agora `logs/` fica ao lado do executável (`AGV_DADOS_DIR` muda o local).
- A troca de modo no painel podia piscar de volta: uma leitura de `/api/status` que saiu antes do clique chegava depois dele, com o modo antigo.

### Refatorado

- `pc_agv/iniciar_sistema.py` (3.700 linhas) dividido em módulos por responsabilidade: `config`, `estado`, `util`, `runtime`, `controle`, `telemetria`, `hardware/` (kinect, zed, arduino), `visao/` (profundidade, rostos) e `web/` (rotas, mjpeg). O arquivo virou só o ponto de entrada (65 linhas).
- Painel web (HTML, CSS e JS) saiu do Python para `pc_agv/web/static/`.
- Comportamento idêntico: mesmas rotas e respostas, mesmo HTML/CSS/JS e capturas do painel e do HUD iguais pixel a pixel às da versão anterior.
- `pc_agv/sqlite_util.py`: abertura dos bancos (integridade, WAL) e preferências compartilhadas entre o aprendizado e o mapeamento.

### Corrigido

- Distâncias da profundidade (11 bits) usavam o ponto mais distante de cada janela; agora usam o mais próximo. O HUD e o modo autônomo passam a enxergar o obstáculo, não a parede atrás dele.
- No modo manual, o AGV parava após 2 s com a tecla segurada; o painel agora reenvia o comando a cada 500 ms (o watchdog continua parando o AGV se o navegador cair).
- PC mostrava o joystick e celular mostrava o WASD (`display: grid` vencia o atributo `hidden`).

### Removido

- Código morto do reconhecimento facial antigo (Haar cascade), rota comentada, `rgb_raw` e `AGV_CAPTURE_LOOP_SECONDS`.
- `.vscode/settings.json` deixa de ser versionado.

### Testes

- Testes do Kinect atualizados e independentes do `python3-freenect`; novos testes de distância e do modo autônomo (19 testes, todos passando).

### Documentação

- README reescrito no modelo da disciplina UPX — Realidade Aumentada (`dr-ohata/upx-modelo`).
- Pasta `docs/` com diagramas (`arquitetura.svg`, `janelas-profundidade.svg`), capturas do painel e do HUD e a síntese do artigo científico.
- Registro das limitações conhecidas, incluindo a análise da escolha do ponto nas janelas de profundidade de 11 bits.
- Registro da troca do computador embarcado: a Raspberry Pi 5 original queimou e o AGV passou a usar um notebook Linux.
- README atualizado para o painel novo, o auto-aprendizado e o auto-mapeamento: funcionalidades RF20–RF32, arquitetura (diagrama redesenhado), API, variáveis, uso passo a passo, fluxos, capturas novas, testes (225) e a validação da odometria em simulação.
- Revisão para a entrega: 14 diagramas novos feitos a partir do código (ligações físicas, inicialização, ciclo do firmware, bancos SQLite, modos de controle, regras do autônomo, perda de conexão, gravação e treino da IA, camada de segurança da IA, correção do operador, cadastro de rosto, CI, linha do tempo e branches) e correções pontuais (referência de seção, tempos do mapeamento, threads no fluxo principal, histórico de branches).

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
