"""Modelos SQLAlchemy para persistencia y estados de clipflow."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Source(Base):
    __tablename__ = "sources"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_kind = Column(String(16), nullable=False)  # "file" | "url"
    canonical_source = Column(String(1024), nullable=False)
    local_path = Column(String(1024), nullable=False)
    sha256 = Column(String(64), nullable=True)
    duration_sec = Column(Float, nullable=True)
    rights_confirmed = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    projects = relationship("Project", back_populates="source", cascade="all, delete-orphan")


class Project(Base):
    __tablename__ = "projects"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_id = Column(String(36), ForeignKey("sources.id"), nullable=False)
    autoclip_project_id = Column(String(64), nullable=True, index=True)
    upstream_version = Column(String(32), nullable=True)
    workflow_status = Column(String(32), default="CREATED", nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    source = relationship("Source", back_populates="projects")
    candidates = relationship("ClipCandidate", back_populates="project", cascade="all, delete-orphan")
    renders = relationship("Render", back_populates="project", cascade="all, delete-orphan")


class ClipCandidate(Base):
    __tablename__ = "clip_candidates"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False)
    source_start_ms = Column(Integer, nullable=False)
    source_end_ms = Column(Integer, nullable=False)
    score = Column(Float, nullable=False, default=0.0)
    reason = Column(Text, nullable=True)
    semantic_group = Column(String(64), nullable=True)
    selection_status = Column(String(32), default="selected")  # selected | dropped | duplicate

    project = relationship("Project", back_populates="candidates")
    renders = relationship("Render", back_populates="candidate")


class Render(Base):
    __tablename__ = "renders"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    candidate_id = Column(String(36), ForeignKey("clip_candidates.id"), nullable=True)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False)
    output_variant_id = Column(String(64), nullable=True)
    studio_job_id = Column(String(64), nullable=True)
    target_platform = Column(String(32), nullable=False)  # "youtube_shorts" | "tiktok"
    video_path = Column(String(1024), nullable=False)
    cover_path = Column(String(1024), nullable=True)
    kit_path = Column(String(1024), nullable=True)
    video_sha256 = Column(String(64), nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    duration_sec = Column(Float, nullable=True)
    qa_status = Column(String(32), default="PENDING")  # PENDING | PASSED | WARNING | FAILED
    qa_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    project = relationship("Project", back_populates="renders")
    candidate = relationship("ClipCandidate", back_populates="renders")
    drafts = relationship("PostDraft", back_populates="render", cascade="all, delete-orphan")


class PostDraft(Base):
    __tablename__ = "post_drafts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    render_id = Column(String(36), ForeignKey("renders.id"), nullable=False)
    platform = Column(String(32), nullable=False)  # "youtube" | "tiktok"
    account_id = Column(String(64), nullable=True)
    title = Column(String(256), nullable=False)
    description = Column(Text, nullable=True)
    hashtags_json = Column(Text, nullable=True)  # JSON list
    visibility = Column(String(32), default="private")  # private | SELF_ONLY | public
    approved_at = Column(DateTime(timezone=True), nullable=True)
    approval_actor = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    render = relationship("Render", back_populates="drafts")
    publish_jobs = relationship("PublishJob", back_populates="post_draft", cascade="all, delete-orphan")


class PublishJob(Base):
    __tablename__ = "publish_jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    post_draft_id = Column(String(36), ForeignKey("post_drafts.id"), nullable=False)
    idempotency_key = Column(String(128), unique=True, nullable=False, index=True)
    scheduled_at_utc = Column(DateTime(timezone=True), nullable=True, index=True)
    status = Column(String(32), default="QUEUED", index=True)  # QUEUED | UPLOADING | REMOTE_PROCESSING | PUBLISHED | REMOTE_REJECTED | RETRYABLE_ERROR | ACTION_REQUIRED | CANCELLED
    remote_request_id = Column(String(64), nullable=True, index=True)
    remote_post_id = Column(String(64), nullable=True, index=True)
    remote_url = Column(String(1024), nullable=True)
    remote_channel_id = Column(String(128), nullable=True)
    remote_privacy_status = Column(String(32), nullable=True)
    upload_session_uri = Column(Text, nullable=True)
    upload_bytes_sent = Column(Integer, default=0)
    attempt_count = Column(Integer, default=0)
    last_error = Column(Text, nullable=True)
    last_checked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    post_draft = relationship("PostDraft", back_populates="publish_jobs")


class JobStatus:
    DRAFT = "DRAFT"
    READY = "READY"
    SCHEDULED = "SCHEDULED"
    QUEUED = "QUEUED"
    UPLOADING = "UPLOADING"
    PROCESSING = "PROCESSING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    REQUIRES_ACTION = "REQUIRES_ACTION"

    # Compatibilidad con estados heredados
    REMOTE_PROCESSING = "REMOTE_PROCESSING"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    RETRYABLE_ERROR = "RETRYABLE_ERROR"
    REMOTE_REJECTED = "REMOTE_REJECTED"


class ConnectionStatus:
    AVAILABLE = "AVAILABLE"
    CONNECTED = "CONNECTED"
    NEEDS_CONFIG = "NEEDS_CONFIG"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"
    INSUFFICIENT_PERMS = "INSUFFICIENT_PERMS"
    EXPIRED = "EXPIRED"
    ERROR = "ERROR"


class AccountConnection(Base):
    __tablename__ = "account_connections"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    provider_id = Column(String(64), nullable=False, index=True)
    account_name = Column(String(256), nullable=False)
    account_id = Column(String(256), nullable=True)
    encrypted_credentials = Column(Text, nullable=True)
    scopes_json = Column(Text, nullable=True)
    status = Column(String(32), default=ConnectionStatus.NEEDS_CONFIG, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    last_checked_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    limits_json = Column(Text, nullable=True)
    niche_tags = Column(String(512), nullable=True)
    daily_upload_limit = Column(Integer, default=6, nullable=False)
    quota_blocked_until = Column(DateTime(timezone=True), nullable=True)
    is_default = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    comments = relationship("InboxComment", back_populates="connection", cascade="all, delete-orphan")


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String(256), nullable=False)
    file_path = Column(String(1024), nullable=False)
    file_type = Column(String(32), default="video")  # video | image | audio
    thumbnail_path = Column(String(1024), nullable=True)
    render_id = Column(String(36), ForeignKey("renders.id"), nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    duration_sec = Column(Float, nullable=True)
    tags_json = Column(Text, nullable=True)
    sha256 = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)


class AutomationRule(Base):
    __tablename__ = "automation_rules"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(128), nullable=False)
    rule_type = Column(String(64), nullable=False)  # auto_approve_qa | auto_schedule | viral_metadata
    is_enabled = Column(Boolean, default=True, nullable=False)
    conditions_json = Column(Text, nullable=True)
    actions_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class InboxComment(Base):
    __tablename__ = "inbox_comments"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    connection_id = Column(String(36), ForeignKey("account_connections.id"), nullable=True)
    platform = Column(String(32), nullable=False)
    remote_comment_id = Column(String(128), nullable=False, index=True)
    remote_post_id = Column(String(128), nullable=True)
    author_name = Column(String(128), nullable=False)
    author_id = Column(String(128), nullable=True)
    content = Column(Text, nullable=False)
    published_at_utc = Column(DateTime(timezone=True), default=utc_now)
    status = Column(String(32), default="PENDING")  # PENDING | REPLIED | IGNORED
    reply_content = Column(Text, nullable=True)
    replied_at_utc = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    connection = relationship("AccountConnection", back_populates="comments")


class AutoReplyRule(Base):
    __tablename__ = "auto_reply_rules"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    is_enabled = Column(Boolean, default=False, nullable=False)
    keyword_filter = Column(String(256), nullable=True)  # Comma-separated or empty for all
    cooldown_minutes = Column(Integer, default=60, nullable=False)
    reply_template = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now)


class OAuthState(Base):
    __tablename__ = "oauth_states"

    state = Column(String(128), primary_key=True)
    provider_id = Column(String(64), nullable=False, index=True)
    connection_id = Column(String(36), nullable=True)
    redirect_uri = Column(String(1024), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    entity_type = Column(String(32), nullable=False)
    entity_id = Column(String(64), nullable=False)
    actor = Column(String(64), nullable=False)
    action = Column(String(64), nullable=False)
    before_json = Column(Text, nullable=True)
    after_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)


class LiveSource(Base):
    __tablename__ = "live_sources"
    __table_args__ = (
        UniqueConstraint("platform", "video_id", name="uq_live_source_platform_video"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    platform = Column(String(32), nullable=False, index=True)
    channel_id = Column(String(128), nullable=False, index=True)
    channel_name = Column(String(256), nullable=True)
    video_id = Column(String(128), nullable=False, index=True)
    stream_url = Column(String(1024), nullable=False)
    title = Column(String(512), nullable=True)
    niche_hint = Column(String(256), nullable=True)
    rights_status = Column(String(32), default="pending", nullable=False)  # confirmed | pending | denied
    owner_approved = Column(Boolean, default=False, nullable=False)
    is_paused = Column(Boolean, default=False, nullable=False)
    monitor_mode = Column(String(32), default="shadow", nullable=False)  # shadow | review | auto
    live_state = Column(String(32), default="LIVE", nullable=False)  # LIVE | STALLED | RECONNECTING | ENDED | NO_ACCESS | RATE_LIMITED
    destination_connection_id = Column(String(36), ForeignKey("account_connections.id"), nullable=True)
    observed_at = Column(DateTime(timezone=True), default=utc_now)
    heartbeat_at = Column(DateTime(timezone=True), default=utc_now)
    last_checkpoint = Column(Text, nullable=True)
    last_chat_cursor = Column(String(256), nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class LiveSignalWindow(Base):
    __tablename__ = "live_signal_windows"
    __table_args__ = (
        Index("ix_live_signal_source_start", "source_id", "window_start_utc"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_id = Column(String(36), ForeignKey("live_sources.id"), nullable=False, index=True)
    window_start_utc = Column(DateTime(timezone=True), nullable=False)
    window_end_utc = Column(DateTime(timezone=True), nullable=False)
    source_clock = Column(Integer, default=0, nullable=False)  # ms desde inicio del directo o PTS
    chat_rate = Column(Float, nullable=True)
    unique_chatters = Column(Integer, nullable=True)
    audience_delta = Column(Float, nullable=True)
    concurrent_viewers = Column(Integer, nullable=True)
    hype_score = Column(Float, default=0.0, nullable=False)
    confidence = Column(Float, default=0.0, nullable=False)
    audio_features_json = Column(Text, nullable=True)
    visual_features_json = Column(Text, nullable=True)
    semantic_features_json = Column(Text, nullable=True)
    signals_missing_json = Column(Text, nullable=True)
    signal_quality_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)


class LivePeakEvent(Base):
    __tablename__ = "live_peak_events"
    __table_args__ = (
        UniqueConstraint(
            "source_id", "onset_ms", "end_ms", "detector_version",
            name="uq_live_peak_event_span",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_id = Column(String(36), ForeignKey("live_sources.id"), nullable=False, index=True)
    onset_ms = Column(Integer, nullable=False)
    peak_ms = Column(Integer, nullable=False)
    end_ms = Column(Integer, nullable=False)
    prerecord_ms = Column(Integer, default=45000, nullable=False)
    postrecord_ms = Column(Integer, default=30000, nullable=False)
    has_gap = Column(Boolean, default=False, nullable=False)
    gap_ms = Column(Integer, default=0, nullable=False)
    state = Column(String(32), default="CAPTURED", nullable=False, index=True)
    peak_score = Column(Float, default=0.0, nullable=False)
    confidence = Column(Float, default=0.0, nullable=False)
    evidence_json = Column(Text, nullable=True)
    segment_manifest_path = Column(String(1024), nullable=True)
    detector_version = Column(String(32), default="v1.0", nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class LiveClipScore(Base):
    __tablename__ = "live_clip_scores"
    __table_args__ = (
        UniqueConstraint("event_id", "render_variant_hash", name="uq_live_clip_event_variant"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    clip_candidate_id = Column(String(36), ForeignKey("clip_candidates.id"), nullable=True)
    event_id = Column(String(36), ForeignKey("live_peak_events.id"), nullable=False, index=True)
    render_variant_hash = Column(String(64), nullable=False)
    content_hash = Column(String(64), nullable=True, index=True)
    title = Column(String(256), nullable=True)
    video_path = Column(String(1024), nullable=True)
    duration_sec = Column(Float, nullable=True)
    niche_tags = Column(String(256), nullable=True)
    media_qa_passed = Column(Boolean, default=False, nullable=False)
    score = Column(Float, nullable=False, index=True)
    confidence = Column(Float, nullable=False)
    editorial_band = Column(String(32), nullable=False)  # Excepcional | Excelente | Muy bueno | Archivo | Descartar
    review_status = Column(String(32), default="PENDING", nullable=False, index=True)  # PENDING | APPROVED | REVIEW_REQUIRED | BLOCKED | REJECTED | SCHEDULED
    score_breakdown_json = Column(Text, nullable=False)
    reason_codes_json = Column(Text, nullable=False)
    model_version = Column(String(32), default="rubric-v1.0", nullable=False)
    evaluated_at = Column(DateTime(timezone=True), default=utc_now)


class LivePublishDecision(Base):
    __tablename__ = "live_publish_decisions"
    __table_args__ = (
        UniqueConstraint(
            "clip_id", "destination_connection_id", "platform", "content_hash",
            name="uq_live_decision_target",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    clip_id = Column(String(36), ForeignKey("live_clip_scores.id"), nullable=False, index=True)
    destination_connection_id = Column(String(36), ForeignKey("account_connections.id"), nullable=True, index=True)
    platform = Column(String(32), nullable=False)
    content_hash = Column(String(64), default="", nullable=False)
    status = Column(String(32), default="PENDING", nullable=False, index=True)  # SHADOW | REVIEW_REQUIRED | SCHEDULED | BLOCKED | REJECTED | DISPATCHED
    eligible = Column(Boolean, default=False, nullable=False)
    priority_score = Column(Float, default=0.0, nullable=False)
    reason = Column(Text, nullable=False)
    scheduled_at_utc = Column(DateTime(timezone=True), nullable=True)
    idempotency_key = Column(String(128), nullable=False, unique=True)
    publish_job_id = Column(String(36), ForeignKey("publish_jobs.id"), nullable=True)
    policy_snapshot_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class ClipOutcome(Base):
    __tablename__ = "clip_outcomes"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id = Column(String(36), ForeignKey("publish_jobs.id"), nullable=False, index=True)
    clip_score_id = Column(String(36), ForeignKey("live_clip_scores.id"), nullable=True, index=True)
    platform = Column(String(32), nullable=False)
    niche = Column(String(128), nullable=True)
    model_version = Column(String(32), default="rubric-v1.0", nullable=False)
    measured_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    metric_name = Column(String(64), nullable=False)  # engaged_views | stayed_to_watch | retention_avg | shares | meaningful_comments | engagement_rate
    metric_value = Column(Float, nullable=True)
    denominator = Column(Float, nullable=True)
    availability = Column(String(32), default="AVAILABLE", nullable=False)  # AVAILABLE | SIN_DATOS | NO_ACCESS
    source = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now)


class LivePeakPolicyStore(Base):
    __tablename__ = "live_peak_policies"

    id = Column(String(36), primary_key=True, default="default")
    policy_json = Column(Text, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
