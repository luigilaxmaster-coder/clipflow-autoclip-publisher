"""Script automatizado de respaldo para base de datos SQLite y claves de cifrado."""
from __future__ import annotations

import datetime
import os
import shutil
import sqlite3
import sys
import zipfile
from pathlib import Path

BACKUP_DIR = Path("data") / "backups"
DB_PATH = Path("data") / "clipflow.db"
KEY_PATH = Path("data") / ".secret.key"


def create_backup() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = BACKUP_DIR / f"clipflow_backup_{timestamp}.zip"

    print(f"[*] Iniciando respaldo en {backup_file}...")

    # Crear una copia limpia de la base de datos usando SQLite backup API
    temp_db = BACKUP_DIR / f"temp_{timestamp}.db"
    if DB_PATH.exists():
        src_conn = sqlite3.connect(str(DB_PATH))
        dst_conn = sqlite3.connect(str(temp_db))
        with dst_conn:
            src_conn.backup(dst_conn)
        src_conn.close()
        dst_conn.close()

    with zipfile.ZipFile(backup_file, "w", zipfile.ZIP_DEFLATED) as zf:
        if temp_db.exists():
            zf.write(temp_db, arcname="clipflow.db")
        if KEY_PATH.exists():
            zf.write(KEY_PATH, arcname=".secret.key")

    if temp_db.exists():
        temp_db.unlink()

    print(f"[+] Respaldo completado exitosamente: {backup_file.stat().st_size} bytes")
    return backup_file


if __name__ == "__main__":
    create_backup()
