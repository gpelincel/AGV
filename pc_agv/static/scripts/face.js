import { uiState } from './state.js';
import { postJson } from './api.js';

export function updateFaceSelector(face) {
	const select = document.getElementById("face-select");
	if (!select) return;

	const names = Array.isArray(face.names) ? face.names : [];
	const selectedName =
		typeof face.selected_name === "string"
			? face.selected_name
			: uiState.selectedFaceName;
	uiState.knownFaces = names.slice();
	uiState.selectedFaceName = selectedName || "";

	const currentOptions = [""];
	names.forEach((name) => currentOptions.push(name));
	const previousValue = select.value;
	const shouldRebuild =
		select.options.length !== currentOptions.length ||
		currentOptions.some(
			(value, index) => (select.options[index] || {}).value !== value,
		);

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

	const nextValue = names.includes(uiState.selectedFaceName)
		? uiState.selectedFaceName
		: "";
	select.value = nextValue;
	if (!nextValue && previousValue && !names.includes(previousValue)) {
		uiState.selectedFaceName = "";
	}
}

export function setFaceFormStatus(message) {
	const status = document.getElementById("face-form-status");
	if (status) status.textContent = message;
}

export async function selectFace(name) {
	try {
		const response = await postJson("/api/face/select", { name: name || "" });
		if (!response.ok) {
			alert("Nao foi possivel selecionar esse rosto.");
			return;
		}
		updateFaceSelector(response.face || {});
		setFaceFormStatus(
			response.face && response.face.selected_name
				? `Procurando ${response.face.selected_name} na webcam ZED.`
				: "Nenhuma pessoa selecionada para a webcam ZED.",
		);
	} catch (error) {
		console.error(error);
		alert("Falha ao selecionar o rosto.");
	}
}

export async function registerFace() {
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
			const errorCode =
				payload && payload.error ? payload.error : "erro desconhecido";
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