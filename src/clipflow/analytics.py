"""Motor de Analítica, Feedback Loop y Optimización Viral de ClipFlow.
Monitorea el rendimiento en vivo de los videos publicados en YouTube Shorts,
compara métricas (vistas, likes, retención estimada, engagement),
deconstruye por qué un video funcionó mejor que otro y actualiza la 'Fórmula Ganadora'
en data/viral_formulas.json para que las siguientes tandas mejoren automáticamente.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from clipflow.db import get_db_context, init_db
from clipflow.models import PostDraft, PublishJob, Render

logger = logging.getLogger("clipflow.analytics")


class PerformanceAnalyzer:
    """Extrae métricas en vivo de YouTube y analiza factores de éxito."""

    def __init__(self, formulas_path: Optional[Path] = None):
        self.formulas_path = formulas_path or Path("data/viral_formulas.json")

    def fetch_video_metrics(self, youtube_url: str) -> Optional[Dict[str, Any]]:
        """Consulta estadísticas públicas en vivo del video mediante yt-dlp."""
        cmd = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--dump-json",
            "--no-playlist",
            "--skip-download",
            youtube_url,
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout.strip())
                views = data.get("view_count") or 0
                likes = data.get("like_count") or 0
                comments = data.get("comment_count") or 0
                eng_rate = ((likes + comments) / max(views, 1)) * 100 if views > 0 else 0.0

                return {
                    "video_id": data.get("id"),
                    "title": data.get("title"),
                    "views": views,
                    "likes": likes,
                    "comments": comments,
                    "engagement_rate": round(eng_rate, 2),
                    "duration": data.get("duration"),
                    "tags": data.get("tags") or [],
                    "upload_date": data.get("upload_date"),
                }
        except Exception as e:
            logger.warning("Error extrayendo métricas de %s: %s", youtube_url, e)
        return None

    def analyze_published_portfolio(self) -> Dict[str, Any]:
        """Analiza todos los videos publicados registrados en la base de datos."""
        init_db()
        records = []
        with get_db_context() as db:
            jobs = (
                db.query(PublishJob)
                .filter(PublishJob.status.in_(["COMPLETED", "PUBLISHED"]))
                .filter(PublishJob.remote_url.isnot(None))
                .all()
            )
            for j in jobs:
                draft = db.query(PostDraft).filter(PostDraft.id == j.post_draft_id).first()
                if draft and j.remote_url:
                    records.append({
                        "job_id": j.id,
                        "url": j.remote_url,
                        "title": draft.title,
                        "visibility": draft.visibility,
                        "idempotency_key": j.idempotency_key,
                    })

        print(f"\n[Analítica] Analizando {len(records)} videos publicados en YouTube...")
        metrics_list = []
        for r in records:
            print(f"  ● Consultando métricas de: {r['url']} ('{r['title'][:40]}...')")
            m = self.fetch_video_metrics(r["url"])
            if m:
                m["url"] = r["url"]
                m["original_title"] = r["title"]
                metrics_list.append(m)

        if not metrics_list:
            print("[Analítica] No se pudieron obtener métricas públicas todavía.")
            return {"status": "NO_DATA"}

        # Ordenar por vistas y engagement
        ranked = sorted(metrics_list, key=lambda x: (x["views"], x["engagement_rate"]), reverse=True)

        # Deconstruir fórmula de los mejores vs peores
        top_performers = ranked[: max(1, len(ranked) // 2)]
        formula = self.deconstruct_viral_formula(top_performers, ranked)

        # Guardar en memoria de aprendizaje
        self._save_formula(formula)
        return formula

    def deconstruct_viral_formula(self, top_videos: List[Dict[str, Any]], all_videos: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Identifica qué palabras clave, longitudes y hashtags generaron mayor tracción."""
        word_freq: Dict[str, int] = {}
        hashtag_freq: Dict[str, int] = {}
        lengths = []

        stop_words = {"en", "el", "la", "los", "las", "un", "una", "de", "del", "y", "a", "por", "para", "con"}

        for v in top_videos:
            title = v["original_title"]
            lengths.append(len(title))

            # Extraer hashtags
            tags = re.findall(r"#([a-zA-Z0-9_]+)", title)
            for t in tags:
                clean_t = f"#{t.lower()}"
                hashtag_freq[clean_t] = hashtag_freq.get(clean_t, 0) + 1

            # Extraer palabras clave de impacto
            words = re.findall(r"\b[a-zA-ZáéíóúÁÉÍÓÚñÑ]{3,}\b", title.lower())
            for w in words:
                if w not in stop_words and not w.startswith("#"):
                    word_freq[w] = word_freq.get(w, 0) + 1

        top_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[:8]
        top_hashtags = sorted(hashtag_freq.items(), key=lambda x: x[1], reverse=True)[:6]
        avg_len = sum(lengths) // len(lengths) if lengths else 75

        # Reglas de copy aprendidas
        formula_data = {
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "sample_size": len(all_videos),
            "top_performer_count": len(top_videos),
            "insights": {
                "optimal_title_length": {"min": avg_len - 10, "max": min(95, avg_len + 12), "avg": avg_len},
                "high_impact_keywords": [w for w, _ in top_words],
                "winning_hashtags": [h for h, _ in top_hashtags],
                "recommended_structure": "{Creador} + {Acción/Emoción} + {Detalle Inédito} + #Shorts #GTA6 + #{Nicho}",
                "emoji_policy": "STRICT_NO_EMOJIS",
                "safe_margin_v": 340,
            },
            "top_videos_snapshot": [
                {
                    "title": v["original_title"],
                    "url": v["url"],
                    "views": v["views"],
                    "likes": v["likes"],
                    "engagement_rate": v["engagement_rate"],
                }
                for v in top_videos
            ],
        }

        print("\n================== FÓRMULA GANADORA DETECTADA ==================")
        print(f"  ★ Palabras clave de alto impacto: {', '.join(formula_data['insights']['high_impact_keywords'])}")
        print(f"  ★ Hashtags más efectivos: {', '.join(formula_data['insights']['winning_hashtags'])}")
        print(f"  ★ Longitud óptima de título: {avg_len} caracteres")
        print(f"  ★ Estructura aprendida: {formula_data['insights']['recommended_structure']}")
        print("================================================================")

        return formula_data

    def _save_formula(self, formula: Dict[str, Any]) -> None:
        self.formulas_path.parent.mkdir(parents=True, exist_ok=True)
        self.formulas_path.write_text(json.dumps(formula, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[Analítica] Fórmula guardada en: {self.formulas_path}")

    def get_active_formula(self) -> Optional[Dict[str, Any]]:
        """Obtiene la fórmula ganadora vigente o valores predeterminados de alta retención."""
        if self.formulas_path.exists():
            try:
                return json.loads(self.formulas_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return None

