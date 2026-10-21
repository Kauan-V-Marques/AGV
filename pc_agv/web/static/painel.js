// Ponto de entrada do painel do AGV (modulo ES nativo, sem etapa de build).
// Cada assunto fica em static/js/: controle, videos, telemetria, rostos, ajustes...

import { iniciarKinect, iniciarLink, iniciarSom, iniciarTema, iniciarVelocidade } from "./js/ajustes.js";
import { desenharCurva, iniciarAprendizado } from "./js/aprendizado.js";
import {
  emergencyStop,
  iniciarJoystick,
  iniciarKeepalive,
  iniciarTeclado,
  setMode,
  setupControlSurface,
} from "./js/controle.js";
import { desenharGraficos, iniciarSetores } from "./js/graficos.js";
import { iniciarNavegacao } from "./js/navegacao.js";
import { ensureAudioReady, iniciarRostos } from "./js/rostos.js";
import { iniciarTelemetria } from "./js/telemetria.js";
import { $ } from "./js/util.js";
import { desenharMapa, iniciarMapa } from "./js/mapa.js";
import { desenharRadar, iniciarRadar } from "./js/radar.js";
import { iniciarHud, iniciarVideos, tirarFoto } from "./js/videos.js";

iniciarTema();
iniciarSetores();
iniciarSom();
iniciarVideos();
iniciarNavegacao();
iniciarHud({ aoParar: emergencyStop });

// Conducao
$("btn-manual").addEventListener("click", () => setMode("manual"));
$("btn-auto").addEventListener("click", () => setMode("auto"));
$("btn-ia").addEventListener("click", () => setMode("ia"));
$("btn-stop").addEventListener("click", emergencyStop);
iniciarTeclado();
iniciarJoystick();
iniciarKeepalive();
setupControlSurface();

// Configuracoes, rostos e camera
iniciarVelocidade();
iniciarLink();
iniciarKinect();
iniciarRostos();
iniciarAprendizado();
iniciarMapa();
iniciarRadar();
$("btn-foto").addEventListener("click", tirarFoto);

// O navegador so libera o audio depois de uma interacao do usuario.
window.addEventListener("pointerdown", ensureAudioReady, { passive: true });
window.addEventListener("keydown", ensureAudioReady);

let redimensionar = null;
window.addEventListener("resize", () => {
  setupControlSurface();
  clearTimeout(redimensionar);
  redimensionar = setTimeout(() => {
    desenharGraficos();
    desenharCurva();
    desenharMapa();
    desenharRadar();
  }, 120);
});
window.addEventListener("agv:tema", () => {
  desenharGraficos();
  desenharCurva();
  desenharMapa();
  desenharRadar();
});

iniciarTelemetria();
