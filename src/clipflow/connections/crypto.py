"""Módulo de cifrado seguro para credenciales y tokens OAuth utilizando Fernet."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

KEY_FILE = Path("data") / ".secret.key"


def _get_or_create_key() -> bytes:
    # 1. Leer de variable de entorno si existe
    env_key = os.getenv("CREDENTIALS_ENCRYPTION_KEY") or os.getenv("APP_SECRET_KEY")
    if env_key:
        # Asegurar clave Fernet válida de 32 bytes en base64 urlsafe
        hashed = hashlib.sha256(env_key.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(hashed)

    # 2. Leer de archivo seguro local en data/
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if KEY_FILE.is_file():
        try:
            return KEY_FILE.read_bytes().strip()
        except Exception:
            pass

    # 3. Generar nueva clave Fernet y persistir
    new_key = Fernet.generate_key()
    try:
        KEY_FILE.write_bytes(new_key)
    except Exception as e:
        logger.warning("No se pudo escribir archivo de clave en %s: %s", KEY_FILE, e)
    return new_key


_fernet_instance: Fernet | None = None


def get_fernet() -> Fernet:
    global _fernet_instance
    if _fernet_instance is None:
        key = _get_or_create_key()
        _fernet_instance = Fernet(key)
    return _fernet_instance


def encrypt_credentials(data: Dict[str, Any]) -> str:
    """Cifra un diccionario de credenciales a un string seguro."""
    payload_str = json.dumps(data)
    f = get_fernet()
    encrypted_bytes = f.encrypt(payload_str.encode("utf-8"))
    return encrypted_bytes.decode("utf-8")


def decrypt_credentials(encrypted_text: str) -> Dict[str, Any]:
    """Descifra un texto cifrado y retorna el diccionario de credenciales."""
    if not encrypted_text:
        return {}
    f = get_fernet()
    try:
        decrypted_bytes = f.decrypt(encrypted_text.encode("utf-8"))
        return json.loads(decrypted_bytes.decode("utf-8"))
    except Exception as e:
        logger.error("Error al descifrar credenciales: %s", e)
        return {}
