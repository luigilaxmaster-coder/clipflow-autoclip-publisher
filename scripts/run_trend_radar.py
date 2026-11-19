"""Punto de entrada principal para la automatización diaria de ClipFlow Radar.
Ejecutable manualmente o programable en Antigravity Automations / Windows Task Scheduler.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from dotenv import load_dotenv

root_dir = Path(__file__).resolve().parent.parent
load_dotenv(root_dir / ".env")

from clipflow.multiagent import MultiAgentSwarmManager
from clipflow.radar import LiveStreamRadar

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def main():
    parser = argparse.ArgumentParser(description="ClipFlow - Live Stream Trend Radar & Multi-Agent Clipper")
    parser.add_argument("--region", choices=["es", "en", "all"], default="es", help="Región objetivo (es: España/LATAM, en: USA)")
    parser.add_argument("--limit", type=int, default=10, help="Límite diario de videos procesados (máx 10)")
    args = parser.parse_args()

    print("=================================================================")
    print("   CLIPFLOW RADAR: AUTOMATIZACIÓN MULTI-AGENTE DE DIRECTOS       ")
    print(f"   Región: {args.region.upper()} | Límite diario: {args.limit} videos")
    print("=================================================================")

    radar = LiveStreamRadar()
    swarm = MultiAgentSwarmManager(daily_limit=args.limit)

    active_streams = []
    if args.region in ("es", "all"):
        active_streams.extend(radar.scan_all_targets(region="es"))
    if args.region in ("en", "all"):
        active_streams.extend(radar.scan_all_targets(region="en"))

    if not active_streams:
        print("\n[Radar] No se encontraron emisiones en directo activas de los creadores en este ciclo.")
        print("[Radar] El sistema volverá a escanear en el próximo intervalo programado.")
        return

    print(f"\n[Radar] Se encontraron {len(active_streams)} directos activos para procesar.")
    results = swarm.process_streams(active_streams)

    print("\n=================== RESUMEN DE EJECUCIÓN MULTI-AGENTE ===================")
    for r in results:
        status_symbol = "✓" if r.status == "COMPLETED" else "✗"
        print(f" {status_symbol} [{r.agent_id}] {r.creator} -> {r.status}")
        if r.gdrive_path:
            print(f"    Google Drive: {r.gdrive_path}")
            print(f"    Título: {r.title}")
        if r.error:
            print(f"    Error: {r.error}")

    print("\nTodos los clips generados están guardados en G:\\My Drive\\clips\\ y preparados en cola.")


if __name__ == "__main__":
    main()

