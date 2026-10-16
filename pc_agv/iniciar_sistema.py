#!/usr/bin/env python3
"""
Servidor web do agv.

Escopo atual:
- stream RGB do Kinect v1
- stream do mapa de profundidade
- telemetria basica
- controle manual por site (W/A/S/D e toque)
- modo autonomo simples por profundidade
- envio serial para o Arduino
"""

import atexit
import select
import glob
import logging
import math
import os
import pickle
import socket
import subprocess
import sys
import threading
import time
from typing import Optional

import cv2
try:
    import face_recognition
except Exception:
    face_recognition = None
import numpy as np
from flask import Flask, Response, jsonify, request, render_template
from werkzeug.serving import make_server
from werkzeug.utils import secure_filename

try:
    import freenect  # type: ignore[import-not-found]
except Exception:
    freenect = None

_face_known_encodings = []
_face_known_names = []
_face_known_image_paths = []
_face_selected_name = ""


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("agv")
logging.getLogger("werkzeug").setLevel(logging.WARNING)

if face_recognition is None:
    log.warning("face-recognition/dlib nao encontrado. Modulo de reconhecimento facial desativado.")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_HOST = os.environ.get("AGV_HOST", "0.0.0.0").strip() or "0.0.0.0"
DEFAULT_PORT = int(os.environ.get("AGV_PORT", "5000"))
CONTROL_LOOP_SECONDS = float(os.environ.get("AGV_CONTROL_LOOP_SECONDS", "0.12"))
AUTO_LOOP_SECONDS = float(os.environ.get("AGV_AUTO_LOOP_SECONDS", "0.18"))
CAPTURE_LOOP_SECONDS = float(os.environ.get("AGV_CAPTURE_LOOP_SECONDS", "0.06"))
# Watchdog: para o agv se nenhum comando manual chegar neste intervalo (0 = desativado)
WATCHDOG_TIMEOUT_S = float(os.environ.get("AGV_WATCHDOG_TIMEOUT_S", "2.0"))
# FPS dos streams MJPEG — reduzido para funcionar bem com sinal fraco.
VIDEO_FPS = int(os.environ.get("AGV_VIDEO_FPS", "6"))
DEPTH_FPS = int(os.environ.get("AGV_DEPTH_FPS", "4"))
ZED_FPS = int(os.environ.get("AGV_ZED_FPS", "60"))
ZED_WIDTH = int(os.environ.get("AGV_ZED_WIDTH", "640"))
ZED_HEIGHT = int(os.environ.get("AGV_ZED_HEIGHT", "360"))
ZED_STREAM_MAX_WIDTH = int(os.environ.get("AGV_ZED_STREAM_MAX_WIDTH", "960"))
ZED_JPEG_QUALITY = int(os.environ.get("AGV_ZED_JPEG_QUALITY", "60"))
ZED_DEVICE = os.environ.get("AGV_ZED_DEVICE", "/dev/video0").strip() or "/dev/video0"
ZED_NAME_HINT = os.environ.get("AGV_ZED_NAME_HINT", "zed").strip().lower() or "zed"
ZED_STRICT_DEVICE = os.environ.get("AGV_ZED_STRICT_DEVICE", "1").strip() != "0"
KINECT_RELEASE_UVCVIDEO = os.environ.get("AGV_KINECT_RELEASE_UVCVIDEO", "0").strip() == "1"
MIN_FORWARD_PWM = int(os.environ.get("AGV_MIN_FORWARD_PWM", "170"))
MIN_TURN_PWM = int(os.environ.get("AGV_MIN_TURN_PWM", "140"))
KINECT_STABILIZATION_ENABLED = os.environ.get("AGV_KINECT_STABILIZATION", "1").strip() == "1"
KINECT_TILT_MIN = float(os.environ.get("AGV_KINECT_TILT_MIN", "-18"))
KINECT_TILT_MAX = float(os.environ.get("AGV_KINECT_TILT_MAX", "18"))
KINECT_TILT_INTERVAL = float(os.environ.get("AGV_KINECT_TILT_INTERVAL", "0.5"))
KINECT_TILT_DEADBAND = float(os.environ.get("AGV_KINECT_TILT_DEADBAND", "0.6"))
KINECT_TILT_NEUTRAL = float(os.environ.get("AGV_KINECT_TILT_NEUTRAL", "0"))
KINECT_TILT_GAIN = float(os.environ.get("AGV_KINECT_TILT_GAIN", "1.20"))
KINECT_TILT_SMOOTHING = float(os.environ.get("AGV_KINECT_TILT_SMOOTHING", "0.45"))
KINECT_TILT_MANUAL_HOLD = float(os.environ.get("AGV_KINECT_TILT_MANUAL_HOLD", "2.5"))
KINECT_TILT_PITCH_DEADBAND = float(os.environ.get("AGV_KINECT_TILT_PITCH_DEADBAND", "0.8"))
KINECT_TILT_MAX_STEP = float(os.environ.get("AGV_KINECT_TILT_MAX_STEP", "2.5"))
FACE_SCAN_INTERVAL = float(os.environ.get("AGV_FACE_SCAN_INTERVAL", "0.7"))
FACE_MATCH_THRESHOLD = float(os.environ.get("AGV_FACE_MATCH_THRESHOLD", "0.665"))
FACE_IMPOSTOR_MARGIN = float(os.environ.get("AGV_FACE_IMPOSTOR_MARGIN", "0.05"))
FACE_SELECTED_MAX_DISTANCE = float(os.environ.get("AGV_FACE_SELECTED_MAX_DISTANCE", "0.52"))
FACE_DETECT_MODEL = os.environ.get("AGV_FACE_DETECT_MODEL", "hog").strip().lower() or "hog"
FACE_SCAN_MAX_WIDTH = int(os.environ.get("AGV_FACE_SCAN_MAX_WIDTH", "640"))
FACE_DB_DIR = os.path.join(PROJECT_ROOT, "logs")
FACE_UPLOADS_DIR = os.path.join(FACE_DB_DIR, "faces")
ENCODINGS_FILE = os.path.join(FACE_DB_DIR, "encodings.pkl")

app = Flask(__name__)

_runtime_lock = threading.Lock()
_runtime_started = False
_stop_event = threading.Event()
_threads = []

_state_lock = threading.Lock()
_state = {
    "rgb": None,
  "rgb_raw": None,
        "zed": None,
    "zed_device": ZED_DEVICE,
    "depth": None,
    "depth_mm": False,
    "distance_m": None,
    "left_clearance_m": None,
    "center_clearance_m": None,
    "right_clearance_m": None,
  "tilt_deg": None,
  "tilt_target_deg": 0.0,
  "accel": [0.0, 0.0, 0.0],
    "fps_rgb": 0.0,
    "fps_depth": 0.0,
    "fps_zed": 0.0,
    "kinect_ok": False,
    "kinect_error": None,
    "last_frame_at": 0.0,
    "zed_ok": False,
    "zed_error": None,
    "zed_last_frame_at": 0.0,
}

_control_lock = threading.Lock()
_control_state = {
    "mode": "manual",
    "manual_speed": 0,
    "manual_steering": 0,
    "active_speed": 0,
    "active_steering": 0,
    "updated_at": time.time(),
    "last_source": "boot",
    "arduino_sent": False,
    "arduino_error": None,
}

_autopilot_lock = threading.Lock()
_autopilot_state = {
    "enabled": True,
    "running": False,
    "target_speed": 0,
    "target_steering": 0,
    "last_reason": "idle",
    "last_tick": 0.0,
}

_arduino_lock = threading.Lock()
_arduino_serial = None
_arduino_state = {
    "enabled": os.environ.get("AGV_ARDUINO_ENABLED", "1").strip() == "1",
    "connected": False,
    "port": None,
    "baud": int(os.environ.get("AGV_ARDUINO_BAUD", "9600")),
    "protocol": str(os.environ.get("AGV_ARDUINO_PROTOCOL", "csv")).strip().lower() or "csv",
    "last_command": None,
    "last_error": None,
    "last_tx_at": 0.0,
}

_arduino_log_state = {
    "payload": None,
    "mode": None,
    "source": None,
    "at": 0.0,
}

_arduino_rx_state = {
    "last_line": None,
    "at": 0.0,
}

_tilt_last_set_at = 0.0
_tilt_last_target = 0.0
_tilt_manual_until = 0.0
_tilt_manual_target = 0.0
_tilt_pitch_filtered = 0.0

_tilt_control_lock = threading.Lock()

_settings_lock = threading.Lock()
_settings = {
    "speed_limit_pct": int(os.environ.get("AGV_SPEED_LIMIT_PCT", "100")),
}

_face_lock = threading.Lock()
_face_cascade = cv2.CascadeClassifier(
  os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
)
_face_runtime = {
  "known": False,
  "label": "",
  "distance": None,
  "last_seen_at": 0.0,
  "last_announce_at": 0.0,
  "bbox": None,
  "next_scan_at": 0.0,
  "template_loaded": False,
    "selected_name": "",
}

_zed_face_lock = threading.Lock()
_zed_face_event = threading.Event()
_zed_face_state = {
    "pending": False,
    "frame": None,
    "next_scan_at": 0.0,
    "known": False,
    "label": "",
    "distance": None,
    "bbox": None,
}


class AGVServerController:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._server = make_server(host, port, app, threaded=True)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name="agv-http")

    def start(self) -> None:
        start_runtime()
        self._thread.start()
        log.info("Servidor HTTP em http://%s:%s", self.host, self.port)

    def stop(self) -> None:
        try:
            self._server.shutdown()
        finally:
            stop_runtime()


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _now() -> float:
    return time.time()


def _detect_lan_ip() -> str:
    # Resolve o IP local mais util para outro dispositivo na mesma rede.
    candidates = []

    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            candidates.append(probe.getsockname()[0])
        finally:
            probe.close()
    except Exception:
        pass

    try:
        candidates.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except Exception:
        pass

    for value in candidates:
        if value and not value.startswith("127."):
            return value
    return "127.0.0.1"


def _release_kinect_usb_claims() -> None:
    """Libera drivers do kernel que costumam prender o Kinect no Linux."""
    base_modules = ["gspca_kinect", "gspca_main", "snd_usb_audio"]
    if KINECT_RELEASE_UVCVIDEO:
        base_modules.extend(["uvcvideo", "videobuf2_v4l2", "videobuf2_vmalloc", "videobuf2_common", "videodev", "mc"])

    commands = [
        ["sudo", "-n", "modprobe", "-r", *base_modules],
        ["sudo", "-n", "rmmod", "gspca_kinect"],
        ["sudo", "-n", "rmmod", "gspca_main"],
        ["sudo", "-n", "rmmod", "snd_usb_audio"],
    ]
    if KINECT_RELEASE_UVCVIDEO:
        commands.append(["sudo", "-n", "rmmod", "uvcvideo"])
    for cmd in commands:
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        except Exception:
            continue


def _face_preprocess(gray_face: np.ndarray) -> np.ndarray:
    normalized = cv2.equalizeHist(gray_face)
    resized = cv2.resize(normalized, (64, 64), interpolation=cv2.INTER_AREA)
    descriptor = resized.astype(np.float32) / 255.0
    descriptor = descriptor.flatten()
    norm = np.linalg.norm(descriptor)
    if norm > 0:
        descriptor = descriptor / norm
    return descriptor


def _face_extract_primary(gray_frame: np.ndarray):
    if _face_cascade.empty():
        return None, None
    faces = _face_cascade.detectMultiScale(
        gray_frame,
        scaleFactor=1.2,
        minNeighbors=5,
        minSize=(60, 60),
    )
    if len(faces) == 0:
        return None, None
    x, y, w, h = max(faces, key=lambda f: int(f[2]) * int(f[3]))
    crop = gray_frame[y : y + h, x : x + w]
    if crop.size == 0:
        return None, None
    return (int(x), int(y), int(w), int(h)), _face_preprocess(crop)


def _load_kauan_face_descriptor() -> None:
    global _face_known_encodings, _face_known_names
def _normalize_face_name(name: str) -> str:
    normalized = " ".join(str(name or "").strip().split())
    return normalized[:60]


def _face_name_slug(name: str) -> str:
    slug = secure_filename(name).strip("._").lower()
    return slug or "rosto"


def _load_face_database() -> tuple[list[np.ndarray], list[str], list[Optional[str]]]:
    if not os.path.exists(ENCODINGS_FILE):
        raise FileNotFoundError(ENCODINGS_FILE)

    with open(ENCODINGS_FILE, "rb") as f:
        payload = pickle.load(f)

    if not isinstance(payload, tuple):
        raise ValueError("Formato invalido do encodings.pkl")

    if len(payload) == 2:
        known_encodings, known_names = payload
        known_image_paths = [None] * len(known_names)
    elif len(payload) == 3:
        known_encodings, known_names, known_image_paths = payload
    else:
        raise ValueError("Formato invalido do encodings.pkl")

    if not isinstance(known_encodings, list) or not isinstance(known_names, list) or not isinstance(known_image_paths, list):
        raise ValueError("Formato invalido do encodings.pkl")
    if len(known_encodings) != len(known_names) or len(known_names) != len(known_image_paths):
        raise ValueError("Banco de rostos inconsistente")

    return (
        [np.array(enc, dtype=np.float32) for enc in known_encodings],
        [_normalize_face_name(name) for name in known_names],
        [path if path else None for path in known_image_paths],
    )


def _save_face_database(encodings: list[np.ndarray], names: list[str], image_paths: list[Optional[str]]) -> None:
    os.makedirs(FACE_DB_DIR, exist_ok=True)
    with open(ENCODINGS_FILE, "wb") as f:
        pickle.dump((encodings, names, image_paths), f)


def _load_known_faces() -> None:
    global _face_known_encodings, _face_known_names, _face_known_image_paths, _face_selected_name

    try:
        known_encodings, known_names, known_image_paths = _load_face_database()
        _face_known_encodings = known_encodings
        _face_known_names = known_names
        _face_known_image_paths = known_image_paths

        with _face_lock:
            if _face_selected_name and _face_selected_name not in _face_known_names:
                _face_selected_name = ""
            _face_runtime["template_loaded"] = bool(_face_known_names)
            _face_runtime["selected_name"] = _face_selected_name

        log.info("Encodings carregados com sucesso (%d faces)", len(_face_known_encodings))
    except FileNotFoundError:
        _face_known_encodings = []
        _face_known_names = []
        _face_known_image_paths = []
        with _face_lock:
            _face_runtime["template_loaded"] = False
            _face_runtime["selected_name"] = ""
        log.warning("Arquivo de encodings nao encontrado: %s", ENCODINGS_FILE)
    except Exception as exc:
        _face_known_encodings = []
        _face_known_names = []
        _face_known_image_paths = []
        with _face_lock:
            _face_runtime["template_loaded"] = False
            _face_runtime["selected_name"] = ""
        log.warning("Falha ao carregar encodings: %s", exc)


def _set_selected_face(name: str) -> tuple[bool, str]:
    global _face_selected_name

    selected_name = _normalize_face_name(name)
    with _face_lock:
        if not selected_name:
            _face_selected_name = ""
            _face_runtime["known"] = False
            _face_runtime["label"] = ""
            _face_runtime["distance"] = None
            _face_runtime["bbox"] = None
            _face_runtime["next_scan_at"] = 0.0
            _face_runtime["selected_name"] = ""
            return True, "nenhum rosto selecionado"

        if selected_name not in _face_known_names:
            return False, "rosto nao encontrado"

        _face_selected_name = selected_name
        _face_runtime["known"] = False
        _face_runtime["label"] = ""
        _face_runtime["distance"] = None
        _face_runtime["bbox"] = None
        _face_runtime["next_scan_at"] = 0.0
        _face_runtime["selected_name"] = selected_name
        return True, "rosto selecionado"


def _selected_face_encoding() -> tuple[str, Optional[np.ndarray]]:
    with _face_lock:
        selected_name = str(_face_selected_name)
        known_names = list(_face_known_names)
        known_encodings = list(_face_known_encodings)

    if not selected_name:
        return "", None

    try:
        index = known_names.index(selected_name)
    except ValueError:
        return selected_name, None

    return selected_name, np.array(known_encodings[index], dtype=np.float32)


def _face_status_snapshot() -> dict:
    with _face_lock:
        return {
            "known": bool(_face_runtime["known"]),
            "label": str(_face_runtime["label"]),
            "distance": _face_runtime["distance"],
            "last_seen_at": float(_face_runtime["last_seen_at"]),
            "template_loaded": bool(_face_runtime["template_loaded"]),
            "selected_name": str(_face_runtime["selected_name"]),
            "names": list(_face_known_names),
        }


def _update_face_runtime(frame_bgr: np.ndarray) -> np.ndarray:
    now = _now()
    selected_name, selected_encoding = _selected_face_encoding()

    with _face_lock:
        cached_bbox = None
        known = False
        label = ""
        if (
            selected_name
            and selected_encoding is not None
            and now < float(_face_runtime["next_scan_at"])
            and str(_face_runtime["selected_name"]) == selected_name
        ):
            cached_bbox = _face_runtime["bbox"]
            known = bool(_face_runtime["known"])
            label = str(_face_runtime["label"])

    if not selected_name or selected_encoding is None:
        with _face_lock:
            _face_runtime["known"] = False
            _face_runtime["label"] = ""
            _face_runtime["distance"] = None
            _face_runtime["bbox"] = None
            _face_runtime["selected_name"] = selected_name
            _face_runtime["template_loaded"] = bool(_face_known_names)
        return frame_bgr

    if cached_bbox is None:
        known = False
        label = ""
        distance = None
        bbox = None

        try:
            # ZED e camera estereo: usa metade esquerda (672x376) em vez de
            # reduzir 0.5x o frame completo (resultaria em 672x188, muito achatado).
            h, w = frame_bgr.shape[:2]
            scan_frame = frame_bgr[:, : w // 2] if w > 700 else frame_bgr

            scan_h, scan_w = scan_frame.shape[:2]
            if scan_w > max(64, FACE_SCAN_MAX_WIDTH):
                scale = float(FACE_SCAN_MAX_WIDTH) / float(scan_w)
                resized_w = int(scan_w * scale)
                resized_h = int(scan_h * scale)
                frame_for_face = cv2.resize(scan_frame, (resized_w, resized_h), interpolation=cv2.INTER_AREA)
                scale_x = float(scan_w) / float(resized_w)
                scale_y = float(scan_h) / float(resized_h)
            else:
                frame_for_face = scan_frame
                scale_x = 1.0
                scale_y = 1.0

            rgb_small = cv2.cvtColor(frame_for_face, cv2.COLOR_BGR2RGB)
            if face_recognition is None:
                return frame_bgr
            model = "cnn" if FACE_DETECT_MODEL == "cnn" else "hog"
            face_locations = face_recognition.face_locations(rgb_small, model=model)
            face_encodings = face_recognition.face_encodings(rgb_small, face_locations)

            best_distance = None
            best_bbox = None
            for (top, right, bottom, left), encoding in zip(face_locations, face_encodings):
                current_distance = float(np.linalg.norm(np.array(encoding, dtype=np.float32) - selected_encoding))
                if best_distance is None or current_distance < best_distance:
                    best_distance = current_distance
                    left_px = int(left * scale_x)
                    top_px = int(top * scale_y)
                    right_px = int(right * scale_x)
                    bottom_px = int(bottom * scale_y)
                    best_bbox = (
                        left_px,
                        top_px,
                        max(1, right_px - left_px),
                        max(1, bottom_px - top_px),
                    )

            if best_distance is not None:
                distance = best_distance
                if best_distance <= FACE_MATCH_THRESHOLD:
                    known = True
                    label = selected_name
                    bbox = best_bbox
        except Exception as exc:
            log.warning("Falha na deteccao facial da ZED: %s", exc)

        with _face_lock:
            _face_runtime["known"] = known
            _face_runtime["label"] = label
            _face_runtime["distance"] = distance
            _face_runtime["last_seen_at"] = now if bbox is not None else _face_runtime["last_seen_at"]
            _face_runtime["bbox"] = bbox if known else None
            _face_runtime["next_scan_at"] = now + FACE_SCAN_INTERVAL
            _face_runtime["template_loaded"] = bool(_face_known_names)
            _face_runtime["selected_name"] = selected_name
            if known and now - float(_face_runtime["last_announce_at"]) >= 4.0:
                log.info("Face reconhecida na ZED: %s", selected_name)
                _face_runtime["last_announce_at"] = now
            cached_bbox = _face_runtime["bbox"]
            known = bool(_face_runtime["known"])
            label = str(_face_runtime["label"])

    if known and cached_bbox is not None:
        x, y, w, h = cached_bbox
        cv2.rectangle(frame_bgr, (x, y), (x + w, y + h), (80, 230, 120), 2)
        cv2.putText(
            frame_bgr,
            label,
            (x, max(18, y - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (80, 230, 120),
            2,
        )
    return frame_bgr


def _detect_selected_face_on_frame(
    frame_bgr: np.ndarray,
    selected_name: str,
    known_names: list[str],
    known_encodings: list[np.ndarray],
) -> tuple[bool, str, Optional[float], Optional[tuple[int, int, int, int]]]:
    known = False
    label = ""
    distance = None
    bbox = None

    h, w = frame_bgr.shape[:2]
    scan_frame = frame_bgr[:, : w // 2] if w > 700 else frame_bgr

    scan_h, scan_w = scan_frame.shape[:2]
    if scan_w > max(64, FACE_SCAN_MAX_WIDTH):
        scale = float(FACE_SCAN_MAX_WIDTH) / float(scan_w)
        resized_w = int(scan_w * scale)
        resized_h = int(scan_h * scale)
        frame_for_face = cv2.resize(scan_frame, (resized_w, resized_h), interpolation=cv2.INTER_AREA)
        scale_x = float(scan_w) / float(resized_w)
        scale_y = float(scan_h) / float(resized_h)
    else:
        frame_for_face = scan_frame
        scale_x = 1.0
        scale_y = 1.0

    rgb_small = cv2.cvtColor(frame_for_face, cv2.COLOR_BGR2RGB)
    if face_recognition is None:
        return False, "", None, None
    model = "cnn" if FACE_DETECT_MODEL == "cnn" else "hog"
    face_locations = face_recognition.face_locations(rgb_small, model=model)
    face_encodings = face_recognition.face_encodings(rgb_small, face_locations)

    try:
        selected_idx = known_names.index(selected_name)
    except ValueError:
        return False, "", None, None

    strict_threshold = min(FACE_MATCH_THRESHOLD, FACE_SELECTED_MAX_DISTANCE)

    best_selected_distance = None
    best_bbox = None

    for (top, right, bottom, left), encoding in zip(face_locations, face_encodings):
        probe = np.array(encoding, dtype=np.float32)
        all_distances = [float(np.linalg.norm(probe - np.array(ref, dtype=np.float32))) for ref in known_encodings]
        if not all_distances:
            continue

        selected_distance = all_distances[selected_idx]
        others = [dist for idx, dist in enumerate(all_distances) if idx != selected_idx]
        best_other_distance = min(others) if others else 999.0
        best_distance = min(all_distances)

        is_selected_best = selected_distance <= (best_distance + 1e-9)
        has_margin = (best_other_distance - selected_distance) >= FACE_IMPOSTOR_MARGIN
        is_valid = (
            selected_distance <= strict_threshold
            and is_selected_best
            and has_margin
        )

        if not is_valid:
            continue

        if best_selected_distance is None or selected_distance < best_selected_distance:
            best_selected_distance = selected_distance
            left_px = int(left * scale_x)
            top_px = int(top * scale_y)
            right_px = int(right * scale_x)
            bottom_px = int(bottom * scale_y)
            best_bbox = (
                left_px,
                top_px,
                max(1, right_px - left_px),
                max(1, bottom_px - top_px),
            )

    if best_selected_distance is not None and best_bbox is not None:
        distance = best_selected_distance
        known = True
        label = selected_name
        bbox = best_bbox

    return known, label, distance, bbox


def _zed_face_worker_loop() -> None:
    while not _stop_event.is_set():
        _zed_face_event.wait(timeout=0.15)
        _zed_face_event.clear()

        with _zed_face_lock:
            frame = _zed_face_state["frame"]
            pending = bool(_zed_face_state["pending"])
            _zed_face_state["frame"] = None

        if not pending or frame is None:
            continue

        now = _now()
        selected_name, selected_encoding = _selected_face_encoding()
        with _face_lock:
            known_names = list(_face_known_names)
            known_encodings = [np.array(enc, dtype=np.float32) for enc in _face_known_encodings]

        known = False
        label = ""
        distance = None
        bbox = None

        if selected_name and selected_encoding is not None and known_names and known_encodings:
            try:
                known, label, distance, bbox = _detect_selected_face_on_frame(
                    frame,
                    selected_name,
                    known_names,
                    known_encodings,
                )
            except Exception as exc:
                log.warning("Falha na deteccao facial da ZED: %s", exc)

        with _face_lock:
            _face_runtime["known"] = known
            _face_runtime["label"] = label
            _face_runtime["distance"] = distance
            _face_runtime["bbox"] = bbox if known else None
            _face_runtime["next_scan_at"] = now + FACE_SCAN_INTERVAL
            _face_runtime["template_loaded"] = bool(_face_known_names)
            _face_runtime["selected_name"] = selected_name
            if bbox is not None:
                _face_runtime["last_seen_at"] = now
            if known and now - float(_face_runtime["last_announce_at"]) >= 4.0:
                log.info("Face reconhecida na ZED: %s", selected_name)
                _face_runtime["last_announce_at"] = now

        with _zed_face_lock:
            _zed_face_state["pending"] = False
            _zed_face_state["known"] = known
            _zed_face_state["label"] = label
            _zed_face_state["distance"] = distance
            _zed_face_state["bbox"] = bbox if known else None
            _zed_face_state["next_scan_at"] = now + FACE_SCAN_INTERVAL


def _compute_tilt_target(ax: float, ay: float, az: float) -> float:
    # Kinect v1: eixo X do acelerometro acompanha bem o "pitch" do tilt.
    gravity = math.sqrt((ax * ax) + (ay * ay) + (az * az))
    if gravity < 1e-6:
        return float(max(KINECT_TILT_MIN, min(KINECT_TILT_MAX, KINECT_TILT_NEUTRAL)))

    pitch_deg = math.degrees(math.atan2(ax, max(1e-6, abs(az))))
    if abs(pitch_deg) < KINECT_TILT_PITCH_DEADBAND:
        pitch_deg = 0.0

    requested = KINECT_TILT_NEUTRAL - (pitch_deg * KINECT_TILT_GAIN)
    return float(max(KINECT_TILT_MIN, min(KINECT_TILT_MAX, requested)))


def _command_kinect_tilt(angle: float) -> tuple[bool, str, Optional[float]]:
    global _tilt_manual_until, _tilt_manual_target, _tilt_last_set_at, _tilt_last_target

    target = float(max(KINECT_TILT_MIN, min(KINECT_TILT_MAX, float(angle))))
    _tilt_manual_target = target
    _tilt_manual_until = _now() + KINECT_TILT_MANUAL_HOLD
    _tilt_last_target = target
    _tilt_last_set_at = 0.0

    with _state_lock:
        _state["tilt_target_deg"] = round(target, 2)

    # Quando disponivel, envia comando imediato sem depender do callback do video.
    # Isso melhora o feedback dos botoes Tilt +/- no painel.
    direct_sent = False
    if freenect is not None:
        try:
            if hasattr(freenect, "sync_set_tilt_degs"):
                freenect.sync_set_tilt_degs(int(round(target)))
                direct_sent = True
        except Exception:
            direct_sent = False

    message = "tilt aplicado" if direct_sent else "comando enviado"
    return True, message, target


def _set_kinect_stabilization(enabled: bool) -> bool:
    global KINECT_STABILIZATION_ENABLED
    with _tilt_control_lock:
        KINECT_STABILIZATION_ENABLED = bool(enabled)
    return KINECT_STABILIZATION_ENABLED


def _get_kinect_stabilization() -> bool:
    with _tilt_control_lock:
        return bool(KINECT_STABILIZATION_ENABLED)


def _do_tilt_body(dev) -> None:
    """Ajusta o tilt do Kinect para manter o frame mais nivelado."""
    global _tilt_last_set_at, _tilt_last_target, _tilt_manual_until, _tilt_manual_target, _tilt_pitch_filtered

    stabilization_enabled = _get_kinect_stabilization()
    # Mesmo com estabilizacao OFF, respeita comando manual recente de tilt.
    if (not stabilization_enabled) and (_now() >= _tilt_manual_until):
        return

    now = _now()
    if now - _tilt_last_set_at < KINECT_TILT_INTERVAL:
        return
    _tilt_last_set_at = now

    try:
        freenect.update_tilt_state(dev)
        tilt_state = freenect.get_tilt_state(dev)
        raw_tilt = float(freenect.get_tilt_degs(tilt_state))
        accel = freenect.get_mks_accel(tilt_state)
        ax, ay, az = float(accel[0]), float(accel[1]), float(accel[2])

        manual_tilt_active = now < _tilt_manual_until
        if manual_tilt_active:
            target_f = _tilt_manual_target
        elif stabilization_enabled:
            # Filtro leve no acelerometro para reduzir jitter de leitura.
            measured_pitch = math.degrees(math.atan2(ax, max(1e-6, abs(az))))
            _tilt_pitch_filtered = (_tilt_pitch_filtered * 0.70) + (measured_pitch * 0.30)
            target_f = _compute_tilt_target(_tilt_pitch_filtered, ay, az)
            alpha = max(0.0, min(1.0, KINECT_TILT_SMOOTHING))
            if alpha > 0.0:
                target_f = (_tilt_last_target * (1.0 - alpha)) + (target_f * alpha)
        else:
            target_f = raw_tilt

        target_f = float(max(KINECT_TILT_MIN, min(KINECT_TILT_MAX, target_f)))

        # Limita passo apenas na estabilizacao automatica.
        if not manual_tilt_active:
            step = float(max(0.2, KINECT_TILT_MAX_STEP))
            if target_f > raw_tilt + step:
                target_f = raw_tilt + step
            elif target_f < raw_tilt - step:
                target_f = raw_tilt - step

        command_deadband = 0.2 if manual_tilt_active else KINECT_TILT_DEADBAND
        if abs(raw_tilt - target_f) >= command_deadband:
            freenect.set_tilt_degs(dev, int(round(target_f)))
        _tilt_last_target = target_f

        with _state_lock:
            _state["tilt_deg"] = raw_tilt
            _state["tilt_target_deg"] = round(target_f, 2)
            _state["accel"] = [round(ax, 3), round(ay, 3), round(az, 3)]
            if abs(ax) < 0.001 and abs(ay) < 0.001 and abs(az) < 0.001:
                _state["kinect_error"] = "tilt sem resposta do acelerometro"
            elif str(_state.get("kinect_error") or "").startswith("tilt sem resposta"):
                _state["kinect_error"] = None
    except Exception as exc:
        with _state_lock:
            _state["kinect_error"] = f"falha estabilizacao: {exc}"


def _candidate_arduino_ports():
    preferred = os.environ.get("AGV_ARDUINO_PORT", "").strip()
    ports = []
    if preferred:
        ports.append(preferred)

    try:
        from serial.tools import list_ports  # type: ignore[import-not-found]
    except Exception:
        list_ports = None

    if list_ports is not None:
        try:
            discovered = []
            for info in list_ports.comports():
                device = str(getattr(info, "device", "") or "").strip()
                if not device:
                    continue
                discovered.append(device)
            ports.extend(sorted(discovered))
        except Exception:
            pass

    ports.extend(sorted(glob.glob("/dev/serial/by-id/*")))
    ports.extend(sorted(glob.glob("/dev/ttyACM*")))
    ports.extend(sorted(glob.glob("/dev/ttyUSB*")))
    ports.extend(sorted(glob.glob("/dev/ttyAMA*")))
    ports.extend(sorted(glob.glob("/dev/rfcomm*")))

    dedup = []
    seen = set()
    for port in ports:
        if not port:
            continue
        key = os.path.realpath(port)
        if key in seen:
            continue
        seen.add(key)
        dedup.append(port)
    return dedup


def _close_arduino_locked() -> None:
    global _arduino_serial

    if _arduino_serial is None:
        return
    try:
        _arduino_serial.close()
    except Exception:
        pass
    _arduino_serial = None


def _drain_arduino_input_locked(ser, max_lines: int = 12) -> list[str]:
    lines = []
    prev_line = _arduino_rx_state["last_line"]
    prev_at = float(_arduino_rx_state["at"])

    try:
        waiting = int(getattr(ser, "in_waiting", 0) or 0)
    except Exception:
        waiting = 0

    while waiting > 0 and len(lines) < max_lines:
        try:
            raw = ser.readline()
        except Exception as exc:
            _arduino_state["last_error"] = f"falha ao ler serial: {exc}"
            break

        if not raw:
            break

        line = raw.decode("utf-8", errors="ignore").strip()
        if line:
            lines.append(line)
            _arduino_rx_state["last_line"] = line
            _arduino_rx_state["at"] = _now()

            if "ERROR" in line or "TIMEOUT" in line:
                _arduino_state["last_error"] = line
            elif (
                line.startswith("ARDUINO:OK")
                or line.startswith("ARDUINO:READY")
                or line.startswith("ARDUINO:PONG")
                or line.startswith("STATUS:")
            ):
                _arduino_state["last_error"] = None

        try:
            waiting = int(getattr(ser, "in_waiting", 0) or 0)
        except Exception:
            break

    if lines:
        last_line = lines[-1]
        should_log = (
            prev_line != last_line
            or (_now() - prev_at) >= 2.0
            or "ERROR" in last_line
            or "TIMEOUT" in last_line
        )
        if should_log:
            log.info("Arduino RX: %s", last_line)

    return lines


def _probe_arduino_locked(ser) -> bool:
    lines = _drain_arduino_input_locked(ser)
    if any(("READY" in line or "PONG" in line or "OK" in line or line.startswith("STATUS:")) for line in lines):
        return True

    try:
        ser.write(b"PING\n")
        ser.flush()
        time.sleep(0.25)
    except Exception:
        return False

    lines = _drain_arduino_input_locked(ser)
    return any(("READY" in line or "PONG" in line or "OK" in line or line.startswith("STATUS:")) for line in lines)


def _connect_arduino() -> bool:
    global _arduino_serial

    if not _arduino_state["enabled"]:
        with _arduino_lock:
            _arduino_state["last_error"] = "serial desativada por ambiente"
        return False

    try:
        import serial  # type: ignore[import-not-found]
    except Exception as exc:
        with _arduino_lock:
            _arduino_state["connected"] = False
            _arduino_state["last_error"] = f"pyserial ausente: {exc}"
        return False

    ports = _candidate_arduino_ports()
    if not ports:
        with _arduino_lock:
            _arduino_state["connected"] = False
            _arduino_state["last_error"] = "nenhuma porta serial encontrada"
        return False

    for port in ports:
        try:
            ser = serial.Serial(
                port=port,
                baudrate=int(_arduino_state["baud"]),
                timeout=0.1,
                write_timeout=0.1,
                dsrdtr=False,
                rtscts=False,
            )
            try:
                ser.setDTR(False)
                ser.setRTS(False)
            except Exception:
                pass

            # O Arduino Uno costuma reiniciar ao abrir a serial USB.
            # Espera curta demais faz os primeiros comandos se perderem.
            time.sleep(1.8)
            try:
                ser.reset_output_buffer()
            except Exception:
                pass

            with _arduino_lock:
                _arduino_serial = ser
                _arduino_state["connected"] = True
                _arduino_state["port"] = os.path.realpath(port)
                _arduino_state["last_error"] = None
                identified = _probe_arduino_locked(ser)
                if not identified:
                    _arduino_state["last_error"] = "porta abriu, mas sem resposta do firmware"
            log.info("Arduino conectado em %s @ %s", os.path.realpath(port), _arduino_state["baud"])
            return True
        except Exception as exc:
            detail = str(exc)
            if isinstance(exc, PermissionError) or "Permission denied" in detail or "Errno 13" in detail:
                detail = f"sem permissao para abrir {port}; entre novamente na sessao ou adicione o usuario ao grupo dialout"
            with _arduino_lock:
                _arduino_state["connected"] = False
                _arduino_state["last_error"] = f"falha ao abrir {port}: {detail}"

    return False


def _speed_steering_to_payload(speed: int, steering: int) -> tuple[int, int]:
    speed_cmd = _clamp(speed, -100, 100)
    steering_cmd = _clamp(steering, -100, 100)

    abs_speed_pct = abs(speed_cmd)
    accel = abs_speed_pct * 255 // 100

    if abs_speed_pct > 0:
        accel = max(_clamp(MIN_FORWARD_PWM, 0, 255), accel)

    if speed_cmd < 0:
        accel = -accel

    if accel == 0 and abs(steering_cmd) >= 20:
        accel = _clamp(MIN_TURN_PWM, 0, 255)

    steering_gain = 0.70 if accel != 0 else 1.00
    dir_val = int(steering_cmd * 254 * steering_gain / 100)
    if abs(dir_val) < 8:
        dir_val = 0

    return accel, dir_val


def _send_to_arduino(speed: int, steering: int, mode: str, source: str) -> bool:
    global _arduino_serial

    if not _arduino_state["enabled"]:
        with _arduino_lock:
            _arduino_state["last_error"] = "serial desativada por ambiente"
        return False

    with _arduino_lock:
        ser = _arduino_serial

    if ser is None and not _connect_arduino():
        return False

    with _arduino_lock:
        ser = _arduino_serial
    if ser is None:
        return False

    accel, dir_val = _speed_steering_to_payload(speed, steering)

    # Speed limit: escala linear em 0-255 para que o usuario sinta diferenca real.
    # A 50% o PWM maximo e 127 (visivelmente mais lento que 255).
    with _settings_lock:
        limit_pct = _settings["speed_limit_pct"]
    if limit_pct < 100 and accel != 0:
        max_pwm = int(255 * limit_pct / 100)
        accel = max(-max_pwm, min(max_pwm, accel))

    payload = f"{accel},{dir_val}"

    try:
        with _arduino_lock:
            _drain_arduino_input_locked(ser)
        ser.write((payload + "\n").encode("ascii", errors="ignore"))
        try:
            ser.flush()
        except Exception:
            pass
        with _arduino_lock:
            _arduino_state["connected"] = True
            _arduino_state["last_command"] = payload
            _arduino_state["last_error"] = None
            _arduino_state["last_tx_at"] = _now()
            _drain_arduino_input_locked(ser)
        log_changed = (
            _arduino_log_state["payload"] != payload
            or _arduino_log_state["mode"] != mode
            or _arduino_log_state["source"] != source
            or (_now() - float(_arduino_log_state["at"])) >= 2.0
        )
        if log_changed:
            log.info("Arduino TX: %s (mode=%s source=%s)", payload, mode, source)
            _arduino_log_state["payload"] = payload
            _arduino_log_state["mode"] = mode
            _arduino_log_state["source"] = source
            _arduino_log_state["at"] = _now()
        return True
    except Exception as exc:
        with _arduino_lock:
            _arduino_state["connected"] = False
            _arduino_state["last_error"] = str(exc)
            _close_arduino_locked()
        return False


def _depth_segment_distance(depth_seg: np.ndarray, is_mm: bool) -> Optional[float]:
    if is_mm:
        valid = depth_seg[(depth_seg > 200) & (depth_seg < 8000)]
        if valid.size:
            return float(np.min(valid)) / 1000.0
        return None

    valid = depth_seg[(depth_seg > 100) & (depth_seg < 2040)]
    if not valid.size:
        return None
    raw = float(np.max(valid))
    denom = raw * -0.0030711016 + 3.3309495161
    if denom <= 0.0:
        return None
    depth_m = 1.0 / denom
    if 0.15 <= depth_m <= 10.0:
        return depth_m
    return None


def _compute_depth_metrics(depth: np.ndarray) -> tuple[bool, Optional[float], Optional[float], Optional[float], Optional[float]]:
    if depth is None or depth.size == 0:
        return False, None, None, None, None

    height, width = depth.shape[:2]
    is_mm = bool(np.nanmax(depth) > 3000)

    row_a = int(height * 0.35)
    row_b = int(height * 0.65)
    left_a = int(width * 0.10)
    left_b = int(width * 0.30)
    center_a = int(width * 0.40)
    center_b = int(width * 0.60)
    right_a = int(width * 0.70)
    right_b = int(width * 0.90)

    left = _depth_segment_distance(depth[row_a:row_b, left_a:left_b], is_mm)
    center = _depth_segment_distance(depth[row_a:row_b, center_a:center_b], is_mm)
    right = _depth_segment_distance(depth[row_a:row_b, right_a:right_b], is_mm)

    distance = center
    candidates = [value for value in (left, center, right) if value is not None]
    if distance is None and candidates:
        distance = min(candidates)

    return is_mm, distance, left, center, right


def _depth_to_colormap(depth: np.ndarray, is_mm: bool) -> np.ndarray:
    if is_mm:
        clipped = np.clip(depth.astype(np.float32), 200, 4000)
        norm = ((clipped - 200.0) / 3800.0 * 255.0).astype(np.uint8)
    else:
        clipped = np.clip(depth.astype(np.float32), 0, 2047)
        norm = (clipped / 2047.0 * 255.0).astype(np.uint8)
    return cv2.applyColorMap(255 - norm, cv2.COLORMAP_JET)


def _placeholder_frame(text: str) -> np.ndarray:
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.rectangle(frame, (20, 20), (620, 460), (30, 30, 30), 2)
    cv2.putText(frame, text, (40, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 200, 255), 2)
    return frame


def _encode_jpeg(frame: np.ndarray, quality: int = 82) -> Optional[bytes]:
    ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        return None
    return buffer.tobytes()


def _kinect_device_available() -> tuple[bool, Optional[str]]:
    if freenect is None:
        return False, "python3-freenect nao disponivel no sistema"

    ctx = None
    try:
        ctx = freenect.init()
        if ctx is None:
            return False, "falha ao iniciar libfreenect"
        count = int(freenect.num_devices(ctx))
        if count <= 0:
            return False, "Kinect nao detectado no USB"
        return True, None
    except Exception as exc:
        return False, f"Kinect indisponivel: {exc}"
    finally:
        if ctx is not None:
            try:
                freenect.shutdown(ctx)
            except Exception:
                pass


def _zed_source_value(source_raw: str):
    value = str(source_raw or "").strip()
    if value.isdigit():
        return int(value)
    return value or 0


def _zed_nodes_from_sysfs() -> list[str]:
    """Detecta nodes ZED pelo sysfs. Tenta varios nomes: zed, stereolabs, usb video, ou qualquer device USB."""
    nodes = []
    for name_path in sorted(glob.glob("/sys/class/video4linux/video*/name")):
        try:
            card_name = open(name_path, "r", encoding="utf-8", errors="ignore").read().strip().lower()
        except Exception:
            continue
        
        # Tenta match por ZED_NAME_HINT ou por pattern STEREOLABS ou USB generico
        is_match = (
            (ZED_NAME_HINT and ZED_NAME_HINT in card_name) or
            "stereolabs" in card_name or
            "stereo" in card_name or
            ("usb" in card_name and "video" in card_name)
        )
        if not is_match:
            continue

        video_node = os.path.basename(os.path.dirname(name_path))
        node_path = f"/dev/{video_node}"
        if os.path.exists(node_path):
            nodes.append(node_path)
            log.info("ZED device detectado via sysfs: %s (%s)", node_path, card_name)
    return nodes


def _zed_candidate_sources() -> list:
    candidates = []
    preferred_raw = str(ZED_DEVICE or "").strip()
    preferred = _zed_source_value(preferred_raw)
    if preferred_raw:
        candidates.append(preferred)

    for node in _zed_nodes_from_sysfs():
        if node not in candidates:
            candidates.append(node)

    if not ZED_STRICT_DEVICE:
        for path in sorted(glob.glob("/dev/video*")):
            if path not in candidates:
                candidates.append(path)

    dedup = []
    seen = set()
    for source in candidates:
        key = str(source)
        if key in seen:
            continue
        seen.add(key)
        dedup.append(source)
    return dedup


def _open_zed_capture(source) -> tuple[Optional[subprocess.Popen], Optional[str]]:
    """Abre stream de video ZED via FFmpeg (OpenCV nao consegue abrir device v4l2)."""
    if not isinstance(source, str) or not source.startswith("/dev/video"):
        return None, "source invalido"
    
    try:
        if not os.path.exists(source):
            return None, f"device {source} nao existe"
        
        base_prefix = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-probesize", "32",
            "-analyzeduration", "0",
            "-f", "v4l2",
            "-thread_queue_size", "32",
            "-video_size", f"{ZED_WIDTH}x{ZED_HEIGHT}",
            "-framerate", str(ZED_FPS),
        ]

        variants = [
            {
                "name": "mjpeg-copy",
                "cmd": base_prefix
                + [
                    "-input_format", "mjpeg",
                    "-i", source,
                    "-an", "-sn", "-dn",
                    "-fflags", "flush_packets",
                    "-f", "mjpeg",
                    "-c:v", "copy",
                    "-",
                ],
            },
            {
                "name": "yuyv-encode",
                "cmd": base_prefix
                + [
                    "-input_format", "yuyv422",
                    "-i", source,
                    "-an", "-sn", "-dn",
                    "-fflags", "flush_packets",
                    "-vsync", "drop",
                    "-f", "image2pipe",
                    "-c:v", "mjpeg",
                    "-q:v", "3",
                    "-",
                ],
            },
        ]

        last_err = ""
        for variant in variants:
            proc = subprocess.Popen(
                variant["cmd"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )

            time.sleep(0.25)
            if proc.poll() is None:
                log.info(
                    "FFmpeg stream aberto: %s (%dx%d @ %d fps, %s)",
                    source,
                    ZED_WIDTH,
                    ZED_HEIGHT,
                    ZED_FPS,
                    variant["name"],
                )
                return proc, None

            try:
                proc.communicate(timeout=0.4)
            except Exception:
                pass
            last_err = f"variant {variant['name']} falhou"

        return None, f"ffmpeg falhou: {last_err or 'unknown'}"
    except Exception as exc:
        return None, str(exc)


def _open_zed_cv_capture(source) -> tuple[Optional[cv2.VideoCapture], Optional[str]]:
    if not isinstance(source, str) or not source.startswith("/dev/video"):
        return None, "source invalido"
    if not os.path.exists(source):
        return None, f"device {source} nao existe"

    try:
        cap = cv2.VideoCapture(source, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap.release()
            return None, "falha ao abrir com OpenCV/V4L2"

        fourcc = cv2.VideoWriter_fourcc(*"YUYV")
        cap.set(cv2.CAP_PROP_FOURCC, fourcc)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(ZED_WIDTH))
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(ZED_HEIGHT))
        cap.set(cv2.CAP_PROP_FPS, float(ZED_FPS))
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        actual_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        log.info(
            "OpenCV stream aberto: %s (%dx%d @ %.1f fps)",
            source,
            actual_w,
            actual_h,
            actual_fps,
        )
        return cap, None
    except Exception as exc:
        return None, str(exc)


def _read_frame_from_ffmpeg(
    proc: subprocess.Popen,
    carry: bytes,
    timeout_s: float = 0.35,
) -> tuple[Optional[np.ndarray], bytes]:
    """Le o frame mais recente do pipe FFmpeg, descartando backlog para reduzir latencia."""
    if not proc or proc.poll() is not None:
        return None, b""

    stream = proc.stdout
    if stream is None:
        return None, b""
    
    frame_data = carry
    deadline = _now() + timeout_s
    
    try:
        while _now() < deadline:
            wait = max(0.0, deadline - _now())
            readable, _, _ = select.select([stream], [], [], wait)
            if not readable:
                break

            chunk = stream.read(65536)
            if not chunk:
                return None, frame_data
            
            frame_data += chunk

            # Mantem apenas uma janela recente para evitar crescimento sem limite.
            if len(frame_data) > 4_000_000:
                frame_data = frame_data[-4_000_000:]
            
            if b"\xff\xd9" in frame_data:
                end_idx = frame_data.rfind(b"\xff\xd9") + 2
                start_idx = frame_data.rfind(b"\xff\xd8", 0, end_idx)
                
                if start_idx >= 0:
                    jpeg_bytes = frame_data[start_idx:end_idx]
                    remaining = frame_data[end_idx:]
                    frame = cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8), cv2.IMREAD_COLOR)
                    if frame is not None:
                        return frame, remaining
                    frame_data = remaining
    except Exception:
        pass
    
    return None, frame_data


def _zed_capture_loop() -> None:
    fps_counter = 0
    fps_mark = _now()
    failed_until: dict[str, float] = {}

    while not _stop_event.is_set():
        proc = None
        cap = None
        pipe_carry = b""
        try:
            selected_source = None
            last_error = None

            for candidate in _zed_candidate_sources():
                candidate_key = str(candidate)
                if float(failed_until.get(candidate_key, 0.0)) > _now():
                    continue

                cap, open_error = _open_zed_cv_capture(candidate)
                if cap is not None:
                    selected_source = candidate
                    break

                proc, open_error = _open_zed_capture(candidate)
                if proc is not None:
                    selected_source = candidate
                    break

                last_error = open_error
                failed_until[candidate_key] = _now() + 20.0

            if cap is None and proc is None:
                with _state_lock:
                    _state["zed_ok"] = False
                    _state["zed_error"] = f"nao abriu webcam ZED ({last_error or 'sem detalhe'})"
                _stop_event.wait(2.0)
                continue

            with _state_lock:
                _state["zed_ok"] = True
                _state["zed_error"] = None
                _state["zed_device"] = str(selected_source)

            while not _stop_event.is_set():
                if cap is not None:
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        frame = None
                else:
                    frame, pipe_carry = _read_frame_from_ffmpeg(proc, pipe_carry)

                if frame is None:
                    with _state_lock:
                        _state["zed_ok"] = False
                        _state["zed_error"] = f"falha de leitura da webcam ZED em {selected_source}"
                    failed_until[str(selected_source)] = _now() + 20.0
                    break

                now = _now()
                with _zed_face_lock:
                    should_queue = (not bool(_zed_face_state["pending"])) and (now >= float(_zed_face_state["next_scan_at"]))
                    if should_queue:
                        _zed_face_state["pending"] = True
                        _zed_face_state["frame"] = frame.copy()
                        _zed_face_event.set()
                    known = bool(_zed_face_state["known"])
                    label = str(_zed_face_state["label"])
                    bbox = _zed_face_state["bbox"]

                annotated_frame = frame
                if known and bbox is not None:
                    x, y, bw, bh = bbox
                    cv2.rectangle(annotated_frame, (x, y), (x + bw, y + bh), (80, 230, 120), 2)
                    cv2.putText(
                        annotated_frame,
                        label,
                        (x, max(18, y - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.65,
                        (80, 230, 120),
                        2,
                    )

                with _state_lock:
                    if isinstance(frame, np.ndarray) and frame.size > 0:
                        _state["zed"] = annotated_frame
                        _state["zed_ok"] = True
                        _state["zed_error"] = None
                        _state["zed_last_frame_at"] = _now()

                fps_counter += 1
                now = _now()
                elapsed = now - fps_mark
                if elapsed >= 1.0:
                    with _state_lock:
                        _state["fps_zed"] = round(fps_counter / elapsed, 1)
                    fps_counter = 0
                    fps_mark = now

                _stop_event.wait(max(0.001, 1.0 / max(1, ZED_FPS)))
        except Exception as exc:
            with _state_lock:
                _state["zed_ok"] = False
                _state["zed_error"] = f"erro na webcam ZED: {exc}"
            _stop_event.wait(2.0)
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
            if proc is not None:
                try:
                    proc.terminate()
                    proc.wait(timeout=1)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass


def _get_status_snapshot() -> dict:
    with _state_lock:
        state = {
            "distance_m": _state["distance_m"],
            "left_clearance_m": _state["left_clearance_m"],
            "center_clearance_m": _state["center_clearance_m"],
            "right_clearance_m": _state["right_clearance_m"],
            "tilt_deg": _state["tilt_deg"],
            "tilt_target_deg": _state["tilt_target_deg"],
            "accel": list(_state["accel"]),
            "fps_rgb": _state["fps_rgb"],
            "fps_depth": _state["fps_depth"],
            "kinect_ok": _state["kinect_ok"],
            "kinect_error": _state["kinect_error"],
            "last_frame_at": _state["last_frame_at"],
        }
    with _control_lock:
        control = {
            "mode": _control_state["mode"],
            "manual_speed": _control_state["manual_speed"],
            "manual_steering": _control_state["manual_steering"],
            "active_speed": _control_state["active_speed"],
            "active_steering": _control_state["active_steering"],
            "updated_at": _control_state["updated_at"],
            "last_source": _control_state["last_source"],
            "arduino_sent": _control_state["arduino_sent"],
            "arduino_error": _control_state["arduino_error"],
        }
    with _autopilot_lock:
        autopilot = dict(_autopilot_state)
    with _arduino_lock:
        arduino = dict(_arduino_state)

    last_frame_at = state["last_frame_at"]
    state["frame_age_s"] = None if last_frame_at <= 0 else round(_now() - last_frame_at, 2)
    zed_last_frame_at = float(_state["zed_last_frame_at"])
    zed_frame_age = None if zed_last_frame_at <= 0 else round(_now() - zed_last_frame_at, 2)

    return {
        "ok": True,
        "state": state,
        "agv": control,
        "autopilot": autopilot,
        "arduino": arduino,
        "kinect": {
            "stabilization_enabled": _get_kinect_stabilization(),
        },
        "zed": {
            "online": bool(_state["zed_ok"]),
            "error": _state["zed_error"],
            "fps": float(_state["fps_zed"]),
            "frame_age_s": zed_frame_age,
            "device": _state.get("zed_device") or ZED_DEVICE,
        },
        "face": _face_status_snapshot(),
        "settings": dict(_settings),
        "runtime": {
            "host": DEFAULT_HOST,
            "port": DEFAULT_PORT,
            "source": os.path.basename(__file__),
            "project_root": PROJECT_ROOT,
        },
    }


def _capture_loop() -> None:
    if freenect is None:
        while not _stop_event.is_set():
            with _state_lock:
                _state["kinect_ok"] = False
                _state["kinect_error"] = "python3-freenect nao disponivel no sistema"
            _stop_event.wait(1.0)
        return

    # Fila mínima para passar dados brutos das callbacks (rápidas) para o
    # thread de processamento pesado (face detection, depth metrics).
    _raw_state: dict = {"rgb": None, "depth": None, "rgb_fresh": False, "depth_fresh": False}
    _raw_lock = threading.Lock()
    _process_event = threading.Event()

    # FPS contadores
    rgb_counter = [0]
    depth_counter = [0]
    fps_mark = [_now()]

    def _process_loop():
        """Thread separado: face detection + depth metrics (pesado, libera GIL)."""
        while not _stop_event.is_set():
            _process_event.wait(timeout=0.1)
            _process_event.clear()

            with _raw_lock:
                rgb = _raw_state["rgb"] if _raw_state["rgb_fresh"] else None
                dep = _raw_state["depth"] if _raw_state["depth_fresh"] else None
                _raw_state["rgb_fresh"] = False
                _raw_state["depth_fresh"] = False

            if rgb is not None:
                with _state_lock:
                    _state["rgb_raw"] = rgb
                    _state["rgb"] = rgb
                    _state["kinect_ok"] = True
                    _state["kinect_error"] = None
                    _state["last_frame_at"] = _now()
                # FPS
                rgb_counter[0] += 1
                now = _now()
                elapsed = now - fps_mark[0]
                if elapsed >= 1.0:
                    with _state_lock:
                        _state["fps_rgb"] = round(rgb_counter[0] / elapsed, 1)
                        _state["fps_depth"] = round(depth_counter[0] / elapsed, 1)
                    rgb_counter[0] = 0
                    depth_counter[0] = 0
                    fps_mark[0] = now

            if dep is not None:
                depth_array = np.asarray(dep)
                is_mm, distance, left, center, right = _compute_depth_metrics(depth_array)
                with _state_lock:
                    _state["depth"] = depth_array
                    _state["depth_mm"] = is_mm
                    _state["distance_m"] = distance
                    _state["left_clearance_m"] = left
                    _state["center_clearance_m"] = center
                    _state["right_clearance_m"] = right
                depth_counter[0] += 1

    proc_thread = threading.Thread(target=_process_loop, daemon=True, name="agv-kinect-proc")
    proc_thread.start()

    while not _stop_event.is_set():
        _release_kinect_usb_claims()
        available, error = _kinect_device_available()
        if not available:
            with _state_lock:
                _state["kinect_ok"] = False
                _state["kinect_error"] = error
            _stop_event.wait(4.0)
            continue

        # Callbacks MÍNIMAS: apenas copia dados brutos e sinaliza evento
        def _video_cb(dev, data, timestamp):
            _do_tilt_body(dev)
            with _raw_lock:
                _raw_state["rgb"] = cv2.cvtColor(data, cv2.COLOR_RGB2BGR)
                _raw_state["rgb_fresh"] = True
            _process_event.set()

        def _depth_cb(dev, data, timestamp):
            with _raw_lock:
                _raw_state["depth"] = data
                _raw_state["depth_fresh"] = True
            _process_event.set()

        def _body_cb(dev, ctx):
            if _stop_event.is_set():
                raise freenect.Kill
            _do_tilt_body(dev)

        try:
            freenect.runloop(depth=_depth_cb, video=_video_cb, body=_body_cb)
        except Exception as exc:
            with _state_lock:
                _state["kinect_ok"] = False
                _state["kinect_error"] = str(exc) or "falha no Kinect"
            try:
                if hasattr(freenect, "sync_stop"):
                    freenect.sync_stop()
            except Exception:
                pass
            if not _stop_event.is_set():
                _stop_event.wait(5.0)


def _compute_autopilot_command() -> tuple[int, int, str]:
    with _state_lock:
        kinect_ok = bool(_state["kinect_ok"])
        left = _state["left_clearance_m"]
        center = _state["center_clearance_m"]
        right = _state["right_clearance_m"]
        distance = _state["distance_m"]

    if not kinect_ok:
        return 0, 0, "sem-kinect"
    if center is None and distance is None:
        return 0, 0, "sem-depth"

    center_distance = center if center is not None else distance
    if center_distance is None:
        return 0, 0, "sem-distancia"

    left_value = left if left is not None else center_distance
    right_value = right if right is not None else center_distance

    if center_distance < 0.35:
        turn = 70 if right_value >= left_value else -70
        return 0, turn, "obstaculo-muito-perto"
    if center_distance < 0.60:
        turn = 65 if right_value >= left_value else -65
        return 18, turn, "desvio-curto"
    if center_distance < 0.95:
        turn = 45 if right_value >= left_value else -45
        return 32, turn, "desvio-suave"

    steering = 0
    if left is not None and right is not None:
        diff = right - left
        steering = _clamp(int(diff * 70.0), -35, 35)
        if abs(steering) < 8:
            steering = 0
    return 48, steering, "livre"


def _autopilot_loop() -> None:
    while not _stop_event.is_set():
        with _control_lock:
            auto_mode = _control_state["mode"] == "auto"

        if auto_mode:
            speed, steering, reason = _compute_autopilot_command()
            with _autopilot_lock:
                _autopilot_state["running"] = True
                _autopilot_state["target_speed"] = speed
                _autopilot_state["target_steering"] = steering
                _autopilot_state["last_reason"] = reason
                _autopilot_state["last_tick"] = _now()
        else:
            with _autopilot_lock:
                _autopilot_state["running"] = False
                _autopilot_state["target_speed"] = 0
                _autopilot_state["target_steering"] = 0
                _autopilot_state["last_reason"] = "manual"
                _autopilot_state["last_tick"] = _now()

        _stop_event.wait(AUTO_LOOP_SECONDS)


def _control_loop() -> None:
    while not _stop_event.is_set():
        with _control_lock:
            mode = _control_state["mode"]
            manual_speed = _control_state["manual_speed"]
            manual_steering = _control_state["manual_steering"]
            source = _control_state["last_source"]
            updated_at = _control_state["updated_at"]

        if mode == "auto":
            with _autopilot_lock:
                speed = int(_autopilot_state["target_speed"])
                steering = int(_autopilot_state["target_steering"])
            command_source = "autopilot"
        else:
            # Watchdog: se nenhum novo comando chegou dentro do timeout, para o AGV.
            if WATCHDOG_TIMEOUT_S > 0 and (_now() - updated_at) > WATCHDOG_TIMEOUT_S:
                speed = 0
                steering = 0
                command_source = "watchdog"
            else:
                speed = manual_speed
                steering = manual_steering
                command_source = source

        sent = _send_to_arduino(speed, steering, mode, command_source)
        error = None
        if not sent:
            with _arduino_lock:
                error = _arduino_state.get("last_error")

        with _control_lock:
            _control_state["active_speed"] = speed
            _control_state["active_steering"] = steering
            _control_state["arduino_sent"] = bool(sent)
            _control_state["arduino_error"] = error

        _stop_event.wait(CONTROL_LOOP_SECONDS)


def start_runtime() -> None:
    global _runtime_started

    with _runtime_lock:
        if _runtime_started:
            return
        _runtime_started = True
        _stop_event.clear()
        _load_known_faces()

        specs = [
            ("agv-kinect", _capture_loop),
            ("agv-zed-face", _zed_face_worker_loop),
            ("agv-zed", _zed_capture_loop),
            ("agv-autopilot", _autopilot_loop),
            ("agv-control", _control_loop),
        ]
        for name, target in specs:
            thread = threading.Thread(target=target, daemon=True, name=name)
            thread.start()
            _threads.append(thread)

        log.info("Runtime agv iniciado")


def stop_runtime() -> None:
    global _runtime_started

    with _runtime_lock:
        if not _runtime_started:
            return
        _runtime_started = False
        _stop_event.set()

    with _arduino_lock:
        _close_arduino_locked()
        _arduino_state["connected"] = False

    for thread in list(_threads):
        thread.join(timeout=1.0)
    _threads.clear()
    log.info("Runtime agv finalizado")


atexit.register(stop_runtime)


def _mjpeg_generator(kind: str, fps: int):
    interval = 1.0 / max(1, fps)
    while True:
        quality = 82
        if kind == "rgb":
            with _state_lock:
                frame = None if _state["rgb"] is None else _state["rgb"].copy()
                error = _state["kinect_error"]
            if frame is None:
                frame = _placeholder_frame("Aguardando Kinect RGB")
                if error:
                    cv2.putText(frame, error[:44], (40, 280), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (120, 180, 255), 2)
        elif kind == "depth":
            with _state_lock:
                depth = None if _state["depth"] is None else _state["depth"].copy()
                is_mm = bool(_state["depth_mm"])
                error = _state["kinect_error"]
            if depth is None:
                frame = _placeholder_frame("Aguardando Kinect Depth")
                if error:
                    cv2.putText(frame, error[:44], (40, 280), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (120, 180, 255), 2)
            else:
                frame = _depth_to_colormap(depth, is_mm)
        else:
            with _state_lock:
                frame = None if _state["zed"] is None else _state["zed"].copy()
                error = _state["zed_error"]
            if frame is None:
                frame = _placeholder_frame("Aguardando webcam ZED")
                if error:
                    cv2.putText(frame, error[:44], (40, 280), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (120, 180, 255), 2)
            else:
                h, w = frame.shape[:2]
                if ZED_STREAM_MAX_WIDTH > 0 and w > ZED_STREAM_MAX_WIDTH:
                    target_w = int(ZED_STREAM_MAX_WIDTH)
                    target_h = max(2, int((h * target_w) / max(1, w)))
                    frame = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_AREA)
            quality = _clamp(ZED_JPEG_QUALITY, 35, 95)

        data = _encode_jpeg(frame, quality=quality)
        if data is not None:
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\nContent-Length: "
                + str(len(data)).encode("ascii")
                + b"\r\n\r\n"
                + data
                + b"\r\n"
            )
        time.sleep(interval)


@app.route("/")
def route_index():
    return render_template("index.html"), 200, {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    }


@app.route("/video")
def route_video():
    return Response(_mjpeg_generator("rgb", VIDEO_FPS), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/depth_map")
def route_depth_map():
    return Response(_mjpeg_generator("depth", DEPTH_FPS), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/zed")
def route_zed():
    return Response(_mjpeg_generator("zed", ZED_FPS), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/api/video")
def route_api_video():
    return route_video()


@app.route("/api/depth_map")
def route_api_depth_map():
    return route_depth_map()


@app.route("/api/zed")
def route_api_zed():
    return route_zed()


@app.route("/status")
@app.route("/api/status")
def route_status():
    resp = jsonify(_get_status_snapshot())
    # Connection: close garante conexao TCP dedicada, sem compartilhar com os streams.
    resp.headers["Connection"] = "close"
    return resp


@app.route("/api/access_link")
def route_access_link():
    lan_ip = _detect_lan_ip()
    scheme = "https" if request.is_secure else "http"
    port = request.environ.get("SERVER_PORT", str(DEFAULT_PORT))
    return jsonify({
        "ok": True,
        "url": f"{scheme}://{lan_ip}:{port}/",
        "ip": lan_ip,
        "port": port,
    })


@app.route("/api/kinect/reconnect", methods=["POST"])
def route_kinect_reconnect():
    if freenect is not None:
        try:
            freenect.sync_stop()
        except Exception:
            pass
    _release_kinect_usb_claims()
    return jsonify({"ok": True, "requested": True})


@app.route("/api/kinect/tilt", methods=["POST"])
def route_kinect_tilt():
    payload = request.get_json(silent=True) or {}
    angle = float(payload.get("angle", 0.0))
    ok, message, applied = _command_kinect_tilt(angle)
    return jsonify({"ok": ok, "message": message, "tilt_target_deg": applied})


@app.route("/api/kinect/stabilization", methods=["GET", "POST"])
def route_kinect_stabilization():
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        enabled = bool(payload.get("enabled", True))
        applied = _set_kinect_stabilization(enabled)
        return jsonify({"ok": True, "enabled": applied})

    return jsonify({"ok": True, "enabled": _get_kinect_stabilization()})


@app.route("/api/face/status")
def route_face_status():
    return jsonify({"ok": True, "face": _face_status_snapshot()})


@app.route("/api/face/register_face", methods=["POST"])
def route_face_register_face():
    face_name = _normalize_face_name(request.form.get("name", ""))
    image_file = request.files.get("image")

    if not face_name:
        return jsonify({"ok": False, "error": "name_required"}), 400
    if image_file is None or not image_file.filename:
        return jsonify({"ok": False, "error": "image_required"}), 400

    image_bytes = image_file.read()
    if not image_bytes:
        return jsonify({"ok": False, "error": "empty_image"}), 400

    frame = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return jsonify({"ok": False, "error": "invalid_image"}), 400

    if face_recognition is None:
        return jsonify({"ok": False, "error": "face_recognition_not_installed"}), 503

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    encodings = face_recognition.face_encodings(rgb)
    if len(encodings) == 0:
        return jsonify({"ok": False, "error": "no_face_detected"}), 400

    descriptor = np.array(encodings[0], dtype=np.float32)
    extension = os.path.splitext(image_file.filename or "")[1].lower()
    if extension not in (".jpg", ".jpeg", ".png", ".bmp", ".webp"):
        extension = ".jpg"

    os.makedirs(FACE_UPLOADS_DIR, exist_ok=True)
    stored_path = os.path.join(FACE_UPLOADS_DIR, f"{_face_name_slug(face_name)}{extension}")
    if not cv2.imwrite(stored_path, frame):
        return jsonify({"ok": False, "error": "failed_to_save_image"}), 500

    try:
        known_encodings, known_names, known_image_paths = _load_face_database()
    except Exception:
        known_encodings, known_names, known_image_paths = [], [], []

    target_index = None
    face_name_lower = face_name.lower()
    for index, existing_name in enumerate(known_names):
        if str(existing_name).lower() == face_name_lower:
            target_index = index
            break

    project_image_path = os.path.relpath(stored_path, PROJECT_ROOT)
    if target_index is None:
        known_encodings.append(descriptor)
        known_names.append(face_name)
        known_image_paths.append(project_image_path)
    else:
        known_encodings[target_index] = descriptor
        known_names[target_index] = face_name
        known_image_paths[target_index] = project_image_path

    _save_face_database(known_encodings, known_names, known_image_paths)
    _load_known_faces()
    _set_selected_face(face_name)

    return jsonify({
        "ok": True,
        "name": face_name,
        "image_path": project_image_path,
        "face": _face_status_snapshot(),
    })


@app.route("/api/face/select", methods=["POST"])
def route_face_select():
    payload = request.get_json(silent=True) or {}
    ok, message = _set_selected_face(str(payload.get("name", "")))
    status_code = 200 if ok else 404
    return jsonify({"ok": ok, "message": message, "face": _face_status_snapshot()}), status_code

#def route_face_register_face():
#    with _state_lock:
#        frame = None if _state["rgb_raw"] is None else _state["rgb_raw"].copy()
#
#    if frame is None:
#        return jsonify({"ok": False, "error": "no_frame"}), 400
#
#    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
#    bbox, descriptor = _face_extract_primary(gray)
#    if bbox is None or descriptor is None:
#        return jsonify({"ok": False, "error": "no_face_detected"}), 400
#
#    _save_kauan_face_descriptor(descriptor)
#    with _face_lock:
#        _face_runtime["known"] = True
#        _face_runtime["label"] = "e o Kauan"
#        _face_runtime["distance"] = 0.0
#        _face_runtime["last_seen_at"] = _now()
#        _face_runtime["bbox"] = bbox
#        _face_runtime["next_scan_at"] = 0.0
#
#    return jsonify({"ok": True, "face": _face_status_snapshot()})


@app.route("/api/control", methods=["POST"])
def route_control():
    payload = request.get_json(silent=True) or {}
    mode = str(payload.get("mode", "manual")).strip().lower()
    if mode not in ("manual", "auto"):
        mode = "manual"

    try:
        speed = int(payload.get("speed", 0))
    except Exception:
        speed = 0
    try:
        steering = int(payload.get("steering", 0))
    except Exception:
        steering = 0

    source = str(payload.get("source", "web")).strip() or "web"
    speed = _clamp(speed, -100, 100)
    steering = _clamp(steering, -100, 100)

    with _control_lock:
        _control_state["mode"] = mode
        _control_state["manual_speed"] = speed
        _control_state["manual_steering"] = steering
        _control_state["updated_at"] = _now()
        _control_state["last_source"] = source

    # Resposta minima - Connection: close para nao bloquear pool de conexoes do browser.
    resp = jsonify({"ok": True})
    resp.headers["Connection"] = "close"
    return resp


@app.route("/api/settings", methods=["GET", "POST"])
def route_settings():
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        if "speed_limit_pct" in payload:
            try:
                pct = max(0, min(100, int(payload["speed_limit_pct"])))
            except Exception:
                return jsonify({"ok": False, "error": "invalid speed_limit_pct"}), 400
            with _settings_lock:
                _settings["speed_limit_pct"] = pct
    with _settings_lock:
        return jsonify({"ok": True, "settings": dict(_settings)})


@app.route("/api/stop", methods=["POST"])
def route_stop():
    _send_to_arduino(0, 0, "manual", "stop")
    with _control_lock:
        _control_state["mode"] = "manual"
        _control_state["manual_speed"] = 0
        _control_state["manual_steering"] = 0
        _control_state["active_speed"] = 0
        _control_state["active_steering"] = 0
        _control_state["updated_at"] = _now()
        _control_state["last_source"] = "stop"
        _control_state["arduino_sent"] = True
        _control_state["arduino_error"] = None

    resp = jsonify({"ok": True})
    resp.headers["Connection"] = "close"
    return resp


def create_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> AGVServerController:
    return AGVServerController(host, port)


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    controller = create_server(host=host, port=port)
    controller.start()
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        controller.stop()


def main() -> int:
    serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
