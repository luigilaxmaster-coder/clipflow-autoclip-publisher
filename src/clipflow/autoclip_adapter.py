"""Adaptador nativo para el motor de AutoClip (Studio, quick_output_runner, store)."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class AutoClipAdapter:
    """Intermediario entre clipflow y el motor de producción de AutoClip."""

    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = data_dir or os.getenv("AUTOCLIP_DATA_DIR")

    def run_produce_python(
        self,
        source: str,
        platforms: Optional[List[str]] = None,
        srt_path: Optional[str] = None,
        portrait_style: str = "podcast",
        template: Optional[str] = "classic",
        timeout: float = 3600.0,
    ) -> Dict[str, Any]:
        """
        Ejecuta la producción directamente a través del módulo backend.services.quick_output_runner.
        Retorna el diccionario con project_id, status y outputs.
        """
        from backend.services import quick_output_runner as quick
        from backend import __version__ as upstream_version

        platforms = platforms or ["youtube_shorts", "tiktok"]
        logger.info("Iniciando AutoClip produce: source=%s, platforms=%s", source, platforms)

        project_id = quick.start(
            source=source,
            platforms=platforms,
            srt_path=srt_path,
            portrait_style=portrait_style,
            template=template,
        )

        res = quick.wait(project_id, timeout=timeout)
        res["upstream_version"] = upstream_version
        return res

    def get_project_status(self, project_id: str, export_kits: bool = True) -> Dict[str, Any]:
        """Consulta el estado del proyecto Studio en disco."""
        from backend.services import quick_output_runner as quick
        return quick.status(project_id, export_kits=export_kits)

    def extract_output_variants(self, produce_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Normaliza las variantes de salida generadas por AutoClip."""
        outputs = produce_result.get("outputs", [])
        variants: List[Dict[str, Any]] = []

        for out in outputs:
            variant_id = out.get("id")
            strategy_id = out.get("strategy_id")
            status = out.get("status")
            video_path = out.get("video_path")
            cover_path = out.get("cover_path")
            kit_path = out.get("kit_path")
            post = out.get("post") or {}

            # Solo incluir o registrar si tiene video_path válido o está en proceso
            variants.append({
                "output_variant_id": variant_id,
                "strategy_id": strategy_id,
                "status": status,
                "video_path": video_path,
                "cover_path": cover_path,
                "kit_path": kit_path,
                "title": post.get("title") or (out.get("result") or {}).get("title"),
                "description": post.get("description"),
                "tags": post.get("tags") or [],
                "error": out.get("error"),
            })

        return variants
