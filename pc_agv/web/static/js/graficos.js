// Visualizacoes da profundidade: setores do AGV visto de cima e grafico dos ultimos 60 s.

import { nivelDistancia } from "./util.js";

// ── Setores (SVG) ───────────────────────────────────────────────────
const CX = 110;
const CY = 128; // frente do AGV no desenho
const RAIO_MIN = 22;
const RAIO_MAX = 108;
const ALCANCE_M = 3; // setor cheio = 3 m ou mais
const ANGULOS = {
  "setor-esq": [-62, -22],
  "setor-cen": [-19, 19],
  "setor-dir": [22, 62],
};

function ponto(graus, raio) {
  const a = (graus * Math.PI) / 180;
  return [CX + raio * Math.sin(a), CY - raio * Math.cos(a)];
}

// Contorno do alcance maximo de cada setor, ao fundo (mostra o "radar" mesmo sem leitura).
export function iniciarSetores() {
  Object.entries(ANGULOS).forEach(([id, [inicio, fim]]) => {
    const setor = document.getElementById(id);
    if (!setor) return;
    const [x1, y1] = ponto(inicio, RAIO_MAX);
    const [x2, y2] = ponto(fim, RAIO_MAX);
    const fundo = document.createElementNS("http://www.w3.org/2000/svg", "path");
    fundo.setAttribute("class", "setor-fundo");
    fundo.setAttribute("d", `M ${CX} ${CY} L ${x1.toFixed(1)} ${y1.toFixed(1)} A ${RAIO_MAX} ${RAIO_MAX} 0 0 1 ${x2.toFixed(1)} ${y2.toFixed(1)} Z`);
    setor.parentNode.insertBefore(fundo, setor);
    atualizarSetor(id, null);
  });
}

export function atualizarSetor(id, distancia) {
  const el = document.getElementById(id);
  if (!el) return;
  const [inicio, fim] = ANGULOS[id];
  const raio = typeof distancia === "number"
    ? RAIO_MIN + ((RAIO_MAX - RAIO_MIN) * Math.min(distancia, ALCANCE_M)) / ALCANCE_M
    : RAIO_MIN;
  const [x1, y1] = ponto(inicio, raio);
  const [x2, y2] = ponto(fim, raio);
  const r = raio.toFixed(1);
  const caminho = `M ${CX} ${CY} L ${x1.toFixed(1)} ${y1.toFixed(1)} A ${r} ${r} 0 0 1 ${x2.toFixed(1)} ${y2.toFixed(1)} Z`;
  el.setAttribute("d", caminho);
  el.style.d = `path("${caminho}")`; // permite a transicao suave no Chrome/Edge
  el.dataset.nivel = nivelDistancia(distancia);
}

// ── Grafico das distancias ──────────────────────────────────────────
const JANELA_MS = 60000;
const historico = [];

export function registrarLeitura(centro, esquerda, direita) {
  const agora = Date.now();
  historico.push({ t: agora, cen: centro, esq: esquerda, dir: direita });
  while (historico.length && agora - historico[0].t > JANELA_MS) historico.shift();
}

function cor(variavel) {
  return getComputedStyle(document.documentElement).getPropertyValue(variavel).trim();
}

function desenharSerie(ctx, chave, x, y, estilo) {
  ctx.beginPath();
  let aberto = false;
  historico.forEach((ponto) => {
    const valor = ponto[chave];
    if (typeof valor !== "number") {
      aberto = false;
      return;
    }
    const px = x(ponto.t);
    const py = y(valor);
    if (aberto) ctx.lineTo(px, py);
    else ctx.moveTo(px, py);
    aberto = true;
  });
  ctx.strokeStyle = estilo.cor;
  ctx.lineWidth = estilo.largura;
  ctx.lineJoin = "round";
  ctx.stroke();
}

function desenhar(canvas) {
  if (!canvas.offsetParent) return; // secao escondida
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

  const esquerda = 30;
  const topo = 6;
  const base = altura - 8;
  const agora = Date.now();
  const x = (t) => esquerda + (1 - (agora - t) / JANELA_MS) * (largura - esquerda - 4);
  const y = (m) => base - (Math.min(Math.max(m, 0), ALCANCE_M) / ALCANCE_M) * (base - topo);

  // grade e escala
  ctx.font = "10px system-ui, sans-serif";
  ctx.fillStyle = cor("--texto-suave");
  ctx.strokeStyle = cor("--grade-grafico");
  ctx.lineWidth = 1;
  for (let m = 0; m <= ALCANCE_M; m += 1) {
    ctx.beginPath();
    ctx.moveTo(esquerda, y(m));
    ctx.lineTo(largura, y(m));
    ctx.stroke();
    ctx.fillText(`${m} m`, 2, y(m) + 3);
  }

  // limites do modo autonomo
  ctx.setLineDash([4, 4]);
  ctx.globalAlpha = 0.6;
  [[0.35, "--perigo"], [0.6, "--alerta"], [0.95, "--atencao"]].forEach(([m, variavel]) => {
    ctx.strokeStyle = cor(variavel);
    ctx.beginPath();
    ctx.moveTo(esquerda, y(m));
    ctx.lineTo(largura, y(m));
    ctx.stroke();
  });
  ctx.setLineDash([]);
  ctx.globalAlpha = 1;

  if (!historico.some((p) => typeof p.cen === "number")) {
    ctx.fillStyle = cor("--texto-suave");
    ctx.textAlign = "center";
    ctx.fillText("aguardando leituras do Kinect", (esquerda + largura) / 2, altura / 2);
    ctx.textAlign = "start";
    return;
  }

  desenharSerie(ctx, "esq", x, y, { cor: "#a78bfa", largura: 1.4 });
  desenharSerie(ctx, "dir", x, y, { cor: "#2dd4bf", largura: 1.4 });
  desenharSerie(ctx, "cen", x, y, { cor: cor("--azul-claro"), largura: 2.4 });
}

export function desenharGraficos() {
  document.querySelectorAll("canvas.grafico").forEach(desenhar);
}
