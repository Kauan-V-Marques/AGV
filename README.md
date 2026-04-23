# AGV Basico

Projeto reduzido para o fluxo essencial do AGV:

- site unico para controle
- video RGB ao vivo do Kinect v1
- mapa de profundidade ao vivo
- botoes W A S D para celular
- teclado W A S D, setas e E como esquerda no PC
- envio serial para o Arduino
- modo manual e modo autonomo simples por profundidade
- um unico executavel principal: `launch_production`

## Estrutura que ficou

```text
agv/
├── arduino/
│   └── sketch_may13a.ino
├── launch_production.py
├── launch_production.spec
├── logs/
├── pc_agv/
│   ├── iniciar_sistema.py
│   └── requirements.txt
└── tests/
```

## O que saiu do projeto

- reconhecimento facial
- banco SQLite da IA
- cadastro de faces
- cliente remoto separado
- launchers alternativos
- builds antigos e executaveis gerados

## Rodando com Docker (jeito mais facil)

Com Docker qualquer pessoa consegue rodar o projeto sem instalar nada manualmente.

### Pre-requisitos

- [Docker](https://docs.docker.com/engine/install/) instalado
- [Docker Compose](https://docs.docker.com/compose/install/) instalado

### Subir o servidor

```bash
# 1. Clone o repositorio
git clone <URL_DO_REPO>
cd agv

# 2. Suba o container (na primeira vez ele constroi a imagem automaticamente)
docker compose up --build

# 3. Acesse no navegador
# http://localhost:5000
```

Para rodar em segundo plano:

```bash
docker compose up -d --build
```

Para parar:

```bash
docker compose down
```

### Dispositivos de hardware

O `docker-compose.yml` ja esta configurado para passar o Arduino (`/dev/ttyACM0`) e o
Kinect (barramento USB) para dentro do container. Se o Arduino aparecer em outra porta
(ex.: `/dev/ttyACM1`), edite o campo `devices` no `docker-compose.yml`.

### Verificar logs

```bash
docker compose logs -f
```

---

## Dependencias no Linux

Instale os pacotes do sistema no PC AGV:

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-freenect libfreenect-dev libfreenect-bin
```

Instale as dependencias Python:

```bash
cd '/home/kauan-linux/Área de trabalho/agv'
pip3 install -r pc_agv/requirements.txt --break-system-packages
```

## Como rodar em desenvolvimento

```bash
cd '/home/kauan-linux/Área de trabalho/agv'
/usr/bin/python3 launch_production.py
```

O launcher:

1. tenta liberar o Kinect
2. sobe o servidor web no mesmo processo
3. abre o navegador em `http://127.0.0.1:5000`
4. mantem o terminal aberto com os logs

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

Instale o PyInstaller se ainda nao estiver instalado:

```bash
pip3 install pyinstaller --break-system-packages
```

Depois gere o binario:

```bash
cd '/home/kauan-linux/Área de trabalho/agv'
~/.local/bin/pyinstaller launch_production.spec
```

Saida esperada:

```text
dist/launch_production
```

Esse executavel ja inclui o launcher e o backend no mesmo processo.

## Variaveis uteis

| Variavel | Padrao | Uso |
|---|---|---|
| `AGV_HOST` | `0.0.0.0` | interface do servidor |
| `AGV_PORT` | `5000` | porta HTTP |
| `AGV_ARDUINO_PORT` | auto | porta serial fixa |
| `AGV_ARDUINO_BAUD` | `9600` | baud do Arduino |
| `AGV_ARDUINO_ENABLED` | `1` | desliga serial se `0` |

## Protocolo serial

O servidor envia para o Arduino o formato:

```text
aceleracao,direcao\n
```

Exemplo:

```text
140,0
100,-177
```

## Observacoes

- O modo autonomo atual e simples: ele usa apenas profundidade para seguir em frente e desviar do lado com mais espaco.
- Se o Kinect nao estiver respondendo, o modo autonomo para o AGV.
- O firmware do Arduino foi mantido porque ja estava servindo para o fluxo basico de motores e seguranca.
