# Estrutura Atual do Projeto

## Pastas e arquivos

```
agv/
├── pc_agv/
│   ├── iniciar_sistema.py      # servidor Flask + freenect (unico arquivo)
│   ├── iniciar_sistema.spec    # spec PyInstaller → dist/iniciar_sistema
│   ├── launch_production.spec  # spec alternativo para build do launcher via pc_agv/
│   └── requirements.txt        # flask, opencv-python-headless, numpy
├── pc_controle/                # pasta para cliente remoto (a preencher)
├── arduino/
│   └── sketch_may13a.ino       # firmware dos motores (protocolo serial)
├── launch_production.py        # launcher: remove gspca, sobe servidor, abre browser
├── launch_production.spec      # spec PyInstaller → dist/launch_production
├── ver_site_teste.py            # launcher alternativo simples
├── abrir_site_teste.sh          # script shell para abrir o painel
├── README.md                   # guia completo de instalacao e uso
└── ESTRUTURA_PC.md             # este arquivo
```

## Fluxo rapido em outro PC Linux

1. Copie a pasta `pc_agv/` e o arquivo `launch_production.py`
2. Instale dependencias:
   ```bash
   sudo apt install -y freenect libfreenect-dev
   pip3 install -r pc_agv/requirements.txt --break-system-packages
   ```
3. Execute:
   ```bash
   python3 launch_production.py
   ```
4. Acesse no navegador: `http://IP_DO_AGV:5000`

## Usar executaveis (sem Python instalado)

Gere os executaveis uma vez:
```bash
pip3 install pyinstaller --break-system-packages
cd pc_agv && ~/.local/bin/pyinstaller iniciar_sistema.spec
cd .. && ~/.local/bin/pyinstaller launch_production.spec
```

Execute:
```bash
# copie ambos para a mesma pasta
cp pc_agv/dist/iniciar_sistema dist/
./dist/launch_production
```

## Comandos uteis

| Acao | Comando |
|------|---------|
| Iniciar (recomendado) | `python3 launch_production.py` |
| Iniciar servidor direto | `cd pc_agv && python3 iniciar_sistema.py` |
| Parar | `Ctrl+C` ou `pkill -f iniciar_sistema` |
| Painel web | `http://127.0.0.1:5000` |
| Status API | `http://127.0.0.1:5000/api/status` |
| Gerar exe servidor | `cd pc_agv && ~/.local/bin/pyinstaller iniciar_sistema.spec` |
| Gerar exe launcher | `~/.local/bin/pyinstaller launch_production.spec` |

## Observacoes

- A porta do servidor pode ser mudada via: `export AGV_PORT=5050`
- O Kinect precisa da lib `libfreenect` instalada no sistema; nao e pura Python
- O spec `iniciar_sistema.spec` inclui automaticamente `libfreenect.so` e `libusb-1.0.so`
- O launcher detecta se existe o executavel `iniciar_sistema` ao lado; senao usa o `.py`
- Arduino: protocolo serial `aceleracao,direcao` (texto simples via USB)

