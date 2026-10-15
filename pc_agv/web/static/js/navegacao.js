// Navegacao: menu lateral, secoes e menu do celular.

import { desenharGraficos } from "./graficos.js";
import { $, gravarPreferencia, lerPreferencia } from "./util.js";
import { posicionarVideos } from "./videos.js";

const SECOES = ["painel", "cameras", "telemetria", "configuracoes"];

function fecharMenu() {
  document.body.classList.remove("menu-aberto");
}

export function mostrarSecao(nome) {
  if (!SECOES.includes(nome)) nome = "painel";
  document.querySelectorAll(".secao").forEach((secao) => {
    const ativa = secao.id === `secao-${nome}`;
    secao.hidden = !ativa;
    if (ativa) {
      secao.classList.remove("ativa");
      void secao.offsetWidth; // reinicia a animacao de entrada
      secao.classList.add("ativa");
      $("titulo-secao").textContent = secao.dataset.titulo;
      document.title = `AGV · ${secao.dataset.titulo}`;
    } else {
      secao.classList.remove("ativa");
    }
  });
  document.querySelectorAll(".menu-item[data-secao]").forEach((item) => {
    const ativo = item.dataset.secao === nome;
    item.classList.toggle("ativo", ativo);
    if (ativo) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  });
  gravarPreferencia("agv_secao", nome);
  if (location.hash !== `#${nome}`) history.replaceState(null, "", `#${nome}`);
  posicionarVideos();
  requestAnimationFrame(desenharGraficos);
  fecharMenu();
}

export function iniciarNavegacao() {
  document.querySelectorAll(".menu-item[data-secao]").forEach((item) => {
    item.addEventListener("click", () => mostrarSecao(item.dataset.secao));
  });
  $("btn-menu").addEventListener("click", () => document.body.classList.toggle("menu-aberto"));
  $("lateral-fundo").addEventListener("click", fecharMenu);
  window.addEventListener("hashchange", () => mostrarSecao(location.hash.slice(1)));

  const inicial = location.hash.slice(1) || lerPreferencia("agv_secao", "painel");
  mostrarSecao(inicial);
}
