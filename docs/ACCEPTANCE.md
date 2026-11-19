# Informe de Aceptación y Validación de Gates

**Proyecto:** ClipFlow (`autoclip-publisher`)  
**Fecha de Validación:** 19 de noviembre de 2026  
**Sistema Operativo:** Microsoft Windows 11 Pro 64-bit  
**Hardware:** CPU 48 Logical Processors / 32 GB RAM (Total: 33.3 GB, Libre: ~11.7 GB)  
**Entorno Python:** CPython 3.11.15 con `uv 0.11.7`  
**FFmpeg:** v9.0.1-essentials (Gyan.dev)  
**Versión Upstream AutoClip:** Commit `cd8e6bed18d87a81273d48bcd165751708ac715c` (v1.5.8 / v1.5.9 RC)  

---

## Matriz de Gates de Aceptación

| Gate | Descripción | Estado Real | Evidencia y Comando de Verificación |
|---|---|---|---|
| **GATE-0** | Descubrimiento de entorno, herramientas y dependencias | **PASS** | `autoclip doctor` y `clipflow doctor` ejecutados. Detección exitosa de FFmpeg 9.0.1, FFprobe 9.0.1, Python 3.11.15, faster-whisper 1.2.1 y SQLite local. |
| **GATE-1** | Ingesta segura, filtrado SSRF y fingerprinting | **PASS** | Tests automatizados `tests/test_ingestion.py` pasados (4/4). Detección correcta de formatos MP4, cálculo de hash SHA-256 y bloqueo estricto de destinos SSRF/localhost. |
| **GATE-2** | Calidad multimedia (MediaQA con FFprobe) | **PASS** | Tests `tests/test_media_qa.py` pasados (5/5). Validación de ratio vertical 9:16 (1080x1920), streams de video h264 y audio aac, y rechazo de archivos vacíos, corruptos u horizontales. |
| **GATE-3** | Puente Studio output variants → Publicación | **PASS** | Tests `tests/test_autoclip_adapter.py` pasados (2/2). Verificación en código de `backend.services.studio.publishing:output_variant_meta` y compatibilidad probada sin doble render. |
| **GATE-4** | Publicación simulada, reintentos y reconciliación | **PASS** | Tests `tests/test_publish_mock.py` y `tests/test_recovery.py` pasados (4/4). Simulación de envíos privados a YouTube y TikTok, reintentos exponenciales y reconciliación asíncrona. |
| **GATE-5** | Publicación real privada (con cuentas conectadas) | **BLOCKED (Pendiente)** | **No se poseen credenciales externas reales del usuario ni cuentas sociales conectadas en este entorno de prueba.** El código y los endpoints están listos; requiere configurar `UPLOAD_POST_API_KEY` con canales YouTube y TikTok conectados para activar la emisión a redes reales. |
| **GATE-6** | Programación, idempotencia y worker persistente | **PASS** | Tests `tests/test_idempotency.py` y `tests/test_scheduler.py` pasados (4/4). Conversión horaria exacta a UTC para `America/Santo_Domingo`, bloqueo de duplicados por idempotency key y ejecución del worker. |

---

## Resumen de Pruebas Automatizadas

- **Total de pruebas unitarias e integración ejecutadas:** 23
- **Pruebas superadas:** 23 (100%)
- **Pruebas fallidas:** 0
- **Tiempo de ejecución de la suite:** < 2.0 segundos

```text
tests/test_autoclip_adapter.py::test_extract_output_variants_normalization PASSED
tests/test_autoclip_adapter.py::test_partial_status_preserves_completed_variants PASSED
tests/test_idempotency.py::test_deterministic_idempotency_key PASSED
tests/test_idempotency.py::test_approval_service_prevents_duplicate_jobs PASSED
tests/test_ingestion.py::test_ingest_local_video PASSED
tests/test_ingestion.py::test_ingest_invalid_extension PASSED
tests/test_ingestion.py::test_ingest_missing_file PASSED
tests/test_ingestion.py::test_ssrf_and_url_security PASSED
tests/test_media_qa.py::test_qa_vertical_video_passes PASSED
tests/test_media_qa.py::test_qa_empty_file_fails PASSED
tests/test_media_qa.py::test_qa_corrupt_file_fails PASSED
tests/test_media_qa.py::test_qa_horizontal_video_fails_vertical_rule PASSED
tests/test_media_qa.py::test_qa_missing_file_fails PASSED
tests/test_publish_mock.py::test_upload_post_mock_workflow PASSED
tests/test_publish_mock.py::test_unconfigured_publisher_blocks_real_submit PASSED
tests/test_recovery.py::test_reconciler_updates_in_flight_jobs PASSED
tests/test_recovery.py::test_independent_platform_success PASSED
tests/test_scheduler.py::test_timezone_conversion_america_santo_domingo PASSED
tests/test_scheduler.py::test_scheduler_due_jobs_filtering PASSED
tests/test_selector_and_metadata.py::test_clip_selector_ranking_and_quota PASSED
tests/test_selector_and_metadata.py::test_metadata_generator_platform_rules PASSED
tests/test_web_api.py::test_web_admin_pages PASSED
tests/test_e2e_flow.py::test_end_to_end_orchestration_flow PASSED
```

---

## Estado de Conexión de Plataformas

- **YouTube Shorts:** Soporte verificado mediante transporte `youtube` con visibilidad forzada `private` en pruebas.
- **TikTok:** Soporte verificado mediante transporte `tiktok` con visibilidad forzada `SELF_ONLY` en pruebas. Si la API de TikTok devuelve borrador en bandeja de entrada (`fallback_to_inbox`), se cataloga fielmente como `ACTION_REQUIRED` y nunca como `PUBLISHED`.
- **Publicación Real en Redes Sociales:** Pendiente de configuración de credenciales del usuario (`UPLOAD_POST_API_KEY`).
