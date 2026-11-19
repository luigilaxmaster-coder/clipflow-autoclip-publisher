"""Generación de informes de proyectos, métricas de calidad y auditoría de aceptación."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from clipflow.models import Project, PublishJob, Render


class ReportingService:
    def __init__(self, session: Session):
        self.session = session

    def get_project_summary(self, project_id: str) -> Dict[str, Any]:
        """Obtiene un desglose completo del estado de un proyecto para inspección o exportación JSON."""
        project = self.session.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise ValueError(f"Proyecto no encontrado: {project_id}")

        candidates_data = [
            {
                "id": c.id,
                "start_ms": c.source_start_ms,
                "end_ms": c.source_end_ms,
                "duration_sec": (c.source_end_ms - c.source_start_ms) / 1000.0,
                "score": c.score,
                "reason": c.reason,
                "status": c.selection_status,
            }
            for c in project.candidates
        ]

        renders_data = []
        for r in project.renders:
            qa_dict = json.loads(r.qa_json) if r.qa_json else {}
            drafts_data = []
            for d in r.drafts:
                jobs_data = [
                    {
                        "job_id": j.id,
                        "status": j.status,
                        "request_id": j.remote_request_id,
                        "remote_url": j.remote_url,
                        "scheduled_at": j.scheduled_at_utc.isoformat() if j.scheduled_at_utc else None,
                        "error": j.last_error,
                        "attempts": j.attempt_count,
                    }
                    for j in d.publish_jobs
                ]
                drafts_data.append({
                    "draft_id": d.id,
                    "platform": d.platform,
                    "title": d.title,
                    "visibility": d.visibility,
                    "approved_at": d.approved_at.isoformat() if d.approved_at else None,
                    "jobs": jobs_data,
                })

            renders_data.append({
                "render_id": r.id,
                "output_variant_id": r.output_variant_id,
                "target_platform": r.target_platform,
                "video_path": r.video_path,
                "duration_sec": r.duration_sec,
                "dimensions": f"{r.width}x{r.height}" if r.width and r.height else None,
                "qa_status": r.qa_status,
                "qa_details": qa_dict,
                "drafts": drafts_data,
            })

        return {
            "project_id": project.id,
            "autoclip_project_id": project.autoclip_project_id,
            "upstream_version": project.upstream_version,
            "workflow_status": project.workflow_status,
            "created_at": project.created_at.isoformat() if project.created_at else None,
            "source": {
                "id": project.source.id,
                "kind": project.source.source_kind,
                "canonical": project.source.canonical_source,
                "duration_sec": project.source.duration_sec,
                "sha256": project.source.sha256,
            } if project.source else None,
            "candidates_count": len(project.candidates),
            "selected_candidates_count": sum(1 for c in project.candidates if c.selection_status == "selected"),
            "renders_count": len(project.renders),
            "candidates": candidates_data,
            "renders": renders_data,
        }
