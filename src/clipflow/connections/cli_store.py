"""Utilidad CLI segura para almacenar credenciales cifradas leyendo únicamente desde stdin."""
from __future__ import annotations

import argparse
import json
import sys

from clipflow.connections.service import ConnectionService
from clipflow.db import get_session, init_db
from clipflow.security import redact_secrets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Almacena credenciales cifradas de un proveedor leyendo JSON desde stdin."
    )
    parser.add_argument("--provider", required=True, help="Identificador del proveedor (ej. youtube).")
    parser.add_argument("--account-name", default="Cuenta del Propietario", help="Nombre visible de la cuenta.")
    parser.add_argument("--no-test", action="store_true", help="No ejecutar diagnóstico de red inmediatamente.")
    args = parser.parse_args(argv)

    raw_stdin = sys.stdin.read()
    if not raw_stdin.strip():
        print("[!] Error: No se recibió carga útil JSON por stdin.", file=sys.stderr)
        return 1

    try:
        payload = json.loads(raw_stdin)
    except json.JSONDecodeError:
        print("[!] Error: Entrada JSON inválida por stdin.", file=sys.stderr)
        return 1

    if not isinstance(payload, dict):
        print("[!] Error: El JSON de entrada debe ser un objeto clave/valor.", file=sys.stderr)
        return 1

    # Filtrar campos vacíos y prohibir bandera mock por CLI
    credentials = {
        k: v.strip() if isinstance(v, str) else v
        for k, v in payload.items()
        if k != "mock" and v is not None and (not isinstance(v, str) or v.strip() != "")
    }

    if not credentials:
        print("[!] Error: No se proporcionaron credenciales válidas.", file=sys.stderr)
        return 1

    # Garantizar separación estricta entre access_token y refresh_token
    if (
        credentials.get("access_token")
        and credentials.get("refresh_token")
        and credentials["access_token"] == credentials["refresh_token"]
    ):
        print(
            "[!] Error: access_token y refresh_token no pueden ser idénticos. "
            "Proporcione únicamente el refresh_token o deje ambos vacíos para usar OAuth interactivo.",
            file=sys.stderr,
        )
        return 1

    init_db()
    session = get_session()
    try:
        service = ConnectionService(session)
        res = service.save_connection(
            provider_id=args.provider,
            account_name=args.account_name,
            credentials=credentials,
            test_now=not args.no_test,
            allow_mock=False,
        )
        print(f"Resultado del diagnóstico: {redact_secrets(res['message'])} (Estado: {res['status']})")
        return 0
    finally:
        # Limpiar referencias en memoria
        credentials.clear()
        payload.clear()
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
