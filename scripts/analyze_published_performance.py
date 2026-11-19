"""Script ejecutable para ejecutar el bucle de retroalimentación y optimización viral de ClipFlow."""
from __future__ import annotations

import json
from pathlib import Path

from clipflow.analytics import PerformanceAnalyzer


def main():
    print("=================================================================")
    print("   CLIPFLOW: FEEDBACK LOOP & ANÁLISIS DE RENDIMIENTO VIRAL       ")
    print("=================================================================")

    analyzer = PerformanceAnalyzer()
    formula = analyzer.analyze_published_portfolio()

    if formula.get("status") == "NO_DATA":
        print("[!] No hay videos suficientes con métricas públicas para optimizar aún.")
        return

    print("\n✓ Análisis completado con éxito. La fórmula viral ha sido aprendida y guardada.")
    print("Las próximas tandas de clips usarán automáticamente estas pautas de éxito.")


if __name__ == "__main__":
    main()
