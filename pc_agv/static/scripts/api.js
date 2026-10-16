import { controlState, uiState } from './state.js';
import { updateFaceSelector, setFaceFormStatus } from './face.js';

let _statusInterval = 500;
let _statusFail = 0;
let _statusTimer = null;
let _controlAbort = null;

export function updateTelemetry(snapshot) {
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
		: state.kinect_error || "offline";
	
	document.getElementById("zed-status").textContent = zed.online
		? `ok | atraso ${zed.frame_age_s ?? "--"}s | ${zed.fps ?? 0} fps`
		: zed.error || "offline";
	
	document.getElementById("serial-status").textContent = arduino.connected
		? `${arduino.port || "usb"} | ${arduino.last_command || "--"}`
		: arduino.last_error || "desconectada";
	
	document.getElementById("face-status").textContent = face.known
		? `${face.label || "rosto"} | dist ${typeof face.distance === "number" ? face.distance.toFixed(3) : "--"}`
		: face.selected_name ? `procurando ${face.selected_name}` : "selecione uma pessoa";
	
	document.getElementById("fps-status").textContent = `${state.fps_rgb || 0} rgb | ${state.fps_depth || 0} depth`;
	
	applyStabilizationButton(!!kinect.stabilization_enabled);

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

export async function postJson(url, payload, signal) {
	const response = await fetch(url, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify(payload || {}),
		signal: signal || null,
	});
	return response.json();
}

export async function sendManualCommand(source) {
	if (controlState.mode !== "manual") return;
	const next = deriveManualCommand();
	if (next.speed === controlState.lastSpeed && next.steering === controlState.lastSteering) return;

	controlState.lastSpeed = next.speed;
	controlState.lastSteering = next.steering;

	if (_controlAbort) {
		try { _controlAbort.abort(); } catch (e) {}
	}
	_controlAbort = new AbortController();
	const signal = _controlAbort.signal;

	const timeoutId = setTimeout(() => {
		try { _controlAbort.abort(); } catch (e) {}
	}, 1500);
	try {
		await postJson(
			"/api/control",
			{ mode: "manual", speed: next.speed, steering: next.steering, source: source || "site-manual" },
			signal,
		);
	} catch (error) {
		if (error.name !== "AbortError") console.error(error);
	} finally {
		clearTimeout(timeoutId);
	}
}

export async function setMode(mode) {
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
		await postJson("/api/control", { mode, speed: 0, steering: 0, source: "site-mode" });
	} catch (error) {
		console.error(error);
	}
}

export async function reconnectKinect() {
	try {
		await postJson("/api/kinect/reconnect", {});
	} catch (error) {
		console.error(error);
	}
}

export async function sendTilt(angle) {
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

export async function refreshStatus() {
	const t0 = Date.now();
	const ctrl = new AbortController();
	const tid = setTimeout(() => ctrl.abort(), 2000); 
	try {
		const response = await fetch("/api/status", { cache: "no-store", signal: ctrl.signal });
		const snapshot = await response.json();
		clearTimeout(tid);
		const rtt = Date.now() - t0;
		updateTelemetry(snapshot);
		_statusFail = 0;
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

export function _updateNetBanner(rtt) {
	let banner = document.getElementById("net-banner");
	if (!banner) return;
	if (rtt === null || rtt > 800 || _statusFail > 0) {
		const msg = rtt === null
			? _statusFail >= 3 ? "⚠ Sem resposta do agv — verifique o Wi-Fi" : "⚠ Conexão lenta..."
			: `⚠ Sinal fraco (${rtt}ms)`;
		banner.textContent = msg;
		banner.hidden = false;
	} else {
		banner.hidden = true;
	}
}

export function playFaceBeep() {
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

export function fmtMeters(value) {
	return typeof value === "number" ? value.toFixed(2) + " m" : "--";
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

export function applyStabilizationButton(enabled) {
	const btn = document.getElementById("btn-stabilization");
	if (!btn) return;
	btn.textContent = enabled ? "Estabilizacao ON" : "Estabilizacao OFF";
	btn.style.background = enabled ? "#1f6f5c" : "#7a1a1a";
}

export async function setStabilization(enabled) {
	try {
		const response = await postJson("/api/kinect/stabilization", { enabled: !!enabled });
		applyStabilizationButton(!!response.enabled);
	} catch (error) {
		console.error(error);
		alert("Falha ao ajustar estabilizacao do Kinect.");
	}
}

export async function toggleStabilization() {
	const btn = document.getElementById("btn-stabilization");
	const enabledNow = btn && btn.textContent.includes("ON");
	await setStabilization(!enabledNow);
}