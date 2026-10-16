#!/usr/bin/env python3
"""Launcher principal do AGV.

Sobe o servidor web no mesmo processo e abre o painel no navegador.
Assim sobra um unico executavel principal para o projeto.
"""

import os
import atexit
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
import webbrowser

try:
    import fcntl
except ImportError:
    fcntl = None

try:
    import msvcrt
except ImportError:
    msvcrt = None


def _ensure_project_venv_python() -> None:
    if getattr(sys, "frozen", False):
        return
    if os.environ.get("AGV_VENV_REEXEC") == "1":
        return

    project_root = os.path.dirname(os.path.abspath(__file__))
    if sys.platform == "win32":
        venv_python = os.path.join(project_root, ".venv", "Scripts", "python.exe")
    else:
        venv_python = os.path.join(project_root, ".venv", "bin", "python")

    if not os.path.exists(venv_python):
        return

    current = os.path.realpath(sys.executable)
    expected = os.path.realpath(venv_python)
    if current == expected:
        return

    env = os.environ.copy()
    env["AGV_VENV_REEXEC"] = "1"
    if sys.platform == "win32":
        ret = subprocess.call([expected] + sys.argv, env=env)
        sys.exit(ret)
    else:
        os.execve(expected, [expected] + sys.argv, env)


_ensure_project_venv_python()

from pc_agv.iniciar_sistema import _release_kinect_usb_claims, create_server


HOST = os.environ.get("AGV_HOST", "0.0.0.0").strip() or "0.0.0.0"
PORT = int(os.environ.get("AGV_PORT", "5000"))
URL = f"http://127.0.0.1:{PORT}"
_LOCK_HANDLE = None
_RUNNING = True
_SERVER = None


def _lock_file_path() -> str:
    if sys.platform == "win32":
        return os.path.join(tempfile.gettempdir(), "agv_launch_production.lock")
    return "/tmp/agv_launch_production.lock"


def _acquire_single_instance_lock() -> bool:
    global _LOCK_HANDLE

    lock_path = _lock_file_path()
    try:
        _LOCK_HANDLE = open(lock_path, "w", encoding="utf-8")
        if fcntl is not None:
            fcntl.flock(_LOCK_HANDLE.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif msvcrt is not None:
            msvcrt.locking(_LOCK_HANDLE.fileno(), msvcrt.LK_NBLCK, 1)
        _LOCK_HANDLE.write(str(os.getpid()))
        _LOCK_HANDLE.flush()
        return True
    except (OSError, IOError):
        return False


def _release_single_instance_lock() -> None:
    global _LOCK_HANDLE

    if _LOCK_HANDLE is None:
        return
    try:
        if fcntl is not None:
            fcntl.flock(_LOCK_HANDLE.fileno(), fcntl.LOCK_UN)
        elif msvcrt is not None:
            _LOCK_HANDLE.seek(0)
            msvcrt.locking(_LOCK_HANDLE.fileno(), msvcrt.LK_UNLCK, 1)
    except Exception:
        pass
    try:
        _LOCK_HANDLE.close()
    except Exception:
        pass
    _LOCK_HANDLE = None


def _handle_stop_signal(signum, _frame) -> None:
    global _RUNNING

    _RUNNING = False
    print(f"\nSinal {signum} recebido. Encerrando agv...")


atexit.register(_release_single_instance_lock)


def _ensure_visible_terminal() -> None:
    # Quando iniciado por clique duplo no Linux, pode rodar sem terminal visivel.
    # Nesse caso, relanca o proprio executavel dentro de um emulador de terminal.
    if os.environ.get("AGV_TERMINAL_RELAUNCHED") == "1":
        return
    if sys.stdout.isatty() and sys.stdin.isatty():
        return
    if not os.environ.get("DISPLAY"):
        return

    argv = [os.path.abspath(sys.argv[0])] + sys.argv[1:]
    if not getattr(sys, "frozen", False):
        argv = [sys.executable] + argv

    env = os.environ.copy()
    env["AGV_TERMINAL_RELAUNCHED"] = "1"

    candidates = [
        ["x-terminal-emulator", "-e"] + argv,
        ["gnome-terminal", "--"] + argv,
        ["konsole", "-e"] + argv,
        ["xfce4-terminal", "-e", " ".join(argv)],
        ["xterm", "-hold", "-e"] + argv,
    ]

    for cmd in candidates:
        binary = cmd[0]
        if shutil.which(binary) is None:
            continue
        try:
            proc = subprocess.Popen(cmd, env=env,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            time.sleep(0.9)
            if proc.poll() is not None and proc.returncode != 0:
                # terminal crashou antes de abrir (ex: gnome-terminal com snap quebrado)
                continue
            print("Abrindo terminal para mostrar os logs do agv...")
            raise SystemExit(0)
        except Exception:
            continue

    if sys.platform == "win32":
        log_path = os.path.join(tempfile.gettempdir(), "agv_production.log")
    else:
        log_path = "/tmp/agv_production.log"
    try:
        log_file = open(log_path, "a", buffering=1, encoding="utf-8")
        sys.stdout = log_file
        sys.stderr = log_file
        print(f"\n=== agv sem terminal — logs em {log_path} ===")
    except Exception:
        pass
    os.environ["AGV_TERMINAL_RELAUNCHED"] = "1"


def _is_server_online(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/api/status", timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


_ensure_visible_terminal()

if not _acquire_single_instance_lock():
    print("Outro agv ja esta em execucao neste PC.")
    if _is_server_online(URL):
        print(f"Abrindo painel existente: {URL}")
        webbrowser.open(URL)
        raise SystemExit(0)
    print("Use Ctrl+C na janela ja aberta para parar o projeto antes de abrir outro.")
    raise SystemExit(1)

signal.signal(signal.SIGINT, _handle_stop_signal)
signal.signal(signal.SIGTERM, _handle_stop_signal)

print("=" * 60)
print("  agv | Site + Kinect + Arduino")
print("=" * 60)

# Remove drivers de kernel que bloqueiam o Kinect via libusb (LIBUSB_ERROR_BUSY)
_release_kinect_usb_claims()

if _is_server_online(URL):
    print("\nServidor ja estava online. Reutilizando processo existente.")
    print(f"Abrindo: {URL}")
    webbrowser.open(URL)
    sys.exit(0)

print("\nIniciando servidor...")
_SERVER = create_server(host=HOST, port=PORT)
_SERVER.start()

print("Aguardando servidor responder", end="", flush=True)
for _ in range(25):
    time.sleep(1)
    print(".", end="", flush=True)
    try:
        urllib.request.urlopen(URL + "/api/status", timeout=1)
        break
    except Exception:
        pass
else:
    print("\nServidor nao respondeu em 25s. Verifique o Kinect e a serial.")
    _SERVER.stop()
    sys.exit(1)

print("\nServidor online!")
print(f"Abrindo: {URL}")
webbrowser.open(URL)

print("\nPressione Ctrl+C para parar.\n")
try:
    while _RUNNING:
        time.sleep(1)
except KeyboardInterrupt:
    _RUNNING = False
finally:
    print("\nEncerrando...")
    if _SERVER is not None:
        _SERVER.stop()
    print("Finalizado.")
