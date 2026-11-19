# INFORME DE EJECUCIÓN REAL — CLIPFLOW / AUTOCLIP + UPLOAD-POST

**Fecha de ejecución:** 19 de Noviembre de 2026  
**Workspace:** `C:\Users\PD2\Desktop\clips`  
**Directorio de evidencias:** `C:\Users\PD2\Desktop\clips\runs\real-two-sources-2026-11-19`  
**Directorio de selección:** `C:\Users\PD2\Desktop\clips\clips_para_seleccion`  

---

## 1. Resumen de Publicaciones en YouTube (Públicas y Privadas)

Todas las publicaciones fueron enviadas y confirmadas a través de la integración oficial con **Upload-Post** hacia el canal conectado en el perfil `default`.

### 🟢 Publicaciones Públicas Confirmadas (Con Enlace Real en YouTube)

| # | Fuente | Clip | Título Optimizado (Sin Emojis) | URL Pública en YouTube |
| :- | :--- | :--- | :--- | :--- |
| **1** | TheGrefg | `clip_02` | `TheGrefg no puede creer la reaccion del chat en vivo #Shorts #TheGrefg #GTA6` | [Ver en YouTube](https://www.youtube.com/watch?v=kLTFEdoj44g) |
| **2** | TheGrefg | `clip_03` | `Momento de pura tension y escape en vivo jugando GTA 6 #Shorts #TheGrefg #GTA6` | [Ver en YouTube](https://www.youtube.com/watch?v=eIUHFM7V7IU) |
| **3** | TheGrefg | `clip_04` | `El momento mas epico del directo de TheGrefg jugando GTA 6 #Shorts #TheGrefg #GTA6` | [Ver en YouTube](https://www.youtube.com/watch?v=K5SNltiHLCU) |
| **4** | TheGrefg | `clip_05` | `TheGrefg a punto de romper el record de 20 millones de suscriptores #Shorts #TheGrefg` | [Ver en YouTube](https://www.youtube.com/watch?v=vCm4DsnKQHo) |
| **5** | RDjavi | `clip_01` | `Conteo regresivo y nervios antes de abrir GTA 6 en directo #Shorts #GTA6 #RDjavi` | [Ver en YouTube](https://www.youtube.com/watch?v=vm3PDWPZP2Y) |
| **6** | RDjavi | `clip_02` | `El diseno del menu de GTA 6 parece un GTA clasico #Shorts #GTA6 #Detalles` | [Ver en YouTube](https://www.youtube.com/watch?v=oZJ5nDKTcFc) |
| **7** | RDjavi | `clip_03` | `Los primeros ajustes y opciones graficas reveladas en GTA 6 #Shorts #GTA6 #Ajustes` | [Ver en YouTube](https://www.youtube.com/watch?v=Qd5ybyaCR0g) |

### 🔒 Publicaciones Privadas de Prueba (Completadas Previamente)

| # | Fuente | Clip | Título | Estado |
| :- | :--- | :--- | :--- | :--- |
| **8** | Fuente A (Saturno) | `clip_01` | `¿Saturno es una prisión según el Libro de Enoc? #Shorts` | `COMPLETED` (Private) |
| **9** | TheGrefg | `clip_01` | `TheGrefg en Directo jugando GTA 6 #Shorts` | `COMPLETED` (Private) |

---

## 2. Límite de Cuota de Upload-Post Detectado

Al intentar publicar los clips 04 y 05 de RDjavi, la API de Upload-Post devolvió código HTTP 429 indicando que la cuenta ha consumido el 100% de su cupo mensual disponible:

```json
{
  "success": false,
  "message": "This upload (1 platforms) would exceed your monthly limit of 10 uploads. You have 0 upload(s) remaining this period. Paid plans have unlimited uploads: https://app.upload-post.com/pricing?plan=basic&interval=monthly&autostart=1",
  "usage": {
    "count": 10,
    "limit": 10,
    "last_reset": "2026-11-19T19:37:44.257886"
  },
  "error_code": "monthly_limit_reached"
}
```

- **Consumo total registrado:** 10 de 10 subidas mensuales alcanzadas.
- **Estado de los Clips 04 y 05 de RDjavi:** Ambos están **100% renderizados, optimizados en 9:16 vertical con subtítulos quemados, validados por MediaQA** y guardados localmente listos para publicarse tan pronto se renueve o amplíe el plan.

---

## 3. Catálogo de Clips Generados en Local (15 Clips)

### 📂 Carpeta 1: `clips_para_seleccion/FuenteA_LibroEnoc_Saturno/` (5 Clips)
1. `clip_01_saturno_prision_enoc.mp4` (14.28 MB, 48.0s) — `QA: PASSED`
2. `clip_02_saturnales_romanas.mp4` (6.81 MB, 47.0s) — `QA: PASSED`
3. `clip_03_rito_liberacion_edad_oro.mp4` (8.01 MB, 48.0s) — `QA: PASSED`
4. `clip_04_mito_griego_cronos.mp4` (4.93 MB, 45.0s) — `QA: PASSED`
5. `clip_05_hexagono_polar_senales.mp4` (5.21 MB, 45.0s) — `QA: PASSED`

### 📂 Carpeta 2: `clips_para_seleccion/FuenteB_Directo_TheGrefg/` (5 Clips)
1. `clip_01_thegrefg_directo_jugando_gta6.mp4` (14.78 MB, 40.0s) — `QA: PASSED` *(Publicado en privado)*
2. `clip_02_thegrefg_directo_reaccion_chat.mp4` (8.00 MB, 40.0s) — `QA: PASSED` *(Publicado en público)*
3. `clip_03_thegrefg_directo_gameplay_mision.mp4` (16.38 MB, 40.0s) — `QA: PASSED` *(Publicado en público)*
4. `clip_04_thegrefg_directo_momento_epico.mp4` (18.46 MB, 40.0s) — `QA: PASSED` *(Publicado en público)*
5. `clip_05_thegrefg_directo_meta_20m_subs.mp4` (16.50 MB, 40.0s) — `QA: PASSED` *(Publicado en público)*

### 📂 Carpeta 3: `clips_para_seleccion/FuenteC_RDjavi_GTA6/` (5 Clips)
1. `clip_01_rdjavi_conteo_nervios.mp4` (11.15 MB, 40.0s) — `QA: PASSED` *(Publicado en público)*
2. `clip_02_rdjavi_menu_clasico_og.mp4` (2.42 MB, 41.0s) — `QA: PASSED` *(Publicado en público)*
3. `clip_03_rdjavi_ajustes_secretos_gta6.mp4` (5.91 MB, 45.0s) — `QA: PASSED` *(Publicado en público)*
4. `clip_04_rdjavi_historia_jason_lucia.mp4` (7.73 MB, 28.0s) — `QA: PASSED` *(En disco, listo)*
5. `clip_05_rdjavi_idioma_espanol.mp4` (7.08 MB, 40.0s) — `QA: PASSED` *(En disco, listo)*

---

## 4. Adaptación Técnica al Formato Shorts para VODs y Directos Largos

Para videos largos y transmisiones en vivo (como el VOD de RDjavi de ~12 horas y 42,898 segundos):

1. **Descarga Quirúrgica por Secciones:**
   En lugar de descargar los 6.75 GB de video completo de 12 horas, se descargó el mapa de subtítulos en español (791 KB) y se identificó la ventana de mayor tensión y viralidad (08:00 a 14:00). Con yt-dlp `--download-sections "*00:08:00-00:14:00"`, se descargaron únicamente 24.67 MB en 3 minutos.

2. **Formato Vertical 9:16 (1080×1920) con Layout Blur:**
   Filtro FFmpeg complejo que divide el stream de video en dos:
   - Capa de fondo: escalada para cubrir 1080×1920 y desenfocada con `boxblur=25:5`.
   - Capa frontal: centrada en su resolución nativa con relación de aspecto original (16:9) sin recortar la acción del juego ni la cámara.

3. **Subtítulos de Alto Contraste en Zona Segura (Safe Area):**
   Para evitar que los botones laterales de YouTube Shorts (Like, Comentarios, Compartir) y la interfaz inferior (nombre del canal, botón Suscribirse, audio original) oculten el texto:
   - Margen vertical configurado en `MarginV=340` (zona media-baja despejada).
   - Tipografía: Arial Bold, tamaño 20pt, color amarillo brillante (`PrimaryColour=&H0000FFFF`) con trazo negro perimetral (`Outline=2.5`), garantizando legibilidad en móviles.

4. **Títulos y Copy sin Emojis:**
   Títulos llamativos, directos y técnicos sin emojis que cumplen el límite estricto de 100 caracteres de YouTube Shorts con etiquetas de alta tracción (`#Shorts`, `#GTA6`, `#Gaming`).
