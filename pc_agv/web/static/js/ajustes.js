// Ajustes: tema, bipe, velocidade maxima, link para outro dispositivo e Kinect.

import { postJson } from "./api.js";
import { uiState } from "./estado.js";
import { $, aviso, gravarPreferencia, lerPreferencia } from "./util.js";

// ── Tema ────────────────────────────────────────────────────────────
function aplicarTema(tema) {
  document.documentElement.dataset.tema = tema;
  $("dark-mode-toggle").checked = tema === "escuro";
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = tema === "escuro" ? "#0d141d" : "#eef2f7";
  gravarPreferencia("agv_tema", tema);
  window.dispatchEvent(new Event("agv:tema")); // graficos redesenham com as novas cores
}

export function iniciarTema() {
  aplicarTema(lerPreferencia("agv_tema", "escuro") === "claro" ? "claro" : "escuro");
  $("btn-tema").addEventListener("click", () => {
    aplicarTema(document.documentElement.dataset.tema === "escuro" ? "claro" : "escuro");
  });
  $("dark-mode-toggle").addEventListener("change", (event) => {
    aplicarTema(event.target.checked ? "escuro" : "claro");
  });
}

// ── Bipe ────────────────────────────────────────────────────────────
export function iniciarSom() {
  const chave = $("som-toggle");
  uiState.som = lerPreferencia("agv_som", "1") !== "0";
  chave.checked = uiState.som;
  chave.addEventListener("change", () => {
    uiState.som = chave.checked;
    gravarPreferencia("agv_som", uiState.som ? "1" : "0");
  });
}

// ── Velocidade maxima ───────────────────────────────────────────────
export function iniciarVelocidade() {
  const slider = $("speed-limit-slider");
  const display = $("speed-limit-display");
  let envio = null;

  async function sendSpeedLimit(pct) {
    try {
      await postJson("/api/settings", { speed_limit_pct: pct });
    } catch (error) {
      console.error(error);
      aviso("Não foi possível aplicar o limite de velocidade.", "erro");
    }
  }

  function applySpeedLimit(pct) {
    display.textContent = pct + "%";
    slider.value = pct;
    slider.style.setProperty("--preenchido", pct + "%");
    gravarPreferencia("agv_speed_limit", pct);
  }

  const salvo = parseInt(lerPreferencia("agv_speed_limit", "100"), 10);
  applySpeedLimit(isNaN(salvo) ? 100 : salvo);
  sendSpeedLimit(slider.value);

  slider.addEventListener("input", () => {
    const pct = parseInt(slider.value, 10);
    applySpeedLimit(pct);
    clearTimeout(envio);
    envio = setTimeout(() => sendSpeedLimit(pct), 300);
  });
}

// ── Link para outro dispositivo ─────────────────────────────────────
function renderShareLink() {
  $("access-link").textContent = uiState.shareLink || "indisponível";
}

async function resolveShareLink() {
  const host = (location.hostname || "").toLowerCase();
  const loopbackHosts = new Set(["localhost", "127.0.0.1", "::1"]);
  if (!loopbackHosts.has(host)) {
    uiState.shareLink = location.origin + "/";
    renderShareLink();
    return;
  }
  try {
    const response = await fetch("/api/access_link", { cache: "no-store" });
    const payload = await response.json();
    uiState.shareLink = payload.url || (location.origin + "/");
  } catch (error) {
    console.error(error);
    uiState.shareLink = location.origin + "/";
  }
  renderShareLink();
}

async function copyShareLink() {
  if (!uiState.shareLink) await resolveShareLink();
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(uiState.shareLink);
    } else {
      const temp = document.createElement("textarea");
      temp.value = uiState.shareLink;
      temp.style.position = "fixed";
      temp.style.opacity = "0";
      document.body.appendChild(temp);
      temp.select();
      document.execCommand("copy");
      document.body.removeChild(temp);
    }
    aviso("Link copiado: " + uiState.shareLink, "ok");
  } catch (error) {
    console.error(error);
    aviso("Não foi possível copiar; selecione o endereço e copie manualmente.", "erro");
  }
}

export function iniciarLink() {
  resolveShareLink();
  $("btn-copy-link").addEventListener("click", copyShareLink);
}

// ── Kinect ──────────────────────────────────────────────────────────
export function aplicarEstabilizacao(ligada) {
  const chave = $("btn-stabilization");
  if (chave && chave.checked !== ligada && !chave.dataset.enviando) chave.checked = ligada;
}

async function sendTilt(angle) {
  try {
    const response = await postJson("/api/kinect/tilt", { angle });
    if (!response.ok) {
      aviso("Não foi possível mover o tilt do Kinect agora.", "erro");
      return;
    }
    aviso(`Tilt do Kinect: ${response.tilt_target_deg}°`, "ok", 2200);
  } catch (error) {
    console.error(error);
    aviso("Falha ao enviar o comando de tilt.", "erro");
  }
}

async function setStabilization(enabled) {
  const chave = $("btn-stabilization");
  chave.dataset.enviando = "1";
  try {
    const response = await postJson("/api/kinect/stabilization", { enabled: !!enabled });
    chave.checked = !!response.enabled;
    aviso(response.enabled ? "Estabilização ligada." : "Estabilização desligada.", "ok", 2200);
  } catch (error) {
    console.error(error);
    chave.checked = !enabled;
    aviso("Falha ao ajustar a estabilização do Kinect.", "erro");
  } finally {
    delete chave.dataset.enviando;
  }
}

async function reconnectKinect() {
  try {
    await postJson("/api/kinect/reconnect", {});
    aviso("Reconexão do Kinect solicitada.", "ok");
  } catch (error) {
    console.error(error);
    aviso("Falha ao pedir a reconexão do Kinect.", "erro");
  }
}

export function iniciarKinect() {
  $("btn-tilt-up").addEventListener("click", () => sendTilt(12));
  $("btn-tilt-center").addEventListener("click", () => sendTilt(0));
  $("btn-tilt-down").addEventListener("click", () => sendTilt(-12));
  $("btn-stabilization").addEventListener("change", (event) => setStabilization(event.target.checked));
  $("btn-reconnect").addEventListener("click", reconnectKinect);
}
