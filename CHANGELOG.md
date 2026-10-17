# Changelog

Todas as mudanças relevantes do projeto. Cada versão tem uma tag no GitHub (`v1.0.2`, `v1.0.3`, `v1.0.4`, `v1.1.0` e `v1.1.1`).

## [Não lançado]

### Adicionado

- **RPLIDAR 360°** somado ao Kinect (`pc_agv/hardware/rplidar.py`, `pc_agv/visao/lidar.py`): driver serial próprio do protocolo Slamtec (A1/A2/A3, sem SDK), thread `agv-lidar` que reconecta sozinha, volta convertida para o referencial do AGV e folga até a borda do chassi em 8 setores.
- **Autônomo com LIDAR:** a frente usa a menor leitura entre Kinect e laser (cobre a zona cega do Kinect abaixo de ~0,5 m); não gira para o lado em que o chassi está encostado; dá ré lenta quando a frente e as diagonais fecham e a traseira está livre; afasta-se de parede lateral. Sem Kinect continua parando.
- **IA com LIDAR:** a camada de segurança usa a frente combinada e bloqueia a ré com obstáculo a menos de 0,35 m atrás.
- **Mapa com LIDAR:** a odometria casa as voltas de 360° do laser; o mapa recebe o laser e o Kinect, cada um da sua posição no AGV; sem o laser, volta ao Kinect.
- **Radar 360°** na página *Telemetria* e no HUD, indicador LIDAR na barra superior e no HUD; rota `/api/lidar` e seção `lidar` em `/api/status`.
- **HUD** com o vídeo da câmera traseira (detecção facial, borda verde e nome quando reconhece) e a folga traseira.
- **Webcam no lugar da ZED:** sem ZED, a câmera traseira usa qualquer outra webcam (nunca o Kinect).
- **`agv.env`**: configuração local lida pelo launcher (modelo em `agv.env.exemplo`).
- `scripts/instalar_regras_udev.sh`: portas fixas `/dev/agv-arduino` e `/dev/rplidar`, ModemManager longe delas e blacklist do `gspca_kinect`.

- **AGV virtual** (`tests/simulador_agv.py`): o código real do autônomo, da IA e do mapa dirigindo um AGV simulado com Kinect, RPLIDAR, firmware e cinemática; cenários em `tests/test_simulacao.py`.
- `scripts/calibrar_lidar.py`: sugere a montagem do RPLIDAR encaixando a faixa do Kinect na volta do laser (recusa resultado fraco ou ambíguo).
- `scripts/testar_reconexao.sh`: tira e recoloca cada USB com o sistema rodando e mede a recuperação.
- `AGV_ZED_STREAM_FPS` (10): FPS do stream da câmera traseira, separado do da captura.

### Corrigido

- **Mapa com LIDAR em movimento** perdia a posição (26% de erro do caminho): a normal das paredes exigia vizinhos a menos de 15 cm, e o laser tem um ponto a cada ~3°. Agora o limite acompanha o espaçamento: 0,3% a 0,6%.
- **Autônomo** batia girando perto de parede e não saía de beco: com o LIDAR só gira com espaço para os cantos do chassi, recua em linha reta até ter espaço, e a ré depende só do corredor atrás; rente a uma parede segue reto.
- **IA** batia dando ré e virando: sem espaço de giro, a segurança zera a direção.
- O stream da câmera traseira reenviava o mesmo quadro (46,5 fps, 683 KB/s): os streams só mandam quadro novo (−78% de rede, CPU 121% → 81%).
- O Arduino não tenta mais as portas `ttyS*` (uma delas viraria um Arduino falso); a webcam religada volta em 3 s (antes ~19 s).
- O Arduino não tenta mais abrir a porta do RPLIDAR.
- `scripts/instalar_freenect_python.sh` com `PYTHON=.venv/bin/python` (caminho relativo) e sem `curl`.
- Arquivo do nome da câmera aberto sem ser fechado na busca da ZED.

### Qualidade

- Testes de 225 para 274 (`tests/test_lidar.py`, `tests/test_simulacao.py`); cobertura do `pc_agv` em 90%.
- Validado no notebook do AGV (Ubuntu 22.04.5, Python 3.10.12) com Kinect, RPLIDAR, webcam e Arduino ligados juntos: firmware (sem mover os motores), reconexão de cada USB, desempenho dos streams, reconhecimento facial e modos pela API.
- AGV virtual: 0 colisões em 36 min e 254 m no armazém simulado; mapa com o LIDAR com 0,3–0,6% de erro do caminho.

### Documentação

- Cronograma técnico com as datas previstas de cada etapa e os responsáveis, iguais aos do quadro de tarefas (revisão de 16/10/2026).

## [1.1.1] — 2026-10-16 — Alinhamento ao artigo V2

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
