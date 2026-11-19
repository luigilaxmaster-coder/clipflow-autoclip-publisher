---
name: clipflow-optimizer
description: >-
  Analiza el rendimiento en vivo de los videos y clips de YouTube Shorts publicados por ClipFlow.
  Monitorea métricas (vistas, likes, retención y engagement), deconstruye por qué un video
  llama más la atención que otro, extrae la 'Fórmula Ganadora' (palabras de alto impacto,
  estructura de títulos sin emojis, hashtags de alta tracción y duración óptima) y retroalimenta
  automáticamente al generador para que la siguiente tanda de clips sea superior.
---

# ClipFlow Performance Optimizer & Viral Feedback Loop

Esta skill permite al asistente y a los agentes de ClipFlow analizar sistemáticamente el rendimiento real de los videos de YouTube Shorts publicados, identificar las correlaciones de éxito y retroalimentar el pipeline de generación para maximizar vistas, retención y velocidad de monetización.

---

## 1. Cuándo Usar Esta Skill

- Cuando se quiera evaluar el rendimiento real de la última tanda de videos subidos a YouTube.
- Antes de generar y publicar una nueva tanda de clips diarios (para aplicar las fórmulas que mejor funcionaron).
- Cuando el usuario pregunte por qué un video tuvo más vistas o likes que otros.
- En tareas programadas diarias de retroalimentación y analítica.

---

## 2. Flujo de Trabajo Operativo

### Paso 1: Recolección de Métricas en Vivo
El analizador consulta la base de datos local (`data/clipflow.db`) para obtener los enlaces públicos de YouTube y extrae mediante `yt-dlp`:
- `views`: Contador de reproducciones públicas.
- `likes`: Reacciones positivas.
- `comments`: Comentarios generados.
- `engagement_rate`: Relación porcentual `(likes + comments) / views * 100`.

### Paso 2: Deconstrucción de la Fórmula Ganadora
El algoritmo compara los videos en el percentil superior frente al resto y aísla:
1. **Palabras Clave de Impacto (Hook Words):** Términos que dispararon la curiosidad o urgencia (ej. *"conteo"*, *"nervios"*, *"locura"*, *"ajustes"*).
2. **Estructura Sintáctica:** Patrón óptimo `{Creador} + {Acción/Emoción} + {Detalle Inédito} + #Shorts #GTA6 + #{Nicho}`.
3. **Longitud de Caracteres:** Rango ideal entre 68 y 90 caracteres (promedio 78).
4. **Política Estricta de Emojis:** Títulos llamativos y limpios sin emojis para evitar penalizaciones de CTR y mantener seriedad técnica.
5. **Safe Zone de Subtítulos:** Altura `MarginV=340` para evitar interferencia con la interfaz de Shorts.

### Paso 3: Persistencia en la Memoria de Aprendizaje
Guarda la fórmula vigente en `data/viral_formulas.json`.

### Paso 4: Inyección en la Siguiente Tanda
Los módulos `src/clipflow/metadata.py` y `src/clipflow/multiagent.py` leen automáticamente `data/viral_formulas.json` para redactar los títulos y etiquetas de los próximos clips a renderizar.

---

## 3. Comandos de Ejecución Rápida

### Ejecutar Análisis y Actualizar Fórmula
```powershell
.\.venv\Scripts\python.exe scripts/analyze_published_performance.py
```

### Ejecutar Radar con la Fórmula Optimizada (Próxima Tanda)
```powershell
.\.venv\Scripts\python.exe scripts/run_trend_radar.py --region es --limit 10
```
