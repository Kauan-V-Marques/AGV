// Auto-aprendizado: indicadores, passos, ajustes, treino, modelos salvos e curva de treino.

import { postJson } from "./api.js";
import { uiState } from "./estado.js";
import { $, aviso, definirTexto } from "./util.js";

const ERROS = {
  banco_indisponivel: "O banco do aprendizado está indisponível.",
  treino_em_andamento: "Já existe um treino em andamento.",
  amostras_insuficientes: "Ainda não há exemplos suficientes do operador.",
  modelo_inexistente: "Esse modelo não existe mais.",
  modelo_incompativel: "Esse modelo foi treinado com outra versão da entrada; treine de novo.",
  valor_invalido: "Valor inválido.",
  sem_modelo: "Treine um modelo em Aprendizado antes de usar o modo IA.",
};
const FASES = { imitacao: "imitação", recompensa: "recompensa" };

let treinandoAntes = false;
let modeloEmUso = undefined; // id do modelo em uso na ultima atualizacao (undefined = ainda nao leu)
let curva = [];

export function mensagemErroIa(codigo) {
  return ERROS[codigo] || codigo || "erro desconhecido";
}

const pct = (valor) => (typeof valor === "number" ? `${Math.round(valor * 100)}%` : "--");

function dataHora(segundos) {
  return new Date(segundos * 1000).toLocaleString("pt-BR", {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

function definirPasso(numero, estado) {
  const passo = $(`ia-passo-${numero}`);
  if (passo && passo.dataset.estado !== estado) passo.dataset.estado = estado;
}

// ── Atualizacao a cada /api/status ──────────────────────────────────
export function atualizarAprendizado(ia) {
  if (!ia) return;
  const amostras = ia.amostras || {};
  const treino = ia.treino || {};
  const prefs = ia.preferencias || {};
  const modelo = ia.modelo;
  const humanas = (amostras.operador || 0) + (amostras.correcao || 0);
  const minimo = ia.minimo_amostras || 0;

  uiState.iaPronta = !!ia.tem_modelo;
  const botaoIa = $("btn-ia");
  botaoIa.classList.toggle("indisponivel", !ia.tem_modelo);
  botaoIa.title = ia.tem_modelo ? "Dirigir pelo modelo treinado" : "Treine um modelo em Aprendizado para liberar";
  $("menu-selo-ia").hidden = !treino.rodando;

  $("ia-alerta").hidden = !!ia.disponivel;
  if (!ia.disponivel) {
    definirTexto("ia-alerta-texto",
      `Banco do aprendizado indisponível (${ia.erro || "erro desconhecido"}). O arquivo foi preservado; o resto do AGV funciona normalmente.`);
  }

  // indicadores
  definirTexto("ia-total", String(amostras.total ?? 0), true);
  definirTexto("ia-total-detalhe", `${amostras.operador ?? 0} operador · ${amostras.correcao ?? 0} correções · ${amostras.ia ?? 0} da IA`);
  if (modelo) {
    const info = modelo.info || {};
    const validacao = info.validacao || {};
    definirTexto("ia-modelo", `#${modelo.id} · ${FASES[modelo.fase] || modelo.fase}`);
    definirTexto("ia-modelo-detalhe", `${dataHora(modelo.criado_em)} · ${info.treino ?? "--"} exemplos`);
    definirTexto("ia-acerto", pct(validacao.acerto_direcao));
    definirTexto("ia-acerto-detalhe", `movimento ${pct(validacao.acerto_movimento)} · erro médio ${validacao.erro_direcao ?? "--"} pts`);
  } else {
    definirTexto("ia-modelo", "nenhum");
    definirTexto("ia-modelo-detalhe", "treine para liberar o modo IA");
    definirTexto("ia-acerto", "--");
    definirTexto("ia-acerto-detalhe", "comparado ao operador");
  }

  const faltam = Math.max(0, minimo - humanas);
  let progresso = 0;
  if (treino.rodando) {
    progresso = treino.progresso || 0;
    definirTexto("ia-treino", "treinando");
    definirTexto("ia-treino-detalhe", `${Math.round(progresso * 100)}% das épocas (pode parar antes)`);
  } else if (treino.erro) {
    definirTexto("ia-treino", "falhou");
    definirTexto("ia-treino-detalhe", mensagemErroIa(treino.erro));
  } else if (faltam > 0) {
    progresso = minimo ? humanas / minimo : 0;
    definirTexto("ia-treino", `faltam ${faltam}`);
    definirTexto("ia-treino-detalhe", `exemplos do operador para o 1º treino (${humanas} de ${minimo})`);
  } else {
    progresso = 1;
    definirTexto("ia-treino", "pronto");
    definirTexto("ia-treino-detalhe", ia.treino_automatico_a_cada > 0
      ? `automático a cada ${ia.treino_automatico_a_cada} novos · ${ia.novas ?? 0} desde o último`
      : "treino automático desligado");
  }
  $("ia-progresso").style.width = `${Math.round(Math.min(1, progresso) * 100)}%`;
  $("ia-progresso").classList.toggle("ativo", !!treino.rodando);

  const botaoTreinar = $("btn-ia-treinar");
  botaoTreinar.disabled = !!treino.rodando || !ia.disponivel;
  botaoTreinar.classList.toggle("ocupado", !!treino.rodando);
  $("btn-ia-apagar").disabled = !!treino.rodando || !ia.disponivel;

  // passos
  const usouIa = (amostras.ia || 0) > 0;
  definirPasso(1, humanas >= minimo ? "feito" : "atual");
  definirPasso(2, ia.tem_modelo ? "feito" : (humanas >= minimo ? "atual" : "pendente"));
  definirPasso(3, usouIa ? "feito" : (ia.tem_modelo ? "atual" : "pendente"));
  definirPasso(4, modelo && modelo.fase === "recompensa" ? "feito" : (usouIa ? "atual" : "pendente"));
  definirTexto("ia-passo-1-info", humanas >= minimo
    ? `${humanas} exemplos do operador gravados`
    : `${humanas} de ${minimo} exemplos para o primeiro treino`);

  // ajustes (nao sobrescreve o controle que o usuario esta mexendo)
  sincronizarAjuste("ia-gravando", (el) => { el.checked = !!prefs.gravando; });
  sincronizarAjuste("ia-explorar", (el) => { el.checked = !!prefs.explorar; });
  if (typeof prefs.velocidade_max === "number") {
    sincronizarAjuste("ia-vel-max", (el) => mostrarVelocidade(el, prefs.velocidade_max));
  }

  // fim de um treino e troca de modelo
  if (treinandoAntes && !treino.rodando) {
    if (treino.erro) aviso(`O treino não terminou: ${mensagemErroIa(treino.erro)}`, "erro", 6000);
    else if (modelo) aviso(`Treino concluído: modelo #${modelo.id}, acerto de direção ${pct((modelo.info?.validacao || {}).acerto_direcao)}.`, "ok", 6000);
  }
  treinandoAntes = !!treino.rodando;
  const idAtual = modelo ? modelo.id : null;
  if (idAtual !== modeloEmUso) {
    modeloEmUso = idAtual;
    carregarModelos();
  }
}

let _editando = null;
function sincronizarAjuste(id, aplicar) {
  const el = $(id);
  if (el && el !== document.activeElement && el !== _editando) aplicar(el);
}

function mostrarVelocidade(slider, valor) {
  slider.value = valor;
  slider.style.setProperty("--preenchido", `${valor}%`);
  $("ia-vel-max-valor").textContent = `${valor}%`;
}

// ── Modelos salvos e curva de treino ────────────────────────────────
async function carregarModelos() {
  try {
    const resposta = await fetch("/api/ia/modelos", { cache: "no-store" });
    const dados = await resposta.json();
    mostrarModelos(dados.modelos || []);
  } catch (erro) {
    console.warn("Falha ao ler os modelos", erro);
  }
}

function celula(texto, classe) {
  const td = document.createElement("td");
  td.textContent = texto;
  if (classe) td.className = classe;
  return td;
}

function mostrarModelos(modelos) {
  const corpo = $("ia-modelos-corpo");
  corpo.replaceChildren();
  if (!modelos.length) {
    const linha = document.createElement("tr");
    const vazio = celula("Nenhum modelo treinado ainda.", "vazio");
    vazio.colSpan = 6;
    linha.appendChild(vazio);
    corpo.appendChild(linha);
  }
  modelos.forEach((modelo) => {
    const info = modelo.info || {};
    const linha = document.createElement("tr");
    if (modelo.ativo) linha.className = "em-uso";
    linha.appendChild(celula(`#${modelo.id}`));
    linha.appendChild(celula(dataHora(modelo.criado_em)));
    const fase = document.createElement("td");
    const etiqueta = document.createElement("span");
    etiqueta.className = "etiqueta";
    etiqueta.dataset.fase = modelo.fase;
    etiqueta.textContent = FASES[modelo.fase] || modelo.fase;
    fase.appendChild(etiqueta);
    linha.appendChild(fase);
    linha.appendChild(celula(String(info.treino ?? "--")));
    linha.appendChild(celula(pct((info.validacao || {}).acerto_direcao)));
    const acao = document.createElement("td");
    if (modelo.ativo) {
      const uso = document.createElement("span");
      uso.className = "em-uso-selo";
      uso.textContent = "em uso";
      acao.appendChild(uso);
    } else {
      const botao = document.createElement("button");
      botao.type = "button";
      botao.className = "botao botao-pequeno";
      botao.textContent = "Usar";
      botao.addEventListener("click", () => ativarModelo(modelo.id));
      acao.appendChild(botao);
    }
    linha.appendChild(acao);
    corpo.appendChild(linha);
  });

  const ativo = modelos.find((modelo) => modelo.ativo);
  curva = (ativo && ativo.info && ativo.info.historico) || [];
  definirTexto("ia-curva-sub", ativo ? `modelo #${ativo.id} · ${curva.length} épocas` : "sem modelo");
  desenharCurva();
}

function cor(variavel) {
  return getComputedStyle(document.documentElement).getPropertyValue(variavel).trim();
}

export function desenharCurva() {
  const canvas = $("grafico-ia");
  if (!canvas || !canvas.offsetParent) return; // secao escondida
  const dpr = window.devicePixelRatio || 1;
  const largura = canvas.clientWidth;
  const altura = canvas.clientHeight;
  if (canvas.width !== Math.round(largura * dpr) || canvas.height !== Math.round(altura * dpr)) {
    canvas.width = Math.round(largura * dpr);
    canvas.height = Math.round(altura * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, largura, altura);
  ctx.font = "10px system-ui, sans-serif";
  ctx.fillStyle = cor("--texto-suave");

  if (!curva.length) {
    ctx.textAlign = "center";
    ctx.fillText("treine um modelo para ver a curva", largura / 2, altura / 2);
    ctx.textAlign = "start";
    return;
  }

  const esquerda = 40;
  const topo = 8;
  const base = altura - 18;
  const maximo = Math.max(...curva.map((p) => Math.max(p.treino, p.validacao)), 1e-6);
  const epocas = curva.length;
  const x = (i) => esquerda + (epocas > 1 ? i / (epocas - 1) : 0.5) * (largura - esquerda - 6);
  const y = (v) => base - (v / maximo) * (base - topo);

  ctx.strokeStyle = cor("--grade-grafico");
  ctx.lineWidth = 1;
  for (let i = 0; i <= 3; i += 1) {
    const valor = (maximo * i) / 3;
    ctx.beginPath();
    ctx.moveTo(esquerda, y(valor));
    ctx.lineTo(largura, y(valor));
    ctx.stroke();
    ctx.fillText(valor.toFixed(3), 2, y(valor) + 3);
  }
  ctx.fillText("época 1", esquerda, altura - 4);
  ctx.textAlign = "end";
  ctx.fillText(`época ${epocas}`, largura - 4, altura - 4);
  ctx.textAlign = "start";

  [["treino", cor("--azul-claro"), 1.6], ["validacao", "#c084fc", 2.2]].forEach(([chave, traco, espessura]) => {
    ctx.beginPath();
    curva.forEach((ponto, i) => (i ? ctx.lineTo(x(i), y(ponto[chave])) : ctx.moveTo(x(i), y(ponto[chave]))));
    ctx.strokeStyle = traco;
    ctx.lineWidth = espessura;
    ctx.lineJoin = "round";
    ctx.stroke();
  });
}

// ── Acoes ───────────────────────────────────────────────────────────
async function configurar(parcial) {
  try {
    const resposta = await postJson("/api/ia/config", parcial);
    if (!resposta.ok) aviso(mensagemErroIa(resposta.error), "erro");
  } catch (erro) {
    aviso("Falha ao salvar o ajuste: sem resposta do AGV.", "erro");
  }
}

async function treinar() {
  try {
    const resposta = await postJson("/api/ia/treinar", {});
    if (resposta.ok) {
      treinandoAntes = true;
      $("btn-ia-treinar").classList.add("ocupado");
      $("btn-ia-treinar").disabled = true;
      aviso("Treino iniciado em segundo plano. Pode continuar usando o painel.", "ok");
    } else {
      aviso(mensagemErroIa(resposta.error), "erro");
    }
  } catch (erro) {
    aviso("Falha ao iniciar o treino: sem resposta do AGV.", "erro");
  }
}

async function ativarModelo(id) {
  try {
    const resposta = await postJson(`/api/ia/modelos/${id}/ativar`, {});
    if (resposta.ok) aviso(`Modelo #${id} em uso.`, "ok");
    else aviso(mensagemErroIa(resposta.error), "erro");
  } catch (erro) {
    aviso("Falha ao trocar o modelo: sem resposta do AGV.", "erro");
  }
  carregarModelos();
}

async function apagar(alvo) {
  try {
    const resposta = await postJson("/api/ia/apagar", { alvo, confirmar: true });
    if (resposta.ok) aviso(alvo === "tudo" ? "Exemplos e modelos apagados." : "Exemplos apagados; o modelo em uso foi mantido.", "ok");
    else aviso(mensagemErroIa(resposta.error), "erro");
  } catch (erro) {
    aviso("Falha ao apagar: sem resposta do AGV.", "erro");
  }
  carregarModelos();
}

export function iniciarAprendizado() {
  $("ia-gravando").addEventListener("change", (evento) => configurar({ gravando: evento.target.checked }));
  $("ia-explorar").addEventListener("change", (evento) => configurar({ explorar: evento.target.checked }));

  const slider = $("ia-vel-max");
  let envio = null;
  slider.addEventListener("input", () => {
    _editando = slider;
    mostrarVelocidade(slider, Number(slider.value));
    clearTimeout(envio);
    envio = setTimeout(async () => {
      await configurar({ velocidade_max: Number(slider.value) });
      _editando = null;
    }, 300);
  });

  $("btn-ia-treinar").addEventListener("click", treinar);

  const dialogo = $("dialogo-apagar");
  $("btn-ia-apagar").addEventListener("click", () => dialogo.showModal());
  $("dialogo-apagar-cancelar").addEventListener("click", () => dialogo.close());
  dialogo.querySelectorAll("[data-alvo]").forEach((botao) => {
    botao.addEventListener("click", () => {
      dialogo.close();
      apagar(botao.dataset.alvo);
    });
  });
  dialogo.addEventListener("click", (evento) => {
    if (evento.target === dialogo) dialogo.close(); // clique fora da caixa
  });
}
