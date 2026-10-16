// Conducao: modo, teclado (WASD/setas), joystick, keepalive do watchdog e parada total.
// No modo IA o mesmo teclado/joystick vale como correcao do operador.

import { postJson } from "./api.js";
import { controlState, uiState } from "./estado.js";
import { $, aviso } from "./util.js";

export function mapKey(raw) {
  const key = (raw || "").toLowerCase();
  if (key === "w" || key === "arrowup") return "w";
  if (key === "a" || key === "e" || key === "arrowleft") return "a";
  if (key === "s" || key === "arrowdown") return "s";
  if (key === "d" || key === "arrowright") return "d";
  return null;
}

export function isTypingTarget(event) {
  const element = event && event.target;
  if (!element) return false;
  const tag = (element.tagName || "").toLowerCase();
  return !!element.isContentEditable || tag === "input" || tag === "textarea" || tag === "select";
}

export function deriveManualCommand() {
  if (uiState.isMobileControl) {
    const deadZone = 0.08;
    const x = controlState.mobile.x;
    const y = controlState.mobile.y;
    const speed = Math.abs(y) < deadZone ? 0 : Math.round(-y * 100);
    const steering = Math.abs(x) < deadZone ? 0 : Math.round(x * 100);
    return { speed, steering };
  }

  let speed = 0;
  let steering = 0;
  if (controlState.pressed.w && !controlState.pressed.s) speed = 100;
  if (controlState.pressed.s && !controlState.pressed.w) speed = -100;
  if (controlState.pressed.a && !controlState.pressed.d) steering = -70;
  if (controlState.pressed.d && !controlState.pressed.a) steering = 70;
  return { speed, steering };
}

// Cancela qualquer fetch de controle em voo antes de mandar novo.
let _controlAbort = null;
let _controlPending = 0;

// Modos em que o comando do operador vale: manual (dirige) e IA (corrige a rede).
const MODOS_COM_OPERADOR = ["manual", "ia"];
const ROTULOS_MODO = { manual: "MANUAL", auto: "AUTÔNOMO", ia: "IA" };
const ESTADO_TOPO = { manual: "ok", auto: "auto", ia: "ia" };

export async function sendManualCommand(source, keepalive) {
  if (!MODOS_COM_OPERADOR.includes(controlState.mode)) return;
  const next = deriveManualCommand();
  if (keepalive) {
    // Keepalive so repete comando em movimento e nunca cancela um envio ainda em voo.
    if (_controlPending > 0) return;
    if (next.speed === 0 && next.steering === 0) return;
  } else if (next.speed === controlState.lastSpeed && next.steering === controlState.lastSteering) {
    return;
  }

  controlState.lastSpeed = next.speed;
  controlState.lastSteering = next.steering;

  if (_controlAbort) { try { _controlAbort.abort(); } catch (e) { /* ja encerrado */ } }
  _controlAbort = new AbortController();
  const signal = _controlAbort.signal;

  // Timeout de 1,5 s para nao acumular requests em fila
  const timeoutId = setTimeout(() => { try { _controlAbort.abort(); } catch (e) { /* ja encerrado */ } }, 1500);
  _controlPending += 1;
  try {
    await postJson("/api/control", {
      mode: controlState.mode,
      speed: next.speed,
      steering: next.steering,
      source: source || "site-manual",
    }, signal);
  } catch (error) {
    if (error.name !== "AbortError") console.error(error);
  } finally {
    _controlPending -= 1;
    clearTimeout(timeoutId);
  }
}

// O servidor para o agv se nenhum comando manual chegar em 2 s (watchdog).
// Enquanto uma tecla ou o stick estiver segurado, reenvia o comando atual.
export function iniciarKeepalive() {
  setInterval(() => sendManualCommand("site-keepalive", true), 500);
}

// Chamada a cada status: so mexe no DOM quando o valor muda.
export function aplicarModoNaTela(mode) {
  const rotulo = ROTULOS_MODO[mode] || "MANUAL";
  const segmentado = document.querySelector(".segmentado");
  if (segmentado && segmentado.dataset.modo !== mode) {
    segmentado.dataset.modo = mode;
    document.querySelectorAll(".mode-button").forEach((button) => {
      const selecionado = button.dataset.mode === mode;
      button.classList.toggle("is-selected", selecionado);
      button.setAttribute("aria-checked", selecionado ? "true" : "false");
    });
  }
  const selo = $("selo-modo");
  if (selo && selo.dataset.modo !== mode) {
    selo.textContent = rotulo;
    selo.dataset.modo = mode;
  }
  const dica = $("dica-ia");
  if (dica) dica.hidden = mode !== "ia";
  // Pilula de estado no topo (a telemetria troca para "SEM CONEXÃO" se o AGV parar de responder).
  const topo = $("estado-agv");
  const estadoTopo = ESTADO_TOPO[mode] || "ok";
  if (topo && topo.dataset.estado !== estadoTopo) topo.dataset.estado = estadoTopo;
  const texto = $("estado-agv-texto");
  if (texto && texto.textContent !== `ONLINE · ${rotulo}`) texto.textContent = `ONLINE · ${rotulo}`;
}

export async function setMode(mode) {
  if (mode === "ia" && !uiState.iaPronta) {
    aviso("Treine um modelo em Aprendizado antes de usar o modo IA.", "erro");
    return;
  }
  const anterior = controlState.mode;
  controlState.mode = mode;
  controlState.modoAlteradoEm = Date.now();
  if (mode !== "manual") {
    controlState.pressed = { w: false, a: false, s: false, d: false };
    controlState.lastSpeed = 0;
    controlState.lastSteering = 0;
    document.querySelectorAll("[data-key]").forEach((button) => button.classList.remove("is-active"));
  }
  aplicarModoNaTela(mode);

  try {
    const resposta = await postJson("/api/control", { mode, speed: 0, steering: 0, source: "site-mode" });
    if (resposta && resposta.ok === false) {
      // O AGV recusou (ex.: modo IA sem modelo): a tela volta ao modo em que ele continua.
      controlState.mode = anterior;
      controlState.modoAlteradoEm = Date.now();
      aplicarModoNaTela(anterior);
      aviso(resposta.error === "sem_modelo"
        ? "Treine um modelo em Aprendizado antes de usar o modo IA."
        : "O AGV recusou a troca de modo.", "erro");
    } else if (mode === "ia" && anterior !== "ia") {
      aviso("Modo IA: a rede treinada está dirigindo. Segure uma tecla para corrigir.", "ok");
    }
  } catch (error) {
    console.error(error);
    aviso("Não foi possível trocar o modo: sem resposta do AGV.", "erro");
  }
}

export function setPressed(key, value, source) {
  if (uiState.isMobileControl) return;
  if (!key) return;
  controlState.pressed[key] = value;
  sendManualCommand(source);
  document.querySelectorAll("[data-key]").forEach((button) => {
    button.classList.toggle("is-active", controlState.pressed[button.dataset.key]);
  });
}

export async function emergencyStop() {
  controlState.mode = "manual";
  controlState.modoAlteradoEm = Date.now();
  controlState.pressed = { w: false, a: false, s: false, d: false };
  controlState.mobile = { x: 0, y: 0, active: false };
  controlState.lastSpeed = 0;
  controlState.lastSteering = 0;
  document.querySelectorAll("[data-key]").forEach((button) => button.classList.remove("is-active"));
  aplicarModoNaTela("manual");

  const botao = $("btn-stop");
  if (botao) {
    botao.classList.remove("acionado");
    void botao.offsetWidth; // reinicia a animacao
    botao.classList.add("acionado");
  }

  try {
    await postJson("/api/stop", {});
    aviso("Parada total enviada: motores zerados e modo manual.", "erro");
  } catch (error) {
    console.error(error);
    aviso("Falha ao enviar a parada total: verifique a conexão com o AGV.", "erro", 6000);
  }
}

export function detectMobileControl() {
  const coarsePointer = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
  const mobileUA = /android|iphone|ipad|ipod|mobile/i.test(navigator.userAgent || "");
  return coarsePointer || mobileUA;
}

export function setupControlSurface() {
  uiState.isMobileControl = detectMobileControl();
  const keyboardPad = $("keyboard-pad");
  const mobileJoystick = $("mobile-joystick");
  const hint = $("control-hint");
  keyboardPad.hidden = uiState.isMobileControl;
  mobileJoystick.hidden = !uiState.isMobileControl;
  hint.textContent = uiState.isMobileControl
    ? "Arraste o centro do joystick para mover; solte para parar."
    : "W A S D ou setas (E também vira à esquerda).";
  if (uiState.isMobileControl) {
    controlState.pressed = { w: false, a: false, s: false, d: false };
    document.querySelectorAll("[data-key]").forEach((button) => button.classList.remove("is-active"));
  } else {
    controlState.mobile = { x: 0, y: 0, active: false };
  }
  sendManualCommand("site-surface");
}

export function iniciarTeclado() {
  document.querySelectorAll("[data-key]").forEach((button) => {
    const key = button.dataset.key;
    const press = (event) => {
      event.preventDefault();
      setPressed(key, true, "site-touch");
    };
    const release = (event) => {
      event.preventDefault();
      setPressed(key, false, "site-touch");
    };
    button.addEventListener("pointerdown", press);
    button.addEventListener("pointerup", release);
    button.addEventListener("pointercancel", release);
    button.addEventListener("pointerleave", release);
  });

  window.addEventListener("keydown", (event) => {
    if (isTypingTarget(event)) return;
    if (event.repeat) return;
    const key = mapKey(event.key);
    if (!key) return;
    event.preventDefault();
    setPressed(key, true, "site-keyboard");
  });

  window.addEventListener("keyup", (event) => {
    if (isTypingTarget(event)) return;
    const key = mapKey(event.key);
    if (!key) return;
    event.preventDefault();
    setPressed(key, false, "site-keyboard");
  });

  window.addEventListener("blur", () => {
    controlState.pressed = { w: false, a: false, s: false, d: false };
    controlState.mobile = { x: 0, y: 0, active: false };
    sendManualCommand("site-blur");
    document.querySelectorAll("[data-key]").forEach((button) => button.classList.remove("is-active"));
  });
}

export function iniciarJoystick() {
  const joystickBase = $("joystick-base");
  const joystickKnob = $("joystick-knob");
  const joystickReadout = $("joystick-readout");
  let activePointerId = null;
  let _stickThrottleAt = 0; // throttle: evita flood de requests no joystick

  function updateStickVisual(nx, ny) {
    const max = joystickBase.clientWidth * 0.22;
    joystickKnob.style.transform = `translate(${(nx * max).toFixed(1)}px, ${(ny * max).toFixed(1)}px)`;
    const speed = Math.round(-ny * 100);
    const steering = Math.round(nx * 100);
    joystickReadout.textContent = Math.abs(speed) < 5 && Math.abs(steering) < 5
      ? "Stick: parado"
      : `Stick: vel ${speed} dir ${steering}`;
  }

  function updateStickFromPointer(clientX, clientY) {
    const rect = joystickBase.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const maxRadius = rect.width * 0.32;
    let dx = clientX - cx;
    let dy = clientY - cy;
    const distance = Math.hypot(dx, dy);
    if (distance > maxRadius) {
      const scale = maxRadius / distance;
      dx *= scale;
      dy *= scale;
    }
    const nx = dx / maxRadius;
    const ny = dy / maxRadius;
    controlState.mobile = { x: nx, y: ny, active: true };
    updateStickVisual(nx, ny);
    // Throttle: envia no maximo 1 request a cada 80 ms
    const now = Date.now();
    if (now - _stickThrottleAt >= 80) {
      _stickThrottleAt = now;
      sendManualCommand("site-stick");
    }
  }

  function resetStick() {
    controlState.mobile = { x: 0, y: 0, active: false };
    updateStickVisual(0, 0);
    sendManualCommand("site-stick-release");
  }

  joystickBase.addEventListener("pointerdown", (event) => {
    if (!uiState.isMobileControl) return;
    event.preventDefault();
    // No autonomo por regras o stick assume o controle; no modo IA ele corrige a rede.
    if (controlState.mode === "auto") setMode("manual");
    activePointerId = event.pointerId;
    joystickBase.setPointerCapture(event.pointerId);
    updateStickFromPointer(event.clientX, event.clientY);
  });

  joystickBase.addEventListener("pointermove", (event) => {
    if (!uiState.isMobileControl) return;
    if (activePointerId !== event.pointerId) return;
    event.preventDefault();
    updateStickFromPointer(event.clientX, event.clientY);
  });

  const onStickRelease = (event) => {
    if (activePointerId !== event.pointerId) return;
    activePointerId = null;
    resetStick();
  };
  joystickBase.addEventListener("pointerup", onStickRelease);
  joystickBase.addEventListener("pointercancel", onStickRelease);
  joystickBase.addEventListener("pointerleave", onStickRelease);
}
