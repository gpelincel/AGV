export const controlState = {
	mode: "manual",
	pressed: { w: false, a: false, s: false, d: false },
	mobile: { x: 0, y: 0, active: false },
	lastSpeed: 0,
	lastSteering: 0,
};

export const uiState = {
	isMobileControl: false,
	shareLink: "",
	selectedFaceName: "",
	knownFaces: [],
	faceBeepArmed: true,
	faceLastLabel: "",
	audioReady: false,
	audioContext: null,
};