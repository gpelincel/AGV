import { controlState, uiState } from './state.js';
import { sendManualCommand, setMode, postJson, reconnectKinect, sendTilt, refreshStatus, toggleStabilization } from './api.js';
import { registerFace, selectFace } from './face.js';
import './joystick.js';

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
	return (
		!!element.isContentEditable ||
		tag === "input" ||
		tag === "textarea" ||
		tag === "select"
	);
}

function setPressed(key, value, source) {
	if (uiState.isMobileControl) return;
	if (!key) return;
	controlState.pressed[key] = value;
	sendManualCommand(source);
	document.querySelectorAll("[data-key]").forEach((button) => {
		button.classList.toggle(
			"is-active",
			controlState.pressed[button.dataset.key],
		);
	});
}

async function emergencyStop() {
	controlState.mode = "manual";
	controlState.pressed = { w: false, a: false, s: false, d: false };
	controlState.mobile = { x: 0, y: 0, active: false };
	controlState.lastSpeed = 0;
	controlState.lastSteering = 0;
	document
		.querySelectorAll("[data-key]")
		.forEach((button) => button.classList.remove("is-active"));
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
	const coarsePointer =
		window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
	const mobileUA = /android|iphone|ipad|ipod|mobile/i.test(
		navigator.userAgent || "",
	);
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
		document
			.querySelectorAll("[data-key]")
			.forEach((button) => button.classList.remove("is-active"));
	} else {
		controlState.mobile = { x: 0, y: 0, active: false };
	}
	sendManualCommand("site-surface");
}

function renderShareLink() {
	const label = document.getElementById("access-link");
	label.textContent =
		"Link para outro dispositivo: " + (uiState.shareLink || "indisponivel");
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
		uiState.shareLink = payload.url || location.origin + "/";
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
	document
		.querySelectorAll("[data-key]")
		.forEach((button) => button.classList.remove("is-active"));
});

setupControlSurface();
resolveShareLink();
window.addEventListener("resize", setupControlSurface);

refreshStatus();

// ── Configuracoes ──────────────────────────────────────────────────

const darkToggle = document.getElementById("dark-mode-toggle");
function applyDark(enabled) {
	document.body.classList.toggle("dark", enabled);
	darkToggle.checked = enabled;
	localStorage.setItem("agv_dark", enabled ? "1" : "0");
}
applyDark(localStorage.getItem("agv_dark") === "1");
darkToggle.addEventListener("change", () => applyDark(darkToggle.checked));

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

const savedSpeed = parseInt(localStorage.getItem("agv_speed_limit") ?? "100", 10);
applySpeedLimit(isNaN(savedSpeed) ? 100 : savedSpeed);
sendSpeedLimit(speedSlider.value);

speedSlider.addEventListener("input", () => {
	const pct = parseInt(speedSlider.value, 10);
	applySpeedLimit(pct);
	clearTimeout(speedSendTimer);
	speedSendTimer = setTimeout(() => sendSpeedLimit(pct), 300);
});