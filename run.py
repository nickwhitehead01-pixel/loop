#!/usr/bin/env python3
"""
LoopLens Hub — setup and launcher script.

Usage:
    python run.py install   — install all dependencies, create data dirs, write .env
    python run.py start     — launch backend (uvicorn) + frontend (npm run dev)
    python run.py start --prod  — launch backend + frontend (npm start) in production mode
"""
from __future__ import annotations

import os
import platform
import shutil
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
VENV = BACKEND / ".venv"
DATA_DIR = BACKEND / "data"
CHROMA_DIR = DATA_DIR / "chroma"
UPLOADS_DIR = BACKEND / "uploads"
ENV_FILE = ROOT / ".env"

IS_WINDOWS = platform.system() == "Windows"
PYTHON = (VENV / "Scripts" / "python.exe") if IS_WINDOWS else (VENV / "bin" / "python")
PIP = (VENV / "Scripts" / "pip.exe") if IS_WINDOWS else (VENV / "bin" / "pip")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print(msg: str, *, prefix: str = ">>") -> None:
    print(f"{prefix} {msg}", flush=True)


def _check_version(cmd: list[str], min_major: int, min_minor: int, label: str) -> None:
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        print(f"ERROR: {label} not found. Please install it and re-run.", file=sys.stderr)
        sys.exit(1)
    # Extract first "M.m" from output
    for part in out.split():
        digits = part.lstrip("vV").split(".")
        if len(digits) >= 2 and digits[0].isdigit() and digits[1].isdigit():
            major, minor = int(digits[0]), int(digits[1])
            if (major, minor) < (min_major, min_minor):
                print(
                    f"ERROR: {label} {major}.{minor} found but {min_major}.{min_minor}+ required.",
                    file=sys.stderr,
                )
                sys.exit(1)
            _print(f"{label} {major}.{minor} OK")
            return
    _print(f"{label} found (version check skipped)")


def _run(cmd: list[str], cwd: Path | None = None, **kwargs) -> None:
    _print(f"Running: {' '.join(str(c) for c in cmd)}")
    subprocess.check_call(cmd, cwd=str(cwd) if cwd else None, **kwargs)


def _write_env_if_missing() -> None:
    if ENV_FILE.exists():
        _print(f".env already exists at {ENV_FILE} — skipping")
        return
    content = (
        "# LoopLens Hub configuration\n"
        "# Edit these values to customise your installation.\n\n"
        "# SQLite database path (relative to backend/ directory)\n"
        "DATABASE_URL=sqlite+aiosqlite:///./data/gemma_edu.db\n\n"
        "# ChromaDB storage path (relative to backend/ directory)\n"
        "CHROMA_DIR=./data/chroma\n\n"
        "# Ollama — must be running separately on this machine\n"
        "OLLAMA_BASE_URL=http://localhost:11434\n"
        "OLLAMA_MODEL_PUPIL=gemma4:e2b\n"
        "OLLAMA_MODEL_TEACHER=gemma4:e2b\n"
        "OLLAMA_EMBED_MODEL=nomic-embed-text\n\n"
        "# File uploads directory (relative to backend/ directory)\n"
        "UPLOAD_DIR=./uploads\n\n"
        "# Whisper model size. The `.en` variants are English-only and ~30–40%\n"
        "# faster than their multilingual counterparts. Tiers (fastest → most accurate):\n"
        "# tiny.en, base.en, small.en, medium.en — or drop `.en` for multilingual.\n"
        "WHISPER_MODEL_SIZE=base.en\n\n"
        "# Debug logging\n"
        "DEBUG=false\n"
    )
    ENV_FILE.write_text(content)
    _print(f".env written to {ENV_FILE}")


def _check_ollama() -> None:
    try:
        import urllib.request
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        _print("Ollama is reachable at http://localhost:11434")
    except Exception:
        _print(
            "WARNING: Ollama is not running at http://localhost:11434.\n"
            "         Start Ollama and pull required models before launching:\n"
            "           ollama pull gemma4:e2b\n"
            "           ollama pull nomic-embed-text",
            prefix="!!"
        )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_install() -> None:
    _print("=== LoopLens Hub — Install ===")

    # Check Python and Node versions
    _check_version([sys.executable, "--version"], 3, 11, "Python")
    _check_version(["node", "--version"], 18, 0, "Node.js")

    # Create Python virtual environment
    if not VENV.exists():
        _print(f"Creating venv at {VENV}...")
        _run([sys.executable, "-m", "venv", str(VENV)])
    else:
        _print(f"Venv already exists at {VENV}")

    # Install backend Python packages
    _print("Installing backend Python dependencies...")
    _run([str(PIP), "install", "--upgrade", "pip"], cwd=BACKEND)
    _run([str(PIP), "install", "-r", "requirements.txt"], cwd=BACKEND)

    # Install frontend Node packages
    _print("Installing frontend Node dependencies...")
    npm = "npm.cmd" if IS_WINDOWS else "npm"
    _run([npm, "install"], cwd=FRONTEND)

    # Create required directories
    for d in (DATA_DIR, CHROMA_DIR, UPLOADS_DIR):
        d.mkdir(parents=True, exist_ok=True)
        _print(f"Directory ensured: {d}")

    # Write .env defaults
    _write_env_if_missing()

    # Check Ollama
    _check_ollama()

    _print("")
    _print("=== Install complete ===")
    _print("Next steps:")
    _print("  1. Ensure Ollama is running:  ollama serve")
    _print("  2. Pull models (first time):  ollama pull gemma4:e2b && ollama pull nomic-embed-text")
    _print("  3. Start the hub:             python setup.py start")


def cmd_start(prod: bool = False) -> None:
    _print("=== LoopLens Hub — Start ===")

    if not PYTHON.exists():
        print(
            "ERROR: Virtual environment not found. Run 'python setup.py install' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Copy .env to backend/ so pydantic-settings picks it up
    backend_env = BACKEND / ".env"
    if ENV_FILE.exists() and not backend_env.exists():
        import shutil as _shutil
        _shutil.copy(ENV_FILE, backend_env)

    npm = "npm.cmd" if IS_WINDOWS else "npm"
    frontend_cmd = [npm, "start"] if prod else [npm, "run", "dev"]

    backend_env_vars = {**os.environ, "PYTHONPATH": str(BACKEND)}

    _print("Starting backend on http://localhost:8000 ...")
    backend_proc = subprocess.Popen(
        [str(PYTHON), "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
        cwd=str(BACKEND),
        env=backend_env_vars,
    )

    _print(f"Starting frontend on http://localhost:3000 ({'prod' if prod else 'dev'}) ...")
    frontend_proc = subprocess.Popen(
        frontend_cmd,
        cwd=str(FRONTEND),
    )

    _print("")
    _print("Hub running. Press Ctrl+C to stop both services.")
    _print("  Backend:  http://localhost:8000")
    _print("  Frontend: http://localhost:3000")
    _print("  API docs: http://localhost:8000/docs")

    def _shutdown(sig, frame):
        _print("\nShutting down...")
        frontend_proc.terminate()
        backend_proc.terminate()
        try:
            frontend_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            frontend_proc.kill()
        try:
            backend_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            backend_proc.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Wait for both processes; exit if either dies unexpectedly
    while True:
        be_rc = backend_proc.poll()
        fe_rc = frontend_proc.poll()
        if be_rc is not None:
            _print(f"Backend exited with code {be_rc}. Stopping frontend.")
            frontend_proc.terminate()
            sys.exit(be_rc)
        if fe_rc is not None:
            _print(f"Frontend exited with code {fe_rc}. Stopping backend.")
            backend_proc.terminate()
            sys.exit(fe_rc)
        import time
        time.sleep(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("install", "start"):
        print("Usage: python setup.py install | start [--prod]", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]
    if command == "install":
        cmd_install()
    elif command == "start":
        cmd_start(prod="--prod" in sys.argv)
