"""Generador y adaptador de metadatos (títulos, descripciones, hashtags) por plataforma."""
from __future__ import annotations

import json
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
