"""Enrutador inteligente de nicho hacia perfiles/cuentas independientes por plataforma."""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from clipflow.connections.service import ConnectionService
from clipflow.ingestion import is_safe_remote_url

logger = logging.getLogger(__name__)


def extract_link_context(url: str, timeout_sec: float = 12.0) -> Dict[str, Any]:
    """
    Extrae título, canal/uploader y etiquetas reales de un enlace YouTube/Bilibili
    sin descargar el video, validando primero contra SSRF.
    """
    ok, reason = is_safe_remote_url(url)
    if not ok:
        raise ValueError(reason)

    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--ignore-config",
        "--no-warnings",
        "--no-playlist",
        "--skip-download",
        "--dump-single-json",
        url,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout)
            title = str(data.get("fulltitle") or data.get("title") or "")
            uploader = str(data.get("uploader") or data.get("channel") or "")
            tags = [str(t) for t in (data.get("tags") or [])[:20]]
            categories = [str(c) for c in (data.get("categories") or [])]
            desc = str(data.get("description") or "")[:500]
            return {
                "url": url,
                "title": title,
                "uploader": uploader,
                "tags": tags,
                "categories": categories,
                "is_live": bool(data.get("is_live")),
                "duration_sec": data.get("duration"),
                "context_text": " ".join([title, uploader, " ".join(tags), " ".join(categories), desc]).strip(),
            }
    except Exception as e:
        logger.debug("No se pudo extraer metadata con yt-dlp para %s: %s", url, e)

    return {
        "url": url,
        "title": "",
        "uploader": "",
        "tags": [],
        "categories": [],
        "is_live": False,
        "duration_sec": None,
        "context_text": url,
    }


def route_text_to_profiles(
    db: Session,
    context_text: str,
    platforms: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Para un texto o contexto de video, identifica el perfil independiente ideal en cada plataforma
    según sus 'niche_tags', respetando la cuota diaria propia de cada cuenta.
    """
    conn_svc = ConnectionService(db)
    target_platforms = [p.lower().strip() for p in (platforms or ["youtube", "tiktok"]) if p.strip()]
    routes: Dict[str, Any] = {}

    for plat in target_platforms:
        match = conn_svc.resolve_account_by_niche(plat, context_text)
        if not match:
            routes[plat] = {
                "found": False,
                "reason": f"No hay perfiles configurados para {plat}.",
            }
            continue

        acc = match["connection"]
        routes[plat] = {
            "found": True,
            "connection_id": acc["connection_id"],
            "account_name": acc["account_name"],
            "account_id": acc["account_id"],
            "niche_tags": acc["niche_tags"],
            "matched_keywords": match["matched_keywords"],
            "score": match["score"],
            "reason": match["reason"],
            "used_today": acc["used_today"],
            "daily_upload_limit": acc["daily_upload_limit"],
            "remaining_today": acc["remaining_today"],
            "quota_available": acc["remaining_today"] > 0,
        }

    return routes
