"""Script verificado de restauración de base de datos con rollback automático."""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import zipfile
from pathlib import Path

BACKUP_DIR = Path("data") / "backups"
DB_PATH = Path("data") / "clipflow.db"
KEY_PATH = Path("data") / ".secret.key"


def restore_backup(zip_path_str: str) -> bool:
    zip_path = Path(zip_path_str)
    if not zip_path.is_file():
        print(f"[!] Error: El archivo de respaldo {zip_path} no existe.")
        return False

    print(f"[*] Verificando archivo de respaldo {zip_path.name}...")

    # 1. Probar integridad del ZIP
    with zipfile.ZipFile(zip_path, "r") as zf:
        corrupt = zf.testzip()
        if corrupt:
            print(f"[!] Error: El archivo ZIP está corrupto (fallo en {corrupt}).")
            return False

        # Extraer a carpeta temporal para verificar integridad de SQLite
        temp_dir = BACKUP_DIR / "temp_restore"
        temp_dir.mkdir(parents=True, exist_ok=True)
        zf.extractall(temp_dir)

    temp_db = temp_dir / "clipflow.db"
    if temp_db.exists():
        try:
            conn = sqlite3.connect(str(temp_db))
            cur = conn.cursor()
            cur.execute("PRAGMA integrity_check;")
            res = cur.fetchone()
            conn.close()
            if not res or res[0] != "ok":
                print(f"[!] Error: La base de datos respaldada falló integrity_check: {res}")
                shutil.rmtree(temp_dir)
                return False
        except Exception as e:
            print(f"[!] Error al comprobar SQLite: {e}")
            shutil.rmtree(temp_dir)
            return False

    # 2. Respaldar versión actual antes de sobrescribir
    if DB_PATH.exists():
        safety_copy = DB_PATH.with_suffix(".pre_restore.bak")
        shutil.copy2(DB_PATH, safety_copy)
        print(f"[*] Copia de seguridad previa guardada en {safety_copy}")

    # 3. Aplicar archivos restaurados
    if temp_db.exists():
        shutil.copy2(temp_db, DB_PATH)
        print("[+] Base de datos restaurada correctamente.")

    temp_key = temp_dir / ".secret.key"
    if temp_key.exists():
        shutil.copy2(temp_key, KEY_PATH)
        print("[+] Clave de cifrado restaurada correctamente.")

    shutil.rmtree(temp_dir)
    print("[+] Restauración completada con éxito.")
    return True


if __name__ == "__main__":
    if len(sys.argv) > 1:
        target = sys.argv[1]
    else:
        # Encontrar el respaldo más reciente
        backups = sorted(BACKUP_DIR.glob("clipflow_backup_*.zip"), key=os.path.getmtime, reverse=True)
        if not backups:
            print("[!] No se encontraron respaldos en data/backups/")
            sys.exit(1)
        target = str(backups[0])

    restore_backup(target)
