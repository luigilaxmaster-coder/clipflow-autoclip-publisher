"""Supervisor multi-proceso para ejecutar FastAPI y el worker durable simultáneamente."""
from __future__ import annotations

import argparse
import logging
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import List, Optional

from clipflow.config import get_settings
from clipflow.db import init_db
from clipflow.security import get_owner_token

logger = logging.getLogger(__name__)


@dataclass
class ManagedProcess:
    name: str
    cmd: List[str]
    proc: Optional[subprocess.Popen] = None
    restart_count: int = 0
    last_restart_ts: float = 0.0
    backoff_sec: float = 2.0


class ProcessSupervisor:
    """Supervisa procesos hijos independientes con apagado limpio y reinicio controlado."""

    def __init__(self, processes: List[ManagedProcess], max_restarts_window: int = 5):
        self.processes = processes
        self.max_restarts_window = max_restarts_window
        self._stopping = False

    def start_all(self) -> None:
        for mp in self.processes:
            self._spawn(mp)

    def _spawn(self, mp: ManagedProcess) -> None:
        logger.info("[supervisor] Iniciando proceso '%s': %s", mp.name, " ".join(mp.cmd))
        mp.proc = subprocess.Popen(mp.cmd)

    def stop_all(self, timeout_sec: float = 10.0) -> None:
        self._stopping = True
        for mp in self.processes:
            if mp.proc and mp.proc.poll() is None:
                logger.info("[supervisor] Enviando terminación limpia a '%s' (PID %d)...", mp.name, mp.proc.pid)
                try:
                    mp.proc.terminate()
                except OSError:
                    pass

        deadline = time.monotonic() + timeout_sec
        for mp in self.processes:
            if mp.proc and mp.proc.poll() is None:
                remaining = max(0.5, deadline - time.monotonic())
                try:
                    mp.proc.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    logger.warning("[supervisor] Forzando cierre de '%s' (PID %d).", mp.name, mp.proc.pid)
                    try:
                        mp.proc.kill()
                    except OSError:
                        pass

    def run_forever(self, poll_sec: float = 1.0) -> int:
        def _handle_signal(signum, _frame):
            logger.info("[supervisor] Señal %s recibida; iniciando apagado ordenado...", signum)
            self.stop_all()

        signal.signal(signal.SIGINT, _handle_signal)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _handle_signal)

        self.start_all()
        try:
            while not self._stopping:
                for mp in self.processes:
                    if mp.proc is None:
                        continue
                    code = mp.proc.poll()
                    if code is not None and not self._stopping:
                        now = time.monotonic()
                        if now - mp.last_restart_ts > 60.0:
                            mp.restart_count = 0
                            mp.backoff_sec = 2.0

                        mp.restart_count += 1
                        mp.last_restart_ts = now

                        if mp.restart_count > self.max_restarts_window:
                            logger.error(
                                "[supervisor] Proceso '%s' superó el máximo de reinicios (%d/min). Deteniendo.",
                                mp.name,
                                self.max_restarts_window,
                            )
                            self.stop_all()
                            return 1

                        wait_time = min(30.0, mp.backoff_sec)
                        logger.warning(
                            "[supervisor] Proceso '%s' finalizó (código %s). Reiniciando en %.1fs (intento %d)...",
                            mp.name,
                            code,
                            wait_time,
                            mp.restart_count,
                        )
                        time.sleep(wait_time)
                        if not self._stopping:
                            mp.backoff_sec = min(30.0, mp.backoff_sec * 2.0)
                            self._spawn(mp)

                time.sleep(poll_sec)
        finally:
            self.stop_all()
        return 0


def build_managed_processes(
    python_exe: str,
    host: str = "127.0.0.1",
    port: int = 8000,
    worker_interval: float = 5.0,
    web_only: bool = False,
    worker_only: bool = False,
    enable_live_peak: Optional[bool] = None,
) -> List[ManagedProcess]:
    procs: List[ManagedProcess] = []
    if not worker_only:
        procs.append(
            ManagedProcess(
                name="web-fastapi",
                cmd=[
                    python_exe,
                    "-m",
                    "uvicorn",
                    "clipflow.web.app:app",
                    "--host",
                    host,
                    "--port",
                    str(port),
                ],
            )
        )
    if not web_only:
        procs.append(
            ManagedProcess(
                name="queue-worker",
                cmd=[
                    python_exe,
                    "-m",
                    "clipflow.cli",
                    "worker",
                    "--interval",
                    str(worker_interval),
                ],
            )
        )
        lp_enabled = get_settings().live_peak.enabled if enable_live_peak is None else bool(enable_live_peak)
        if lp_enabled:
            procs.append(
                ManagedProcess(
                    name="live-peak-worker",
                    cmd=[
                        python_exe,
                        "-m",
                        "clipflow.cli",
                        "live-peak-worker",
                        "--interval",
                        str(worker_interval),
                    ],
                )
            )
    return procs


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Supervisor de AutoClip Publisher (FastAPI + Worker).")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--web-only", action="store_true")
    parser.add_argument("--worker-only", action="store_true")
    args = parser.parse_args(argv)

    if not args.worker_only and not get_owner_token():
        logger.error(
            "OWNER_TOKEN no está configurado. Define OWNER_TOKEN en .env o ejecuta scripts/run-publisher.ps1."
        )
        return 1

    init_db()
    procs = build_managed_processes(
        python_exe=sys.executable,
        host=args.host,
        port=args.port,
        worker_interval=args.interval,
        web_only=args.web_only,
        worker_only=args.worker_only,
    )
    supervisor = ProcessSupervisor(procs)
    return supervisor.run_forever()


if __name__ == "__main__":
    raise SystemExit(main())
