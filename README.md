# AGV — Guia Completo

Servidor Kinect v1 com painel web, streams de video e API de controle do AGV.

## 1. O que este projeto faz

**PC AGV (Linux):**
- Stream de video RGB ao vivo (MJPEG)
- Mapa de profundidade colorido ao vivo (MJPEG)
- Telemetria: distancia ao objeto, acelerometro, angulo do motor (tilt)
- Estabilizacao automatica do motor Kinect via acelerometro
- API REST para comandos de movimento (manual/auto)
- Painel web com streams + telemetria + botoes de controle

**PC Controle (qualquer OS na mesma rede):**
- Acessa o painel pelo navegador: `http://IP_DO_AGV:5000`
- Envia comandos via botoes web ou API REST

## 2. Estrutura

```
agv/
├── pc_agv/
│   ├── iniciar_sistema.py      # servidor principal (Flask + freenect)
│   ├── iniciar_sistema.spec    # spec PyInstaller para gerar executavel
│   └── requirements.txt        # dependencias Python
├── pc_controle/                # pasta para cliente Windows (a criar)
├── arduino/
│   └── sketch_may13a.ino       # firmware Arduino
├── launch_production.py        # launcher de producao (inicia servidor + browser)
├── launch_production.spec      # spec PyInstaller para gerar executavel do launcher
└── ver_site_teste.py           # launcher simples alternativo
```

## 3. Instalacao

### 3.1 Sistema Linux (PC AGV)

```bash
sudo apt update
sudo apt install -y python3 python3-pip freenect libfreenect-dev libfreenect-bin
```

### 3.2 Dependencias Python

```bash
cd '/home/kauan-linux/Área de trabalho/agv'
pip3 install -r pc_agv/requirements.txt --break-system-packages
```

## 4. Como iniciar

### 4.1 Launcher de producao (recomendado)

```bash
cd '/home/kauan-linux/Área de trabalho/agv'
/usr/bin/python3 launch_production.py
```

Faz automaticamente:
1. Remove o driver `gspca_kinect` se estiver bloqueando o freenect
2. Inicia `pc_agv/iniciar_sistema.py` em segundo plano
3. Aguarda o servidor responder
4. Abre o navegador no painel

### 4.2 Servidor direto (sem abrir browser)

```bash
cd '/home/kauan-linux/Área de trabalho/agv/pc_agv'
/usr/bin/python3 iniciar_sistema.py
```

### 4.3 Variaveis de ambiente

| Variavel   | Padrao    | Descricao                  |
|------------|-----------|----------------------------|
| AGV_HOST   | `0.0.0.0` | Interface de escuta         |
| AGV_PORT   | `5000`    | Porta do servidor           |

Exemplo:
```bash
AGV_PORT=5050 /usr/bin/python3 launch_production.py
```

## 5. Endpoints da API

| Metodo | Rota            | Descricao                         |
|--------|-----------------|-----------------------------------|
| GET    | `/`             | Painel web                        |
| GET    | `/video`        | Stream MJPEG RGB (20 fps)         |
| GET    | `/depth_map`    | Stream MJPEG profundidade (10 fps)|
| GET    | `/api/status`   | JSON com toda a telemetria        |
| POST   | `/api/control`  | Enviar comando de movimento       |
| POST   | `/api/stop`     | Parar o AGV                       |

Exemplo de comando:
```bash
curl -X POST http://IP_DO_AGV:5000/api/control \
     -H "Content-Type: application/json" \
     -d '{"mode":"manual","speed":50,"steering":0}'
```

## 6. Gerar executaveis (PyInstaller)

Instale o PyInstaller uma vez:
```bash
pip3 install pyinstaller --break-system-packages
```

### 6.1 Servidor AGV (`iniciar_sistema`)

```bash
cd '/home/kauan-linux/Área de trabalho/agv/pc_agv'
~/.local/bin/pyinstaller iniciar_sistema.spec
# Executavel gerado em: pc_agv/dist/iniciar_sistema
```

### 6.2 Launcher de producao (`launch_production`)

```bash
cd '/home/kauan-linux/Área de trabalho/agv'
~/.local/bin/pyinstaller launch_production.spec
# Executavel gerado em: dist/launch_production
```

Alternativa (se voce ja estiver dentro de `pc_agv`):

```bash
cd '/home/kauan-linux/Área de trabalho/agv/pc_agv'
~/.local/bin/pyinstaller launch_production.spec
# Executavel gerado em: pc_agv/dist/launch_production
```

### 6.3 Usar juntos sem Python instalado

Copie os dois executaveis para a mesma pasta:
```bash
mkdir -p executaveis
cp pc_agv/dist/iniciar_sistema executaveis/
cp dist/launch_production       executaveis/
# Para rodar:
cd executaveis && ./launch_production
```

O launcher detecta automaticamente se o `iniciar_sistema` executavel esta na mesma pasta ou se deve usar o `.py` com Python.

### 6.4 Atualizar executaveis apos mudar frontend/backend

Sempre que editar `pc_agv/iniciar_sistema.py` (o frontend esta embutido nele), gere novamente os dois executaveis e recopie:

```bash
cd '/home/kauan-linux/Área de trabalho/agv/pc_agv'
~/.local/bin/pyinstaller iniciar_sistema.spec

cd '/home/kauan-linux/Área de trabalho/agv'
~/.local/bin/pyinstaller launch_production.spec

cp pc_agv/dist/iniciar_sistema EXECUTAVEIS/agv_pc/iniciar_sistema
cp dist/launch_production       EXECUTAVEIS/agv_pc/launch_production
cp pc_agv/dist/iniciar_sistema  EXECUTAVEIS/teste_local_pc/iniciar_sistema
cp ver_site_teste               EXECUTAVEIS/teste_local_pc/ver_site_teste
```

No painel, confira o bloco `Runtime` na barra lateral para validar revisao e data/hora do build realmente carregado.

## 7. Como parar

Pelo terminal onde esta rodando: `Ctrl + C`

Por comando:
```bash
pkill -f iniciar_sistema
```

## 8. Verificar Kinect

Se o Kinect nao abrir:

1. Desconecte e reconecte o cabo USB
2. Reinicie o Linux com o Kinect conectado
3. Teste rapido:

```bash
/usr/bin/python3 - <<'PY'
import freenect
r = freenect.sync_get_video(format=freenect.VIDEO_RGB)
d = freenect.sync_get_depth(format=freenect.DEPTH_11BIT)
print('rgb ok :', r is not None and r[0] is not None)
print('depth ok:', d is not None and d[0] is not None)
PY
```

Ambos devem retornar `True`.

## 9. Problemas comuns

| Sintoma | Causa provavel | Solucao |
|---------|---------------|---------|
| Sem frames no video | Driver `gspca_kinect` ativo | `sudo rmmod gspca_kinect` |
| Porta 5000 ocupada | Processo anterior ainda rodando | `pkill -f iniciar_sistema` |
| `freenect` nao encontrado | Lib nao instalada | `sudo apt install python3-freenect` |
| Motor retorna −64° | Leitura invalida (normal) | O sistema ignora e usa None |

## 10. Fluxo diario

1. Conectar Kinect ao USB
2. Abrir terminal na raiz do projeto
3. Rodar `launch_production.py` ou o executavel gerado
4. Acessar o painel no browser
5. Finalizar com `Ctrl + C`
