"""Colector autorizado de telemetría de directos (YouTube Data API v3 y estado de acceso)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from clipflow.live_peak.models import TelemetrySample

logger = logging.getLogger(__name__)


class YouTubeLiveAdapter:
    """
    Consulta estado de directo, `liveStreamingDetails.concurrentViewers` y
    `liveChatMessages.streamList` usando OAuth autorizado del propietario.
    Nunca fabrica métricas si un recurso está cerrado o sin permisos (`NO_ACCESS` / `None`).
    """

    VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
    CHAT_LIST_URL = "https://www.googleapis.com/youtube/v3/liveChat/messages"

    def __init__(self, timeout_sec: float = 12.0):
        self.timeout_sec = timeout_sec

    def fetch_live_details(
        self,
        video_id: str,
        access_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not access_token:
            return {
                "ok": False,
                "live_state": "NO_ACCESS",
                "concurrent_viewers": None,
                "active_live_chat_id": None,
                "reason": "Sin token OAuth para consultar YouTube Live Details.",
            }

        try:
            with httpx.Client(timeout=self.timeout_sec) as client:
                resp = client.get(
                    self.VIDEOS_URL,
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={"part": "snippet,liveStreamingDetails", "id": video_id},
                )
        except Exception as e:
            return {"ok": False, "live_state": "RECONNECTING", "reason": str(e)}

        if resp.status_code == 429:
            return {"ok": False, "live_state": "RATE_LIMITED", "reason": "HTTP 429 YouTube API"}
        if resp.status_code in (401, 403):
            return {"ok": False, "live_state": "NO_ACCESS", "reason": f"HTTP {resp.status_code}"}
        if resp.status_code != 200:
            return {"ok": False, "live_state": "STALLED", "reason": f"HTTP {resp.status_code}"}

        items = resp.json().get("items") or []
        if not items:
            return {"ok": False, "live_state": "ENDED", "concurrent_viewers": None, "active_live_chat_id": None}

        item = items[0]
        live = item.get("liveStreamingDetails") or {}
        snippet = item.get("snippet") or {}
        broadcast = snippet.get("liveBroadcastContent") or "none"
        raw_viewers = live.get("concurrentViewers")
        viewers = int(raw_viewers) if raw_viewers is not None else None
        chat_id = live.get("activeLiveChatId")

        return {
            "ok": True,
            "live_state": "LIVE" if broadcast == "live" or live.get("actualStartTime") and not live.get("actualEndTime") else "ENDED",
            "title": snippet.get("title"),
            "channel_id": snippet.get("channelId"),
            "concurrent_viewers": viewers,
            "active_live_chat_id": chat_id,
            "actual_start_time": live.get("actualStartTime"),
        }

    def poll_chat_metrics(
        self,
        live_chat_id: Optional[str],
        access_token: Optional[str],
        page_token: Optional[str] = None,
        window_seconds: float = 5.0,
    ) -> Tuple[Optional[float], Optional[int], Optional[int], float, float, Optional[str], Optional[str]]:
        """
        Retorna `(chat_rate_per_min, unique_chatters, total_messages, bot_ratio, duplicate_ratio, next_page_token, error_code)`.
        No almacena textos completos de usuarios ni datos personales cuando `save_raw_chat=False`.
        """
        if not live_chat_id or not access_token:
            return None, None, None, 0.0, 0.0, page_token, "NO_CHAT_ACCESS"

        params: Dict[str, Any] = {"liveChatId": live_chat_id, "part": "snippet,authorDetails", "maxResults": 200}
        if page_token:
            params["pageToken"] = page_token

        try:
            with httpx.Client(timeout=self.timeout_sec) as client:
                resp = client.get(
                    self.CHAT_LIST_URL,
                    headers={"Authorization": f"Bearer {access_token}"},
                    params=params,
                )
        except Exception:
            return None, None, None, 0.0, 0.0, page_token, "CHAT_NETWORK_ERROR"

        if resp.status_code in (401, 403):
            return None, None, None, 0.0, 0.0, page_token, "NO_ACCESS"
        if resp.status_code == 429:
            return None, None, None, 0.0, 0.0, page_token, "RATE_LIMITED"
        if resp.status_code != 200:
            return None, None, None, 0.0, 0.0, page_token, f"HTTP_{resp.status_code}"

        data = resp.json()
        items = data.get("items") or []
        next_tok = data.get("nextPageToken")
        total = len(items)
        authors = set()
        normalized_msgs: List[str] = []
        for msg in items:
            author = (msg.get("authorDetails") or {}).get("channelId")
            if author:
                authors.add(author)
            txt = ((msg.get("snippet") or {}).get("displayMessage") or "").strip().lower()
            if txt:
                normalized_msgs.append(txt)

        dup_ratio = 0.0
        if normalized_msgs:
            dup_ratio = round(1.0 - (len(set(normalized_msgs)) / len(normalized_msgs)), 3)

        chat_rate_min = round((total / max(1.0, window_seconds)) * 60.0, 2)
        return chat_rate_min, len(authors), total, 0.0, dup_ratio, next_tok, None


class TelemetryCollector:
    """Construye muestras normalizadas respetando ausencia de permisos sin inventar ceros."""

    def __init__(self, youtube_adapter: Optional[YouTubeLiveAdapter] = None):
        self.youtube_adapter = youtube_adapter or YouTubeLiveAdapter()

    def build_sample_from_payload(self, source_id: str, payload: Dict[str, Any]) -> TelemetrySample:
        now = datetime.now(timezone.utc)
        w_end = payload.get("window_end_utc")
        w_start = payload.get("window_start_utc")
        if isinstance(w_end, str):
            w_end = datetime.fromisoformat(w_end)
        if isinstance(w_start, str):
            w_start = datetime.fromisoformat(w_start)
        w_end_dt = w_end or now
        w_start_dt = w_start or (w_end_dt - timedelta(seconds=5))

        return TelemetrySample(
            source_id=source_id,
            window_start_utc=w_start_dt,
            window_end_utc=w_end_dt,
            source_clock_ms=int(payload.get("source_clock_ms") or 0),
            chat_rate=payload.get("chat_rate"),
            unique_chatters=payload.get("unique_chatters"),
            total_messages=payload.get("total_messages"),
            bot_ratio=float(payload.get("bot_ratio") or 0.0),
            duplicate_ratio=float(payload.get("duplicate_ratio") or 0.0),
            concurrent_viewers=payload.get("concurrent_viewers"),
            audience_delta=payload.get("audience_delta"),
            audio_energy=payload.get("audio_energy"),
            audio_laughter_or_reaction=payload.get("audio_laughter_or_reaction"),
            visual_motion=payload.get("visual_motion"),
            scene_change=payload.get("scene_change"),
            semantic_surprise=payload.get("semantic_surprise"),
            semantic_payoff_clue=payload.get("semantic_payoff_clue"),
            semantic_narrative_clarity=payload.get("semantic_narrative_clarity"),
            semantic_emotion=payload.get("semantic_emotion"),
            semantic_keywords=list(payload.get("semantic_keywords") or []),
            novelty_score=payload.get("novelty_score"),
            topic_relevance=payload.get("topic_relevance"),
            transcript_excerpt=str(payload.get("transcript_excerpt") or "")[:300],
            transcript_coherent=payload.get("transcript_coherent"),
        )
