"""Módulo de validación de calidad y conformidad multimedia (FFprobe / QA)."""
from __future__ import annotations

import json
import logging
import math
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def find_ffprobe() -> str:
    """Localiza el ejecutable de ffprobe en PATH o en ubicaciones conocidas."""
    probe = shutil.which("ffprobe")
    if probe:
        return probe
    from backend.utils.ffmpeg_utils import get_ffprobe_path
    try:
        p = get_ffprobe_path()
        if p and Path(p).exists():
            return str(p)
    except Exception:
        pass
    raise FileNotFoundError("ffprobe no fue encontrado en el sistema.")


@dataclass
class QARuleResult:
    rule: str
    status: str  # "pass" | "warn" | "fail"
    message: str
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class QAReport:
    target_path: str
    overall_status: str  # "PASSED" | "WARNING" | "FAILED"
    passed: bool
    duration_sec: float
    width: int
    height: int
    aspect_ratio: str
    video_codec: str
    audio_codec: str
    file_size_bytes: int
    rules: List[QARuleResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        return data


class MediaQA:
    """Validador estricto de especificaciones técnicas para YouTube Shorts y TikTok."""

    def __init__(self, ffprobe_bin: Optional[str] = None):
        self.ffprobe_bin = ffprobe_bin or find_ffprobe()

    def probe(self, video_path: str | Path, timeout: float = 30.0) -> Dict[str, Any]:
        path = Path(video_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Archivo de video no encontrado: {path}")

        cmd = [
            self.ffprobe_bin,
            "-v", "error",
            "-show_format",
            "-show_streams",
            "-print_format", "json",
            str(path),
        ]
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=timeout,
            )
            return json.loads(res.stdout)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"Tiempo de espera agotado al inspeccionar video con ffprobe: {e}") from e
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Error al ejecutar ffprobe (código {e.returncode}): {e.stderr}") from e
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Salida inválida de ffprobe: {e}") from e

    def validate_clip(
        self,
        video_path: str | Path,
        platform: str = "youtube_shorts",
        min_duration_sec: float = 10.0,
        max_duration_sec: float = 180.0,
        require_audio: bool = True,
        require_vertical: bool = True,
    ) -> QAReport:
        """Aplica la batería completa de reglas de conformidad QA a un MP4."""
        path = Path(video_path).resolve()
        rules: List[QARuleResult] = []

        # Regla 1: Existencia y tamaño
        if not path.exists():
            rules.append(QARuleResult("file_exists", "fail", "El archivo no existe."))
            return QAReport(str(path), "FAILED", False, 0, 0, 0, "unknown", "", "", 0, rules)

        size_bytes = path.stat().st_size
        if size_bytes == 0:
            rules.append(QARuleResult("file_not_empty", "fail", "El archivo está vacío (0 bytes).", {"bytes": 0}))
            return QAReport(str(path), "FAILED", False, 0, 0, 0, "unknown", "", "", 0, rules)
        else:
            rules.append(QARuleResult("file_not_empty", "pass", f"Tamaño válido: {size_bytes / (1024*1024):.2f} MB", {"bytes": size_bytes}))

        # Sondear streams con ffprobe
        try:
            probe_data = self.probe(path)
        except Exception as e:
            rules.append(QARuleResult("ffprobe_readable", "fail", f"Fallo al decodificar streams: {e}"))
            return QAReport(str(path), "FAILED", False, 0, 0, 0, "unknown", "", "", size_bytes, rules)

        rules.append(QARuleResult("ffprobe_readable", "pass", "Metadatos y streams legibles."))

        fmt = probe_data.get("format", {})
        streams = probe_data.get("streams", [])
        v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

        # Regla 2: Stream de video presente
        if not v_stream:
            rules.append(QARuleResult("video_stream", "fail", "No se detectó ningún stream de video."))
            return QAReport(str(path), "FAILED", False, 0, 0, 0, "none", "", "", size_bytes, rules)
        
        v_codec = v_stream.get("codec_name", "")
        rules.append(QARuleResult("video_stream", "pass", f"Stream de video detectado ({v_codec}).", {"codec": v_codec}))

        # Regla 3: Stream de audio
        a_codec = a_stream.get("codec_name", "") if a_stream else ""
        if require_audio and not a_stream:
            rules.append(QARuleResult("audio_stream", "fail", "Falta stream de audio en el clip."))
        elif not a_stream:
            rules.append(QARuleResult("audio_stream", "warn", "No hay stream de audio (fuente silenciosa)."))
        else:
            rules.append(QARuleResult("audio_stream", "pass", f"Stream de audio detectado ({a_codec}).", {"codec": a_codec}))

        # Regla 4: Duración válida
        duration_sec = 0.0
        dur_raw = fmt.get("duration") or v_stream.get("duration")
        try:
            duration_sec = float(dur_raw or 0)
        except (ValueError, TypeError):
            pass

        if not math.isfinite(duration_sec) or duration_sec <= 0:
            rules.append(QARuleResult("duration_valid", "fail", f"Duración inválida: {dur_raw}"))
        elif duration_sec < min_duration_sec:
            rules.append(QARuleResult("duration_valid", "warn", f"Duración muy corta: {duration_sec:.1f}s (< {min_duration_sec}s)", {"duration": duration_sec}))
        elif duration_sec > max_duration_sec:
            rules.append(QARuleResult("duration_valid", "fail", f"Duración excede el límite de {max_duration_sec}s: {duration_sec:.1f}s", {"duration": duration_sec}))
        else:
            rules.append(QARuleResult("duration_valid", "pass", f"Duración en rango óptimo: {duration_sec:.1f}s", {"duration": duration_sec}))

        # Regla 5: Dimensiones y orientación 9:16
        width = int(v_stream.get("width") or 0)
        height = int(v_stream.get("height") or 0)
        aspect = f"{width}:{height}"

        if width <= 0 or height <= 0:
            rules.append(QARuleResult("dimensions_valid", "fail", f"Dimensiones no válidas: {width}x{height}"))
        else:
            rules.append(QARuleResult("dimensions_valid", "pass", f"Dimensiones: {width}x{height}"))

        if require_vertical:
            if height > width:
                # Relación esperada 9:16 (~0.5625)
                ratio = width / height
                if 0.50 <= ratio <= 0.60:
                    rules.append(QARuleResult("aspect_ratio_9_16", "pass", f"Orientación vertical estándar 9:16 (ratio {ratio:.3f})"))
                else:
                    rules.append(QARuleResult("aspect_ratio_9_16", "warn", f"Orientación vertical no estándar (ratio {ratio:.3f})"))
            else:
                rules.append(QARuleResult("aspect_ratio_9_16", "fail", f"El clip es horizontal ({width}x{height}), se requiere vertical 9:16 para Shorts/TikTok."))

        # Regla 6: Codec y Pixel format
        pix_fmt = v_stream.get("pix_fmt", "")
        if v_codec in ("h264", "hevc", "av1", "vp9"):
            rules.append(QARuleResult("video_codec_standard", "pass", f"Codec estándar soportado: {v_codec}"))
        else:
            rules.append(QARuleResult("video_codec_standard", "warn", f"Codec no óptimo para redes: {v_codec}"))

        # Determinar overall_status
        has_fail = any(r.status == "fail" for r in rules)
        has_warn = any(r.status == "warn" for r in rules)
        if has_fail:
            overall = "FAILED"
            passed = False
        elif has_warn:
            overall = "WARNING"
            passed = True
        else:
            overall = "PASSED"
            passed = True

        return QAReport(
            target_path=str(path),
            overall_status=overall,
            passed=passed,
            duration_sec=duration_sec,
            width=width,
            height=height,
            aspect_ratio=aspect,
            video_codec=v_codec,
            audio_codec=a_codec,
            file_size_bytes=size_bytes,
            rules=rules,
        )
