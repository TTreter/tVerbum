"""
Orquestrador Multi-Provedor com Failover Automático e Balanceamento de Cotas (tVerbum).
Gerencia a execução entre Gemini, Groq, OpenRouter e Ollama Local.
"""

import os
import time
import logging
from typing import Any, Dict, List, Optional, Tuple

from ..models import AIGeneratedPayload
from .base import BaseLLMProvider
from .gemini_provider import GeminiProvider
from .groq_provider import GroqProvider
from .openrouter_provider import OpenRouterProvider
from .ollama_provider import OllamaProvider

logger = logging.getLogger("tverbum.router")


class ProviderRouter:
    """
    Roteador inteligente de provedores de IA.
    Executa a requisição no provedor prioritário e, caso encontre erros de limite de cota (429)
    ou instabilidade temporária (503/504), faz a transição automática (failover) para o próximo
    provedor gratuito disponível sem interromper a geração da ata.
    """

    def __init__(self, primary_provider: Optional[str] = None):
        self.primary_provider = (
            primary_provider or os.getenv("AI_PROVIDER", "gemini")
        ).lower().strip()

        # Instanciação dos provedores suportados
        self.providers: Dict[str, BaseLLMProvider] = {
            "gemini": GeminiProvider(),
            "groq": GroqProvider(),
            "openrouter": OpenRouterProvider(),
            "ollama": OllamaProvider(),
        }

    def get_provider(self, name: str) -> Optional[BaseLLMProvider]:
        return self.providers.get(name.lower().strip())

    def list_available_providers(self) -> Dict[str, bool]:
        """Retorna o status de disponibilidade de cada provedor configurado."""
        status = {}
        for name, provider in self.providers.items():
            status[name] = provider.is_available()
        return status

    def _determine_execution_order(self, requested_provider: Optional[str] = None) -> List[Tuple[str, BaseLLMProvider]]:
        """Calcula a ordem de tentativa com base na preferência e disponibilidade."""
        first_choice = requested_provider or self.primary_provider
        order_names: List[str] = []

        if first_choice in self.providers and first_choice != "auto":
            order_names.append(first_choice)

        # Adiciona demais provedores disponíveis como fallback
        default_priority = ["gemini", "groq", "openrouter", "ollama"]
        for name in default_priority:
            if name not in order_names and name in self.providers:
                order_names.append(name)

        # Inclui quaisquer provedores adicionais registrados
        for name in self.providers:
            if name not in order_names:
                order_names.append(name)

        # Filtra apenas provedores que estejam disponíveis (chaves configuradas ou servidor online)
        active_providers: List[Tuple[str, BaseLLMProvider]] = []
        for name in order_names:
            if name in self.providers:
                provider = self.providers[name]
                if provider.is_available():
                    active_providers.append((name, provider))

        if not active_providers:
            # Se nenhum estiver estritamente disponível, retorna ao menos o prioritário para gerar mensagem de erro clara
            fallback_name = first_choice if first_choice in self.providers else "gemini"
            active_providers.append((fallback_name, self.providers[fallback_name]))

        return active_providers

    def generate_structured(
        self,
        contents: List[Any],
        system_instruction: str,
        user_prompt_header: str = "",
        preferred_provider: Optional[str] = None,
        model_name: Optional[str] = None,
    ) -> Tuple[AIGeneratedPayload, str, str]:
        """
        Executa a geração da ata estruturada com fallback transparente entre provedores.
        Retorna (payload, provedor_usado, modelo_usado).
        """
        execution_order = self._determine_execution_order(preferred_provider)
        errors: List[str] = []

        for name, provider in execution_order:
            try:
                payload, used_model = provider.generate_structured(
                    contents=contents,
                    system_instruction=system_instruction,
                    user_prompt_header=user_prompt_header,
                    model_name=model_name,
                )
                return payload, name, used_model
            except Exception as e:
                err_msg = f"Falha no provedor '{name}': {e}"
                logger.warning(err_msg)
                errors.append(err_msg)
                # Continua para o próximo provedor na lista de fallback
                continue

        all_errors = " | ".join(errors)
        raise RuntimeError(
            f"Todos os provedores de IA falharam na geração estruturada. Detalhes: {all_errors}"
        )

    def refine_snippet(
        self,
        snippet: str,
        instruction: str,
        preferred_provider: Optional[str] = None,
        model_name: Optional[str] = None,
    ) -> Tuple[str, str, str]:
        """
        Executa o refinamento rápido de trecho com fallback automático.
        Retorna (trecho_refinado, provedor_usado, modelo_usado).
        """
        execution_order = self._determine_execution_order(preferred_provider)
        errors: List[str] = []

        for name, provider in execution_order:
            try:
                refined, used_model = provider.refine_snippet(
                    snippet=snippet,
                    instruction=instruction,
                    model_name=model_name,
                )
                return refined, name, used_model
            except Exception as e:
                err_msg = f"Falha no provedor '{name}': {e}"
                logger.warning(err_msg)
                errors.append(err_msg)
                continue

        all_errors = " | ".join(errors)
        raise RuntimeError(
            f"Todos os provedores de IA falharam no refinamento do trecho. Detalhes: {all_errors}"
        )

    def test_provider(self, provider_name: str) -> Dict[str, Any]:
        """Executa um teste rápido de ping/geração para verificar se o provedor está operacional."""
        provider = self.get_provider(provider_name)
        if not provider:
            return {"provider": provider_name, "success": False, "error": "Provedor desconhecido"}

        if not provider.is_available():
            return {
                "provider": provider_name,
                "success": False,
                "error": "Credenciais não configuradas ou servidor inalcançável",
            }

        start = time.time()
        try:
            sample_snippet = "O vereador solicitou melhorias na iluminação pública."
            instruction = "Torne mais formal"
            refined, used_model = provider.refine_snippet(sample_snippet, instruction)
            latency = round(time.time() - start, 3)
            return {
                "provider": provider_name,
                "success": True,
                "latency_seconds": latency,
                "model_used": used_model,
                "sample_output": refined,
            }
        except Exception as e:
            return {
                "provider": provider_name,
                "success": False,
                "latency_seconds": round(time.time() - start, 3),
                "error": str(e),
            }
