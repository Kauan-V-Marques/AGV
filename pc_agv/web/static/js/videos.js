// Videos: cada stream MJPEG existe uma unica vez e e movido para o espaco
// (data-slot) da secao aberta. Mover um <img> nao reinicia o stream.

import { uiState } from "./estado.js";
import { $, aviso, gravarPreferencia, lerPreferencia } from "./util.js";

const NOMES = { rgb: "Kinect RGB", depth: "Profundidade", zed: "Câmera ZED" };
const ORDEM = ["rgb", "depth", "zed"];

export const streams = {
  rgb: $("rgb-stream"),
  depth: $("depth-stream"),
  zed: $("zed-stream"),
};

function marcarQuadro(img) {
  const espaco = img.parentElement;
  if (espaco && espaco.classList.contains("video")) {
    espaco.classList.toggle("tem-quadro", img.naturalWidth > 0);
  }
}

// O primeiro quadro tira o efeito de "carregando"; chamado tambem a cada status.
export function marcarQuadros() {
  Object.values(streams).forEach(marcarQuadro);
}

function colocar(espaco, nome) {
  const img = streams[nome];
  if (img.parentElement !== espaco) espaco.appendChild(img);
  espaco.dataset.stream = nome;
  marcarQuadro(img);
}

function hudAberto() {
  return !$("hud-overlay").hidden;
}

export function posicionarVideos() {
  if (hudAberto()) return;
  const secao = document.querySelector(".secao.ativa");
  if (!secao) return;
  const usados = new Set();

  // 1) espacos com nome de camera fixo (ex.: a ZED no quadro de deteccao facial)
  secao.querySelectorAll("[data-slot]").forEach((espaco) => {
    const nome = espaco.dataset.slot;
    if (streams[nome]) {
      colocar(espaco, nome);
      usados.add(nome);
    }
  });

  // 2) camera em destaque da secao
  const principal = secao.querySelector('[data-slot="principal"]');
  if (principal) {
    let foco = uiState.foco[secao.id] || "rgb";
    if (usados.has(foco)) foco = ORDEM.find((nome) => !usados.has(nome));
    if (foco) {
      colocar(principal, foco);
      usados.add(foco);
    }
  }

  // 3) miniaturas recebem as cameras que sobraram
  secao.querySelectorAll('[data-slot="mini"]').forEach((espaco) => {
    const botao = espaco.closest(".miniatura");
    const livre = ORDEM.find((nome) => !usados.has(nome));
    if (botao) botao.hidden = !livre;
    if (!livre) return;
    colocar(espaco, livre);
    usados.add(livre);
    if (botao) botao.dataset.stream = livre;
  });

  const nomePrincipal = principal ? NOMES[principal.dataset.stream] : "";
  secao.querySelectorAll("[data-rotulo-principal]").forEach((el) => { el.textContent = nomePrincipal; });
  secao.querySelectorAll(".miniatura").forEach((botao) => {
    const rotulo = botao.querySelector("[data-rotulo-mini]");
    if (rotulo) rotulo.textContent = NOMES[botao.dataset.stream] || "--";
  });
}

export function iniciarVideos() {
  iniciarQualidadeDeVideo();
  try {
    uiState.foco = JSON.parse(lerPreferencia("agv_foco", "{}")) || {};
  } catch (erro) {
    uiState.foco = {};
  }
  Object.values(streams).forEach((img) => img.addEventListener("load", () => marcarQuadro(img)));

  document.querySelectorAll(".miniatura").forEach((botao) => {
    botao.addEventListener("click", () => {
      const secao = botao.closest(".secao");
      const nome = botao.dataset.stream;
      if (!secao || !nome) return;
      uiState.foco[secao.id] = nome;
      gravarPreferencia("agv_foco", JSON.stringify(uiState.foco));
      posicionarVideos();
    });
  });
}

// ── Qualidade dos videos conforme o aparelho e a rede ───────────────
// Os tres streams somam ~4 Mbit/s no perfil completo; num celular ou num Wi-Fi cheio
// isso enche a fila do link e atrasa tudo, inclusive os comandos (picos de 1 s medidos).
// O servidor nunca manda mais que o configurado; aqui o painel so pede menos.
const ROTAS = { rgb: "/video", depth: "/depth_map", zed: "/zed" };
const PERFIS = {
  completo: {},
  leve: { rgb: { fps: 8, q: 60, w: 480 }, depth: { fps: 4, q: 55, w: 320 }, zed: { fps: 5, q: 55, w: 400 } },
  minimo: { rgb: { fps: 4, q: 45, w: 320 }, depth: { fps: 2, q: 45, w: 240 }, zed: { fps: 2, q: 45, w: 320 } },
};
const ORDEM_PERFIS = ["completo", "leve", "minimo"];
const RTT_PIORA_MS = 600;     // mediana acima disso: pede menos
const RTT_MELHORA_MS = 250;   // abaixo disso: volta a pedir mais (ate o perfil do aparelho)
const TROCA_MIN_MS = 10000;   // trocar reinicia os streams: no maximo uma vez a cada 10 s

let perfilVideo = null;
let perfilBase = "completo";
let ultimaTroca = 0;
const rtts = [];

function rotaDoStream(nome, perfil) {
  const opcoes = (PERFIS[perfil] || {})[nome];
  return opcoes ? `${ROTAS[nome]}?fps=${opcoes.fps}&q=${opcoes.q}&w=${opcoes.w}` : ROTAS[nome];
}

function aplicarPerfilDeVideo(perfil) {
  if (perfil === perfilVideo) return;
  perfilVideo = perfil;
  ultimaTroca = Date.now();
  ORDEM.forEach((nome) => { streams[nome].src = rotaDoStream(nome, perfil); });
  document.body.dataset.video = perfil;
  const rede = $("st-rede");
  if (rede) rede.title = `Tempo de resposta do AGV · vídeo: ${perfil}`;
}

export function perfilDeVideo() {
  return perfilVideo;
}

// Chamado a cada /api/status com o tempo de resposta (null = sem resposta).
export function registrarRtt(rtt) {
  rtts.push(rtt === null ? 3000 : rtt);
  if (rtts.length > 8) rtts.shift();
  if (rtts.length < 5 || Date.now() - ultimaTroca < TROCA_MIN_MS) return;
  const ordenados = [...rtts].sort((a, b) => a - b);
  const mediana = ordenados[Math.floor(ordenados.length / 2)];
  const atual = ORDEM_PERFIS.indexOf(perfilVideo);
  if (mediana > RTT_PIORA_MS && atual < ORDEM_PERFIS.length - 1) {
    aplicarPerfilDeVideo(ORDEM_PERFIS[atual + 1]);
    rtts.length = 0;
  } else if (mediana < RTT_MELHORA_MS && atual > ORDEM_PERFIS.indexOf(perfilBase)) {
    aplicarPerfilDeVideo(ORDEM_PERFIS[atual - 1]);
    rtts.length = 0;
  }
}

function iniciarQualidadeDeVideo() {
  const celular = window.matchMedia("(pointer: coarse)").matches || window.innerWidth < 900;
  perfilBase = celular ? "leve" : "completo";
  aplicarPerfilDeVideo(perfilBase);
}

function carimbo() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

// Salva o quadro atual da camera em destaque como PNG.
export function tirarFoto() {
  const principal = document.querySelector('.secao.ativa [data-slot="principal"]');
  const nome = (principal && principal.dataset.stream) || "rgb";
  const img = streams[nome];
  if (!img.naturalWidth) {
    aviso("Ainda não há imagem dessa câmera para salvar.", "erro");
    return;
  }
  const canvas = document.createElement("canvas");
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  canvas.getContext("2d").drawImage(img, 0, 0);
  canvas.toBlob((blob) => {
    if (!blob) {
      aviso("Não foi possível gerar a foto.", "erro");
      return;
    }
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `agv-${nome}-${carimbo()}.png`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 2000);
    aviso(`Foto salva: ${link.download}`, "ok");
  }, "image/png");
}

// ── HUD em tela cheia ───────────────────────────────────────────────
function requestRealFullscreen(el) {
  const request = el.requestFullscreen || el.webkitRequestFullscreen || el.msRequestFullscreen;
  if (!request) return;
  try {
    const resultado = request.call(el);
    if (resultado && resultado.catch) resultado.catch(() => { /* fica so o modo em CSS */ });
  } catch (error) { /* segue so com o fallback em CSS */ }
}

function exitRealFullscreen() {
  const exit = document.exitFullscreen || document.webkitExitFullscreen || document.msExitFullscreen;
  if (document.fullscreenElement && exit) {
    try { exit.call(document); } catch (error) { /* ignora */ }
  }
}

export function iniciarHud({ aoParar }) {
  const overlay = $("hud-overlay");
  const principal = $("hud-main-frame");
  const pip = $("hud-pip-frame");
  const pipZed = $("hud-pip-zed");
  const joystickWrap = $("hud-joystick-wrap");
  const dica = $("hud-hint");
  const joystick = $("mobile-joystick");
  const joystickCasa = { parent: joystick.parentElement, next: joystick.nextElementSibling };

  function abrir() {
    principal.appendChild(streams.rgb);
    pip.appendChild(streams.depth);
    pipZed.appendChild(streams.zed); // webcam traseira com a caixa do rosto reconhecido
    overlay.hidden = false;
    document.body.classList.add("hud-active");
    if (uiState.isMobileControl) {
      joystickWrap.appendChild(joystick);
      joystickWrap.hidden = false;
      joystick.hidden = false;
      dica.hidden = true;
    } else {
      joystickWrap.hidden = true;
      dica.hidden = false;
    }
    requestRealFullscreen(overlay);
    window.dispatchEvent(new Event("agv:hud"));
  }

  function fechar() {
    if (overlay.hidden) return;
    overlay.hidden = true;
    document.body.classList.remove("hud-active");
    joystickCasa.parent.insertBefore(joystick, joystickCasa.next);
    joystick.hidden = !uiState.isMobileControl;
    posicionarVideos();
    exitRealFullscreen();
  }

  $("btn-hud-toggle").addEventListener("click", abrir);
  $("btn-hud-exit").addEventListener("click", fechar);
  $("btn-hud-stop").addEventListener("click", aoParar);
  document.addEventListener("fullscreenchange", () => {
    if (!document.fullscreenElement) fechar();
  });
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !overlay.hidden) fechar();
  });
}
