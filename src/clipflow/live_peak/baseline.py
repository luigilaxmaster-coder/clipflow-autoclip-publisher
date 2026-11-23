"""Línea base estadística robusta (mediana / MAD) por creador y detección de spam."""
from __future__ import annotations

import math
from collections import deque
from statistics import median
from typing import Deque, Dict, Optional, Tuple


class RobustBaseline:
    """
    Mantiene ventanas deslizantes por métrica continua y calcula z_robust:
        z_robust = (x - median_ref) / max(1.4826 * MAD_ref, epsilon)
    Normalizado a [0, 1] mediante función sigmoidal monótona acotada, con warmup conservador.
    """

    def __init__(
        self,
        window_size: int = 720,  # 60 min a intervalos de 5s
        warmup_min_samples: int = 120,  # 10 min a intervalos de 5s
        max_z: float = 6.0,
    ):
        self.window_size = max(12, int(window_size))
        self.warmup_min_samples = max(6, int(warmup_min_samples))
        self.max_z = max(2.0, float(max_z))
        self._series: Dict[str, Deque[float]] = {}

    def _buf(self, key: str) -> Deque[float]:
        if key not in self._series:
            self._series[key] = deque(maxlen=self.window_size)
        return self._series[key]

    @staticmethod
    def _transform(value: float, use_log1p: bool) -> float:
        safe_v = max(0.0, float(value)) if use_log1p else float(value)
        return math.log1p(safe_v) if use_log1p else safe_v

    def score_and_update(
        self,
        metric_key: str,
        raw_value: Optional[float],
        use_log1p: bool = False,
        epsilon: float = 0.25,
        record: bool = True,
    ) -> Tuple[Optional[float], float, bool]:
        """
        Retorna `(score_0_1, z_robust, is_warmup)`.
        Si `raw_value is None`, retorna `(None, 0.0, False)` sin falsificar ceros.
        """
        if raw_value is None:
            return None, 0.0, False

        buf = self._buf(metric_key)
        val = self._transform(raw_value, use_log1p=use_log1p)
        is_warmup = len(buf) < self.warmup_min_samples

        if len(buf) < 4:
            if record:
                buf.append(val)
            # En arranque frío sin histórico, devolver respuesta conservadora (baja) para evitar falsas alarmas
            return 0.15, 0.0, True

        med = median(buf)
        abs_devs = [abs(x - med) for x in buf]
        mad = median(abs_devs)
        scale = max(1.4826 * mad, epsilon)
        z_raw = (val - med) / scale
        z_clamped = max(-self.max_z, min(self.max_z, z_raw))

        # Penalización conservadora durante primeros 10-15 min sin baseline maduro
        effective_z = z_clamped * (0.65 if is_warmup else 1.0)
        # Normalización monótona [0, 1] centrada en z ~ 2.2
        norm = 1.0 / (1.0 + math.exp(-(effective_z - 2.2) * 1.15))
        norm = max(0.0, min(1.0, norm))

        if record:
            buf.append(val)

        return round(norm, 4), round(z_clamped, 3), is_warmup

    @staticmethod
    def is_spam_burst(
        bot_ratio: float = 0.0,
        duplicate_ratio: float = 0.0,
        unique_chatters: Optional[int] = None,
        total_messages: Optional[int] = None,
    ) -> bool:
        """Detecta ráfagas de spam/bots repetitivos que no deben disparar clips positivos."""
        if bot_ratio >= 0.45 or duplicate_ratio >= 0.55:
            return True
        if total_messages is not None and unique_chatters is not None and total_messages >= 25:
            diversity = unique_chatters / max(1, total_messages)
            if diversity < 0.08:
                return True
        return False

    def export_state(self) -> Dict[str, list[float]]:
        return {k: list(v) for k, v in self._series.items()}

    def import_state(self, state: Optional[Dict[str, list[float]]]) -> None:
        if not state:
            return
        for k, vals in state.items():
            buf = self._buf(k)
            buf.clear()
            for v in vals[-self.window_size :]:
                buf.append(float(v))
