"""Radar de directos en tendencia para ClipFlow.
Monitorea canales de alta relevancia (España y USA) y búsquedas en vivo de GTA 6 / Gaming.
Filtra únicamente transmisiones que están en directo ahora (is_live=True) o recién finalizadas (< 6h).
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("clipflow.radar")

# Creadores clave monitoreados por nicho y región
TARGET_CREATORS = {
    "es": [
        {"name": "TheGrefg", "channel_url": "https://www.youtube.com/@TheGrefg/live"},
        {"name": "DjMaRiiO", "channel_url": "https://www.youtube.com/@DjMaRiiO/live"},
        {"name": "IlloJuan", "channel_url": "https://www.youtube.com/@IlloJuan/live"},
        {"name": "RDjavi", "channel_url": "https://www.youtube.com/@RDjavi/live"},
        {"name": "Willyrex", "channel_url": "https://www.youtube.com/@Willyrex/live"},
        {"name": "elxokas", "channel_url": "https://www.youtube.com/@elxokas/live"},
    ],
    "en": [
        {"name": "Typical Gamer", "channel_url": "https://www.youtube.com/@typicalgamer/live"},
        {"name": "theRadBrad", "channel_url": "https://www.youtube.com/@theRadBrad/live"},
        {"name": "TGG", "channel_url": "https://www.youtube.com/@TGG/live"},
    ]
}


class LiveStreamRadar:
    """Explorador inteligente de emisiones en directo activas."""

    def __init__(self, history_file: Optional[Path] = None):
        self.history_file = history_file or Path("data/radar_history.json")
        self.history = self._load_history()

    def _load_history(self) -> Dict[str, Any]:
        if self.history_file.exists():
            try:
                return json.loads(self.history_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"processed_streams": {}, "daily_count": {}}

    def _save_history(self) -> None:
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        self.history_file.write_text(json.dumps(self.history, indent=2, ensure_ascii=False), encoding="utf-8")

    def check_channel_live(self, creator_name: str, live_url: str) -> Optional[Dict[str, Any]]:
        """Verifica con yt-dlp si el canal está transmitiendo en directo en este instante."""
        cmd = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--dump-json",
            "--no-playlist",
            "--skip-download",
            live_url,
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout.strip())
                is_live = data.get("is_live") or data.get("live_status") == "is_live"
                video_id = data.get("id")
                
                # Descartar si ya fue procesado en las últimas 24 horas
                if video_id in self.history.get("processed_streams", {}):
                    return None

                return {
                    "video_id": video_id,
                    "url": data.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}",
                    "title": data.get("title"),
                    "creator": creator_name,
                    "is_live": is_live,
                    "view_count": data.get("view_count") or data.get("concurrent_view_count") or 0,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
        except Exception as e:
            logger.debug("Canal %s no está en vivo actualmente: %s", creator_name, e)
        return None

    def search_live_topics(self, query: str = "GTA 6 en vivo", max_results: int = 5) -> List[Dict[str, Any]]:
        """Busca emisiones en directo activas mediante búsqueda de YouTube."""
        cmd = [
            sys.executable,
            "-m",
            "yt_dlp",
            f"ytsearch{max_results}:{query}",
            "--dump-json",
            "--default-search", "ytsearch",
            "--no-playlist",
            "--skip-download",
        ]
        found = []
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
            if res.returncode == 0:
                for line in res.stdout.strip().splitlines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        is_live = data.get("is_live") or data.get("live_status") == "is_live"
                        video_id = data.get("id")
                        if video_id and is_live and video_id not in self.history.get("processed_streams", {}):
                            found.append({
                                "video_id": video_id,
                                "url": f"https://www.youtube.com/watch?v={video_id}",
                                "title": data.get("title"),
                                "creator": data.get("uploader") or data.get("channel"),
                                "is_live": True,
                                "view_count": data.get("concurrent_view_count") or data.get("view_count") or 0,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            })
                    except Exception:
                        continue
        except Exception as e:
            logger.warning("Error buscando temas en vivo: %s", e)
        return found

    def scan_all_targets(self, region: str = "es") -> List[Dict[str, Any]]:
        """Escanea todos los canales fijados y la búsqueda de directos en tendencia."""
        creators = TARGET_CREATORS.get(region, TARGET_CREATORS["es"])
        active_streams = []

        print(f"\n[Radar] Escaneando canales prioritarios ({region.upper()})...")
        for item in creators:
            live_info = self.check_channel_live(item["name"], item["channel_url"])
            if live_info and live_info.get("is_live"):
                print(f"  🔴 EN VIVO DETECTADO: {live_info['creator']} - '{live_info['title']}' ({live_info['view_count']} viewers)")
                active_streams.append(live_info)
            else:
                print(f"  ⚪ {item['name']}: Sin emisión activa en este momento.")

        # Buscar también directos por término clave en tendencia
        query = "GTA 6 en vivo" if region == "es" else "GTA 6 live stream"
        print(f"\n[Radar] Buscando directos en tendencia ('{query}')...")
        topic_streams = self.search_live_topics(query=query, max_results=5)
        for s in topic_streams:
            if not any(x["video_id"] == s["video_id"] for x in active_streams):
                print(f"  🔴 DIRECTO TENDENCIA: {s['creator']} - '{s['title']}' ({s['view_count']} viewers)")
                active_streams.append(s)

        return active_streams
