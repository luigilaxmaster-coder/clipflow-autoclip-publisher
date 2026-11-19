"""Generador y adaptador de metadatos (títulos, descripciones, hashtags) por plataforma."""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional
from clipflow.models import PostDraft

YOUTUBE_TITLE_LIMIT = 100
TIKTOK_CAPTION_LIMIT = 2200

CJK_PATTERN = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def sanitize_text(text: str) -> str:
    """Limpia caracteres especiales, saltos excesivos y dobles espacios."""
    if not text:
        return ""
    text = re.sub(r"[\r\n]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def remove_cjk(text: str) -> str:
    """Elimina caracteres ideográficos CJK si el contenido es en español o inglés."""
    return CJK_PATTERN.sub("", text).strip()


class MetadataGenerator:
    def __init__(self, default_language: str = "es"):
        self.default_language = default_language

    def generate_drafts(
        self,
        render_id: str,
        base_title: str,
        base_description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        visibility_yt: str = "private",
        visibility_tt: str = "SELF_ONLY",
    ) -> List[PostDraft]:
        """Crea borradores de publicación diferenciados para YouTube y TikTok."""
        clean_title = sanitize_text(base_title)
        if not clean_title:
            clean_title = "Clip Destacado"

        clean_desc = sanitize_text(base_description or "")
        tags = [t.strip("#").lower() for t in (tags or []) if t.strip()]

        # Tags base sugeridos
        yt_tags = list(dict.fromkeys(["shorts", "viral"] + tags))
        tt_tags = list(dict.fromkeys(["fyp", "parati", "viral"] + tags))

        # 1. YouTube Shorts Draft
        yt_title = clean_title
        # Asegurar límite de 100 caracteres en YouTube
        if len(yt_title) > YOUTUBE_TITLE_LIMIT:
            yt_title = yt_title[:YOUTUBE_TITLE_LIMIT].rstrip()

        yt_desc = clean_desc or yt_title
        if "#Shorts" not in yt_desc:
            yt_desc = f"{yt_desc}\n\n#Shorts " + " ".join(f"#{t}" for t in yt_tags[:10])

        yt_draft = PostDraft(
            render_id=render_id,
            platform="youtube",
            title=yt_title,
            description=yt_desc,
            hashtags_json=json.dumps(yt_tags, ensure_ascii=False),
            visibility=visibility_yt,
        )

        # 2. TikTok Draft
        tt_title = clean_title
        if len(tt_title) > 150:
            tt_title = tt_title[:150].rstrip()

        tt_hashtags_str = " ".join(f"#{t}" for t in tt_tags[:8])
        tt_desc = f"{tt_title} {tt_hashtags_str}".strip()
        if len(tt_desc) > TIKTOK_CAPTION_LIMIT:
            tt_desc = tt_desc[:TIKTOK_CAPTION_LIMIT].rstrip()

        tt_draft = PostDraft(
            render_id=render_id,
            platform="tiktok",
            title=tt_title,
            description=tt_desc,
            hashtags_json=json.dumps(tt_tags, ensure_ascii=False),
            visibility=visibility_tt,
        )

        return [yt_draft, tt_draft]

    @staticmethod
    def apply_viral_formula(creator: str, topic: str = "GTA 6", hook_phrase: Optional[str] = None) -> Dict[str, str]:
        """Aplica la fórmula ganadora aprendida de los videos con mayor rendimiento."""
        formula_file = Path("data/viral_formulas.json")
        winning_keywords = ["momento", "directo", "increible", "nervios", "reaccion", "ajustes", "record"]
        winning_tags = ["#Shorts", "#GTA6", "#Gaming"]

        if formula_file.exists():
            try:
                f_data = json.loads(formula_file.read_text(encoding="utf-8"))
                insights = f_data.get("insights", {})
                if insights.get("high_impact_keywords"):
                    winning_keywords = insights["high_impact_keywords"]
                if insights.get("winning_hashtags"):
                    raw_tags = [t if t.startswith("#") else f"#{t}" for t in insights["winning_hashtags"]]
                    winning_tags = ["#Shorts" if t.lower() == "#shorts" else t for t in raw_tags]
                    if "#Shorts" not in winning_tags:
                        winning_tags = ["#Shorts"] + winning_tags
            except Exception:
                pass

        clean_creator = re.sub(r'[^a-zA-Z0-9 ]', '', creator).strip()
        kw = winning_keywords[0] if winning_keywords else "momento"

        if hook_phrase:
            clean_hook = re.sub(r'[^a-zA-Z0-9áéíóúÁÉÍÓÚñÑ ]', '', hook_phrase).strip()
            title = f"{clean_creator} {clean_hook} en {topic} {' '.join(winning_tags[:3])}"
        else:
            title = f"{clean_creator} en {kw} epico jugando {topic} {' '.join(winning_tags[:3])}"

        # Eliminar cualquier emoji accidental y acotar a 95 caracteres
        clean_title = sanitize_text(title)
        if len(clean_title) > 95:
            clean_title = clean_title[:95].rstrip()

        description = (
            f"El mejor clip de {clean_creator} en vivo jugando {topic}.\n\n"
            f"{' '.join(winning_tags[:5])} #{clean_creator.replace(' ', '')}"
        )

        return {"title": clean_title, "description": description}
