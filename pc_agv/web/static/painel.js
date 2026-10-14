    const controlState = {
      mode: "manual",
      pressed: { w: false, a: false, s: false, d: false },
            mobile: { x: 0, y: 0, active: false },
      lastSpeed: 0,
      lastSteering: 0,
    };

        const uiState = {
            isMobileControl: false,
            shareLink: "",
            selectedFaceName: "",
            knownFaces: [],
                        faceBeepArmed: true,
                        faceLastLabel: "",
                        audioReady: false,
                        audioContext: null,
        };

        function ensureAudioReady() {
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

        function playFaceBeep() {
            if (!uiState.audioReady || !uiState.audioContext) return;
            try {
                const ctx = uiState.audioContext;
                if (ctx.state === "suspended") {
                    ctx.resume().catch(() => {});
                }
                const now = ctx.currentTime;
                const master = ctx.createGain();
                master.gain.setValueAtTime(0.0001, now);
                master.gain.exponentialRampToValueAtTime(0.12, now + 0.015);
                master.gain.exponentialRampToValueAtTime(0.0001, now + 0.22);
                master.connect(ctx.destination);

                const toneA = ctx.createOscillator();
                toneA.type = "sine";
                toneA.frequency.setValueAtTime(900, now);
                toneA.frequency.exponentialRampToValueAtTime(1250, now + 0.10);
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

    function fmtMeters(value) {
      return typeof value === "number" ? value.toFixed(2) + " m" : "--";
    }

    function mapKey(raw) {
      const key = (raw || "").toLowerCase();
      if (key === "w" || key === "arrowup") return "w";
      if (key === "a" || key === "e" || key === "arrowleft") return "a";
      if (key === "s" || key === "arrowdown") return "s";
      if (key === "d" || key === "arrowright") return "d";
      return null;
    }

        function isTypingTarget(event) {
            const element = event && event.target;
            if (!element) return false;
            const tag = (element.tagName || "").toLowerCase();
            return !!element.isContentEditable || tag === "input" || tag === "textarea" || tag === "select";
        }

    function deriveManualCommand() {
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

    async function postJson(url, payload, signal) {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload || {}),
        signal: signal || null,
      });
      return response.json();
    }

        function updateFaceSelector(face) {
            const select = document.getElementById("face-select");
            if (!select) return;

            const names = Array.isArray(face.names) ? face.names : [];
            const selectedName = typeof face.selected_name === "string" ? face.selected_name : uiState.selectedFaceName;
            uiState.knownFaces = names.slice();
            uiState.selectedFaceName = selectedName || "";

            const currentOptions = [""];
            names.forEach((name) => currentOptions.push(name));
            const previousValue = select.value;
            const shouldRebuild = select.options.length !== currentOptions.length || currentOptions.some((value, index) => (select.options[index] || {}).value !== value);

            if (shouldRebuild) {
                select.innerHTML = "";
                const emptyOption = document.createElement("option");
                emptyOption.value = "";
                emptyOption.textContent = "Nenhuma pessoa selecionada";
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
            if (!nextValue && previousValue && !names.includes(previousValue)) {
                uiState.selectedFaceName = "";
            }
        }

        function setFaceFormStatus(message) {
            const status = document.getElementById("face-form-status");
            if (status) status.textContent = message;
        }

        async function selectFace(name) {
            try {
                const response = await postJson("/api/face/select", { name: name || "" });
                if (!response.ok) {
                    alert("Nao foi possivel selecionar esse rosto.");
                    return;
                }
                updateFaceSelector(response.face || {});
                setFaceFormStatus(response.face && response.face.selected_name
                    ? `Procurando ${response.face.selected_name} na webcam ZED.`
                    : "Nenhuma pessoa selecionada para a webcam ZED.");
            } catch (error) {
                console.error(error);
                alert("Falha ao selecionar o rosto.");
            }
        }

    // Cancela qualquer fetch de controle em voo antes de mandar novo.
    let _controlAbort = null;
    let _controlPending = 0;

    async function sendManualCommand(source, keepalive) {
      if (controlState.mode !== "manual") return;
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

      // Cancela request anterior que ainda não terminou
      if (_controlAbort) { try { _controlAbort.abort(); } catch(e) {} }
      _controlAbort = new AbortController();
      const signal = _controlAbort.signal;

      // Timeout de 1.5 s para não acumular requests em fila
      const timeoutId = setTimeout(() => { try { _controlAbort.abort(); } catch(e) {} }, 1500);
      _controlPending += 1;
      try {
        await postJson("/api/control", {
          mode: "manual",
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
    setInterval(() => sendManualCommand("site-keepalive", true), 500);

    async function setMode(mode) {
      controlState.mode = mode;
      if (mode === "auto") {
        controlState.pressed = { w: false, a: false, s: false, d: false };
        controlState.lastSpeed = 0;
        controlState.lastSteering = 0;
      }

      document.querySelectorAll(".mode-button").forEach((button) => {
        button.classList.toggle("is-selected", button.dataset.mode === mode);
      });

      try {
        await postJson("/api/control", {
          mode,
          speed: 0,
          steering: 0,
          source: "site-mode",
        });
      } catch (error) {
        console.error(error);
      }
    }

    function setPressed(key, value, source) {
            if (uiState.isMobileControl) return;
      if (!key) return;
      controlState.pressed[key] = value;
      sendManualCommand(source);
      document.querySelectorAll("[data-key]").forEach((button) => {
        button.classList.toggle("is-active", controlState.pressed[button.dataset.key]);
      });
    }

    async function emergencyStop() {
      controlState.mode = "manual";
      controlState.pressed = { w: false, a: false, s: false, d: false };
            controlState.mobile = { x: 0, y: 0, active: false };
      controlState.lastSpeed = 0;
      controlState.lastSteering = 0;
      document.querySelectorAll("[data-key]").forEach((button) => button.classList.remove("is-active"));
      document.querySelectorAll(".mode-button").forEach((button) => {
        button.classList.toggle("is-selected", button.dataset.mode === "manual");
      });
      try {
        await postJson("/api/stop", {});
      } catch (error) {
        console.error(error);
      }
    }

        function detectMobileControl() {
            const coarsePointer = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
            const mobileUA = /android|iphone|ipad|ipod|mobile/i.test(navigator.userAgent || "");
            return coarsePointer || mobileUA;
        }

        function setupControlSurface() {
            uiState.isMobileControl = detectMobileControl();
            const keyboardPad = document.getElementById("keyboard-pad");
            const mobileJoystick = document.getElementById("mobile-joystick");
            const hint = document.getElementById("control-hint");
            keyboardPad.hidden = uiState.isMobileControl;
            mobileJoystick.hidden = !uiState.isMobileControl;
            hint.textContent = uiState.isMobileControl
                ? "Stick virtual ativo. Arraste o centro para mover o agv e solte para parar."
                : "No PC, use W A S D, setas ou E como esquerda.";
            if (uiState.isMobileControl) {
                controlState.pressed = { w: false, a: false, s: false, d: false };
                document.querySelectorAll("[data-key]").forEach((button) => button.classList.remove("is-active"));
            } else {
                controlState.mobile = { x: 0, y: 0, active: false };
            }
            sendManualCommand("site-surface");
        }

        function renderShareLink() {
            const label = document.getElementById("access-link");
            label.textContent = "Link para outro dispositivo: " + (uiState.shareLink || "indisponivel");
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
            if (!uiState.shareLink) {
                await resolveShareLink();
            }
            const button = document.getElementById("btn-copy-link");
            const originalText = button.textContent;
            try {
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    await navigator.clipboard.writeText(uiState.shareLink);
                } else {
                    const tempInput = document.createElement("textarea");
                    tempInput.value = uiState.shareLink;
                    tempInput.style.position = "fixed";
                    tempInput.style.opacity = "0";
                    document.body.appendChild(tempInput);
                    tempInput.select();
                    document.execCommand("copy");
                    document.body.removeChild(tempInput);
                }
                button.textContent = "Copiado";
            } catch (error) {
                console.error(error);
                button.textContent = "Falhou";
            }
            setTimeout(() => {
                button.textContent = originalText;
            }, 1200);
        }

    async function reconnectKinect() {
      try {
        await postJson("/api/kinect/reconnect", {});
      } catch (error) {
        console.error(error);
      }
    }

    async function sendTilt(angle) {
      try {
        const response = await postJson("/api/kinect/tilt", { angle });
        if (!response.ok) {
          alert("Nao foi possivel mover o tilt do Kinect agora.");
        }
      } catch (error) {
        console.error(error);
        alert("Falha ao enviar comando de tilt.");
      }
    }

        function applyStabilizationButton(enabled) {
            const btn = document.getElementById("btn-stabilization");
            if (!btn) return;
            btn.textContent = enabled ? "Estabilizacao ON" : "Estabilizacao OFF";
            btn.style.background = enabled ? "#1f6f5c" : "#7a1a1a";
        }

        async function setStabilization(enabled) {
            try {
                const response = await postJson("/api/kinect/stabilization", { enabled: !!enabled });
                applyStabilizationButton(!!response.enabled);
            } catch (error) {
                console.error(error);
                alert("Falha ao ajustar estabilizacao do Kinect.");
            }
        }

        async function toggleStabilization() {
            const btn = document.getElementById("btn-stabilization");
            const enabledNow = btn && btn.textContent.includes("ON");
            await setStabilization(!enabledNow);
        }

        async function registerFace() {
            const button = document.getElementById("btn-face-register");
            const nameInput = document.getElementById("face-name-input");
            const fileInput = document.getElementById("face-file-input");
            const faceName = (nameInput.value || "").trim();
            const imageFile = fileInput.files && fileInput.files[0];

            if (!faceName) {
                alert("Digite o nome da pessoa antes de registrar.");
                nameInput.focus();
                return;
            }
            if (!imageFile) {
                alert("Escolha uma imagem para cadastrar o rosto.");
                fileInput.focus();
                return;
            }

            const form = new FormData();
            form.append("name", faceName);
            form.append("image", imageFile);

            button.disabled = true;
            setFaceFormStatus("Enviando imagem e cadastrando rosto...");
            try {
                const response = await fetch("/api/face/register_face", {
                    method: "POST",
                    body: form,
                });
                const payload = await response.json();
                if (!response.ok || !payload.ok) {
                    const errorCode = payload && payload.error ? payload.error : "erro desconhecido";
                    alert("Nao foi possivel cadastrar o rosto: " + errorCode);
                    setFaceFormStatus("Falha ao cadastrar rosto.");
                    return;
                }

                nameInput.value = "";
                fileInput.value = "";
                updateFaceSelector(payload.face || {});
                setFaceFormStatus(`Rosto de ${payload.name} salvo em ${payload.image_path}.`);
            } catch (error) {
                console.error(error);
                setFaceFormStatus("Falha ao cadastrar rosto.");
                alert("Falha ao cadastrar rosto.");
            } finally {
                button.disabled = false;
            }
        }

    function updateTelemetry(snapshot) {
      const state = snapshot.state || {};
      const agv = snapshot.agv || {};
      const autopilot = snapshot.autopilot || {};
      const arduino = snapshot.arduino || {};
            const kinect = snapshot.kinect || {};
                        const zed = snapshot.zed || {};
      const face = snapshot.face || {};

      document.getElementById("chip-mode").textContent = "modo " + (agv.mode || "manual");
      document.getElementById("chip-kinect").textContent = state.kinect_ok ? "kinect online" : "kinect offline";
            document.getElementById("chip-zed").textContent = zed.online ? "zed online" : "zed offline";
      document.getElementById("chip-serial").textContent = arduino.connected ? "serial conectada" : "serial desconectada";
      if (!face.template_loaded) {
        document.getElementById("chip-face").textContent = "face sem cadastro";
      } else if (face.known && face.label) {
        document.getElementById("chip-face").textContent = face.label;
            } else if (face.selected_name) {
                document.getElementById("chip-face").textContent = `procurando ${face.selected_name}`;
      } else {
                document.getElementById("chip-face").textContent = "face aguardando selecao";
      }

            updateFaceSelector(face);

      document.getElementById("distance").textContent = fmtMeters(state.distance_m);
      document.getElementById("left-clearance").textContent = fmtMeters(state.left_clearance_m);
      document.getElementById("center-clearance").textContent = fmtMeters(state.center_clearance_m);
      document.getElementById("right-clearance").textContent = fmtMeters(state.right_clearance_m);
      document.getElementById("active-command").textContent = `${agv.active_speed ?? 0} / ${agv.active_steering ?? 0}`;
      document.getElementById("auto-reason").textContent = autopilot.last_reason || "--";
      document.getElementById("kinect-status").textContent = state.kinect_ok
        ? `ok | atraso ${state.frame_age_s ?? "--"}s | tilt ${state.tilt_deg ?? "--"} | alvo ${state.tilt_target_deg ?? "--"}`
        : (state.kinect_error || "offline");
            document.getElementById("zed-status").textContent = zed.online
                ? `ok | atraso ${zed.frame_age_s ?? "--"}s | ${zed.fps ?? 0} fps`
                : (zed.error || "offline");
      document.getElementById("serial-status").textContent = arduino.connected ? `${arduino.port || "usb"} | ${arduino.last_command || "--"}` : (arduino.last_error || "desconectada");
            document.getElementById("face-status").textContent = face.known
                ? `${face.label || "rosto"} | dist ${typeof face.distance === "number" ? face.distance.toFixed(3) : "--"}`
                : (face.selected_name ? `procurando ${face.selected_name}` : "selecione uma pessoa");
      document.getElementById("fps-status").textContent = `${state.fps_rgb || 0} rgb | ${state.fps_depth || 0} depth`;
    applyStabilizationButton(!!kinect.stabilization_enabled);

            document.getElementById("hud-chip-mode").textContent = "modo " + (agv.mode || "manual");
            document.getElementById("hud-chip-kinect").textContent = state.kinect_ok ? "kinect online" : "kinect offline";
            document.getElementById("hud-chip-kinect").classList.toggle("hud-warn", !state.kinect_ok);
            document.getElementById("hud-chip-serial").textContent = arduino.connected ? "serial conectada" : "serial desconectada";
            document.getElementById("hud-chip-serial").classList.toggle("hud-warn", !arduino.connected);
            document.getElementById("hud-distance").textContent = fmtMeters(state.distance_m);
            document.getElementById("hud-left").textContent = fmtMeters(state.left_clearance_m);
            document.getElementById("hud-center").textContent = fmtMeters(state.center_clearance_m);
            document.getElementById("hud-right").textContent = fmtMeters(state.right_clearance_m);
            document.getElementById("hud-cmd").textContent = `${agv.active_speed ?? 0} / ${agv.active_steering ?? 0}`;
            document.getElementById("hud-fps").textContent = `${state.fps_rgb || 0}`;

            if (!face.template_loaded) {
                setFaceFormStatus("Cadastre uma imagem com nome para procurar alguem na ZED.");
            } else if (face.known && face.label) {
                setFaceFormStatus(`Rosto encontrado na ZED: ${face.label}.`);
            } else if (face.selected_name) {
                setFaceFormStatus(`Procurando ${face.selected_name} na webcam ZED.`);
            }

            const faceKnown = !!(face && face.known && face.label);
            const faceLabel = faceKnown ? String(face.label) : "";
            if (faceKnown && (uiState.faceBeepArmed || uiState.faceLastLabel !== faceLabel)) {
                playFaceBeep();
                uiState.faceBeepArmed = false;
            }
            if (!faceKnown) {
                uiState.faceBeepArmed = true;
            }
            uiState.faceLastLabel = faceLabel;

      if ((agv.mode || "manual") !== controlState.mode) {
        controlState.mode = agv.mode || "manual";
        document.querySelectorAll(".mode-button").forEach((button) => {
          button.classList.toggle("is-selected", button.dataset.mode === controlState.mode);
        });
      }
    }

    async function refreshStatus() {
            const t0 = Date.now();
            const ctrl = new AbortController();
            const tid = setTimeout(() => ctrl.abort(), 2000); // timeout 2s
            try {
                const response = await fetch("/api/status", { cache: "no-store", signal: ctrl.signal });
                const snapshot = await response.json();
                clearTimeout(tid);
                const rtt = Date.now() - t0;
                updateTelemetry(snapshot);
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

        let _statusInterval = 500;
        let _statusFail = 0;
        let _statusTimer = null;

        function _updateNetBanner(rtt) {
            let banner = document.getElementById("net-banner");
            if (!banner) return;
            if (rtt === null || rtt > 800 || _statusFail > 0) {
                const msg = rtt === null
                    ? (_statusFail >= 3 ? "⚠ Sem resposta do agv — verifique o Wi-Fi" : "⚠ Conexão lenta...")
                    : `⚠ Sinal fraco (${rtt}ms)`;
                banner.textContent = msg;
                banner.hidden = false;
            } else {
                banner.hidden = true;
            }
        }

    document.getElementById("btn-manual").addEventListener("click", () => setMode("manual"));
    document.getElementById("btn-auto").addEventListener("click", () => setMode("auto"));
    document.getElementById("btn-stop").addEventListener("click", emergencyStop);
    document.getElementById("btn-reconnect").addEventListener("click", reconnectKinect);
    document.getElementById("btn-stabilization").addEventListener("click", toggleStabilization);
    document.getElementById("btn-tilt-up").addEventListener("click", () => sendTilt(12));
    document.getElementById("btn-tilt-center").addEventListener("click", () => sendTilt(0));
    document.getElementById("btn-tilt-down").addEventListener("click", () => sendTilt(-12));
        document.getElementById("btn-face-register").addEventListener("click", registerFace);
        document.getElementById("face-select").addEventListener("change", (event) => {
            selectFace(event.target.value);
        });
    document.getElementById("btn-copy-link").addEventListener("click", copyShareLink);

    window.addEventListener("pointerdown", ensureAudioReady, { passive: true });
    window.addEventListener("keydown", ensureAudioReady);

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

        const joystickBase = document.getElementById("joystick-base");
        const joystickKnob = document.getElementById("joystick-knob");
        const joystickReadout = document.getElementById("joystick-readout");
        let activePointerId = null;
        let _stickThrottleAt = 0;   // throttle: evita flood de requests no joystick

        function updateStickVisual(nx, ny) {
            const max = joystickBase.clientWidth * 0.22;
            joystickKnob.style.transform = `translate(${(nx * max).toFixed(1)}px, ${(ny * max).toFixed(1)}px)`;
            const speed = Math.round(-ny * 100);
            const steering = Math.round(nx * 100);
            if (Math.abs(speed) < 5 && Math.abs(steering) < 5) {
                joystickReadout.textContent = "Stick: parado";
            } else {
                joystickReadout.textContent = `Stick: vel ${speed} dir ${steering}`;
            }
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
            // Throttle: envia no máximo 1 request a cada 80 ms
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
            if (controlState.mode !== "manual") {
                setMode("manual");
            }
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

        // ── Visualizador HUD em tela cheia ───────────────────────────────
        const hudOverlay = document.getElementById("hud-overlay");
        const hudMainFrame = document.getElementById("hud-main-frame");
        const hudPipFrame = document.getElementById("hud-pip-frame");
        const hudJoystickWrap = document.getElementById("hud-joystick-wrap");
        const btnHudToggle = document.getElementById("btn-hud-toggle");
        const btnHudExit = document.getElementById("btn-hud-exit");
        const hudHint = document.getElementById("hud-hint");

        const rgbStream = document.getElementById("rgb-stream");
        const depthStream = document.getElementById("depth-stream");
        const hudMobileJoystick = document.getElementById("mobile-joystick");
        const rgbHome = { parent: rgbStream.parentElement, next: rgbStream.nextElementSibling };
        const depthHome = { parent: depthStream.parentElement, next: depthStream.nextElementSibling };
        const joystickHome = { parent: hudMobileJoystick.parentElement, next: hudMobileJoystick.nextElementSibling };

        function requestRealFullscreen(el) {
            const request = el.requestFullscreen || el.webkitRequestFullscreen || el.msRequestFullscreen;
            if (!request) return;
            try { request.call(el); } catch (error) { /* segue so com o fallback em CSS */ }
        }

        function exitRealFullscreen() {
            const exit = document.exitFullscreen || document.webkitExitFullscreen || document.msExitFullscreen;
            if (document.fullscreenElement && exit) {
                try { exit.call(document); } catch (error) { /* ignora */ }
            }
        }

        function openHud() {
            hudMainFrame.appendChild(rgbStream);
            hudPipFrame.appendChild(depthStream);
            hudOverlay.hidden = false;
            document.body.classList.add("hud-active");
            if (uiState.isMobileControl) {
                hudJoystickWrap.appendChild(hudMobileJoystick);
                hudJoystickWrap.hidden = false;
                hudMobileJoystick.hidden = false;
                hudHint.hidden = true;
            } else {
                hudJoystickWrap.hidden = true;
                hudHint.hidden = false;
            }
            requestRealFullscreen(hudOverlay);
        }

        function closeHud() {
            if (hudOverlay.hidden) return;
            hudOverlay.hidden = true;
            document.body.classList.remove("hud-active");
            rgbHome.parent.insertBefore(rgbStream, rgbHome.next);
            depthHome.parent.insertBefore(depthStream, depthHome.next);
            joystickHome.parent.insertBefore(hudMobileJoystick, joystickHome.next);
            hudMobileJoystick.hidden = !uiState.isMobileControl;
            exitRealFullscreen();
        }

        btnHudToggle.addEventListener("click", openHud);
        btnHudExit.addEventListener("click", closeHud);
        document.addEventListener("fullscreenchange", () => {
            if (!document.fullscreenElement) closeHud();
        });
        window.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && !hudOverlay.hidden) closeHud();
        });

        setupControlSurface();
        resolveShareLink();
        window.addEventListener("resize", setupControlSurface);

    refreshStatus();
    _statusTimer = setTimeout(refreshStatus, 500);

    // ── Configuracoes ──────────────────────────────────────────────────

    // Modo escuro
    const darkToggle = document.getElementById("dark-mode-toggle");
    function applyDark(enabled) {
      document.body.classList.toggle("dark", enabled);
      darkToggle.checked = enabled;
      localStorage.setItem("agv_dark", enabled ? "1" : "0");
    }
    applyDark(localStorage.getItem("agv_dark") === "1");
    darkToggle.addEventListener("change", () => applyDark(darkToggle.checked));

    // Velocidade maxima
    const speedSlider = document.getElementById("speed-limit-slider");
    const speedDisplay = document.getElementById("speed-limit-display");
    let speedSendTimer = null;

    async function sendSpeedLimit(pct) {
      try {
        await postJson("/api/settings", { speed_limit_pct: pct });
      } catch (e) {
        console.error(e);
      }
    }

    function applySpeedLimit(pct) {
      speedDisplay.textContent = pct + "%";
      speedSlider.value = pct;
      localStorage.setItem("agv_speed_limit", pct);
    }

    // Carregar valor salvo
    const savedSpeed = parseInt(localStorage.getItem("agv_speed_limit") ?? "100", 10);
    applySpeedLimit(isNaN(savedSpeed) ? 100 : savedSpeed);
    sendSpeedLimit(speedSlider.value);

    speedSlider.addEventListener("input", () => {
      const pct = parseInt(speedSlider.value, 10);
      applySpeedLimit(pct);
      clearTimeout(speedSendTimer);
      speedSendTimer = setTimeout(() => sendSpeedLimit(pct), 300);
    });
  
