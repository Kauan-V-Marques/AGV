# Síntese da pesquisa — artigo científico do projeto

**Título:** Uso de Realidade Aumentada na teleoperação de um AGV: apoio à navegação remota em intralogística
**Title:** Use of Augmented Reality in the teleoperation of an AGV: support for remote navigation in intralogistics

**Autores:** Kauan Victor Marques, Pedro Henrique Santana de Oliveira, Gabriel Pelincel Ramalho, Jorge Henrique Gobbo Berenschot, Felipe Matias de Jesus
**Orientação:** Prof. Daniel Ohata — Centro Universitário Facens, Sorocaba/SP

Este documento resume o desenho da pesquisa que orienta o desenvolvimento do AGV. O README traz apenas a síntese; aqui estão as questões, hipóteses, variáveis e trabalhos relacionados.

---

## 1. Problema de pesquisa

| Nível | Descrição |
|---|---|
| Prático | Operadores que assumem o controle remoto de um AGV têm dificuldade para estimar distâncias e antecipar obstáculos usando apenas o vídeo 2D da câmera embarcada, o que leva a colisões, maior tempo de intervenção e maior esforço. |
| Tecnológico | As soluções de RA que mitigam isso dependem, em sua maioria, de óculos de alto custo (ex.: HoloLens) e robôs comerciais; já os HUDs das plataformas de baixo custo mostram números fixos na tela, sem indicar **onde** está o obstáculo. |
| Científico | Ainda não está estabelecido se sobreposições registradas espacialmente, geradas a partir de um sensor RGB-D e exibidas em um navegador comum, melhoram o desempenho e reduzem a carga de trabalho do operador na teleoperação de AGVs em intralogística. |

## 2. Questões de pesquisa e hipóteses

| ID | Questão / hipótese |
|---|---|
| **RQ1** | Qual é o efeito da sobreposição, em RA, de informações de profundidade e de obstáculos registradas sobre o vídeo de teleoperação no **tempo de conclusão** e no **número de colisões** de operadores não especialistas em um percurso representativo de intralogística? |
| **RQ2** | Como essa sobreposição afeta a **carga de trabalho percebida (NASA-TLX)** e a **usabilidade (SUS)** da interface nessa mesma tarefa? |
| **H1** | Com as sobreposições de RA registradas, os operadores concluirão o percurso com menos colisões e em menos tempo do que com o vídeo RGB + HUD de telemetria convencional. |
| **H2** | A interface com sobreposições de RA terá menor carga de trabalho (NASA-TLX) e maior usabilidade (SUS) do que a interface convencional. |

## 3. Variáveis

| Tipo | Variáveis |
|---|---|
| Independente | Tipo de interface: **HUD de telemetria fixo na tela** × **HUD + sobreposições de RA registradas** |
| Dependentes | Tempo de conclusão, número de colisões, pontuação SUS, pontuação NASA-TLX |
| Complementares | Número de intervenções, latência (RTT) e taxa de quadros, observações e relatos dos operadores |
| De controle | Percurso e obstáculos, AGV e velocidade máxima, rede e latência monitorada, iluminação, dispositivo de controle, instruções e treino de cada participante |

## 4. Classificação da pesquisa

- **Natureza:** aplicada.
- **Objetivos:** predominantemente explicativa.
- **Abordagem:** mista — dados quantitativos de desempenho e percepção, complementados por observações e relatos.

## 5. Objetivos específicos

1. Levantar e comparar trabalhos que empregam RA e interfaces espaciais na teleoperação de robôs móveis.
2. Identificar os requisitos das sobreposições: quais informações serão apresentadas e como serão registradas sobre o vídeo a partir do mapa de profundidade.
3. Desenvolver uma prova de conceito das sobreposições integrada ao servidor de teleoperação existente.
4. Evoluir a prova de conceito para um protótipo experimental estável.
5. Estabelecer o procedimento de avaliação (tarefa, cenário, condições comparadas e instrumentos).
6. Coletar dados de desempenho e de percepção com operadores.
7. Analisar os resultados e confrontá-los com as hipóteses.

## 6. Relação com os ODS (Agenda 2030)

- **ODS 9 — Indústria, Inovação e Infraestrutura:** metas 9.4, 9.5 e 9.b (modernização industrial, capacidade tecnológica e inovação em países em desenvolvimento). Um AGV acessível só é viável para empresas menores se a interface de operação também dispensar equipamentos caros.
- **ODS 8 — Trabalho Decente e Crescimento Econômico:** meta 8.8 (ambientes de trabalho seguros; indicador 8.8.1, acidentes de trabalho). A teleoperação afasta o trabalhador de áreas de risco, e interfaces que reduzem colisões diminuem a exposição de quem divide o piso com o veículo. No Brasil, a movimentação de materiais e a operação de máquinas são regidas pelas NR-11 e NR-12.

## 7. Fundamentação — conceitos-chave

- **RA (Azuma, 1997):** combina real e virtual, é interativa em tempo real e é **registrada em 3D**.
- **Exibição por vídeo (video see-through, Milgram e Kishino, 1994):** o mundo é capturado por câmera e mostrado em tela com os elementos virtuais compostos sobre a imagem. É a categoria deste projeto.
- **Elemento fixo na tela (screen-fixed)** × **elemento registrado (world-fixed/conformal):** o HUD atual é do primeiro tipo; a contribuição pretendida são elementos do segundo tipo, posicionados a partir do mapa de profundidade alinhado à imagem RGB.
- **Consciência situacional (Endsley, 1995):** percepção (nível 1), compreensão (nível 2) e projeção (nível 3). O vídeo monocular limita principalmente os níveis 2 e 3, e é sobre eles que as sobreposições pretendem atuar.
- **Sensor Kinect v1 (Wasenmüller e Stricker, 2016):** o erro de profundidade cresce exponencialmente com a distância (cerca de 40 mm de desvio a 1,8 m), o que precisa ser considerado nos limiares de alerta.

## 8. Trabalhos relacionados (Quadro 1 resumido)

| Trabalho | Solução | Resultado | Limitação para este contexto |
|---|---|---|---|
| Fong e Thorpe (2001) | Classificação de interfaces de teleoperação de veículos | Limitações da operação direta por vídeo | Conceitual, sem experimento |
| Nielsen, Goodrich e Ricks (2007) | Interface ecológica (vídeo + mapa + modelo do robô) | Menos colisões, mais robustez à latência | Depende de mapa prévio; sem RA sobre o vídeo |
| Hedayati, Walker e Szafir (2018) | RA em HoloLens para teleoperação de robô aéreo | Melhor desempenho e avaliação subjetiva | Operador co-localizado; HMD caro |
| Walker et al. (2018) | RA para comunicar intenção de movimento | Mais eficiência na tarefa | Foco na intenção, não em obstáculos |
| De Oliveira Faria, Kandil e Gabbard (2019) | HUD conformal × HUD fixo (automotivo) | Desempenho semelhante | Direção veicular, não robótica |
| Chan et al. (2022) | RA em HoloLens para equipes humano-robô (n=26) | Menos demanda física e tempo; menor confiabilidade percebida | Co-localizado; manufatura |
| Thapa et al. (2017) | Desvio de obstáculos com RGB-D + ROS | Alta precisão, baixa latência em hardware barato | Sem RA nem teleoperação |
| Wasenmüller e Stricker (2016) | Acurácia Kinect v1 × v2 | v1 piora com a distância | Só caracterização do sensor |
| Hashimoto et al. (2013) — TouchMe | Controle tocando no vídeo da câmera | Interface intuitiva | Manipulador estático |
| Kästner e Lambrecht (2019) | RA (HoloLens) de mapa, laser e trajetória | Visualização adequada | Limite de desempenho; sem usuários |
| Sun et al. (2020) | Teleoperação em realidade mista | Ganho de manobrabilidade | Braço robótico |
| Adekoya, Sgorbissa e Recchiuto (2025) — preprint | HORUS: RA para equipes de robôs móveis | Carga (NASA-TLX) comparável | Preprint; múltiplos robôs |
| Kalinov, Trinitatova e Tsetserukou (2021) — WareVR | RV para supervisão de robôs em armazém | Interface validada com usuários leigos | Headset de RV |
| Schimpe et al. (2022) | Software aberto de teleoperação de veículos | Latência como requisito central | Sem RA |
| Inam et al. (2018) | HAZOP em armazém com robôs | Colisão é risco central | Não avalia a interface do operador |
| Bhattathiri et al. (2024) | Comunicação de intenção de AMR | Mais produtividade e confiança | Robô → pessoa, não operador remoto |
| Fragapane et al. (2021) | Revisão de AMRs na intralogística | Interação humano-robô na agenda de pesquisa | Sem interface de teleoperação |
| Makhataeva e Varol (2020); Suzuki et al. (2022) | Revisão e taxonomia de RA em robótica | Predominância de dispositivos vestíveis | Sem evidência empírica própria |

**Lacuna:** a maior parte da evidência foi obtida com óculos de RA, robôs aéreos, manipuladores ou equipes de robôs, com operadores co-localizados. Poucos trabalhos isolam a diferença entre **HUD fixo** e **sobreposição registrada** na teleoperação remota, por vídeo comum, de um único AGV terrestre de baixo custo.
