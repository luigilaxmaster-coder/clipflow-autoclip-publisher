# Guía Rápida — PC Dedicado de Automatización 24/7 (Windows)

Esta guía resume los comandos para instalar y dejar encendido **AutoClip Publisher — Personal Edition V2** en un segundo PC con Windows 10/11 trabajando de forma desatendida 24/7.

---

## 1. Prerrequisitos (PowerShell como Administrador)

```powershell
winget install Git.Git
winget install Gyan.FFmpeg
winget install astral-sh.uv
```

> Reinicia PowerShell después de instalar para que `git`, `ffmpeg`, `ffprobe` y `uv` estén disponibles en `PATH`.

---

## 2. Clonar e Instalar desde GitHub

```powershell
mkdir C:\AutoClip -ErrorAction SilentlyContinue
cd C:\AutoClip
git clone -b feature/publisher-personal-edition https://github.com/luigilaxmaster-coder/clipflow-autoclip-publisher.git clips
cd C:\AutoClip\clips

.\scripts\setup-windows.ps1
.\scripts\check-environment.ps1
```

---

## 3. Migrar Secretos de Forma Segura (Sin Git)

Nunca subas secretos a GitHub. Para que el PC dedicado herede tu canal de YouTube **Geist** ya conectado y tu misma Clave Maestra, copia manualmente (por USB o red privada local) estos 4 archivos desde tu PC actual hacia `C:\AutoClip\clips\`:

- `.env`
- `data\.secret.key` *(clave Fernet indispensable para descifrar `clipflow.db`)*
- `data\.owner.token` *(tu clave maestra de acceso al panel)*
- `data\clipflow.db`

> **Si prefieres empezar limpio en el PC 2:** copia `.env.example` a `.env`. Al iniciar `.\scripts\run-publisher.ps1` se generarán automáticamente nuevos `data\.owner.token` y `data\.secret.key`, y solo tendrás que autorizar YouTube/TikTok una vez en ese equipo.

---

## 4. Configuración 24/7 (Sin Suspensión + Auto-Arranque)

```powershell
# Evitar que Windows entre en suspensión o hibernación (PowerShell Administrador)
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0

# Validar suite completa de pruebas
.\.venv\Scripts\python.exe -m pytest -v

# Registrar auto-arranque automático en el Programador de Tareas de Windows
.\scripts\install-autostart-windows.ps1
```

Para iniciar inmediatamente la tarea programada sin esperar al próximo reinicio:

```powershell
Start-ScheduledTask -TaskName "AutoClipPublisher24x7"
```

---

## 5. Acceso Remoto Seguro desde tu PC Principal

El servidor escucha por seguridad solo en `127.0.0.1:8000`. Para administrarlo desde tu otro PC sin abrir puertos a Internet:

1. Instala **Tailscale** en ambos equipos (o usa OpenSSH Server de Windows en el PC dedicado).
2. Crea un túnel SSH desde tu PC principal:
   ```powershell
   ssh -L 8000:127.0.0.1:8000 TU_USUARIO@IP_PRIVADA_PC2
   ```
3. Abre en tu navegador local: `http://127.0.0.1:8000`

---

## 6. Mantenimiento y Actualizaciones

- **Actualizar código:** `.\scripts\update-system.ps1` (o `git pull origin feature/publisher-personal-edition` + `.\.venv\Scripts\python.exe -m pytest -v`).
- **Guía completa con diagramas (YouTube, TikTok OAuth, Meta, X, Postiz):** ver [GUIA_TECNICA_AUTOCLIP_TIKTOK_Y_PLATAFORMAS.pdf](./GUIA_TECNICA_AUTOCLIP_TIKTOK_Y_PLATAFORMAS.pdf).
