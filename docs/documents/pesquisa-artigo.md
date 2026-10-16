# Síntese da pesquisa — artigo científico do projeto

**Título:** Uso de Realidade Aumentada na teleoperação de um AGV: apoio à navegação remota em intralogística
**Title:** Use of Augmented Reality in the teleoperation of an AGV: support for remote navigation in intralogistics

**Autores:** Kauan Victor Marques, Pedro Henrique Santana de Oliveira, Gabriel Pelincel Ramalho, Jorge Henrique Gobbo Berenschot, Felipe Matias de Jesus e Daniel Ohata
**Orientação:** Prof. Dr. Daniel Ohata — Centro Universitário Facens, Sorocaba/SP
**Versão:** 2, submetida em 14/10/2026 (a versão 2 incorpora o feedback do professor sobre o desenho experimental e a avaliação técnica das sobreposições)

Este documento resume o desenho da pesquisa que orienta o desenvolvimento do AGV. O README traz apenas a síntese; aqui estão as questões, hipóteses, variáveis, o método e os trabalhos relacionados.

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
| Complementares | Número de intervenções e taxa de sucesso; observações e entrevista |
| De controle | Percurso e obstáculos, AGV e velocidade máxima, rede, latência e taxa de quadros, iluminação, dispositivo de comando e treino |

## 4. Classificação da pesquisa

- **Natureza:** aplicada, pois relaciona um artefato a um problema concreto e busca evidência sobre seu efeito.
- **Objetivos:** predominantemente explicativa, com etapa exploratória inicial de requisitos e prova de conceito.
- **Abordagem:** mista, combinando medidas objetivas e escalas validadas com observação e entrevista.
- **Procedimento central:** experimento controlado com usuários, precedido de avaliação técnica do artefato e de um estudo piloto com dois participantes externos à amostra. O projeto segue ciclos semanais Scrumban.

## 5. Objetivos específicos

1. Comparar trabalhos que empregam RA e interfaces espaciais na teleoperação de robôs móveis.
2. Definir os requisitos das sobreposições e o método de registro a partir do mapa de profundidade.
3. Desenvolver o protótipo integrado ao servidor de teleoperação existente.
4. Verificar tecnicamente a precisão e a estabilidade do registro.
5. Conduzir o experimento com operadores segundo o protocolo da seção 7.
6. Analisar os resultados à luz das hipóteses.

## 6. Artefato e condições comparadas

O artefato é o AGV deste repositório: chassi controlado por Arduino, Kinect v1 (RGB 640×480 e profundidade por luz estruturada, alcance útil de cerca de 0,8 m a 4 m) e o servidor Python/Flask, que já oferece modo manual, modo autônomo, watchdog, monitoramento de latência e o HUD fixo com as distâncias. Sobre essa base serão desenvolvidas três sobreposições registradas, com a profundidade reprojetada para o referencial da câmera RGB:

- **destaque das regiões de obstáculo;**
- **distância ancorada a cada obstáculo;**
- **zona de segurança projetada no piso à frente do veículo.**

Quando a região frontal não tiver medição válida (abaixo do alcance mínimo do sensor), a interface mostra um aviso de **zona não medida** em vez de omitir a informação.

| Condição | O que o operador vê |
|---|---|
| **C1 — referência** | Vídeo RGB com o HUD de telemetria fixo |
| **C2 — RA** | O mesmo vídeo e HUD, acrescidos das sobreposições registradas |

As duas condições executam o mesmo processamento no servidor e diferem apenas na exibição, para que a taxa de quadros e a latência não se tornem fatores de confusão.

## 7. Desenho experimental

| Aspecto | Definição |
|---|---|
| **Desenho** | Intrassujeitos (medidas repetidas): cada participante opera nas duas condições |
| **Percursos** | Dois percursos isomórficos, A e B (B é o espelhamento de A), com o mesmo comprimento e o mesmo número de curvas, passagens estreitas e obstáculos, mais um terceiro percurso só para treino |
| **Contrabalanceamento** | Quatro sequências distribuídas igualmente: C1-A/C2-B, C1-B/C2-A, C2-A/C1-B e C2-B/C1-A |
| **Treino** | Demonstração das duas interfaces e treino no terceiro percurso até completá-lo sem colisões ou por no máximo 5 minutos; pausa de 5 minutos entre as condições, usada para os questionários |
| **Amostra** | 20 participantes não especialistas, maiores de 18 anos, estudantes e colaboradores da Facens, sem experiência prévia com o AGV. Com dz = 0,7, α = 0,05 e poder de 0,80, o teste t pareado requer 19; com 20 (múltiplo das quatro sequências), o poder fica em cerca de 0,84 |
| **Tarefa** | Conduzir o AGV do ponto de partida à linha de chegada num corredor que representa um armazém, com caixas e palete como obstáculos e um obstáculo de exceção em posição não informada. O operador fica em outra sala, sem ver o veículo, e usa o teclado do mesmo notebook, com a mesma velocidade máxima nas duas condições |
| **Colisão** | Qualquer contato físico do AGV com um obstáculo ou limite do corredor, confirmado pela câmera externa de vista superior; contatos separados por mais de 1 s contam separadamente |
| **Intervenção** | Parada de emergência por risco iminente ou reposicionamento manual do veículo; paradas por falha técnica (perda de vídeo, watchdog, queda de comunicação) são registradas à parte |
| **Conclusão** | O AGV cruza a linha de chegada em até 5 minutos sem reposicionamento manual; o tempo vai do primeiro comando à chegada, pelos registros do servidor |
| **Instrumentos** | NASA-TLX sem ponderação e SUS após cada condição; entrevista semiestruturada breve ao final |
| **Análise** | Teste t pareado (ou Wilcoxon pareado, se as diferenças não forem normais pelo Shapiro-Wilk); Wilcoxon para colisões e intervenções; McNemar para a taxa de sucesso; α = 0,05, com tamanhos de efeito |
| **Ética** | Protocolo submetido ao Comitê de Ética em Pesquisa antes da coleta (Resolução CNS nº 510/2016), com termo de consentimento livre e esclarecido |

## 8. Avaliação técnica das sobreposições

Como a validade da comparação depende de as sobreposições estarem bem posicionadas e estáveis, o protótipo é avaliado antes do estudo com usuários, com o AGV parado e em movimento lento, sob a iluminação do experimento. Caixas com marcadores de alto contraste ficam a 1,0; 1,5; 2,0; 2,5; 3,0 e 3,5 m (medidos com trena), em três posições laterais.

| Medida | Critério de aceitação |
|---|---|
| Erro de registro (pixels entre o centro da sobreposição e o do obstáculo) e IoU entre as regiões | Erro médio de até 10 px (cerca de 1,5% da largura da imagem) e IoU média mínima de 0,7 |
| Erro de distância (exibida × trena) | No máximo 5% para obstáculos até 2,5 m |
| Estabilidade (desvio-padrão em 300 quadros com o AGV parado) | Até 3 px |
| Persistência (quadros em que o obstáculo presente é destacado) | Mínimo de 95% |
| Custo de processamento (taxa de quadros de C2 em relação a C1) | Queda inferior a 10% |

Se algum critério não for atendido, aplicam-se ajustes como filtragem temporal e histerese de detecção, e a avaliação é repetida. Obstáculos escuros ou reflexivos, que prejudicam a luz estruturada, são testados à parte para documentar os casos de falha.

## 9. Relação com os ODS (Agenda 2030)

- **ODS 9 — Indústria, Inovação e Infraestrutura:** metas 9.4, 9.5 e 9.b (modernização industrial, capacidade tecnológica e inovação em países em desenvolvimento). Um AGV acessível só é viável para empresas menores se a interface de operação também dispensar equipamentos caros.
- **ODS 8 — Trabalho Decente e Crescimento Econômico:** meta 8.8 (ambientes de trabalho seguros; indicador 8.8.1, acidentes de trabalho). A teleoperação afasta o trabalhador de áreas de risco, e interfaces que reduzem colisões diminuem a exposição de quem divide o piso com o veículo. No Brasil, a movimentação de materiais e a operação de máquinas são regidas pelas NR-11 e NR-12.

## 10. Fundamentação — conceitos-chave

- **RA (Azuma, 1997):** combina real e virtual, é interativa em tempo real e é **registrada em 3D**.
- **Exibição por vídeo (video see-through, Milgram e Kishino, 1994):** o mundo é capturado por câmera e mostrado em tela com os elementos virtuais compostos sobre a imagem. É a categoria deste projeto.
- **Elemento fixo na tela (screen-fixed)** × **elemento registrado (world-fixed/conformal):** o HUD atual é do primeiro tipo; a contribuição pretendida são elementos do segundo tipo, posicionados a partir do mapa de profundidade alinhado à imagem RGB. O ganho não é automático (De Oliveira Faria, Kandil e Gabbard, 2019), por isso a avaliação técnica da seção 8 vem antes do experimento.
- **Consciência situacional (Endsley, 1995):** percepção (nível 1), compreensão (nível 2) e projeção (nível 3). O vídeo monocular limita principalmente os níveis 2 e 3, e é sobre eles que as sobreposições pretendem atuar.
- **Sensor Kinect v1 (Wasenmüller e Stricker, 2017):** o erro de profundidade cresce exponencialmente com a distância (cerca de 40 mm a 1,8 m), e a distância mínima de medição fica próxima de 0,8 m; isso orienta os limiares de alerta, os critérios da avaliação técnica e as limitações do estudo.
- **Latência (Schimpe et al., 2022):** monitorar a latência é requisito tão central quanto o próprio vídeo, por isso ela é registrada como variável de controle.

## 11. Trabalhos relacionados (Quadro 1 resumido)

| Trabalho | Solução | Resultado | Limitação para este contexto |
|---|---|---|---|
| Fong e Thorpe (2001) | Classificação de interfaces de teleoperação de veículos | Limitações da operação direta por vídeo | Conceitual, sem experimento |
| Nielsen, Goodrich e Ricks (2007) | Interface ecológica (vídeo + mapa + modelo do robô) | Menos colisões, mais robustez à latência | Depende de mapa prévio; sem RA sobre o vídeo |
| Hedayati, Walker e Szafir (2018) | RA em HoloLens para teleoperação de robô aéreo | Melhor desempenho e avaliação subjetiva | Operador co-localizado; HMD caro |
| Walker et al. (2018) | RA para comunicar intenção de movimento | Mais eficiência na tarefa | Foco na intenção, não em obstáculos |
| De Oliveira Faria, Kandil e Gabbard (2019) | HUD conformal × HUD fixo (automotivo) | Desempenho semelhante | Direção veicular, não robótica |
| Chan et al. (2022) | RA em HoloLens para equipes humano-robô (n=26) | Menos demanda física e tempo; menor confiabilidade percebida | Co-localizado; manufatura |
| Thapa et al. (2017) | Desvio de obstáculos com RGB-D + ROS | Alta precisão, baixa latência em hardware barato | Sem RA nem teleoperação |
| Wasenmüller e Stricker (2017) | Acurácia Kinect v1 × v2 | v1 piora com a distância | Só caracterização do sensor |
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

## 12. Próximas versões

A versão 2 não apresenta resultados. As próximas versões vão trazer o protótipo das sobreposições, os resultados da avaliação técnica (seção 8) e do experimento (seção 7) e a discussão diante de H1 e H2.
