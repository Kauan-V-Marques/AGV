// Telemetria: le /api/status com polling adaptativo e atualiza todos os quadros.

import { aplicarEstabilizacao } from "./ajustes.js";
import { atualizarAprendizado } from "./aprendizado.js";
import { atualizarResumoMapa } from "./mapa.js";
import { aplicarModoNaTela } from "./controle.js";
import { controlState } from "./estado.js";
import { atualizarSetor, desenharGraficos, registrarLeitura } from "./graficos.js";
import { atualizarEstadoRosto, updateFaceSelector } from "./rostos.js";
import { $, definirTexto, fmtMetros, limitar, nivelDistancia } from "./util.js";
import { marcarQuadros } from "./videos.js";

function definirEstado(id, estado) {
  const el = $(id);
  if (el && el.dataset.estado !== estado) el.dataset.estado = estado;
}

function atualizarLeitura(id, distancia) {
  const el = $(id);
  if (!el) return;
  el.dataset.nivel = nivelDistancia(distancia);
  const valor = el.querySelector("strong");
  const texto = fmtMetros(distancia);
  if (valor.textContent !== texto) valor.textContent = texto;
}

function atualizarMedidor(barraId, valorId, valor) {
  const v = limitar(Number(valor) || 0, -100, 100);
  const barra = $(barraId);
  barra.style.left = v >= 0 ? "50%" : `${50 + v / 2}%`;
  barra.style.width = `${Math.abs(v) / 2}%`;
  barra.classList.toggle("negativo", v < 0);
  definirTexto(valorId, String(v));
}

function grau(valor) {
  return typeof valor === "number" ? `${valor.toFixed(1)}°` : "--";
}

// iniciadoEm: quando o pedido de status saiu (ms). Um status que saiu antes da ultima
// troca de modo feita na tela ainda traz o modo antigo e nao deve desfaze-la.
export function updateTelemetry(snapshot, iniciadoEm = Date.now()) {
  const state = snapshot.state || {};
  const agv = snapshot.agv || {};
  const autopilot = snapshot.autopilot || {};
  const arduino = snapshot.arduino || {};
  const kinect = snapshot.kinect || {};
  const zed = snapshot.zed || {};
  const lidar = snapshot.lidar || {};
  const face = snapshot.face || {};
  const settings = snapshot.settings || {};
  if ((agv.mode || "manual") !== controlState.mode && iniciadoEm > controlState.modoAlteradoEm) {
    controlState.mode = agv.mode || "manual";
  }
  // Modo que a tela mostra: o do AGV, salvo se a tela acabou de troca-lo.
  const modo = controlState.mode;

  // ── barra superior ──
  aplicarModoNaTela(modo);
  definirEstado("st-kinect", state.kinect_ok ? "ok" : "erro");
  definirEstado("st-zed", zed.online ? "ok" : "off");
  definirEstado("st-lidar", lidar.online ? "ok" : (lidar.habilitado === false ? "off" : "erro"));
  definirTexto("proximidade-fonte", lidar.online ? "Kinect + LIDAR (Radar 360° em Telemetria)" : "Kinect · profundidade");
  definirEstado("st-serial", arduino.connected ? "ok" : "erro");

  // ── proximidade ──
  const centro = typeof state.center_clearance_m === "number" ? state.center_clearance_m : state.distance_m;
  atualizarLeitura("dist-esq", state.left_clearance_m);
  atualizarLeitura("dist-cen", centro);
  atualizarLeitura("dist-dir", state.right_clearance_m);
  atualizarSetor("setor-esq", state.left_clearance_m);
  atualizarSetor("setor-cen", centro);
  atualizarSetor("setor-dir", state.right_clearance_m);
  registrarLeitura(centro, state.left_clearance_m, state.right_clearance_m);
  desenharGraficos();

  // ── controle ──
  atualizarMedidor("medidor-vel", "valor-vel", agv.active_speed);
  atualizarMedidor("medidor-dir", "valor-dir", agv.active_steering);
  const decisao = agv.active_source === "correcao" ? "correção do operador" : (autopilot.last_reason || "--");
  definirTexto("motivo-auto", modo === "manual" ? "" : `decisão: ${decisao}`);

  // ── telemetria geral ──
  const accel = Array.isArray(state.accel) ? state.accel : [];
  definirTexto("tg-vel", `${agv.active_speed ?? 0}`, true);
  definirTexto("tg-dir", `${agv.active_steering ?? 0}`, true);
  definirTexto("tg-tilt", grau(state.tilt_deg), true);
  definirTexto("tg-tilt-alvo", `alvo ${grau(state.tilt_target_deg)}`);
  definirTexto("tg-accel", accel.length === 3 ? accel.map((v) => Number(v).toFixed(1)).join(" · ") : "--", true);
  definirTexto("tg-fps", `${state.fps_rgb || 0} · ${state.fps_depth || 0} · ${zed.fps || 0}`, true);
  definirTexto("tg-atraso", typeof state.frame_age_s === "number" ? `${state.frame_age_s.toFixed(2)} s` : "--", true);
  definirTexto("tg-motivo", autopilot.last_reason || "--", true);
  definirTexto("tg-limite", typeof settings.speed_limit_pct === "number" ? `${settings.speed_limit_pct}%` : "--", true);

  // ── secao telemetria (detalhes) ──
  definirTexto("distance", fmtMetros(state.distance_m));
  definirTexto("left-clearance", fmtMetros(state.left_clearance_m));
  definirTexto("center-clearance", fmtMetros(state.center_clearance_m));
  definirTexto("right-clearance", fmtMetros(state.right_clearance_m));
  definirTexto("active-command", `${agv.active_speed ?? 0} / ${agv.active_steering ?? 0}`);
  definirTexto("auto-reason", autopilot.last_reason || "--");
  definirTexto("kinect-status", state.kinect_ok
    ? `ok | atraso ${state.frame_age_s ?? "--"}s | tilt ${state.tilt_deg ?? "--"} | alvo ${state.tilt_target_deg ?? "--"}`
    : (state.kinect_error || "offline"));
  definirTexto("zed-status", zed.online
    ? `ok | atraso ${zed.frame_age_s ?? "--"}s | ${zed.fps ?? 0} fps`
    : (zed.error || "offline"));
  const info = lidar.info || {};
  definirTexto("lidar-status", lidar.online
    ? `${lidar.porta || "usb"} | ${lidar.hz ?? 0} voltas/s | ${lidar.pontos ?? 0} pontos | fw ${info.firmware || "--"} | saúde ${info.saude || "--"}`
    : (lidar.erro || "offline"));
  definirTexto("serial-status", arduino.connected
    ? `${arduino.port || "usb"} | ${arduino.last_command || "--"}`
    : (arduino.last_error || "desconectada"));
  definirTexto("face-status", face.known
    ? `${face.label || "rosto"} | dist ${typeof face.distance === "number" ? face.distance.toFixed(3) : "--"}`
    : (face.selected_name ? `procurando ${face.selected_name}` : "nenhuma pessoa selecionada"));
  definirTexto("fps-status", `${state.fps_rgb || 0} rgb | ${state.fps_depth || 0} depth`);
  aplicarEstabilizacao(!!kinect.stabilization_enabled);

  // ── HUD ──
  definirTexto("hud-chip-mode", "modo " + modo);
  definirTexto("hud-chip-kinect", state.kinect_ok ? "kinect online" : "kinect offline");
  $("hud-chip-kinect").classList.toggle("hud-warn", !state.kinect_ok);
  definirTexto("hud-chip-serial", arduino.connected ? "serial conectada" : "serial desconectada");
  $("hud-chip-serial").classList.toggle("hud-warn", !arduino.connected);
  definirTexto("hud-chip-lidar", lidar.online ? "lidar online" : "lidar offline");
  $("hud-chip-lidar").classList.toggle("hud-warn", !lidar.online);
  definirTexto("hud-distance", fmtMetros(state.distance_m));
  definirTexto("hud-left", fmtMetros(state.left_clearance_m));
  definirTexto("hud-center", fmtMetros(state.center_clearance_m));
  definirTexto("hud-right", fmtMetros(state.right_clearance_m));
  const folgasLidar = lidar.online ? (lidar.folgas || {}) : {};
  const tras = ["tras", "tras_esq", "tras_dir"].map((s) => folgasLidar[s]).filter((v) => typeof v === "number");
  definirTexto("hud-rear", lidar.online ? (tras.length ? fmtMetros(Math.min(...tras)) : "livre") : "--");
  definirTexto("hud-zed-label", face.known ? `Traseira · ${face.label || "rosto"}` : "Traseira · rostos");
  $("hud-pip-zed").classList.toggle("hud-rosto", !!face.known);
  definirTexto("hud-cmd", `${agv.active_speed ?? 0} / ${agv.active_steering ?? 0}`);
  definirTexto("hud-fps", `${state.fps_rgb || 0}`);

  // ── rostos ──
  updateFaceSelector(face);
  atualizarEstadoRosto(face);

  // ── aprendizado e mapa ──
  atualizarAprendizado(snapshot.ia);
  atualizarResumoMapa(snapshot.mapa);

  marcarQuadros();
}

// ── Polling adaptativo e aviso de rede ──────────────────────────────
let _statusInterval = 500;
let _statusFail = 0;
let _statusTimer = null;

function _updateNetBanner(rtt) {
  const banner = $("net-banner");
  const ruim = rtt === null || rtt > 800 || _statusFail > 0;
  if (ruim) {
    banner.textContent = rtt === null
      ? (_statusFail >= 3 ? "⚠ Sem resposta do AGV — verifique o Wi-Fi" : "⚠ Conexão lenta...")
      : `⚠ Sinal fraco (${rtt} ms)`;
    banner.classList.toggle("grave", rtt === null && _statusFail >= 3);
    banner.hidden = false;
  } else {
    banner.hidden = true;
  }

  definirEstado("st-rede", rtt === null ? "erro" : rtt < 300 ? "ok" : rtt < 800 ? "aviso" : "erro");
  definirTexto("st-rede-texto", rtt === null ? "sem resposta" : `${rtt} ms`);
  if (rtt === null && _statusFail >= 3) {
    definirEstado("estado-agv", "erro");
    definirTexto("estado-agv-texto", "SEM CONEXÃO");
  }
}

async function refreshStatus() {
  const t0 = Date.now();
  const ctrl = new AbortController();
  const tid = setTimeout(() => ctrl.abort(), 2000);
  try {
    const response = await fetch("/api/status", { cache: "no-store", signal: ctrl.signal });
    const snapshot = await response.json();
    clearTimeout(tid);
    const rtt = Date.now() - t0;
    updateTelemetry(snapshot, t0);
    _statusFail = 0;
    // Polling adaptativo: quanto mais rapido o RTT, mais frequente o poll.
    _statusInterval = rtt < 300 ? 500 : rtt < 800 ? 900 : 1800;
    _updateNetBanner(rtt);
  } catch (error) {
    clearTimeout(tid);
    _statusFail++;
    _statusInterval = Math.min(3000, 500 + _statusFail * 500);
    _updateNetBanner(null);
    console.warn("status timeout/erro #" + _statusFail);
  } finally {
    clearTimeout(_statusTimer);
    _statusTimer = setTimeout(refreshStatus, _statusInterval);
  }
}

export function iniciarTelemetria() {
  refreshStatus();
}
