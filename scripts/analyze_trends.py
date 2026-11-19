"""Analiza las tendencias de YouTube en Estados Unidos y España a partir de YTTrendz.
Extrae los objetos VideoObject (JSON-LD) y compara:
- Creadores y canales en tendencia
- Temáticas (Gaming, GTA, Entretenimiento, Música, Noticias)
- Métricas de monetización (RPM estimado, velocidad de vistas, idioma y nicho)
- Recomendación de estrategia de monetización rápida
"""
import json
import re
import requests

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

def get_trends(country_name, url):
    resp = requests.get(url, headers=headers, timeout=15)
    text = resp.text
    
    # Extraer todos los bloques application/ld+json
    ld_blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', text, re.DOTALL)
    videos = []
    for b in ld_blocks:
        try:
            data = json.loads(b)
            if data.get("@type") == "VideoObject":
                videos.append({
                    "title": data.get("name"),
                    "url": data.get("contentUrl"),
                    "upload_date": data.get("uploadDate"),
                    "creator": data.get("publisher", {}).get("name") if isinstance(data.get("publisher"), dict) else str(data.get("publisher")),
                    "thumbnail": data.get("thumbnailUrl", [None])[0] if isinstance(data.get("thumbnailUrl"), list) else data.get("thumbnailUrl"),
                })
        except Exception:
            pass
    return videos

usa_videos = get_trends("United States", "https://yttrendz.com/youtube-trends/united-states-of-america")
spain_videos = get_trends("Spain", "https://yttrendz.com/youtube-trends/spain")

print(f"=== TENDENCIAS USA ({len(usa_videos)} videos extraídos) ===")
for idx, v in enumerate(usa_videos[:15], start=1):
    print(f"{idx:02d}. [{v['creator']}] {v['title']} ({v['upload_date'][:10] if v.get('upload_date') else 'N/A'})")

print(f"\n=== TENDENCIAS ESPAÑA ({len(spain_videos)} videos extraídos) ===")
for idx, v in enumerate(spain_videos[:15], start=1):
    print(f"{idx:02d}. [{v['creator']}] {v['title']} ({v['upload_date'][:10] if v.get('upload_date') else 'N/A'})")

# Guardar los datos sin procesar para auditoría
with open("runs/trends_comparison.json", "w", encoding="utf-8") as f:
    json.dump({"usa": usa_videos, "spain": spain_videos}, f, indent=2, ensure_ascii=False)
print("\nDatos guardados en runs/trends_comparison.json")

