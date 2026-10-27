// Radar 360° do RPLIDAR: pontos em volta do AGV vistos de cima (frente para cima) e
// folgas por setor. Desenhado na pagina Telemetria e no HUD; so le /api/lidar
// quando um dos dois esta visivel.

import { $, fmtMetros, gravarPreferencia, lerPreferencia, nivelDistancia } from "./util.js";

const SETORES = ["frente", "frente_esq", "esquerda", "tras_esq", "tras", "tras_dir", "direita", "frente_dir"];
const ANEIS_M = [0.5, 1, 2, 4];
const ALCANCE_PADRAO_M = 3;
const INTERVALO_MS = 200;
const CAMPO_KINECT_RAD = (57 * Math.PI) / 180;
const COR_NIVEL = { perigo: "--perigo", alerta: "--alerta", atencao: "--atencao", livre: "--azul-claro", nenhum: "--azul-claro" };

let ultimo = null;
let timer = null;

function cor(variavel) {
  return getComputedStyle(document.documentElement).getPropertyValue(variavel).trim();
}

function secaoVisivel() {
  const secao = $("secao-telemetria");
  return secao && !secao.hidden;
}

function hudVisivel() {
  const hud = $("hud-overlay");
  const radar = $("hud-radar");
  return hud && !hud.hidden && radar && !radar.hidden;
}

// Botao RADAR do HUD: esconde ou mostra o radar (lembrado no navegador).
function mostrarRadarNoHud(mostrar) {
  const radar = $("hud-radar");
  const botao = $("hud-radar-toggle");
  if (!radar || !botao) return;
  radar.hidden = !mostrar;
  botao.setAttribute("aria-pressed", mostrar ? "true" : "false");
  gravarPreferencia("agv_hud_radar", mostrar ? "1" : "0");
  if (mostrar) requestAnimationFrame(desenharRadar);
}

function atualizarFolga(setor, valor) {
  const el = $(`lidar-${setor}`);
  if (!el) return;
  el.dataset.nivel = nivelDistancia(valor);
  const texto = typeof valor === "number" ? fmtMetros(valor) : (ultimo && ultimo.online ? "livre" : "--");
  const forte = el.querySelector("strong");
  if (forte.textContent !== texto) forte.textContent = texto;
}

export function desenharRadar() {
  if (secaoVisivel()) desenharEm($("radar-canvas"), false);
  if (hudVisivel()) desenharEm($("hud-radar-canvas"), true);
}

// compacto: versao do HUD (menor, sem textos longos, alcance fixo para nao "pulsar")
function desenharEm(canvas, compacto) {
  if (!canvas) return;
  const dpr = window.devicePixelRatio || 1;
  const lado = canvas.clientWidth;
  if (!lado) return;
  if (canvas.width !== Math.round(lado * dpr)) {
    canvas.width = Math.round(lado * dpr);
    canvas.height = Math.round(lado * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, lado, lado);

  const pontos = (ultimo && ultimo.lista) || [];
  let alcance = compacto ? 2.5 : ALCANCE_PADRAO_M;
  if (!compacto) for (const [x, y] of pontos) alcance = Math.max(alcance, Math.min(6, Math.hypot(x, y)));
  const escala = (lado / 2 - (compacto ? 6 : 14)) / alcance; // px por metro
  const cx = lado / 2;
  const cy = lado / 2;
  // referencial do AGV: x para a frente (tela: para cima), y para a esquerda (tela: para a esquerda)
  const tela = (x, y) => [cx - y * escala, cy - x * escala];

  // aneis de distancia
  ctx.strokeStyle = cor("--grade-grafico");
  ctx.fillStyle = cor("--texto-suave");
  ctx.font = "11px system-ui, sans-serif";
  ctx.lineWidth = 1;
  ctx.setLineDash([3, 4]);
  for (const r of ANEIS_M) {
    if (r > alcance) continue;
    ctx.beginPath();
    ctx.arc(cx, cy, r * escala, 0, Math.PI * 2);
    ctx.stroke();
    if (!compacto || r === 1) ctx.fillText(`${r} m`, cx + 4, cy - r * escala - 3);
  }
  ctx.setLineDash([]);

  // campo de visao do Kinect (a frente)
  ctx.fillStyle = cor("--roxo");
  ctx.globalAlpha = 0.08;
  ctx.beginPath();
  ctx.moveTo(cx, cy);
  ctx.arc(cx, cy, alcance * escala, -Math.PI / 2 - CAMPO_KINECT_RAD / 2, -Math.PI / 2 + CAMPO_KINECT_RAD / 2);
  ctx.closePath();
  ctx.fill();
  ctx.globalAlpha = 1;

  // corpo do AGV
  const corpo = (ultimo && ultimo.corpo) || { frente_m: 0.3, tras_m: 0.3, largura_m: 0.5 };
  const [x0, y0] = tela(corpo.frente_m, corpo.largura_m / 2);
  ctx.fillStyle = cor("--quadro-3");
  ctx.strokeStyle = cor("--borda-forte");
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.rect(x0, y0, corpo.largura_m * escala, (corpo.frente_m + corpo.tras_m) * escala);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = cor("--azul");
  ctx.fillRect(x0, y0, corpo.largura_m * escala, 3);

  // pontos, coloridos pela distancia ate o centro do AGV
  for (const [x, y] of pontos) {
    if (compacto && Math.hypot(x, y) > alcance) continue;
    const [px, py] = tela(x, y);
    ctx.fillStyle = cor(COR_NIVEL[nivelDistancia(Math.hypot(x, y) - corpo.largura_m / 2)]);
    ctx.beginPath();
    ctx.arc(px, py, 2.4, 0, Math.PI * 2);
    ctx.fill();
  }

  // perto demais para o laser medir (sem eco): um "x" vermelho onde a seguranca supoe o obstaculo
  ctx.strokeStyle = cor("--perigo");
  ctx.lineWidth = 1.5;
  for (const [x, y] of (ultimo && ultimo.lista_cegos) || []) {
    const [px, py] = tela(x, y);
    ctx.beginPath();
    ctx.moveTo(px - 3, py - 3);
    ctx.lineTo(px + 3, py + 3);
    ctx.moveTo(px + 3, py - 3);
    ctx.lineTo(px - 3, py + 3);
    ctx.stroke();
  }

  if ((!ultimo || !ultimo.online) && compacto) {
    ctx.fillStyle = cor("--perigo");
    ctx.font = "12px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("LIDAR offline", cx, cy - 28);
    ctx.textAlign = "start";
  } else if (!ultimo || !ultimo.online) {
    ctx.fillStyle = cor("--texto-suave");
    ctx.font = "13px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText((ultimo && ultimo.erro) || "Aguardando RPLIDAR", cx, cy + (corpo.tras_m * escala) + 28);
    ctx.textAlign = "start";
  }
}

async function ler() {
  clearTimeout(timer);
  if (secaoVisivel() || hudVisivel()) {
    try {
      const resposta = await fetch("/api/lidar", { cache: "no-store" });
      ultimo = (await resposta.json()).lidar;
      const folgas = (ultimo && ultimo.folgas) || {};
      SETORES.forEach((setor) => atualizarFolga(setor, folgas[setor]));
      $("radar-sub").textContent = ultimo && ultimo.online
        ? `RPLIDAR · ${ultimo.hz} voltas/s · ${ultimo.pontos} pontos`
          + (ultimo.cegos ? ` · ${ultimo.cegos} perto demais (x)` : "") + " · frente para cima"
        : "RPLIDAR offline";
      desenharRadar();
    } catch (erro) {
      console.warn("Falha ao ler o LIDAR", erro);
    }
  }
  timer = setTimeout(ler, INTERVALO_MS);
}

export function iniciarRadar() {
  mostrarRadarNoHud(lerPreferencia("agv_hud_radar", "1") === "1");
  const botao = $("hud-radar-toggle");
  if (botao) botao.addEventListener("click", () => mostrarRadarNoHud($("hud-radar").hidden));
  ler();
  window.addEventListener("agv:hud", () => { ler(); requestAnimationFrame(desenharRadar); });
}
