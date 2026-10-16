import { controlState, uiState } from './state.js';
import { sendManualCommand, setMode } from './api.js';

const joystickBase = document.getElementById("joystick-base");
const joystickKnob = document.getElementById("joystick-knob");
const joystickReadout = document.getElementById("joystick-readout");
let activePointerId = null;
let _stickThrottleAt = 0; 

export function updateStickVisual(nx, ny) {
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

export function updateStickFromPointer(clientX, clientY) {
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
	
	const now = Date.now();
	if (now - _stickThrottleAt >= 80) {
		_stickThrottleAt = now;
		sendManualCommand("site-stick");
	}
}

export function resetStick() {
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