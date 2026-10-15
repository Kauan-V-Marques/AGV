// Utilitarios do painel: DOM, formatacao, niveis de distancia, avisos e preferencias.

export const $ = (id) => document.getElementById(id);

export function fmtMetros(valor) {
  return typeof valor === "number" ? valor.toFixed(2) + " m" : "--";
}

// Mesmas faixas do modo autonomo (pc_agv/controle.py).
export function nivelDistancia(distancia) {
  if (typeof distancia !== "number") return "nenhum";
  if (distancia < 0.35) return "perigo";
  if (distancia < 0.6) return "alerta";
  if (distancia < 0.95) return "atencao";
  return "livre";
}

export function limitar(valor, minimo, maximo) {
  return Math.max(minimo, Math.min(maximo, valor));
}

// Troca o texto so quando muda e marca a mudanca (pisca na cor de destaque).
export function definirTexto(id, texto, piscar = false) {
  const el = $(id);
  if (!el || el.textContent === texto) return;
  el.textContent = texto;
  if (piscar) {
    el.classList.add("mudou");
    clearTimeout(el._piscar);
    el._piscar = setTimeout(() => el.classList.remove("mudou"), 600);
  }
}

// localStorage pode falhar (aba anonima, bloqueio): o painel funciona sem ele.
export function lerPreferencia(chave, padrao) {
  try {
    const valor = localStorage.getItem(chave);
    return valor === null ? padrao : valor;
  } catch (erro) {
    return padrao;
  }
}

export function gravarPreferencia(chave, valor) {
  try {
    localStorage.setItem(chave, valor);
  } catch (erro) {
    /* sem armazenamento: so nao lembra a preferencia */
  }
}

// Avisos (toasts) no canto da tela, no lugar dos alert() do navegador.
export function aviso(mensagem, tipo = "info", duracaoMs = 3800) {
  const caixa = $("avisos");
  if (!caixa) return;
  const item = document.createElement("div");
  item.className = "aviso " + tipo;
  item.setAttribute("role", tipo === "erro" ? "alert" : "status");
  const texto = document.createElement("p");
  texto.textContent = mensagem;
  item.appendChild(texto);
  caixa.appendChild(item);
  while (caixa.children.length > 4) caixa.firstElementChild.remove();
  setTimeout(() => {
    item.classList.add("saindo");
    item.addEventListener("animationend", () => item.remove(), { once: true });
    setTimeout(() => item.remove(), 600);
  }, duracaoMs);
}
