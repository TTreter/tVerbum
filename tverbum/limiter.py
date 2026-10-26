"""
Módulo de Controle de Taxa e Resiliência (Rate Limiting & Exponential Backoff com Jitter) - tVerbum v2.0.
Protege contra estouro de requisições por minuto (RPM) e bloqueios HTTP 429 em camadas gratuitas de IA.
"""

import time
import random
import logging
import threading
from typing import Callable, Any, Dict, List, Optional

logger = logging.getLogger("tverbum.limiter")


class RateLimiter:
    """
    Gerencia taxa de requisições por minuto (RPM) em janela deslizante (sliding window)
    e provê retentativas automáticas com espera exponencial (exponential backoff) e jitter.
    """

    _lock = threading.Lock()
    _timestamps: Dict[str, List[float]] = {}

    DEFAULT_RPM_LIMITS = {
        "gemini": 15,      # 15 RPM na camada gratuita do Google AI Studio
        "groq": 30,        # 30 RPM na camada gratuita da Groq
        "openrouter": 20,  # ~20 RPM nos endpoints :free
        "ollama": 999,     # Sem restrições de rede externa
        "default": 15,
    }

    @classmethod
    def throttle(cls, provider: str = "default") -> None:
        """
        Garante que a cadência de disparos respeite o teto de RPM do provedor.
        Se a frequência estiver próxima do limite, aplica uma pequena pausa transparente.
        """
        prov_key = provider.lower().strip()
        max_rpm = cls.DEFAULT_RPM_LIMITS.get(prov_key, cls.DEFAULT_RPM_LIMITS["default"])

        with cls._lock:
            now = time.time()
            if prov_key not in cls._timestamps:
                cls._timestamps[prov_key] = []

            # Remove timestamps mais antigos que 60 segundos
            cls._timestamps[prov_key] = [t for t in cls._timestamps[prov_key] if now - t < 60.0]

            if len(cls._timestamps[prov_key]) >= max_rpm:
                oldest = cls._timestamps[prov_key][0]
                sleep_needed = max(0.1, 60.0 - (now - oldest) + 0.1)
                logger.info(f"RateLimiter: limite de {max_rpm} RPM atingido para '{prov_key}'. Aguardando {sleep_needed:.2f}s...")
                time.sleep(sleep_needed)
                now = time.time()
                cls._timestamps[prov_key] = [t for t in cls._timestamps[prov_key] if now - t < 60.0]

            cls._timestamps[prov_key].append(now)

    @classmethod
    def execute_with_backoff(
        cls,
        func: Callable[[], Any],
        provider: str = "default",
        max_retries: int = 3,
        base_delay: float = 2.0,
        max_delay: float = 20.0,
    ) -> Any:
        """
        Executa uma função com rate limiting preventivo e retentativas exponenciais com jitter.
        """
        cls.throttle(provider)

        attempt = 0
        while True:
            try:
                return func()
            except Exception as e:
                attempt += 1
                err_str = str(e).lower()
                is_transient = any(code in err_str for code in ("429", "503", "502", "504", "resource_exhausted", "timeout", "timed out"))

                if attempt > max_retries or not is_transient:
                    raise e

                jitter = random.uniform(0.2, 1.2)
                delay = min(max_delay, (base_delay * (2 ** (attempt - 1)))) + jitter
                logger.warning(
                    f"Tentativa {attempt}/{max_retries} falhou para '{provider}' ({e}). "
                    f"Retentando em {delay:.2f}s..."
                )
                time.sleep(delay)
                cls.throttle(provider)
