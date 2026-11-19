"""Módulo de ingesta y validación de fuentes (archivos locales y URLs compatibles)."""
from __future__ import annotations

import hashlib
import ipaddress
import os
import socket
from pathlib import Path
from urllib.parse import urlparse
from typing import Optional, Tuple

from clipflow.media_qa import find_ffprobe, MediaQA
from clipflow.models import Source, utc_now


ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
ALLOWED_DOMAINS = {"youtube.com", "youtu.be", "bilibili.com", "b23.tv"}


def compute_sha256(file_path: Path, block_size: int = 1024 * 1024) -> str:
    """Calcula el hash SHA-256 de un archivo local en bloques de 1MB."""
    hasher = hashlib.sha256()
    with file_path.open("rb") as f:
        while chunk := f.read(block_size):
            hasher.update(chunk)
    return hasher.hexdigest()


def is_safe_remote_url(url: str) -> Tuple[bool, str]:
    """Valida esquema HTTPS, dominios autorizados y previene SSRF (localhost, redes privadas)."""
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        return False, "Solo se admiten URLs seguras con esquema HTTPS."

    host = (parsed.hostname or "").lower()
    if not host:
        return False, "La URL no contiene un nombre de host válido."

    if parsed.username or parsed.password:
        return False, "No se permiten credenciales embebidas en la URL."

    # Validar dominio permitido
    if not any(host == d or host.endswith("." + d) for d in ALLOWED_DOMAINS):
        return False, f"Dominio no soportado: {host}. Permitidos: YouTube y Bilibili."

    # Protección anti-SSRF: resolución DNS
    try:
        addrinfo = socket.getaddrinfo(host, None)
        for _, _, _, _, sockaddr in addrinfo:
            ip_str = sockaddr[0]
            ip_obj = ipaddress.ip_address(ip_str)
            if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_multicast:
                return False, f"Acceso denegado: el destino resuelve a una dirección privada/restringida ({ip_str})."
    except Exception as e:
        return False, f"No se pudo resolver el host para verificación de seguridad: {e}"

    return True, "URL válida."


class IngestionService:
    def __init__(self, qa: Optional[MediaQA] = None):
        self.qa = qa or MediaQA()

    def process_source(
        self,
        source_input: str,
        srt_path: Optional[str] = None,
        rights_confirmed: bool = True,
    ) -> Source:
        """Valida e ingesta una fuente local o remota creando un registro Source tipado."""
        source_input = source_input.strip()
        parsed = urlparse(source_input)

        if parsed.scheme in ("http", "https"):
            is_safe, reason = is_safe_remote_url(source_input)
            if not is_safe:
                raise ValueError(f"URL rechazada: {reason}")

            return Source(
                source_kind="url",
                canonical_source=source_input,
                local_path="",
                sha256=None,
                duration_sec=None,
                rights_confirmed=rights_confirmed,
                created_at=utc_now(),
            )

        # Archivo local
        path = Path(source_input).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Archivo de video no encontrado: {path}")

        if path.suffix.lower() not in ALLOWED_VIDEO_EXTENSIONS:
            raise ValueError(
                f"Formato de video no soportado: {path.suffix}. Permitidos: {', '.join(ALLOWED_VIDEO_EXTENSIONS)}"
            )

        # Sondear metadatos y calcular SHA-256
        probe_data = self.qa.probe(path)
        fmt = probe_data.get("format", {})
        try:
            duration = float(fmt.get("duration") or 0.0)
        except (ValueError, TypeError):
            duration = 0.0

        file_sha256 = compute_sha256(path)

        return Source(
            source_kind="file",
            canonical_source=str(path),
            local_path=str(path),
            sha256=file_sha256,
            duration_sec=duration,
            rights_confirmed=rights_confirmed,
            created_at=utc_now(),
        )
