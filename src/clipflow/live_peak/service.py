"""Servicio orquestador persistente para AutoClip — Live Peak Intelligence 24/7."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from sqlalchemy.orm import Session

from clipflow.config import LivePeakConfig, get_settings
from clipflow.connections.service import ConnectionService
from clipflow.db import record_audit
from clipflow.live_peak.adaptive_scheduler import (
    AdaptiveLiveScheduler,
    build_live_peak_adaptive_slot,
)
from clipflow.live_peak.alignment import TimeAligner, _ensure_utc
from clipflow.live_peak.capture import SegmentRingBuffer
from clipflow.live_peak.collector import TelemetryCollector
from clipflow.live_peak.detector import PeakDetector
from clipflow.live_peak.editing import LivePeakEditor, compute_content_hash
from clipflow.live_peak.models import ClipEvaluation, PeakCandidateEvent
from clipflow.live_peak.policy import LivePeakPolicyEngine
from clipflow.live_peak.scoring import EngagementScorer
from clipflow.models import (
    AccountConnection,
    ClipOutcome,
    ConnectionStatus,
    JobStatus,
    LiveClipScore,
    LivePeakEvent,
    LivePeakPolicyStore,
    LivePublishDecision,
    LiveSignalWindow,
    LiveSource,
    PostDraft,
    Project,
    PublishJob,
    Render,
    Source,
    utc_now,
)

logger = logging.getLogger(__name__)


class LivePeakService:
    """
    Coordina vigilancia continua de directos 24/7, detección estadística multi-señal,
    scorecard explicable de 8 criterios, políticas de derechos/QA, programación adaptativa
    por perfil y seguimiento post-publicación.
    """

    def __init__(self, db: Session):
        self.db = db
        self.collector = TelemetryCollector()
        self.aligner = TimeAligner()
        self.editor = LivePeakEditor()

    # ------------------------------------------------------------------
    # Política persistente (settings + override en SQLite)
    # ------------------------------------------------------------------
    def get_policy(self) -> LivePeakConfig:
        base = get_settings().live_peak.model_copy(deep=True)
        row = self.db.query(LivePeakPolicyStore).filter(LivePeakPolicyStore.id == "default").first()
        if row and row.policy_json:
            try:
                overrides = json.loads(row.policy_json)
                if isinstance(overrides, dict):
                    merged = {**base.model_dump(), **overrides}
                    return LivePeakConfig(**merged)
            except Exception as e:
                logger.warning("No se pudo parsear LivePeakPolicyStore: %s", e)
        return base

    def update_policy(self, updates: Dict[str, Any], actor: str = "owner") -> LivePeakConfig:
        current = self.get_policy().model_dump()
        for k, v in updates.items():
            if k in current and v is not None:
                current[k] = v
        validated = LivePeakConfig(**current)
        row = self.db.query(LivePeakPolicyStore).filter(LivePeakPolicyStore.id == "default").first()
        payload = json.dumps(validated.model_dump())
        before = json.loads(row.policy_json) if (row and row.policy_json) else None
        if not row:
            row = LivePeakPolicyStore(id="default", policy_json=payload)
            self.db.add(row)
        else:
            row.policy_json = payload
            row.updated_at = utc_now()
        record_audit(self.db, "LivePeakPolicy", "default", actor, "policy_updated", before, validated.model_dump())
        self.db.commit()
        return validated

    # ------------------------------------------------------------------
    # Gestión de streams monitoreados (sesiones continuas 24h+)
    # ------------------------------------------------------------------
    def upsert_stream(
        self,
        *,
        platform: str,
        channel_id: str,
        video_id: str,
        stream_url: str,
        title: Optional[str] = None,
        channel_name: Optional[str] = None,
        niche_hint: Optional[str] = None,
        rights_status: str = "pending",
        owner_approved: bool = False,
        monitor_mode: Optional[str] = None,
        destination_connection_id: Optional[str] = None,
    ) -> LiveSource:
        plat = platform.strip().lower()
        row = (
            self.db.query(LiveSource)
            .filter(LiveSource.platform == plat, LiveSource.video_id == video_id)
            .first()
        )
        policy = self.get_policy()
        now = utc_now()
        if row:
            row.stream_url = stream_url
            row.channel_id = channel_id
            if title:
                row.title = title
            if channel_name:
                row.channel_name = channel_name
            if niche_hint is not None:
                row.niche_hint = niche_hint
            row.rights_status = rights_status
            row.owner_approved = bool(owner_approved)
            if monitor_mode:
                row.monitor_mode = monitor_mode
            if destination_connection_id is not None:
                row.destination_connection_id = destination_connection_id
            if row.live_state in ("ENDED", "STALLED"):
                row.live_state = "LIVE"
            row.heartbeat_at = now
            row.updated_at = now
        else:
            row = LiveSource(
                platform=plat,
                channel_id=channel_id,
                channel_name=channel_name or channel_id,
                video_id=video_id,
                stream_url=stream_url,
                title=title or f"Directo {video_id}",
                niche_hint=niche_hint,
                rights_status=rights_status,
                owner_approved=bool(owner_approved),
                monitor_mode=monitor_mode or policy.mode,
                live_state="LIVE",
                destination_connection_id=destination_connection_id,
                observed_at=now,
                heartbeat_at=now,
            )
            self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def patch_stream(self, stream_id: str, updates: Dict[str, Any], actor: str = "owner") -> LiveSource:
        row = self.db.query(LiveSource).filter(LiveSource.id == stream_id).first()
        if not row:
            raise ValueError(f"LiveSource no encontrado: {stream_id}")
        allowed = (
            "is_paused",
            "rights_status",
            "owner_approved",
            "monitor_mode",
            "live_state",
            "destination_connection_id",
            "niche_hint",
            "title",
        )
        before = {k: getattr(row, k) for k in allowed}
        for k in allowed:
            if k in updates and updates[k] is not None:
                setattr(row, k, updates[k])
        row.updated_at = utc_now()
        row.heartbeat_at = utc_now()
        after = {k: getattr(row, k) for k in allowed}
        record_audit(self.db, "LiveSource", row.id, actor, "stream_updated", before, after)
        self.db.commit()
        self.db.refresh(row)
        return row

    def serialize_stream(self, row: LiveSource) -> Dict[str, Any]:
        return {
            "id": row.id,
            "platform": row.platform,
            "channel_id": row.channel_id,
            "channel_name": row.channel_name,
            "video_id": row.video_id,
            "stream_url": row.stream_url,
            "title": row.title,
            "niche_hint": row.niche_hint,
            "rights_status": row.rights_status,
            "owner_approved": bool(row.owner_approved),
            "is_paused": bool(row.is_paused),
            "monitor_mode": row.monitor_mode,
            "live_state": row.live_state,
            "destination_connection_id": row.destination_connection_id,
            "observed_at": row.observed_at.isoformat() if row.observed_at else None,
            "heartbeat_at": row.heartbeat_at.isoformat() if row.heartbeat_at else None,
            "last_checkpoint": json.loads(row.last_checkpoint) if row.last_checkpoint else None,
            "last_error": row.last_error,
        }

    # ------------------------------------------------------------------
    # Ingesta de señales 24/7 + checkpoints sin duplicar episodios
    # ------------------------------------------------------------------
    def _build_detector(self, policy: LivePeakConfig) -> PeakDetector:
        return PeakDetector(
            peak_enter=policy.peak_enter,
            peak_exit=policy.peak_exit,
            peak_enter_consecutive_windows=policy.peak_enter_consecutive_windows,
            peak_exit_seconds=policy.peak_exit_seconds,
            cooldown_seconds=policy.cooldown_seconds,
            max_event_seconds=policy.max_event_seconds,
            prerecord_seconds=policy.prerecord_seconds,
            postrecord_seconds=policy.postrecord_seconds,
            require_two_signal_families=policy.require_two_signal_families,
            min_distinct_chatters_for_chat_only_trigger=policy.min_distinct_chatters_for_chat_only_trigger,
        )

    def ingest_signal_windows(
        self,
        stream_id: str,
        windows: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Procesa lotes o frames continuos de telemetría para un directo vivo (sin descartarlo tras 24h),
        persiste `LiveSignalWindow` y crea `LivePeakEvent` únicos ante picos válidos.
        """
        source = self.db.query(LiveSource).filter(LiveSource.id == stream_id).first()
        if not source:
            raise ValueError(f"LiveSource no encontrado: {stream_id}")

        policy = self.get_policy()
        detector = self._build_detector(policy)

        # Restaurar checkpoint previo si existe (incluye soporte de directos de 25+ horas)
        if source.last_checkpoint:
            try:
                ckpt = json.loads(source.last_checkpoint)
                self.aligner.restore(source.id, ckpt.get("aligner"))
                detector._baseline(source.id).import_state(ckpt.get("baseline"))
            except Exception:
                pass

        stored_windows = 0
        emitted_events: List[LivePeakEvent] = []
        last_result = None

        for raw in windows:
            sample = self.collector.build_sample_from_payload(source.id, raw)
            aligned = self.aligner.align_sample(sample, stream_origin_utc=source.observed_at)
            sig_res, candidate_ev = detector.process_sample(aligned)
            last_result = sig_res

            win_row = LiveSignalWindow(
                source_id=source.id,
                window_start_utc=aligned.window_start_utc,
                window_end_utc=aligned.window_end_utc,
                source_clock=aligned.source_clock_ms,
                chat_rate=aligned.chat_rate,
                unique_chatters=aligned.unique_chatters,
                audience_delta=aligned.audience_delta,
                concurrent_viewers=aligned.concurrent_viewers,
                hype_score=sig_res.hype_score,
                confidence=sig_res.confidence,
                audio_features_json=json.dumps({"energy": aligned.audio_energy, "reaction": aligned.audio_laughter_or_reaction}),
                visual_features_json=json.dumps({"motion": aligned.visual_motion, "scene_change": aligned.scene_change}),
                semantic_features_json=json.dumps({
                    "surprise": aligned.semantic_surprise,
                    "payoff_clue": aligned.semantic_payoff_clue,
                    "clarity": aligned.semantic_narrative_clarity,
                    "emotion": aligned.semantic_emotion,
                }),
                signals_missing_json=json.dumps(sig_res.missing_signals),
                signal_quality_json=json.dumps({
                    "active_families": sig_res.active_families,
                    "spam_filtered": sig_res.spam_filtered,
                    "late_sample": aligned.late_sample,
                    "gap_detected": aligned.gap_detected,
                    "gap_ms": aligned.gap_ms,
                }),
            )
            self.db.add(win_row)
            stored_windows += 1

            if candidate_ev is not None:
                ev_row = self._persist_peak_event(source, candidate_ev, policy)
                if ev_row:
                    emitted_events.append(ev_row)

        source.heartbeat_at = utc_now()
        source.last_checkpoint = json.dumps({
            "aligner": self.aligner.checkpoint(source.id),
            "baseline": detector._baseline(source.id).export_state(),
        })
        self.db.commit()

        return {
            "source_id": source.id,
            "windows_processed": stored_windows,
            "events_created": len(emitted_events),
            "event_ids": [e.id for e in emitted_events],
            "latest_hype_score": last_result.hype_score if last_result else 0.0,
            "latest_state": last_result.state if last_result else "WATCHING",
        }

    def _persist_peak_event(
        self,
        source: LiveSource,
        candidate: PeakCandidateEvent,
        policy: LivePeakConfig,
    ) -> Optional[LivePeakEvent]:
        existing = (
            self.db.query(LivePeakEvent)
            .filter(
                LivePeakEvent.source_id == source.id,
                LivePeakEvent.onset_ms == candidate.onset_ms,
                LivePeakEvent.end_ms == candidate.end_ms,
                LivePeakEvent.detector_version == candidate.detector_version,
            )
            .first()
        )
        if existing:
            return existing

        data_root = Path(get_settings().app.data_dir or "data")
        ring = SegmentRingBuffer(
            source.id,
            base_dir=data_root / "live_ring" / source.id,
            retention_minutes=policy.ring_buffer_minutes,
        )
        manifest_path = ring.freeze_episode_manifest(
            event_id=f"{candidate.onset_ms}_{candidate.end_ms}",
            onset_ms=candidate.onset_ms,
            end_ms=candidate.end_ms,
            prerecord_ms=candidate.prerecord_ms,
            postrecord_ms=candidate.postrecord_ms,
        )
        ev = LivePeakEvent(
            source_id=source.id,
            onset_ms=candidate.onset_ms,
            peak_ms=candidate.peak_ms,
            end_ms=candidate.end_ms,
            prerecord_ms=candidate.prerecord_ms,
            postrecord_ms=candidate.postrecord_ms,
            has_gap=candidate.has_gap,
            gap_ms=candidate.gap_ms,
            state="CAPTURED",
            peak_score=candidate.peak_score,
            confidence=candidate.confidence,
            evidence_json=json.dumps(candidate.evidence),
            segment_manifest_path=manifest_path,
            detector_version=candidate.detector_version,
        )
        self.db.add(ev)
        self.db.flush()
        return ev

    # ------------------------------------------------------------------
    # Evaluación de candidatos (8 criterios), compuerta de política y cola adaptativa
    # ------------------------------------------------------------------
    def score_and_evaluate_clip(
        self,
        *,
        event_id: str,
        title: str,
        dimensions: Dict[str, Any],
        video_path: Optional[str] = None,
        duration_sec: Optional[float] = None,
        niche_tags: Optional[str] = None,
        media_qa_passed: Optional[bool] = None,
        transcript_coherent: bool = True,
        spam_chat_only: bool = False,
        render_variant_hash: Optional[str] = None,
        destination_connection_id: Optional[str] = None,
        platform: str = "youtube",
        custom_weights: Optional[Dict[str, float]] = None,
        model_version: str = "rubric-v1.0",
        allow_immediate_urgent: bool = False,
    ) -> Dict[str, Any]:
        ev = self.db.query(LivePeakEvent).filter(LivePeakEvent.id == event_id).first()
        if not ev:
            raise ValueError(f"LivePeakEvent no encontrado: {event_id}")
        source = self.db.query(LiveSource).filter(LiveSource.id == ev.source_id).one()
        policy = self.get_policy()

        ev_evidence = json.loads(ev.evidence_json or "{}")
        missing_signals = list(ev_evidence.get("missing_signals") or [])

        if media_qa_passed is None:
            if video_path:
                variants = self.editor.produce_variants_from_source(video_path, run_autoclip=False)
                qa_ok = bool(variants and variants[0].get("media_qa_passed"))
            else:
                qa_ok = False
        else:
            qa_ok = bool(media_qa_passed)

        c_hash = compute_content_hash(video_path, title=title, onset_ms=ev.onset_ms, end_ms=ev.end_ms)
        var_hash = render_variant_hash or c_hash[:24]

        scorer = EngagementScorer(
            weights=custom_weights,
            model_version=model_version,
            min_auto_score=policy.min_auto_score,
            min_auto_confidence=policy.min_auto_confidence,
        )
        rights_ok = source.rights_status == policy.min_rights_status and bool(source.owner_approved)
        evaluation: ClipEvaluation = scorer.evaluate(
            dimensions,
            missing_signals=missing_signals,
            has_gap=ev.has_gap,
            transcript_coherent=transcript_coherent,
            spam_chat_only=spam_chat_only,
            media_qa_passed=qa_ok,
            rights_confirmed=rights_ok,
            model_version=model_version,
        )

        existing_clip = (
            self.db.query(LiveClipScore)
            .filter(
                LiveClipScore.event_id == ev.id,
                LiveClipScore.render_variant_hash == var_hash,
            )
            .first()
        )
        if existing_clip:
            clip_row = existing_clip
            clip_row.score = evaluation.score
            clip_row.confidence = evaluation.confidence
            clip_row.editorial_band = evaluation.editorial_band
            clip_row.review_status = evaluation.review_status
            clip_row.score_breakdown_json = json.dumps(evaluation.breakdown)
            clip_row.reason_codes_json = json.dumps(evaluation.reason_codes)
            clip_row.model_version = evaluation.model_version
            clip_row.evaluated_at = utc_now()
        else:
            clip_row = LiveClipScore(
                event_id=ev.id,
                render_variant_hash=var_hash,
                content_hash=c_hash,
                title=title[:256],
                video_path=video_path,
                duration_sec=duration_sec,
                niche_tags=niche_tags or source.niche_hint,
                media_qa_passed=qa_ok,
                score=evaluation.score,
                confidence=evaluation.confidence,
                editorial_band=evaluation.editorial_band,
                review_status=evaluation.review_status,
                score_breakdown_json=json.dumps(evaluation.breakdown),
                reason_codes_json=json.dumps(evaluation.reason_codes),
                model_version=evaluation.model_version,
            )
            self.db.add(clip_row)
            self.db.flush()

        ev.state = "RANKED"

        # VerificarHashes previos en biblioteca para deduplicación
        existing_hashes: Set[str] = {
            r[0]
            for r in self.db.query(LiveClipScore.content_hash)
            .filter(LiveClipScore.id != clip_row.id, LiveClipScore.content_hash.isnot(None))
            .all()
        }

        dest_conn_id = destination_connection_id or source.destination_connection_id
        conn_row: Optional[AccountConnection] = None
        if dest_conn_id:
            conn_row = self.db.query(AccountConnection).filter(AccountConnection.id == dest_conn_id).first()
        else:
            conn_row = ConnectionService(self.db).get_connection(platform)
            if conn_row:
                dest_conn_id = conn_row.id

        account_authorized = bool(conn_row and conn_row.status == ConnectionStatus.CONNECTED)
        publish_publicly_allowed = bool(policy.publish_publicly and source.owner_approved)

        gate = LivePeakPolicyEngine(policy).evaluate_gate(
            score=evaluation.score,
            confidence=evaluation.confidence,
            media_qa_passed=qa_ok,
            rights_status=source.rights_status,
            owner_approved=bool(source.owner_approved),
            account_authorized=account_authorized,
            publish_publicly_allowed=publish_publicly_allowed,
            mode=source.monitor_mode or policy.mode,
            content_hash=c_hash,
            existing_hashes=existing_hashes,
            transcript_coherent=transcript_coherent,
            freshness_bonus=float(dimensions.get("freshness_bonus") or 0.0),
            audience_fit_bonus=float(dimensions.get("audience_fit_bonus") or 0.0),
        )
        clip_row.review_status = gate.status

        idem_key = f"livepeak-{clip_row.id}-{dest_conn_id or 'none'}-{platform.lower()}"
        existing_dec = (
            self.db.query(LivePublishDecision)
            .filter(LivePublishDecision.idempotency_key == idem_key)
            .first()
        )
        scheduled_at_utc: Optional[datetime] = None
        publish_job_id: Optional[str] = None
        dec_status = gate.status

        # Solo programar en cola persistente si pasa política estricta Y NO está en modo sombra/revisión
        if gate.status == "APPROVED" and policy.enabled and conn_row:
            recent_posts = self._recent_live_peak_post_times(dest_conn_id, platform.lower())
            slot = build_live_peak_adaptive_slot(
                now_utc=utc_now(),
                recent_profile_posts_utc=recent_posts,
                max_posts_per_day_per_profile=policy.max_auto_posts_per_day_per_profile,
                minimum_post_gap_minutes=policy.minimum_post_gap_minutes,
                is_exceptional=evaluation.score >= 95.0,
                allow_immediate_urgent=allow_immediate_urgent,
            )
            scheduled_at_utc = slot["scheduled_at_utc"]
            dec_status = "SCHEDULED"
            ev.state = "SCHEDULED"
            publish_job_id = self._create_publish_job_for_clip(
                clip_row=clip_row,
                source=source,
                platform=platform.lower(),
                conn_row=conn_row,
                slot=slot,
                visibility="public" if policy.publish_publicly else ("SELF_ONLY" if platform.lower() == "tiktok" else "private"),
                idem_key=idem_key,
            )

        if existing_dec:
            dec = existing_dec
            dec.status = dec_status
            dec.eligible = gate.eligible
            dec.priority_score = gate.priority_score
            dec.reason = gate.reason
            dec.scheduled_at_utc = scheduled_at_utc or dec.scheduled_at_utc
            dec.publish_job_id = publish_job_id or dec.publish_job_id
            dec.policy_snapshot_json = json.dumps(gate.snapshot)
        else:
            dec = LivePublishDecision(
                clip_id=clip_row.id,
                destination_connection_id=dest_conn_id,
                platform=platform.lower(),
                content_hash=c_hash,
                status=dec_status,
                eligible=gate.eligible,
                priority_score=gate.priority_score,
                reason=gate.reason,
                scheduled_at_utc=scheduled_at_utc,
                idempotency_key=idem_key,
                publish_job_id=publish_job_id,
                policy_snapshot_json=json.dumps(gate.snapshot),
            )
            self.db.add(dec)

        record_audit(
            self.db,
            "LiveClipScore",
            clip_row.id,
            "live_peak_engine",
            "evaluated",
            None,
            {
                "score": clip_row.score,
                "confidence": clip_row.confidence,
                "status": dec_status,
                "reason_codes": evaluation.reason_codes,
            },
        )
        self.db.commit()
        return self.serialize_clip_score(clip_row)

    def _recent_live_peak_post_times(self, connection_id: Optional[str], platform: str) -> List[datetime]:
        since = utc_now() - timedelta(days=2)
        rows = (
            self.db.query(LivePublishDecision)
            .filter(
                LivePublishDecision.platform == platform,
                LivePublishDecision.destination_connection_id == connection_id,
                LivePublishDecision.status.in_(["SCHEDULED", "DISPATCHED"]),
                LivePublishDecision.scheduled_at_utc >= since,
            )
            .all()
        )
        return [_ensure_utc(r.scheduled_at_utc) for r in rows if r.scheduled_at_utc is not None]

    def _create_publish_job_for_clip(
        self,
        *,
        clip_row: LiveClipScore,
        source: LiveSource,
        platform: str,
        conn_row: AccountConnection,
        slot: Dict[str, Any],
        visibility: str,
        idem_key: str,
    ) -> str:
        existing = self.db.query(PublishJob).filter(PublishJob.idempotency_key == idem_key).first()
        if existing:
            return existing.id

        src = self.db.query(Source).filter(Source.canonical_source == source.stream_url).first()
        if not src:
            src = Source(source_kind="url", canonical_source=source.stream_url, local_path=clip_row.video_path or "")
            self.db.add(src)
            self.db.flush()

        proj = self.db.query(Project).filter(Project.source_id == src.id).first()
        if not proj:
            proj = Project(source_id=src.id, workflow_status="COMPLETED")
            self.db.add(proj)
            self.db.flush()

        rnd = Render(
            project_id=proj.id,
            target_platform=platform,
            video_path=clip_row.video_path or "",
            duration_sec=clip_row.duration_sec,
            qa_status="PASSED" if clip_row.media_qa_passed else "PENDING",
            qa_json=json.dumps({"reason": "Live Peak QA Verified" if clip_row.media_qa_passed else "QA Pending"}),
        )
        self.db.add(rnd)
        self.db.flush()

        draft = PostDraft(
            render_id=rnd.id,
            platform=platform,
            account_id=conn_row.id,
            title=(clip_row.title or "Live Highlight")[:100],
            description=f"{clip_row.title or ''}\n\n#Shorts #AutoClipLive",
            visibility=visibility,
            approval_actor="live_peak_engine",
            approved_at=utc_now(),
        )
        self.db.add(draft)
        self.db.flush()

        job = PublishJob(
            post_draft_id=draft.id,
            idempotency_key=idem_key,
            status=slot["status"],
            scheduled_at_utc=slot["scheduled_at_utc"],
        )
        self.db.add(job)
        self.db.flush()
        return job.id

    def review_clip(self, clip_id: str, action: str, actor: str = "owner", notes: str = "") -> Dict[str, Any]:
        """Revisión manual explícita (`approve` | `reject` | `block`) de un clip en cola."""
        clip = self.db.query(LiveClipScore).filter(LiveClipScore.id == clip_id).first()
        if not clip:
            raise ValueError(f"LiveClipScore no encontrado: {clip_id}")
        act = action.lower().strip()
        before = clip.review_status
        if act == "approve":
            clip.review_status = "APPROVED"
        elif act == "block":
            clip.review_status = "BLOCKED"
        elif act == "reject":
            clip.review_status = "REJECTED"
        else:
            raise ValueError(f"Acción inválida: {action}")

        decs = self.db.query(LivePublishDecision).filter(LivePublishDecision.clip_id == clip.id).all()
        for d in decs:
            d.status = clip.review_status
            if notes:
                d.reason = notes

        record_audit(self.db, "LiveClipScore", clip.id, actor, f"manual_{act}", {"status": before}, {"status": clip.review_status, "notes": notes})
        self.db.commit()
        return self.serialize_clip_score(clip)

    def rescore_clip(
        self,
        clip_id: str,
        *,
        custom_weights: Optional[Dict[str, float]] = None,
        model_version: str = "rubric-v1.1",
    ) -> Dict[str, Any]:
        """Recalcula el score del candidato usando nuevos pesos o versión de modelo sin alterar evidencia previa."""
        clip = self.db.query(LiveClipScore).filter(LiveClipScore.id == clip_id).first()
        if not clip:
            raise ValueError(f"LiveClipScore no encontrado: {clip_id}")
        old_bd = json.loads(clip.score_breakdown_json or "{}")
        dims = {k: v.get("value", 5.0) for k, v in old_bd.items() if isinstance(v, dict)}
        dims["confidence"] = clip.confidence

        ev = self.db.query(LivePeakEvent).filter(LivePeakEvent.id == clip.event_id).one()
        ev_evidence = json.loads(ev.evidence_json or "{}")
        source = self.db.query(LiveSource).filter(LiveSource.id == ev.source_id).one()
        policy = self.get_policy()

        scorer = EngagementScorer(
            weights=custom_weights,
            model_version=model_version,
            min_auto_score=policy.min_auto_score,
            min_auto_confidence=policy.min_auto_confidence,
        )
        evaluation = scorer.evaluate(
            dims,
            missing_signals=list(ev_evidence.get("missing_signals") or []),
            has_gap=ev.has_gap,
            media_qa_passed=clip.media_qa_passed,
            rights_confirmed=(source.rights_status == policy.min_rights_status and bool(source.owner_approved)),
            model_version=model_version,
        )
        clip.score = evaluation.score
        clip.confidence = evaluation.confidence
        clip.editorial_band = evaluation.editorial_band
        clip.review_status = evaluation.review_status
        clip.score_breakdown_json = json.dumps(evaluation.breakdown)
        clip.reason_codes_json = json.dumps(evaluation.reason_codes)
        clip.model_version = evaluation.model_version
        clip.evaluated_at = utc_now()
        self.db.commit()
        return self.serialize_clip_score(clip)

    # ------------------------------------------------------------------
    # Serialización y consultas Dashboard
    # ------------------------------------------------------------------
    def serialize_clip_score(self, clip: LiveClipScore) -> Dict[str, Any]:
        decisions = self.db.query(LivePublishDecision).filter(LivePublishDecision.clip_id == clip.id).all()
        return {
            "id": clip.id,
            "event_id": clip.event_id,
            "title": clip.title,
            "video_path": clip.video_path,
            "duration_sec": clip.duration_sec,
            "niche_tags": clip.niche_tags,
            "media_qa_passed": bool(clip.media_qa_passed),
            "score": clip.score,
            "rating_10": round(clip.score / 10.0, 1),
            "confidence": clip.confidence,
            "editorial_band": clip.editorial_band,
            "review_status": clip.review_status,
            "model_version": clip.model_version,
            "reason_codes": json.loads(clip.reason_codes_json or "[]"),
            "breakdown": json.loads(clip.score_breakdown_json or "{}"),
            "evaluated_at": clip.evaluated_at.isoformat() if clip.evaluated_at else None,
            "decisions": [
                {
                    "id": d.id,
                    "platform": d.platform,
                    "destination_connection_id": d.destination_connection_id,
                    "status": d.status,
                    "eligible": bool(d.eligible),
                    "priority_score": d.priority_score,
                    "reason": d.reason,
                    "scheduled_at_utc": d.scheduled_at_utc.isoformat() if d.scheduled_at_utc else None,
                    "publish_job_id": d.publish_job_id,
                }
                for d in decisions
            ],
        }

    def run_worker_tick(self) -> Dict[str, Any]:
        """Ciclo seguro del worker 24/7: solo ejecuta observación si `policy.enabled` es True."""
        policy = self.get_policy()
        if not policy.enabled:
            return {"enabled": False, "active_streams": 0}
        active = (
            self.db.query(LiveSource)
            .filter(LiveSource.is_paused.is_(False), LiveSource.live_state.in_(["LIVE", "RECONNECTING"]))
            .all()
        )
        for src in active:
            src.heartbeat_at = utc_now()
        self.db.commit()
        return {"enabled": True, "mode": policy.mode, "active_streams": len(active)}
