"""Pruebas de tarjetas independientes multi-perfil, enrutamiento por nicho y cuota por cuenta."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from clipflow.connections.service import ConnectionService
from clipflow.db import get_session, init_db
from clipflow.models import ConnectionStatus, JobStatus, PostDraft, Project, PublishJob, Render, Source
from clipflow.publishing.unified import UnifiedPublisher
from clipflow.queue import PublishQueueWorker
from clipflow.web.app import app


def test_independent_profiles_and_niche_routing(db_session: Session):
    svc = ConnectionService(db_session)

    # Crear 2 tarjetas independientes de YouTube con nichos y límites propios
    res_gaming = svc.save_connection(
        provider_id="youtube",
        account_name="Geist Gaming",
        credentials={"client_id": "cid-gaming", "client_secret": "sec-gaming", "access_token": "tok-gaming", "mock": True},
        test_now=False,
        allow_mock=True,
        create_new=True,
        niche_tags="gta6, gaming, directos, vegetta777",
        daily_upload_limit=6,
        is_default=True,
    )
    res_misterio = svc.save_connection(
        provider_id="youtube",
        account_name="Archivos Misterio",
        credentials={"client_id": "cid-mist", "client_secret": "sec-mist", "access_token": "tok-mist", "mock": True},
        test_now=False,
        allow_mock=True,
        create_new=True,
        niche_tags="enoc, saturno, misterio, historia",
        daily_upload_limit=25,
        is_default=False,
    )

    # Crear también 2 tarjetas independientes de TikTok
    tt_gaming = svc.save_connection(
        provider_id="tiktok",
        account_name="TikTok Gaming",
        credentials={"client_key": "tt-k1", "client_secret": "tt-s1", "access_token": "tt-tok1", "mock": True},
        test_now=False,
        allow_mock=True,
        create_new=True,
        niche_tags="gta6, gaming",
        daily_upload_limit=10,
    )
    tt_misterio = svc.save_connection(
        provider_id="tiktok",
        account_name="TikTok Misterios",
        credentials={"client_key": "tt-k2", "client_secret": "tt-s2", "access_token": "tt-tok2", "mock": True},
        test_now=False,
        allow_mock=True,
        create_new=True,
        niche_tags="enoc, misterio",
        daily_upload_limit=10,
    )

    # Marcar conectadas para probar resolución
    for cid in (res_gaming["connection_id"], res_misterio["connection_id"], tt_gaming["connection_id"], tt_misterio["connection_id"]):
        c = svc.get_connection_by_id(cid)
        c.status = ConnectionStatus.CONNECTED
    db_session.commit()

    yt_accounts = svc.list_provider_accounts("youtube")
    tt_accounts = svc.list_provider_accounts("tiktok")
    assert len(yt_accounts) == 2
    assert len(tt_accounts) == 2

    # Aislamiento estricto de credenciales por connection_id
    creds_g = svc.get_decrypted_credentials("youtube", account_ref=res_gaming["connection_id"])
    creds_m = svc.get_decrypted_credentials("youtube", account_ref=res_misterio["connection_id"])
    assert creds_g["client_id"] == "cid-gaming"
    assert creds_m["client_id"] == "cid-mist"

    # Enrutamiento automático por nicho
    match_g = svc.resolve_account_by_niche("youtube", "Vegetta777 jugando GTA 6 en vivo")
    assert match_g["connection"]["connection_id"] == res_gaming["connection_id"]

    match_m = svc.resolve_account_by_niche("youtube", "Secretos del Libro de Enoc y Saturno")
    assert match_m["connection"]["connection_id"] == res_misterio["connection_id"]
    assert match_m["connection"]["daily_upload_limit"] == 25

    match_tt_m = svc.resolve_account_by_niche("tiktok", "Misterio antiguo de Enoc")
    assert match_tt_m["connection"]["connection_id"] == tt_misterio["connection_id"]

    # UnifiedPublisher debe instanciar credenciales exactas por perfil
    pub = UnifiedPublisher(connection_service=svc, mock_mode=False)
    yt_m_pub = pub.select_publisher_for_platform("youtube", account_ref=res_misterio["connection_id"])
    assert yt_m_pub.client_id == "cid-mist"
    tt_g_pub = pub.select_publisher_for_platform("tiktok", account_ref=tt_gaming["connection_id"])
    assert tt_g_pub.client_key == "tt-k1"


def test_per_profile_daily_quota_isolation_in_worker(db_session: Session, vertical_video_path: Path):
    svc = ConnectionService(db_session)
    c1 = svc.save_connection(
        provider_id="youtube",
        account_name="Canal Limite 1",
        credentials={"client_id": "c1", "client_secret": "s1", "access_token": "t1"},
        test_now=False,
        create_new=True,
        niche_tags="nicho_a",
        daily_upload_limit=1,
    )
    c2 = svc.save_connection(
        provider_id="youtube",
        account_name="Canal Limite 10",
        credentials={"client_id": "c2", "client_secret": "s2", "access_token": "t2"},
        test_now=False,
        create_new=True,
        niche_tags="nicho_b",
        daily_upload_limit=10,
    )
    conn1 = svc.get_connection_by_id(c1["connection_id"])
    conn2 = svc.get_connection_by_id(c2["connection_id"])
    conn1.status = ConnectionStatus.CONNECTED
    conn1.account_id = "UC_CH_1"
    conn2.status = ConnectionStatus.CONNECTED
    conn2.account_id = "UC_CH_2"

    src = Source(source_kind="file", canonical_source=str(vertical_video_path), local_path=str(vertical_video_path))
    db_session.add(src)
    db_session.flush()
    proj = Project(source_id=src.id, workflow_status="COMPLETED")
    db_session.add(proj)
    db_session.flush()
    rnd = Render(project_id=proj.id, target_platform="youtube", video_path=str(vertical_video_path), qa_status="PASSED")
    db_session.add(rnd)
    db_session.flush()

    # Ya existe 1 publicación hoy en Canal 1 -> alcanzó su límite de 1
    d1_done = PostDraft(render_id=rnd.id, platform="youtube", account_id=conn1.id, title="Done C1")
    d1_next = PostDraft(render_id=rnd.id, platform="youtube", account_id=conn1.id, title="Over Quota C1")
    d2_ready = PostDraft(render_id=rnd.id, platform="youtube", account_id=conn2.id, title="Ready C2")
    db_session.add_all([d1_done, d1_next, d2_ready])
    db_session.flush()

    j_done = PublishJob(
        post_draft_id=d1_done.id,
        idempotency_key="iso-1",
        status=JobStatus.PUBLISHED,
        remote_post_id="VID_DONE_1",
        remote_channel_id="UC_CH_1",
    )
    j_over = PublishJob(post_draft_id=d1_next.id, idempotency_key="iso-2", status=JobStatus.QUEUED)
    j_c2 = PublishJob(post_draft_id=d2_ready.id, idempotency_key="iso-3", status=JobStatus.QUEUED)
    db_session.add_all([j_done, j_over, j_c2])
    db_session.commit()

    worker = PublishQueueWorker(lambda: db_session, publisher=None)
    # 1. El trabajo de Canal 1 debe quedar retenido por su propia cuota, nunca saltar a Canal 2
    res1 = worker.process_due_job(db_session)
    assert res1.id == j_over.id
    assert res1.status == "QUEUED_FOR_DAILY_QUOTA"

    # 2. Canal 2 sí tiene cupo (0/10) y no se bloquea por Canal 1
    with patch("clipflow.publishing.native_youtube.NativeYouTubePublisher.submit_post") as mock_sub:
        mock_sub.return_value = MagicMock(
            ok=True,
            request_id="yt-native-c2",
            remote_post_id="VID_C2_OK",
            remote_url="https://www.youtube.com/shorts/VID_C2_OK",
            status=JobStatus.PUBLISHED,
            raw_payload={"channelId": "UC_CH_2", "privacyStatus": "private"},
        )
        res2 = worker.process_due_job(db_session)
    assert res2.id == j_c2.id
    assert res2.status == JobStatus.PUBLISHED


def test_web_multi_profile_api_and_niche_resolve(monkeypatch, tmp_path: Path, vertical_video_path: Path):
    token = "multi-profile-owner-token-1234567890"
    monkeypatch.setenv("OWNER_TOKEN", token)
    db_file = tmp_path / "multi_prof_web.db"
    monkeypatch.setenv("CLIPFLOW_DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setattr("clipflow.config._active_settings", None)
    monkeypatch.setattr("clipflow.db._engine", None)
    monkeypatch.setattr("clipflow.db._SessionFactory", None)
    init_db()

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    r1 = client.post(
        "/api/connections/youtube/save",
        headers=headers,
        json={
            "account_name": "Canal Gaming",
            "credentials": {"client_id": "cid-g", "client_secret": "sec-g"},
            "test_now": False,
            "create_new": True,
            "niche_tags": "gta6, gaming",
            "daily_upload_limit": 6,
        },
    )
    r2 = client.post(
        "/api/connections/youtube/save",
        headers=headers,
        json={
            "account_name": "Canal Noticias IA",
            "credentials": {"client_id": "cid-ia", "client_secret": "sec-ia"},
            "test_now": False,
            "create_new": True,
            "niche_tags": "inteligencia artificial, ia, tech",
            "daily_upload_limit": 30,
        },
    )
    assert r1.status_code == 200 and r2.status_code == 200
    c1_id = r1.json()["connection_id"]
    c2_id = r2.json()["connection_id"]
    assert c1_id != c2_id

    # Actualizar límite y nicho de una tarjeta
    upd = client.post(
        f"/api/connections/profile/{c2_id}/update",
        headers=headers,
        json={"daily_upload_limit": 40, "niche_tags": "ia, python, tech"},
    )
    assert upd.status_code == 200
    assert upd.json()["profile"]["daily_upload_limit"] == 40

    # Resolver nicho por texto
    res_niche = client.post(
        "/api/niche/resolve",
        headers=headers,
        json={"text": "Novedades de Python e IA en 2026", "platforms": ["youtube"]},
    )
    assert res_niche.status_code == 200
    yt_route = res_niche.json()["routes"]["youtube"]
    assert yt_route["connection_id"] == c2_id
    assert yt_route["daily_upload_limit"] == 40

    # Verificar páginas /connections y /publisher renderizan tarjetas independientes
    assert "Canal Gaming" in client.get("/connections", headers=headers).text
    assert "Canal Noticias IA" in client.get("/connections", headers=headers).text
    pub_html = client.get("/publisher", headers=headers).text
    assert "Canal Noticias IA" in pub_html
    assert "Detectar Nicho Automático" in pub_html


def test_two_wave_batch_schedule_and_endpoint(monkeypatch, tmp_path: Path, vertical_video_path: Path):
    from datetime import datetime, timezone
    from clipflow.models import MediaAsset
    from clipflow.scheduler import build_wave_schedule

    # 1. Prueba matemática del planificador 2-Wave (09:00 AM local = 13:00 UTC, 6 clips, cuota 6)
    now_utc = datetime(2026, 11, 23, 13, 0, 0, tzinfo=timezone.utc)
    plan = build_wave_schedule(total_clips=6, remaining_today=6, daily_limit=6, now_utc=now_utc)
    assert len(plan) == 6
    assert [p["status"] for p in plan] == ["QUEUED", "QUEUED", "QUEUED", "SCHEDULED", "SCHEDULED", "SCHEDULED"]
    assert plan[3]["scheduled_local"] == "2026-11-23 18:30"
    assert plan[4]["scheduled_local"] == "2026-11-23 19:30"
    assert plan[5]["scheduled_local"] == "2026-11-23 20:30"

    # 2. Excedente sobre cuota diaria pasa al día siguiente
    plan_over = build_wave_schedule(total_clips=8, remaining_today=4, daily_limit=6, now_utc=now_utc)
    assert len(plan_over) == 8
    assert sum(1 for p in plan_over if p["wave"] == 1) == 2
    assert sum(1 for p in plan_over if p["wave"] == 2) == 2
    assert sum(1 for p in plan_over if p["wave"] >= 3) == 4

    # 3. Endpoint /api/automation/wave-dispatch
    token = "wave-owner-token-1234567890"
    monkeypatch.setenv("OWNER_TOKEN", token)
    db_file = tmp_path / "wave_dispatch.db"
    monkeypatch.setenv("CLIPFLOW_DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setattr("clipflow.config._active_settings", None)
    monkeypatch.setattr("clipflow.db._engine", None)
    monkeypatch.setattr("clipflow.db._SessionFactory", None)
    init_db()

    sess = get_session()
    try:
        svc = ConnectionService(sess)
        saved = svc.save_connection(
            provider_id="youtube",
            account_name="Canal Wave",
            credentials={"client_id": "cid-w", "client_secret": "sec-w", "access_token": "tok-w"},
            test_now=False,
            daily_upload_limit=6,
        )
        conn_id = saved["connection_id"]
        asset_ids = []
        for idx in range(4):
            a = MediaAsset(title=f"Wave Clip {idx+1}", file_path=str(vertical_video_path), file_type="video")
            sess.add(a)
            sess.flush()
            asset_ids.append(a.id)
        sess.commit()
    finally:
        sess.close()

    client = TestClient(app)
    res = client.post(
        "/api/automation/wave-dispatch",
        headers={"Authorization": f"Bearer {token}"},
        json={"asset_ids": asset_ids, "platform": "youtube", "connection_id": conn_id, "visibility": "private"},
    )
    assert res.status_code == 200
    jobs = res.json()["jobs"]
    assert len(jobs) == 4
    assert sum(1 for j in jobs if j["status"] == "QUEUED") == 2
    assert sum(1 for j in jobs if j["status"] == "SCHEDULED") == 2


def test_per_account_quota_reset_and_remote_limit_tracking(monkeypatch, tmp_path: Path, vertical_video_path: Path):
    from datetime import datetime, timedelta, timezone
    from clipflow.models import AccountConnection
    from clipflow.queue import PublishQueueWorker

    token = "quota-reset-owner-token-123456"
    monkeypatch.setenv("OWNER_TOKEN", token)
    db_file = tmp_path / "quota_reset.db"
    monkeypatch.setenv("CLIPFLOW_DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setattr("clipflow.config._active_settings", None)
    monkeypatch.setattr("clipflow.db._engine", None)
    monkeypatch.setattr("clipflow.db._SessionFactory", None)
    init_db()

    sess = get_session()
    try:
        svc = ConnectionService(sess)
        yt_acc = svc.save_connection(
            provider_id="youtube",
            account_name="Canal Reset YT",
            credentials={"client_id": "cid", "client_secret": "sec", "access_token": "tok"},
            test_now=False,
            create_new=True,
            daily_upload_limit=2,
            niche_tags="gaming",
        )
        tt_acc = svc.save_connection(
            provider_id="tiktok",
            account_name="Cuenta Reset TT",
            credentials={"client_key": "ck", "client_secret": "cs", "access_token": "tt"},
            test_now=False,
            create_new=True,
            daily_upload_limit=4,
            niche_tags="gaming",
        )
        conn_yt_row = sess.query(AccountConnection).filter_by(id=yt_acc["connection_id"]).one()
        conn_yt_row.account_id = "UC_RESET_1"
        conn_tt_row = sess.query(AccountConnection).filter_by(id=tt_acc["connection_id"]).one()
        conn_tt_row.account_id = "tt_reset_1"
        sess.commit()

        from clipflow.connections.service import format_reset_window
        from clipflow.models import utc_now

        src = Source(source_kind="file", canonical_source=str(vertical_video_path), local_path=str(vertical_video_path))
        sess.add(src)
        sess.flush()
        proj = Project(source_id=src.id, workflow_status="COMPLETED")
        sess.add(proj)
        sess.flush()
        rnd = Render(project_id=proj.id, target_platform="youtube", video_path=str(vertical_video_path))
        sess.add(rnd)
        sess.flush()

        now_utc = utc_now()
        # 2 subidas previas escalonadas en el canal YT: una hace 20h (se libera en 4h) y otra hace 2h (se libera en 22h)
        d1 = PostDraft(render_id=rnd.id, platform="youtube", account_id=yt_acc["connection_id"], title="Video Viejo")
        d2 = PostDraft(render_id=rnd.id, platform="youtube", account_id=yt_acc["connection_id"], title="Video Reciente")
        sess.add_all([d1, d2])
        sess.flush()
        j1 = PublishJob(
            post_draft_id=d1.id,
            idempotency_key="qr-1",
            status=JobStatus.PUBLISHED,
            remote_channel_id="UC_RESET_1",
            updated_at=now_utc - timedelta(hours=20),
            created_at=now_utc - timedelta(hours=20),
        )
        j2 = PublishJob(
            post_draft_id=d2.id,
            idempotency_key="qr-2",
            status=JobStatus.PUBLISHED,
            remote_channel_id="UC_RESET_1",
            updated_at=now_utc - timedelta(hours=2),
            created_at=now_utc - timedelta(hours=2),
        )
        sess.add_all([j1, j2])
        sess.commit()

        conn_yt = sess.query(AccountConnection).filter_by(id=yt_acc["connection_id"]).one()
        qinfo = svc.get_quota_reset_info(conn_yt, now=now_utc)
        expected_next = format_reset_window(now_utc + timedelta(hours=4), now_utc=now_utc)
        expected_full = format_reset_window(now_utc + timedelta(hours=22), now_utc=now_utc)
        assert qinfo["used_today"] == 2
        assert qinfo["remaining_today"] == 0
        assert qinfo["quota_reset_status"] == "exhausted"
        assert qinfo["next_quota_reset_local"] == expected_next["local_label"]
        assert qinfo["next_quota_reset_countdown"] == "en 4h 00m"
        assert qinfo["full_quota_reset_local"] == expected_full["local_label"]
        assert "(00:00 PT)" in qinfo["api_reset_local"]

        # Cuenta TikTok sin subidas debe figurar libre y en estado available_full
        conn_tt = sess.query(AccountConnection).filter_by(id=tt_acc["connection_id"]).one()
        qinfo_tt = svc.get_quota_reset_info(conn_tt, now=now_utc)
        assert qinfo_tt["remaining_today"] == 4
        assert qinfo_tt["quota_reset_status"] == "available_full"
        assert "Disponible ahora" in qinfo_tt["next_quota_reset_local"]

        # Si una cuenta TikTok recibe un error remoto de límite API, queda bloqueada hasta +24h sin marcar FAILED
        d_tt = PostDraft(render_id=rnd.id, platform="tiktok", account_id=tt_acc["connection_id"], title="Clip TT Limit")
        sess.add(d_tt)
        sess.flush()
        j_tt = PublishJob(post_draft_id=d_tt.id, idempotency_key="qr-tt-1", status=JobStatus.QUEUED)
        sess.add(j_tt)
        sess.commit()

        worker = PublishQueueWorker(lambda: sess)
        worker._handle_failure(sess, j_tt, "TikTok HTTP 429: spam_risk_too_many_posts", "tiktok")
        sess.commit()
        sess.refresh(conn_tt)
        assert j_tt.status == "QUEUED_FOR_DAILY_QUOTA"
        assert conn_tt.quota_blocked_until is not None
        blocked_info = svc.get_quota_reset_info(conn_tt)
        assert blocked_info["is_quota_blocked_remote"] is True
        assert blocked_info["remaining_today"] == 0
    finally:
        sess.close()

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}
    for path, expected in (
        ("/connections", "Próxima actualización:"),
        ("/overview", "Cuota Diaria y Actualización de API por Cuenta"),
        ("/queue", "Actualización por cuenta:"),
    ):
        html = client.get(path, headers=headers).text
        assert expected in html
        assert "Canal Reset YT" in html
        assert "Reset API" in html or "Reset Google API" in html


