"""Seguridad de propietario único, sesiones firmadas, protección CSRF y guardas anti-mock."""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
from pathlib import Path
from typing import Optional, Tuple

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse

logger = logging.getLogger(__name__)

COOKIE_NAME = "clipflow_owner_session"
CSRF_COOKIE_NAME = "clipflow_csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
OWNER_TOKEN_FILE = Path("data") / ".owner.token"

_SECRET_PATTERNS = [
    re.compile(r"(Bearer\s+)[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE),
    re.compile(r"(access_token|refresh_token|client_secret|api_key|bot_token|app_password|password|token)([\"']?\s*[:=]\s*[\"']?)([^\"'\s&,;\}]+)", re.IGNORECASE),
    re.compile(r"(ya29\.[A-Za-z0-9\-_]+)"),
    re.compile(r"(1//[A-Za-z0-9\-_]+)"),
    re.compile(r"(GOCSPX-[A-Za-z0-9\-_]+)"),
]


class OwnerAuthNotConfiguredError(RuntimeError):
    """Se lanza cuando no existe ningún OWNER_TOKEN configurado en el entorno."""


def is_production_env() -> bool:
    """Determina si el entorno se ejecuta en modo producción."""
    env = (os.getenv("AUTOCLIP_ENV") or os.getenv("CLIPFLOW_ENV") or "").strip().lower()
    return env in ("prod", "production")


def is_mock_allowed() -> bool:
    """
    El modo mock está estrictamente prohibido en producción.
    Solo se permite en ejecución de pruebas automatizadas (pytest) o si
    CLIPFLOW_ALLOW_MOCKS=true fue habilitado explícitamente fuera de producción.
    """
    if is_production_env():
        return False
    if os.getenv("PYTEST_CURRENT_TEST"):
        return True
    return os.getenv("CLIPFLOW_ALLOW_MOCKS", "false").strip().lower() in ("true", "1", "yes")


def assert_mock_allowed(context: str = "operación") -> None:
    """Lanza un error explícito si se intenta usar modo mock cuando está prohibido."""
    if not is_mock_allowed():
        raise RuntimeError(
            f"Modo mock prohibido en producción o entorno activo ({context}). "
            "Configure credenciales reales verificadas."
        )


def redact_secrets(text: str) -> str:
    """Elimina tokens, secretos OAuth y cabeceras Bearer antes de registrar logs o errores."""
    if not text:
        return text
    sanitized = text
    sanitized = _SECRET_PATTERNS[0].sub(r"\1[REDACTED]", sanitized)
    sanitized = _SECRET_PATTERNS[1].sub(r"\1\2[REDACTED]", sanitized)
    for pat in _SECRET_PATTERNS[2:]:
        sanitized = pat.sub("[REDACTED]", sanitized)
    return sanitized


def get_owner_token() -> Optional[str]:
    """
    Obtiene el token maestro del propietario desde OWNER_TOKEN o desde data/.owner.token.
    Nunca devuelve un valor predeterminado fijo en código fuente.
    """
    env_token = (os.getenv("OWNER_TOKEN") or "").strip()
    if env_token:
        return env_token

    if OWNER_TOKEN_FILE.is_file():
        try:
            file_token = OWNER_TOKEN_FILE.read_text(encoding="utf-8").strip()
            if len(file_token) >= 24:
                return file_token
        except Exception as e:
            logger.warning("No se pudo leer archivo de token de propietario: %s", e)

    return None


def ensure_owner_token_file() -> str:
    """
    Genera y persiste un token aleatorio criptográficamente seguro en data/.owner.token
    si no existe OWNER_TOKEN configurado. Nunca usa valores fijos.
    """
    existing = get_owner_token()
    if existing:
        return existing

    OWNER_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    generated = secrets.token_urlsafe(32)
    OWNER_TOKEN_FILE.write_text(generated, encoding="utf-8")
    try:
        os.chmod(OWNER_TOKEN_FILE, 0o600)
    except OSError:
        pass
    return generated


def _sign_value(secret_key: str, payload: str) -> str:
    return hmac.new(
        secret_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def create_session_cookie_value(owner_token: str) -> str:
    """Crea una cookie de sesión firmada con HMAC sin exponer el OWNER_TOKEN plano."""
    nonce = secrets.token_urlsafe(24)
    sig = _sign_value(owner_token, f"session:{nonce}")
    return f"{nonce}.{sig}"


def verify_session_cookie(cookie_value: Optional[str], owner_token: str) -> bool:
    """Verifica la firma HMAC de la cookie de sesión del propietario."""
    if not cookie_value or "." not in cookie_value:
        return False
    parts = cookie_value.split(".", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return False
    nonce, provided_sig = parts
    expected_sig = _sign_value(owner_token, f"session:{nonce}")
    return hmac.compare_digest(provided_sig, expected_sig)


def generate_csrf_token(session_cookie: str, owner_token: str) -> str:
    """Deriva un token CSRF vinculado criptográficamente a la sesión actual."""
    return _sign_value(owner_token, f"csrf:{session_cookie}")


def verify_csrf_token(provided_csrf: Optional[str], session_cookie: Optional[str], owner_token: str) -> bool:
    """Valida el token CSRF con comparación en tiempo constante."""
    if not provided_csrf or not session_cookie:
        return False
    expected = generate_csrf_token(session_cookie, owner_token)
    return hmac.compare_digest(provided_csrf.strip(), expected)


def authenticate_request(request: Request) -> Tuple[bool, str]:
    """
    Autentica una petición HTTP y retorna (es_valido, metodo_auth).
    Métodos posibles: "bearer", "header", "cookie", "none".
    NO existe bypass por localhost.
    """
    owner_token = get_owner_token()
    if not owner_token:
        return False, "unconfigured"

    # 1. Authorization: Bearer <OWNER_TOKEN>
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        if token and hmac.compare_digest(token, owner_token):
            return True, "bearer"

    # 2. Header X-Owner-Token: <OWNER_TOKEN>
    custom_header = request.headers.get("X-Owner-Token")
    if custom_header and hmac.compare_digest(custom_header.strip(), owner_token):
        return True, "header"

    # 3. Cookie de sesión firmada con HMAC
    session_cookie = request.cookies.get(COOKIE_NAME)
    if verify_session_cookie(session_cookie, owner_token):
        return True, "cookie"

    return False, "none"


def verify_owner_access(request: Request) -> bool:
    """Verifica si la petición cuenta con autenticación válida del propietario único."""
    ok, _ = authenticate_request(request)
    return ok


def get_request_csrf_token(request: Request) -> str:
    """Obtiene el token CSRF activo para inyectar en plantillas HTML."""
    owner_token = get_owner_token()
    session_cookie = request.cookies.get(COOKIE_NAME)
    if owner_token and session_cookie and verify_session_cookie(session_cookie, owner_token):
        return generate_csrf_token(session_cookie, owner_token)
    return ""


def wants_html_response(request: Request) -> bool:
    """Determina si el cliente espera una página HTML (navegador) en lugar de JSON API."""
    path = request.url.path
    if path.startswith("/api/"):
        return False
    accept = (request.headers.get("Accept") or "").lower()
    return "text/html" in accept or "*/*" in accept or not accept


def build_unauthorized_response(request: Request):
    """Devuelve 303 a /login para páginas HTML o 401 JSON para endpoints API."""
    if wants_html_response(request):
        next_url = request.url.path
        return RedirectResponse(url=f"/login?next={next_url}", status_code=status.HTTP_303_SEE_OTHER)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Acceso denegado: Se requiere autenticación del propietario único.",
        headers={"WWW-Authenticate": "Bearer"},
    )
