"""Seguridad de propietario único (Single-Owner Authentication) y control de acceso para el dashboard."""
from __future__ import annotations

import hmac
import logging
import os
from typing import Optional

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

logger = logging.getLogger(__name__)

COOKIE_NAME = "clipflow_owner_session"


def get_owner_token() -> str:
    """Obtiene el token maestro del propietario desde variables de entorno."""
    return os.getenv("OWNER_TOKEN") or os.getenv("APP_SECRET_KEY") or "clipflow-personal-owner-2026"


def verify_owner_access(request: Request) -> bool:
    """
    Verifica si la petición proviene del propietario legítimo mediante:
    1. Header Authorization Bearer
    2. Header X-Owner-Token
    3. Cookie de sesión del navegador
    4. Acceso directo por localhost si ALLOW_LOCALHOST_DEV está activado
    """
    owner_token = get_owner_token()

    # 1. Bearer Token
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        if hmac.compare_digest(token, owner_token):
            return True

    # 2. Header X-Owner-Token
    custom_header = request.headers.get("X-Owner-Token")
    if custom_header and hmac.compare_digest(custom_header.strip(), owner_token):
        return True

    # 3. Cookie de sesión
    session_cookie = request.cookies.get(COOKIE_NAME)
    if session_cookie and hmac.compare_digest(session_cookie.strip(), owner_token):
        return True

    # 4. Modo desarrollo local permisivo si está explícitamente configurado
    if os.getenv("ALLOW_LOCALHOST_DEV", "true").lower() in ("true", "1", "yes"):
        client_host = request.client.host if request.client else ""
        if client_host in ("127.0.0.1", "localhost", "::1", "testclient"):
            return True

    return False


def require_owner(request: Request):
    """Dependencia de FastAPI para proteger rutas administrativas."""
    if not verify_owner_access(request):
        # Si es petición HTML en navegador, redirigir al login
        if "text/html" in request.headers.get("Accept", ""):
            return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Acceso denegado: Se requiere autenticación del propietario único.",
        )
