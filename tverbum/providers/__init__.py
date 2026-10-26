"""
Camada de Provedores de IA do tVerbum (Multi-Provider LLM Adapter).
Suporta Google Gemini, Groq, OpenRouter e Ollama Local com roteamento inteligente e failover automático.
"""

from .base import BaseLLMProvider
from .gemini_provider import GeminiProvider
from .groq_provider import GroqProvider
from .openrouter_provider import OpenRouterProvider
from .ollama_provider import OllamaProvider
from .router import ProviderRouter

__all__ = [
    "BaseLLMProvider",
    "GeminiProvider",
    "GroqProvider",
    "OpenRouterProvider",
    "OllamaProvider",
    "ProviderRouter",
]
