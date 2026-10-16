// Reconhecimento facial: selecao da pessoa procurada na ZED, cadastro por foto e bipe.

import { postJson } from "./api.js";
import { uiState } from "./estado.js";
import { $, aviso } from "./util.js";

const ERROS_CADASTRO = {
  name_required: "Digite o nome da pessoa.",
  image_required: "Escolha uma foto.",
  empty_image: "A foto está vazia.",
  invalid_image: "O arquivo não é uma imagem válida.",
  no_face_detected: "Nenhum rosto encontrado na foto. Use uma foto de frente e bem iluminada.",
  failed_to_save_image: "O AGV não conseguiu salvar a foto.",
  face_database_unreadable: "O banco de rostos do AGV está ilegível; o cadastro foi recusado para não apagá-lo.",
  file_too_large: "A foto é grande demais.",
};

// ── Bipe ────────────────────────────────────────────────────────────
export function ensureAudioReady() {
  if (uiState.audioReady) return;
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  if (!AudioCtx) return;
  try {
    uiState.audioContext = new AudioCtx();
    uiState.audioReady = true;
  } catch (error) {
    console.warn("AudioContext indisponivel", error);
  }
}

export function playFaceBeep() {
  if (!uiState.som || !uiState.audioReady || !uiState.audioContext) return;
  try {
    const ctx = uiState.audioContext;
    if (ctx.state === "suspended") ctx.resume().catch(() => {});
    const now = ctx.currentTime;
    const master = ctx.createGain();
    master.gain.setValueAtTime(0.0001, now);
    master.gain.exponentialRampToValueAtTime(0.12, now + 0.015);
    master.gain.exponentialRampToValueAtTime(0.0001, now + 0.22);
    master.connect(ctx.destination);

    const toneA = ctx.createOscillator();
    toneA.type = "sine";
    toneA.frequency.setValueAtTime(900, now);
    toneA.frequency.exponentialRampToValueAtTime(1250, now + 0.1);
    toneA.connect(master);
    toneA.start(now);
    toneA.stop(now + 0.11);

    const toneB = ctx.createOscillator();
    toneB.type = "triangle";
    toneB.frequency.setValueAtTime(1250, now + 0.11);
    toneB.frequency.exponentialRampToValueAtTime(1600, now + 0.22);
    toneB.connect(master);
    toneB.start(now + 0.11);
    toneB.stop(now + 0.23);
  } catch (error) {
    console.warn("Falha ao tocar beep de reconhecimento", error);
  }
}

// ── Selecao ─────────────────────────────────────────────────────────
export function updateFaceSelector(face) {
  const select = $("face-select");
  if (!select) return;

  const names = Array.isArray(face.names) ? face.names : [];
  const selectedName = typeof face.selected_name === "string" ? face.selected_name : uiState.selectedFaceName;
  uiState.knownFaces = names.slice();
  uiState.selectedFaceName = selectedName || "";

  const currentOptions = [""].concat(names);
  const previousValue = select.value;
  const shouldRebuild = select.options.length !== currentOptions.length
    || currentOptions.some((value, index) => (select.options[index] || {}).value !== value);

  if (shouldRebuild) {
    select.innerHTML = "";
    const emptyOption = document.createElement("option");
    emptyOption.value = "";
    emptyOption.textContent = names.length ? "Ninguém selecionado" : "Nenhum rosto cadastrado";
    select.appendChild(emptyOption);
    names.forEach((name) => {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      select.appendChild(option);
    });
  }

  const nextValue = names.includes(uiState.selectedFaceName) ? uiState.selectedFaceName : "";
  select.value = nextValue;
  if (!nextValue && previousValue && !names.includes(previousValue)) uiState.selectedFaceName = "";
}

export function setFaceFormStatus(message) {
  const status = $("face-form-status");
  if (status && status.textContent !== message) status.textContent = message;
}

// Atualiza o quadro de deteccao facial a partir do status do AGV.
export function atualizarEstadoRosto(face) {
  const caixa = $("estado-rosto");
  let estado = "vazio";
  if (!face.template_loaded) {
    setFaceFormStatus("Nenhum rosto cadastrado. Cadastre uma foto em Configurações.");
  } else if (face.known && face.label) {
    estado = "encontrado";
    setFaceFormStatus(`Rosto encontrado na ZED: ${face.label}.`);
  } else if (face.selected_name) {
    estado = "procurando";
    setFaceFormStatus(`Procurando ${face.selected_name} na câmera ZED...`);
  } else {
    setFaceFormStatus("Escolha a pessoa que a câmera ZED deve procurar.");
  }
  if (caixa) caixa.dataset.estado = estado;

  const encontrado = estado === "encontrado";
  const rotulo = encontrado ? String(face.label) : "";
  if (encontrado && (uiState.faceBeepArmed || uiState.faceLastLabel !== rotulo)) {
    playFaceBeep();
    uiState.faceBeepArmed = false;
  }
  if (!encontrado) uiState.faceBeepArmed = true;
  uiState.faceLastLabel = rotulo;
}

export async function selectFace(name) {
  try {
    const response = await postJson("/api/face/select", { name: name || "" });
    if (!response.ok) {
      aviso("Não foi possível selecionar esse rosto.", "erro");
      return;
    }
    updateFaceSelector(response.face || {});
    aviso(response.face && response.face.selected_name
      ? `Procurando ${response.face.selected_name} na câmera ZED.`
      : "Busca de rosto desligada.", "ok");
  } catch (error) {
    console.error(error);
    aviso("Falha ao selecionar o rosto: sem resposta do AGV.", "erro");
  }
}

// ── Cadastro ────────────────────────────────────────────────────────
function mostrarPrevia(arquivo) {
  const previa = $("previa-foto");
  const texto = $("texto-foto");
  if (previa.src) URL.revokeObjectURL(previa.src);
  if (!arquivo) {
    previa.removeAttribute("src");
    previa.hidden = true;
    texto.hidden = false;
    return;
  }
  previa.src = URL.createObjectURL(arquivo);
  previa.hidden = false;
  texto.hidden = true;
}

export async function registerFace() {
  const button = $("btn-face-register");
  const nameInput = $("face-name-input");
  const fileInput = $("face-file-input");
  const faceName = (nameInput.value || "").trim();
  const imageFile = fileInput.files && fileInput.files[0];

  if (!faceName) {
    aviso("Digite o nome da pessoa antes de registrar.", "erro");
    nameInput.focus();
    return;
  }
  if (!imageFile) {
    aviso("Escolha uma foto para cadastrar o rosto.", "erro");
    fileInput.focus();
    return;
  }

  const form = new FormData();
  form.append("name", faceName);
  form.append("image", imageFile);

  button.disabled = true;
  button.classList.add("ocupado");
  try {
    const response = await fetch("/api/face/register_face", { method: "POST", body: form });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      const codigo = payload && payload.error ? payload.error : "erro desconhecido";
      aviso("Não foi possível cadastrar o rosto. " + (ERROS_CADASTRO[codigo] || codigo), "erro", 6000);
      return;
    }
    nameInput.value = "";
    fileInput.value = "";
    mostrarPrevia(null);
    updateFaceSelector(payload.face || {});
    aviso(`Rosto de ${payload.name} cadastrado e selecionado.`, "ok");
  } catch (error) {
    console.error(error);
    aviso("Falha ao cadastrar rosto: sem resposta do AGV.", "erro");
  } finally {
    button.disabled = false;
    button.classList.remove("ocupado");
  }
}

export function iniciarRostos() {
  const area = $("area-foto");
  const fileInput = $("face-file-input");

  $("btn-face-register").addEventListener("click", registerFace);
  $("face-select").addEventListener("change", (event) => selectFace(event.target.value));
  fileInput.addEventListener("change", () => mostrarPrevia(fileInput.files && fileInput.files[0]));

  ["dragenter", "dragover"].forEach((tipo) => area.addEventListener(tipo, (event) => {
    event.preventDefault();
    area.classList.add("arrastando");
  }));
  ["dragleave", "drop"].forEach((tipo) => area.addEventListener(tipo, () => area.classList.remove("arrastando")));
  area.addEventListener("drop", (event) => {
    event.preventDefault();
    const arquivos = event.dataTransfer && event.dataTransfer.files;
    if (!arquivos || !arquivos.length) return;
    fileInput.files = arquivos;
    mostrarPrevia(arquivos[0]);
  });
}
