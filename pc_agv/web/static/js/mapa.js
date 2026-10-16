// Auto-mapeamento: indicadores, canvas do mapa (zoom, arrasto, seguir o AGV), controles e mapas salvos.

import { postJson } from "./api.js";
import { $, aviso, definirTexto } from "./util.js";

const ERROS = {
  banco_indisponivel: "O banco de mapas está indisponível.",
  mapa_inexistente: "Esse mapa não existe mais.",
  mapa_somente_leitura: "Mapa salvo aberto só para consulta. Para mapear, comece um mapa novo.",
  mapa_vazio: "O mapa ainda está vazio: ligue o mapeamento e dirija o AGV.",
  mapa_ilegivel: "O arquivo desse mapa está ilegível.",
  nome_obrigatorio: "Dê um nome ao mapa.",
  valor_invalido: "Valor inválido.",
};
const CAMPO_DE_VISAO_RAD = (57 * Math.PI) / 180; // largura da imagem de profundidade do Kinect v1
const ESCALA_MIN = 8;    // px por metro
const ESCALA_MAX = 400;

let status = null;
let imagem = null;          // HTMLImageElement do recorte observado
let versaoImagem = -1;
let carregandoImagem = false;
let escala = 60;            // px por metro
let centro = { x: 0, y: 0 }; // ponto do mundo no meio do canvas
let seguir = true;
let enquadrado = false;
let ultimaLista = "";

const mensagem = (codigo) => ERROS[codigo] || codigo || "erro desconhecido";
const fmt = (valor, casas = 2) => (typeof valor === "number" ? valor.toFixed(casas).replace(".", ",") : "--");

function cor(variavel) {
  return getComputedStyle(document.documentElement).getPropertyValue(variavel).trim();
}

function secaoVisivel() {
  const secao = $("secao-mapa");
  return secao && !secao.hidden;
}

// ── Resumo a cada /api/status (menu) ────────────────────────────────
export function atualizarResumoMapa(resumo) {
  if (!resumo) return;
  $("menu-selo-mapa").hidden = !resumo.rodando;
}

// ── Status detalhado (so com a pagina aberta) ───────────────────────
async function lerStatus() {
  if (!secaoVisivel()) return;
  try {
    const resposta = await fetch("/api/mapa/status", { cache: "no-store" });
    const dados = await resposta.json();
    aplicarStatus(dados.mapa);
  } catch (erro) {
    console.warn("Falha ao ler o mapa", erro);
  }
}

function aplicarStatus(novo) {
  if (!novo) return;
  status = novo;

  $("mapa-alerta").hidden = !!novo.disponivel;
  if (!novo.disponivel) {
    definirTexto("mapa-alerta-texto",
      `Banco de mapas indisponível (${novo.erro || "erro desconhecido"}). O mapeamento funciona, mas não dá para salvar; o arquivo foi preservado.`);
  }

  definirTexto("mapa-area", `${fmt(novo.area_m2, 1)} m²`, true);
  definirTexto("mapa-area-detalhe", novo.recorte
    ? `recorte de ${fmt(novo.recorte.largura_m, 1)} × ${fmt(novo.recorte.altura_m, 1)} m`
    : "metros quadrados observados");
  definirTexto("mapa-distancia", `${fmt(novo.distancia_m, 2)} m`, true);
  const pose = novo.pose || {};
  definirTexto("mapa-pose", `${fmt(pose.x)} · ${fmt(pose.y)}`);
  definirTexto("mapa-pose-detalhe", `direção ${fmt(pose.theta_graus, 0)}° · x · y em metros, a partir do início`);

  let odometria = "--";
  let detalhe = "casamento das varreduras";
  if (novo.somente_leitura) {
    odometria = "consulta";
    detalhe = "mapa salvo aberto, sem mapear";
  } else if (!novo.rodando) {
    odometria = "pausada";
  } else if (novo.prevendo) {
    odometria = "prevendo";
    detalhe = "vista tapada: seguindo o último movimento";
  } else if (novo.casamento) {
    odometria = "ok";
    detalhe = `erro ${fmt(novo.casamento.erro_cm, 1)} cm · ${Math.round(novo.casamento.casados * 100)}% casados`;
  } else {
    odometria = "aguardando";
    detalhe = "sem leitura do Kinect";
  }
  definirTexto("mapa-odometria", odometria);
  definirTexto("mapa-odometria-detalhe", `${detalhe} · ${novo.falhas || 0} falhas em ${novo.leituras || 0}`);

  const selo = $("mapa-selo");
  const estado = novo.somente_leitura ? "consulta" : (novo.rodando ? "mapeando" : "pausado");
  if (selo.dataset.estado !== estado) {
    selo.dataset.estado = estado;
    selo.textContent = { consulta: "CONSULTA", mapeando: "MAPEANDO", pausado: "PAUSADO" }[estado];
  }
  definirTexto("mapa-nome", novo.nome
    ? `${novo.nome}${novo.salvo ? "" : " · alterações não salvas"}`
    : (novo.recorte ? "mapa novo, não salvo" : "mapa novo"));
  if (novo.nome && document.activeElement !== $("mapa-nome-input") && !$("mapa-nome-input").value) {
    $("mapa-nome-input").value = novo.nome;
  }

  const ligar = $("btn-mapa-ligar");
  ligar.querySelector("span").textContent = novo.rodando ? "Pausar" : "Mapear";
  ligar.querySelector("use").setAttribute("href", novo.rodando ? "#i-pausa" : "#i-play");
  ligar.classList.toggle("botao-primario", !novo.rodando);
  ligar.disabled = !!novo.somente_leitura;
  $("btn-mapa-salvar").disabled = !novo.disponivel;
  const avanco = $("mapa-avanco");
  if (document.activeElement !== avanco && typeof novo.avanco_cm === "number") avanco.value = novo.avanco_cm;

  $("mapa-vazio").hidden = !!novo.recorte;
  if (novo.versao !== versaoImagem) carregarImagem(novo.versao);
  if (novo.recorte && !enquadrado) enquadrar();
  desenharMapa();
}

function carregarImagem(versao) {
  if (carregandoImagem) return;
  if (!status || !status.recorte) {
    imagem = null;
    versaoImagem = versao;
    return;
  }
  carregandoImagem = true;
  const nova = new Image();
  nova.onload = () => {
    imagem = { elemento: nova, recorte: status.recorte };
    versaoImagem = versao;
    carregandoImagem = false;
    desenharMapa();
  };
  nova.onerror = () => { carregandoImagem = false; };
  nova.src = `/api/mapa/imagem.png?v=${versao}`;
}

// ── Desenho ─────────────────────────────────────────────────────────
function paraTela(x, y, largura, altura) {
  return [largura / 2 + (x - centro.x) * escala, altura / 2 - (y - centro.y) * escala];
}

export function desenharMapa() {
  const canvas = $("mapa-canvas");
  if (!canvas || !canvas.offsetParent) return;
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

  const pose = status && status.pose;
  if (seguir && pose) centro = { x: pose.x, y: pose.y };

  // grade de 1 m
  ctx.strokeStyle = cor("--grade-grafico");
  ctx.lineWidth = 1;
  const passo = escala >= 25 ? 1 : 5;
  const x0 = Math.floor((centro.x - largura / 2 / escala) / passo) * passo;
  const x1 = centro.x + largura / 2 / escala;
  const y0 = Math.floor((centro.y - altura / 2 / escala) / passo) * passo;
  const y1 = centro.y + altura / 2 / escala;
  ctx.beginPath();
  for (let x = x0; x <= x1; x += passo) {
    const [px] = paraTela(x, 0, largura, altura);
    ctx.moveTo(Math.round(px) + 0.5, 0);
    ctx.lineTo(Math.round(px) + 0.5, altura);
  }
  for (let y = y0; y <= y1; y += passo) {
    const [, py] = paraTela(0, y, largura, altura);
    ctx.moveTo(0, Math.round(py) + 0.5);
    ctx.lineTo(largura, Math.round(py) + 0.5);
  }
  ctx.stroke();

  // mapa observado
  if (imagem) {
    const r = imagem.recorte;
    const [px, py] = paraTela(r.x_min, r.y_max, largura, altura);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(imagem.elemento, px, py, r.largura_m * escala, r.altura_m * escala);
  }
  if (!status) return;

  // inicio do mapa
  const [ox, oy] = paraTela(0, 0, largura, altura);
  ctx.strokeStyle = cor("--texto-suave");
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(ox - 6, oy); ctx.lineTo(ox + 6, oy);
  ctx.moveTo(ox, oy - 6); ctx.lineTo(ox, oy + 6);
  ctx.stroke();

  // trajeto
  const trajeto = status.trajetoria || [];
  if (trajeto.length > 1) {
    ctx.beginPath();
    trajeto.forEach(([x, y], i) => {
      const [px, py] = paraTela(x, y, largura, altura);
      if (i) ctx.lineTo(px, py); else ctx.moveTo(px, py);
    });
    ctx.strokeStyle = "#c084fc";
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    ctx.stroke();
  }

  // varredura atual
  ctx.fillStyle = cor("--atencao");
  (status.varredura || []).forEach(([x, y]) => {
    const [px, py] = paraTela(x, y, largura, altura);
    ctx.fillRect(px - 1.5, py - 1.5, 3, 3);
  });

  // AGV: campo de visao do Kinect e seta na direcao
  if (pose) {
    const [px, py] = paraTela(pose.x, pose.y, largura, altura);
    const theta = (pose.theta_graus * Math.PI) / 180;
    const alcance = Math.min(4.5 * escala, 220);
    ctx.fillStyle = "rgba(96, 165, 250, 0.12)";
    ctx.beginPath();
    ctx.moveTo(px, py);
    ctx.arc(px, py, alcance, -theta - CAMPO_DE_VISAO_RAD / 2, -theta + CAMPO_DE_VISAO_RAD / 2);
    ctx.closePath();
    ctx.fill();

    const tamanho = Math.max(9, Math.min(18, escala * 0.25));
    ctx.save();
    ctx.translate(px, py);
    ctx.rotate(-theta);
    ctx.beginPath();
    ctx.moveTo(tamanho, 0);
    ctx.lineTo(-tamanho * 0.7, tamanho * 0.65);
    ctx.lineTo(-tamanho * 0.35, 0);
    ctx.lineTo(-tamanho * 0.7, -tamanho * 0.65);
    ctx.closePath();
    ctx.fillStyle = status.prevendo ? cor("--alerta") : cor("--azul");
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 1.5;
    ctx.fill();
    ctx.stroke();
    ctx.restore();
  }

  atualizarEscala();
}

function atualizarEscala() {
  // barra de escala com um comprimento "redondo" entre 40 e 120 px
  const opcoes = [0.1, 0.2, 0.5, 1, 2, 5, 10];
  const metros = opcoes.find((m) => m * escala >= 40) || 10;
  $("mapa-escala-barra").style.width = `${Math.round(metros * escala)}px`;
  definirTexto("mapa-escala-texto", metros >= 1 ? `${metros} m` : `${Math.round(metros * 100)} cm`);
}

// ── Navegacao no mapa ───────────────────────────────────────────────
function definirSeguir(valor) {
  seguir = valor;
  const botao = $("mapa-seguir");
  botao.setAttribute("aria-pressed", valor ? "true" : "false");
  botao.classList.toggle("ativo", valor);
}

function enquadrar() {
  const canvas = $("mapa-canvas");
  if (!status || !status.recorte || !canvas.clientWidth) return;
  const r = status.recorte;
  escala = Math.max(ESCALA_MIN, Math.min(ESCALA_MAX,
    Math.min(canvas.clientWidth / r.largura_m, canvas.clientHeight / r.altura_m) * 0.9));
  centro = { x: r.x_min + r.largura_m / 2, y: r.y_max - r.altura_m / 2 };
  enquadrado = true;
  definirSeguir(false);
  desenharMapa();
}

function zoom(fator, alvoTela) {
  const canvas = $("mapa-canvas");
  const nova = Math.max(ESCALA_MIN, Math.min(ESCALA_MAX, escala * fator));
  if (alvoTela && !seguir) {
    // mantem o ponto sob o cursor no mesmo lugar
    const [mx, my] = alvoTela;
    const mundoX = centro.x + (mx - canvas.clientWidth / 2) / escala;
    const mundoY = centro.y - (my - canvas.clientHeight / 2) / escala;
    centro = {
      x: mundoX - (mx - canvas.clientWidth / 2) / nova,
      y: mundoY + (my - canvas.clientHeight / 2) / nova,
    };
  }
  escala = nova;
  desenharMapa();
}

function iniciarInteracao() {
  const canvas = $("mapa-canvas");
  let arrasto = null;
  canvas.addEventListener("pointerdown", (evento) => {
    arrasto = { x: evento.clientX, y: evento.clientY, centro: { ...centro } };
    canvas.setPointerCapture(evento.pointerId);
    canvas.classList.add("arrastando");
  });
  canvas.addEventListener("pointermove", (evento) => {
    if (!arrasto) return;
    const dx = evento.clientX - arrasto.x;
    const dy = evento.clientY - arrasto.y;
    if (seguir && Math.hypot(dx, dy) > 4) definirSeguir(false);
    if (!seguir) {
      centro = { x: arrasto.centro.x - dx / escala, y: arrasto.centro.y + dy / escala };
      desenharMapa();
    }
  });
  const soltar = () => {
    arrasto = null;
    canvas.classList.remove("arrastando");
  };
  canvas.addEventListener("pointerup", soltar);
  canvas.addEventListener("pointercancel", soltar);
  canvas.addEventListener("wheel", (evento) => {
    evento.preventDefault();
    const caixa = canvas.getBoundingClientRect();
    zoom(evento.deltaY < 0 ? 1.15 : 1 / 1.15, [evento.clientX - caixa.left, evento.clientY - caixa.top]);
  }, { passive: false });
  canvas.addEventListener("dblclick", enquadrar);

  $("mapa-zoom-mais").addEventListener("click", () => zoom(1.3));
  $("mapa-zoom-menos").addEventListener("click", () => zoom(1 / 1.3));
  $("mapa-seguir").addEventListener("click", () => {
    definirSeguir(!seguir);
    desenharMapa();
  });
  $("mapa-enquadrar").addEventListener("click", enquadrar);
}

// ── Comandos e mapas salvos ─────────────────────────────────────────
async function comando(url, corpo, sucesso) {
  try {
    const resposta = await postJson(url, corpo || {});
    if (!resposta.ok) {
      aviso(mensagem(resposta.error), "erro", 5000);
      return null;
    }
    if (resposta.mapa) aplicarStatus(resposta.mapa);
    if (sucesso) aviso(sucesso, "ok");
    return resposta;
  } catch (erro) {
    aviso("Sem resposta do AGV.", "erro");
    return null;
  }
}

async function carregarLista() {
  try {
    const resposta = await fetch("/api/mapa/lista", { cache: "no-store" });
    const dados = await resposta.json();
    mostrarLista(dados.mapas || []);
  } catch (erro) {
    console.warn("Falha ao listar os mapas", erro);
  }
}

function mostrarLista(mapas) {
  const chave = JSON.stringify(mapas.map((m) => [m.id, m.nome, m.atualizado_em])) + (status ? status.mapa_id : "");
  if (chave === ultimaLista) return;
  ultimaLista = chave;
  const lista = $("lista-mapas");
  lista.replaceChildren();
  if (!mapas.length) {
    const vazio = document.createElement("li");
    vazio.className = "vazio";
    vazio.textContent = "Nenhum mapa salvo ainda.";
    lista.appendChild(vazio);
    return;
  }
  mapas.forEach((mapa) => {
    const item = document.createElement("li");
    if (status && status.mapa_id === mapa.id) item.classList.add("aberto");
    const info = document.createElement("div");
    const nome = document.createElement("strong");
    nome.textContent = mapa.nome;
    const detalhe = document.createElement("small");
    const data = new Date(mapa.atualizado_em * 1000).toLocaleString("pt-BR", {
      day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
    });
    detalhe.textContent = `${data} · ${fmt(mapa.area_m2, 1)} m² · ${fmt(mapa.distancia_m, 1)} m percorridos`;
    info.append(nome, detalhe);
    const abrir = document.createElement("button");
    abrir.type = "button";
    abrir.className = "botao botao-pequeno";
    abrir.textContent = "Abrir";
    abrir.addEventListener("click", async () => {
      if (await comando(`/api/mapa/${mapa.id}/abrir`, {}, `Mapa "${mapa.nome}" aberto para consulta.`)) {
        enquadrado = false;
        ultimaLista = "";
        carregarLista();
      }
    });
    const apagar = document.createElement("button");
    apagar.type = "button";
    apagar.className = "botao botao-pequeno botao-perigo";
    apagar.textContent = "Apagar";
    apagar.addEventListener("click", async () => {
      if (!window.confirm(`Apagar o mapa "${mapa.nome}"? Isso não pode ser desfeito.`)) return;
      if (await comando(`/api/mapa/${mapa.id}/apagar`, {}, "Mapa apagado.")) {
        ultimaLista = "";
        carregarLista();
      }
    });
    item.append(info, abrir, apagar);
    lista.appendChild(item);
  });
}

export function aoMostrarMapa() {
  lerStatus();
  carregarLista();
  requestAnimationFrame(desenharMapa);
}

export function iniciarMapa() {
  iniciarInteracao();
  definirSeguir(true);

  $("btn-mapa-ligar").addEventListener("click", () => {
    if (status && status.rodando) comando("/api/mapa/pausar", {}, "Mapeamento pausado.");
    else comando("/api/mapa/ligar", {}, "Mapeamento ligado: dirija o AGV devagar.");
  });
  $("btn-mapa-novo").addEventListener("click", async () => {
    if (status && status.recorte && !status.salvo
        && !window.confirm("Começar um mapa novo? O mapa atual ainda não foi salvo e será descartado.")) return;
    if (await comando("/api/mapa/novo", {}, "Mapa novo: tudo zerado.")) {
      enquadrado = false;
      imagem = null;
      versaoImagem = -1;
      $("mapa-nome-input").value = "";
      definirSeguir(true);
      ultimaLista = "";
      carregarLista();
    }
  });
  const salvar = async () => {
    const nome = $("mapa-nome-input").value.trim();
    if (await comando("/api/mapa/salvar", { nome }, `Mapa "${nome}" salvo.`)) {
      ultimaLista = "";
      carregarLista();
    }
  };
  $("btn-mapa-salvar").addEventListener("click", salvar);
  $("mapa-nome-input").addEventListener("keydown", (evento) => {
    if (evento.key === "Enter") salvar();
  });
  $("mapa-avanco").addEventListener("change", (evento) => {
    const valor = Number(evento.target.value);
    if (!Number.isFinite(valor)) return;
    comando("/api/mapa/config", { avanco_cm: valor }, `Avanço do Kinect: ${fmt(valor, 1)} cm.`);
  });

  setInterval(lerStatus, 700);
}
