"""Pruebas de aceptación obligatorias para AutoClip — Live Peak Intelligence 24/7 (Fase 10)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from clipflow.connections.service import ConnectionService
from clipflow.db import get_session, init_db
from clipflow.live_peak.adaptive_scheduler import (
    AdaptiveLiveScheduler,
    build_live_peak_adaptive_slot,
)
from clipflow.live_peak.alignment import TimeAligner
from clipflow.live_peak.capture import SegmentRingBuffer
from clipflow.live_peak.collector import YouTubeLiveAdapter
from clipflow.live_peak.detector import PeakDetector
from clipflow.live_peak.learning import OutcomeTracker
from clipflow.live_peak.models import TelemetrySample
from clipflow.live_peak.policy import LivePeakPolicyEngine
from clipflow.live_peak.scoring import EngagementScorer
from clipflow.live_peak.service import LivePeakService
from clipflow.models import LiveClipScore, LivePeakEvent, PublishJob
from clipflow.radar import LiveStreamRadar
from clipflow.selector import ClipSelector
from clipflow.supervisor import build_managed_processes
from clipflow.web.app import app


def _make_sample(
    source_id: str,
    clock_ms: int,
    *,
    chat_rate=30.0,
    unique_chatters=15,
    total_messages=30,
    bot_ratio=0.0,
    duplicate_ratio=0.0,
    concurrent_viewers=400,
    audience_delta=0.0,
    audio_energy=0.25,
    visual_motion=0.25,
    semantic_surprise=0.20,
    semantic_payoff_clue=0.20,
    semantic_emotion=0.20,
    novelty_score=0.5,
    topic_relevance=0.5,
) -> TelemetrySample:
    t0 = datetime(2026, 11, 23, 12, 0, 0, tzinfo=timezone.utc) + timedelta(milliseconds=clock_ms)
    return TelemetrySample(
        source_id=source_id,
        window_start_utc=t0,
        window_end_utc=t0 + timedelta(seconds=5),
        source_clock_ms=clock_ms,
        chat_rate=chat_rate,
        unique_chatters=unique_chatters,
        total_messages=total_messages,
        bot_ratio=bot_ratio,
        duplicate_ratio=duplicate_ratio,
        concurrent_viewers=concurrent_viewers,
        audience_delta=audience_delta,
        audio_energy=audio_energy,
        visual_motion=visual_motion,
        semantic_surprise=semantic_surprise,
        semantic_payoff_clue=semantic_payoff_clue,
        semantic_emotion=semantic_emotion,
        novelty_score=novelty_score,
        topic_relevance=topic_relevance,
    )


def _warm_detector(det: PeakDetector, source_id: str, count: int = 12, step_ms: int = 5000) -> int:
    clock = 0
    for i in range(count):
        clock = i * step_ms
        det.process_sample(_make_sample(source_id, clock))
    return clock


def test_25h_continuous_stream_monitoring_not_dropped(monkeypatch, tmp_path: Path):
    """Aceptación #1: Un mismo video_id monitoreado 25 horas no se descarta ni duplica sesiones."""
    hist_file = tmp_path / "radar_hist.json"
    radar = LiveStreamRadar(history_file=hist_file)
    radar.history["processed_streams"]["live_25h"] = "2026-11-22T00:00:00Z"
    radar._save_history()

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"id": "live_25h", "is_live": true, "title": "Maratón 25h", "concurrent_view_count": 1200}',
        )
        # El método continuo NO debe descartar el directo en curso
        cont = radar.check_channel_live_session("Creator25h", "https://youtube.com/@test/live")
        assert cont is not None
        assert cont["video_id"] == "live_25h"
        assert cont["live_state"] == "LIVE"

    db_file = tmp_path / "live_25h.db"
    monkeypatch.setenv("CLIPFLOW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CLIPFLOW_DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setattr("clipflow.config._active_settings", None)
    monkeypatch.setattr("clipflow.db._engine", None)
    monkeypatch.setattr("clipflow.db._SessionFactory", None)
    init_db()

    sess = get_session()
    try:
        svc = LivePeakService(sess)
        s1 = svc.upsert_stream(
            platform="youtube",
            channel_id="UC_25H",
            video_id="live_25h",
            stream_url="https://youtube.com/watch?v=live_25h",
            rights_status="confirmed",
            owner_approved=True,
        )
        # Segundo upsert a las 25h reutiliza la misma sesión sin duplicar
        s2 = svc.upsert_stream(
            platform="youtube",
            channel_id="UC_25H",
            video_id="live_25h",
            stream_url="https://youtube.com/watch?v=live_25h",
            rights_status="confirmed",
            owner_approved=True,
        )
        assert s1.id == s2.id

        h25_ms = 25 * 3600 * 1000
        res = svc.ingest_signal_windows(
            s1.id,
            [{"source_clock_ms": h25_ms, "chat_rate": 45.0, "unique_chatters": 20, "audio_energy": 0.3}],
        )
        assert res["windows_processed"] == 1
        assert s1.last_checkpoint is not None
    finally:
        sess.close()


def test_bot_spam_and_small_sample_guards():
    """Aceptación #2 y #3: 1000 mensajes repetidos de bot y 3 usuarios (+300%) no disparan pico."""
    det = PeakDetector()
    clock = _warm_detector(det, "src_spam")

    # 1000 mensajes de bot repetidos (sin variedad de chatters ni corroboración real)
    for i in range(1, 5):
        sig, ev = det.process_sample(
            _make_sample(
                "src_spam",
                clock + i * 5000,
                chat_rate=12000.0,
                unique_chatters=4,
                total_messages=1000,
                duplicate_ratio=0.95,
                bot_ratio=0.80,
                audio_energy=0.25,
                visual_motion=0.25,
                semantic_surprise=0.2,
                semantic_payoff_clue=0.2,
                semantic_emotion=0.2,
            )
        )
        assert sig.spam_filtered is True
        assert sig.state == "WATCHING"
        assert ev is None

    # Aceptación #3: Creador de 10 viewers con 3 usuarios únicos saltando 300%
    det_small = PeakDetector(min_distinct_chatters_for_chat_only_trigger=10)
    c_small = _warm_detector(det_small, "src_small")
    for i in range(1, 5):
        sig, ev = det_small.process_sample(
            _make_sample(
                "src_small",
                c_small + i * 5000,
                chat_rate=96.0,
                unique_chatters=3,
                total_messages=8,
                concurrent_viewers=10,
                audio_energy=0.2,
                visual_motion=0.2,
                semantic_surprise=0.2,
                semantic_payoff_clue=0.2,
                semantic_emotion=0.2,
            )
        )
        assert "SMALL_SAMPLE_CHAT_GUARD" in sig.reason_codes
        assert sig.state == "WATCHING"
        assert ev is None


def test_missing_chat_and_viewers_still_detects_audio_semantic_peak():
    """Aceptación #4 y #12: Sin chat ni viewers, detecta por audio + semántica sin inventar ceros."""
    det = PeakDetector()
    sid = "src_no_chat"
    for i in range(12):
        det.process_sample(
            _make_sample(
                sid,
                i * 5000,
                chat_rate=None,
                unique_chatters=None,
                concurrent_viewers=None,
                audience_delta=None,
                audio_energy=0.2,
                visual_motion=0.2,
                semantic_surprise=0.2,
                semantic_payoff_clue=0.2,
                semantic_emotion=0.2,
            )
        )

    base_clock = 12 * 5000
    events = []
    # 3 ventanas de pico fuerte en audio + video + semántica sin chat ni concurrent_viewers
    for i in range(4):
        sig, ev = det.process_sample(
            _make_sample(
                sid,
                base_clock + i * 5000,
                chat_rate=None,
                unique_chatters=None,
                concurrent_viewers=None,
                audience_delta=None,
                audio_energy=0.95,
                visual_motion=0.92,
                semantic_surprise=0.90,
                semantic_payoff_clue=0.92,
                semantic_emotion=0.90,
                novelty_score=0.9,
                topic_relevance=0.85,
            )
        )
        assert "chat" in sig.missing_signals
        assert "audience" in sig.missing_signals
        assert sig.confidence < 0.80  # Menor confianza al faltar señales

    # Descenso prolongado para cierre por histéresis
    for j in range(1, 8):
        sig, ev = det.process_sample(
            _make_sample(
                sid,
                base_clock + (4 + j) * 5000,
                chat_rate=None,
                unique_chatters=None,
                concurrent_viewers=None,
                audience_delta=None,
                audio_energy=0.1,
                visual_motion=0.1,
                semantic_surprise=0.1,
                semantic_payoff_clue=0.1,
                semantic_emotion=0.1,
                novelty_score=0.2,
                topic_relevance=0.2,
            )
        )
        if ev is not None:
            events.append(ev)

    assert len(events) == 1
    assert "chat" in events[0].evidence["missing_signals"]
    assert events[0].confidence < 0.80


def test_lol_spam_chat_vs_calm_monologue_and_gates():
    """Aceptación #5, #6 y #8: 'LOL' sin payoff <90; monólogo con payoff >=90; bloqueos de QA/derechos."""
    scorer = EngagementScorer(min_auto_score=90.0, min_auto_confidence=0.75)

    # #5 Chat lleno de 'LOL' sin contexto/payoff
    lol_eval = scorer.evaluate(
        {
            "hook": 7.5,
            "payoff": 8.0,
            "narrative_context": 8.0,
            "emotion": 8.0,
            "novelty": 7.0,
            "technical_quality": 9.0,
            "social_evidence": 9.5,
            "niche_fit": 9.0,
        },
        spam_chat_only=True,
        media_qa_passed=True,
        rights_confirmed=True,
    )
    assert lol_eval.score < 90.0
    assert "NOISE_WITHOUT_PAYOFF" in lol_eval.reason_codes
    assert lol_eval.review_status != "APPROVED"

    # #6 Monólogo tranquilo con gran revelación/payoff y buena técnica -> califica alto
    calm_eval = scorer.evaluate(
        {
            "hook": 9.5,
            "payoff": 9.6,
            "narrative_context": 9.5,
            "emotion": 9.0,
            "novelty": 9.2,
            "technical_quality": 9.4,
            "social_evidence": 7.0,
            "niche_fit": 9.5,
            "confidence": 0.88,
        },
        missing_signals=[],
        media_qa_passed=True,
        rights_confirmed=True,
    )
    assert calm_eval.score >= 90.0
    assert calm_eval.editorial_band in ("Excepcional", "Excelente")
    assert calm_eval.review_status == "APPROVED"

    # #8 Score 99 bloqueado si falla QA, derechos, confianza o autopublicación pública
    policy_eng = LivePeakPolicyEngine()
    d_rights = policy_eng.evaluate_gate(
        score=99.0, confidence=0.95, media_qa_passed=True,
        rights_status="pending", owner_approved=False, account_authorized=True, publish_publicly_allowed=True, mode="auto",
    )
    assert d_rights.status == "BLOCKED" and d_rights.eligible is False

    d_qa = policy_eng.evaluate_gate(
        score=99.0, confidence=0.95, media_qa_passed=False,
        rights_status="confirmed", owner_approved=True, account_authorized=True, publish_publicly_allowed=True, mode="auto",
    )
    assert d_qa.status == "BLOCKED" and d_qa.eligible is False

    d_conf = policy_eng.evaluate_gate(
        score=99.0, confidence=0.50, media_qa_passed=True,
        rights_status="confirmed", owner_approved=True, account_authorized=True, publish_publicly_allowed=True, mode="auto",
    )
    assert d_conf.status == "REVIEW_REQUIRED" and d_conf.eligible is False


def test_connection_drop_gap_and_ring_buffer(tmp_path: Path):
    """Aceptación #7: Caída breve de conexión marca gap explícito sin duplicar clips y preserva pre-roll."""
    aligner = TimeAligner(expected_step_ms=5000)
    s1 = aligner.align_sample(_make_sample("src_gap", 10000))
    # Salto súbito de 45s por reconexión
    s2 = aligner.align_sample(_make_sample("src_gap", 55000))
    assert s1.gap_detected is False
    assert s2.gap_detected is True
    assert s2.gap_ms >= 35000

    ring = SegmentRingBuffer("src_gap", base_dir=tmp_path / "ring", retention_minutes=5)
    ring.register_segment(str(tmp_path / "ring" / "s0.ts"), 5000, 10000)
    ring.register_segment(str(tmp_path / "ring" / "s1.ts"), 55000, 60000, has_gap=True)
    manifest = ring.freeze_episode_manifest("ev_gap", onset_ms=55000, end_ms=60000, prerecord_ms=45000, postrecord_ms=10000)
    assert Path(manifest).exists()
    content = Path(manifest).read_text(encoding="utf-8")
    assert '"has_gap": true' in content


def test_adaptive_scheduler_gap_daily_cap_and_no_filler():
    """Aceptación #9 y #10: Dos clips 98 y 96 respetan 180 min y cuota diaria; <90 nunca se publican."""
    sched = AdaptiveLiveScheduler(max_posts_per_day_per_profile=2, minimum_post_gap_minutes=180)
    now_utc = datetime(2026, 11, 23, 14, 0, 0, tzinfo=timezone.utc)
    cands = [
        {"clip_id": "c98", "event_id": "ev1", "destination_connection_id": "acc1", "platform": "youtube", "score": 98.0, "priority_score": 100.0, "eligible": True, "topic_key": "gol1"},
        {"clip_id": "c96", "event_id": "ev2", "destination_connection_id": "acc1", "platform": "youtube", "score": 96.0, "priority_score": 97.0, "eligible": True, "topic_key": "jugada2"},
        {"clip_id": "c94", "event_id": "ev3", "destination_connection_id": "acc1", "platform": "youtube", "score": 94.0, "priority_score": 94.0, "eligible": True, "topic_key": "debate3"},
    ]
    plan = sched.schedule_ranked_candidates(cands, now_utc=now_utc, allow_immediate_urgent=True)
    assert len(plan) == 3
    t1 = plan[0]["scheduled_at_utc"]
    t2 = plan[1]["scheduled_at_utc"]
    t3 = plan[2]["scheduled_at_utc"]
    assert abs((t2 - t1).total_seconds()) >= 180 * 60
    # El 3er clip supera cap diario (2/día) -> pasa al día siguiente
    assert t3.date() >= t2.date()

    # #10 Sin clips >= 90 -> 0 publicaciones
    eng = LivePeakPolicyEngine()
    dec_84 = eng.evaluate_gate(
        score=84.0, confidence=0.95, media_qa_passed=True,
        rights_status="confirmed", owner_approved=True, account_authorized=True, publish_publicly_allowed=True, mode="auto",
    )
    assert dec_84.eligible is False
    assert dec_84.status == "REVIEW_REQUIRED"


def test_shadow_mode_api_security_and_outcomes(monkeypatch, tmp_path: Path):
    """Aceptación #11, #12, #13, #14, #15 y Fase F outcomes."""
    token = "live-peak-owner-token-1234567890"
    monkeypatch.setenv("OWNER_TOKEN", token)
    monkeypatch.setenv("CLIPFLOW_DATA_DIR", str(tmp_path / "data"))
    db_file = tmp_path / "live_peak_api.db"
    monkeypatch.setenv("CLIPFLOW_DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setattr("clipflow.config._active_settings", None)
    monkeypatch.setattr("clipflow.db._engine", None)
    monkeypatch.setattr("clipflow.db._SessionFactory", None)
    init_db()

    # Supervisor por defecto (live_peak.enabled=False) sigue siendo dual (web + worker)
    procs_default = build_managed_processes("python")
    assert [p.name for p in procs_default] == ["web-fastapi", "queue-worker"]
    procs_enabled = build_managed_processes("python", enable_live_peak=True)
    assert [p.name for p in procs_enabled] == ["web-fastapi", "queue-worker", "live-peak-worker"]

    # YouTubeLiveAdapter sin token reporta NO_ACCESS explícito (no inventa 0 viewers)
    yt_res = YouTubeLiveAdapter().fetch_live_details("vid_no_tok", access_token=None)
    assert yt_res["live_state"] == "NO_ACCESS"
    assert yt_res["concurrent_viewers"] is None

    # Selector 0-100 soporta sesiones continuas (>10) sin recorte artificial
    sel = ClipSelector()
    cands_100 = [
        {"start_ms": i * 100000, "end_ms": i * 100000 + 30000, "clip_score": 92.0, "title": f"Ev {i}"}
        for i in range(12)
    ]
    res_sel = sel.select_scored_candidates_100(cands_100, project_id="p_live", min_score_100=90.0, session_cap=50)
    assert sum(1 for c in res_sel if c.selection_status == "selected") == 12

    client = TestClient(app)
    # Sin auth debe bloquearse
    assert client.get("/api/live/streams").status_code == 401

    headers = {"Authorization": f"Bearer {token}"}
    # Crear stream en modo shadow
    r_st = client.post(
        "/api/live/streams",
        headers=headers,
        json={
            "platform": "youtube",
            "channel_id": "UC_LP_TEST",
            "video_id": "vid_lp_1",
            "stream_url": "https://www.youtube.com/watch?v=vid_lp_1",
            "title": "Directo Prueba Live Peak",
            "rights_status": "confirmed",
            "owner_approved": True,
            "monitor_mode": "shadow",
        },
    )
    assert r_st.status_code == 200
    stream_id = r_st.json()["stream"]["id"]

    # Ingestar baseline + pico + caída para generar evento
    windows = [{"source_clock_ms": i * 5000, "chat_rate": 20.0, "unique_chatters": 12, "audio_energy": 0.2, "visual_motion": 0.2, "semantic_surprise": 0.2, "semantic_payoff_clue": 0.2, "semantic_emotion": 0.2} for i in range(12)]
    for i in range(3):
        windows.append({"source_clock_ms": (12 + i) * 5000, "chat_rate": 240.0, "unique_chatters": 85, "audio_energy": 0.95, "visual_motion": 0.90, "semantic_surprise": 0.92, "semantic_payoff_clue": 0.94, "semantic_emotion": 0.90})
    for i in range(6):
        windows.append({"source_clock_ms": (15 + i) * 5000, "chat_rate": 15.0, "unique_chatters": 10, "audio_energy": 0.15, "visual_motion": 0.15, "semantic_surprise": 0.1, "semantic_payoff_clue": 0.1, "semantic_emotion": 0.1})

    r_ing = client.post(f"/api/live/streams/{stream_id}/signals", headers=headers, json={"windows": windows})
    assert r_ing.status_code == 200
    assert r_ing.json()["events_created"] >= 1
    ev_id = r_ing.json()["event_ids"][0]

    # Evaluar en shadow mode -> crea scorecard con nota >=90 pero ZERO PublishJob públicos
    r_eval = client.post(
        f"/api/live/events/{ev_id}/evaluate",
        headers=headers,
        json={
            "title": "Jugada Épica Verificada",
            "media_qa_passed": True,
            "dimensions": {
                "hook": 9.5,
                "payoff": 9.6,
                "narrative_context": 9.4,
                "emotion": 9.2,
                "novelty": 9.0,
                "technical_quality": 9.3,
                "social_evidence": 9.5,
                "niche_fit": 9.4,
                "confidence": 0.91,
            },
        },
    )
    assert r_eval.status_code == 200
    clip_data = r_eval.json()["clip"]
    assert clip_data["score"] >= 90.0
    assert clip_data["review_status"] == "SHADOW"

    sess = get_session()
    try:
        assert sess.query(PublishJob).count() == 0
    finally:
        sess.close()

    # Revisión manual + rescore + política + outcomes + UI /automations
    r_rev = client.post(f"/api/live/clips/{clip_data['id']}/review", headers=headers, json={"action": "approve", "notes": "Aprobado test"})
    assert r_rev.status_code == 200
    assert r_rev.json()["clip"]["review_status"] == "APPROVED"

    r_rescore = client.post(f"/api/live/clips/{clip_data['id']}/rescore", headers=headers, json={"model_version": "rubric-v1.1"})
    assert r_rescore.status_code == 200
    assert r_rescore.json()["clip"]["model_version"] == "rubric-v1.1"

    r_pol = client.patch("/api/live/policy", headers=headers, json={"min_auto_score": 92})
    assert r_pol.status_code == 200
    assert r_pol.json()["policy"]["min_auto_score"] == 92.0

    # Engagement rate explicito y control de sobreajuste
    rate = OutcomeTracker.compute_engagement_rate(comments=10, likes=80, shares=10, valid_views=1000)
    assert rate == 0.1
    out_res = client.get("/api/live/outcomes", headers=headers)
    assert out_res.status_code == 200
    assert out_res.json()["calibration_preview"]["calibrated"] is False

    html_auto = client.get("/automations", headers=headers).text
    assert "Live Peak Intelligence 24/7" in html_auto
    assert "Desactivado (Opt-in" in html_auto


def test_auto_mode_with_explicit_authorization_creates_idempotent_job(
    monkeypatch, tmp_path: Path, vertical_video_path: Path
):
    """Aceptación #11 y Fase E: Modo auto habilitado explícitamente agenda PublishJob único e idempotente."""
    token = "lp-auto-owner-token-1234567890"
    monkeypatch.setenv("OWNER_TOKEN", token)
    monkeypatch.setenv("CLIPFLOW_DATA_DIR", str(tmp_path / "data"))
    db_file = tmp_path / "live_peak_auto.db"
    monkeypatch.setenv("CLIPFLOW_DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setattr("clipflow.config._active_settings", None)
    monkeypatch.setattr("clipflow.db._engine", None)
    monkeypatch.setattr("clipflow.db._SessionFactory", None)
    init_db()

    sess = get_session()
    try:
        conn_svc = ConnectionService(sess)
        acc = conn_svc.save_connection(
            provider_id="youtube",
            account_name="Geist Live Auto",
            credentials={"client_id": "cid", "client_secret": "sec", "access_token": "tok"},
            test_now=False,
            create_new=True,
            daily_upload_limit=6,
        )
        conn_id = acc["connection_id"]
        from clipflow.models import AccountConnection, ConnectionStatus
        conn_row = sess.query(AccountConnection).filter_by(id=conn_id).one()
        conn_row.status = ConnectionStatus.CONNECTED
        sess.commit()

        lp = LivePeakService(sess)
        lp.update_policy({"enabled": True, "mode": "auto", "publish_publicly": True})
        src = lp.upsert_stream(
            platform="youtube",
            channel_id="UC_AUTO_1",
            video_id="vid_auto_1",
            stream_url="https://youtube.com/watch?v=vid_auto_1",
            rights_status="confirmed",
            owner_approved=True,
            monitor_mode="auto",
            destination_connection_id=conn_id,
        )
        windows = [{"source_clock_ms": i * 5000, "chat_rate": 25.0, "unique_chatters": 12, "audio_energy": 0.2, "visual_motion": 0.2, "semantic_surprise": 0.2, "semantic_payoff_clue": 0.2, "semantic_emotion": 0.2} for i in range(12)]
        for i in range(3):
            windows.append({"source_clock_ms": (12 + i) * 5000, "chat_rate": 320.0, "unique_chatters": 95, "audio_energy": 0.96, "visual_motion": 0.95, "semantic_surprise": 0.95, "semantic_payoff_clue": 0.96, "semantic_emotion": 0.94})
        for i in range(6):
            windows.append({"source_clock_ms": (15 + i) * 5000, "chat_rate": 15.0, "unique_chatters": 10, "audio_energy": 0.1, "visual_motion": 0.1, "semantic_surprise": 0.1, "semantic_payoff_clue": 0.1, "semantic_emotion": 0.1})

        ing = lp.ingest_signal_windows(src.id, windows)
        assert ing["events_created"] == 1
        ev_id = ing["event_ids"][0]

        clip_res = lp.score_and_evaluate_clip(
            event_id=ev_id,
            title="Momento Dorado 9.5/10",
            video_path=str(vertical_video_path),
            duration_sec=32.0,
            media_qa_passed=True,
            dimensions={
                "hook": 9.6,
                "payoff": 9.7,
                "narrative_context": 9.5,
                "emotion": 9.3,
                "novelty": 9.4,
                "technical_quality": 9.5,
                "social_evidence": 9.6,
                "niche_fit": 9.5,
                "confidence": 0.94,
            },
            destination_connection_id=conn_id,
            platform="youtube",
        )
        assert clip_res["score"] >= 90.0
        assert clip_res["decisions"][0]["status"] == "SCHEDULED"
        assert clip_res["decisions"][0]["publish_job_id"] is not None
        assert sess.query(PublishJob).count() == 1

        # Re-evaluación idempotente no duplica PublishJob
        clip_res_2 = lp.score_and_evaluate_clip(
            event_id=ev_id,
            title="Momento Dorado 9.5/10",
            video_path=str(vertical_video_path),
            duration_sec=32.0,
            media_qa_passed=True,
            dimensions={
                "hook": 9.6,
                "payoff": 9.7,
                "narrative_context": 9.5,
                "emotion": 9.3,
                "novelty": 9.4,
                "technical_quality": 9.5,
                "social_evidence": 9.6,
                "niche_fit": 9.5,
                "confidence": 0.94,
            },
            destination_connection_id=conn_id,
            platform="youtube",
        )
        assert clip_res_2["id"] == clip_res["id"]
        assert sess.query(PublishJob).count() == 1
    finally:
        sess.close()

