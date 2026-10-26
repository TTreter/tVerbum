"""
Adaptador de Provedor para Ollama Local (100% Offline e Privado) no tVerbum.
Permite geração sem limites de cota e sem envio de dados para servidores externos.
Recomendado para uso com modelos como 'qwen2.5:7b', 'llama3.2:3b' ou 'llama3.3:70b'.
"""

import os
from typing import Any, List, Optional, Tuple
import httpx

from ..models import AIGeneratedPayload
from .base import BaseLLMProvider


class OllamaProvider(BaseLLMProvider):
    """Adaptador para instâncias locais do Ollama via API HTTP REST."""

    name: str = "ollama"

    def __init__(
        self,
        base_url: Optional[str] = None,
        default_model: Optional[str] = None,
    ):
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")
        self.default_model = default_model or os.getenv("OLLAMA_MODEL", "qwen2.5:7b")

    def is_available(self) -> bool:
        """Verifica se o servidor Ollama local está ativo e respondendo na porta configurada."""
        try:
            with httpx.Client(timeout=2.0) as client:
                res = client.get(f"{self.base_url}/api/version")
                return res.status_code == 200
        except Exception:
            return False

    def _extract_text_content(self, contents: List[Any], user_prompt_header: str = "") -> str:
        text_parts = []
        if user_prompt_header:
            text_parts.append(user_prompt_header)
        for c in contents:
            if isinstance(c, str):
                text_parts.append(c)
            elif hasattr(c, "text") and getattr(c, "text", None):
                text_parts.append(c.text)
            elif isinstance(c, dict) and "text" in c:
                text_parts.append(c["text"])
        return "\n\n".join(text_parts)

    def generate_structured(
        self,
        contents: List[Any],
        system_instruction: str,
        user_prompt_header: str = "",
        model_name: Optional[str] = None,
    ) -> Tuple[AIGeneratedPayload, str]:
        """Gera ata formal estruturada via Ollama local com formato JSON."""
        model = model_name or self.default_model
        user_text = self._extract_text_content(contents, user_prompt_header)
        full_system = system_instruction + self.get_structured_prompt_instructions()

        # Usa o endpoint OpenAI-compatible do Ollama (/v1/chat/completions) com format json
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": full_system},
                {"role": "user", "content": user_text},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }

        try:
            with httpx.Client(timeout=300.0) as client:
                resp = client.post(f"{self.base_url}/v1/chat/completions", json=payload)
                resp.raise_for_status()
                data = resp.json()
                raw_text = data["choices"][0]["message"]["content"]
                parsed = self.parse_ai_payload(raw_text)
                return parsed, f"ollama:{model}"
        except Exception as e:
            raise RuntimeError(f"OllamaProvider falhou ao comunicar com {self.base_url}: {e}")

    def refine_snippet(
        self,
        snippet: str,
        instruction: str,
        model_name: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Refina trecho via Ollama local."""
        model = model_name or self.default_model
        system_instruction = (
            "Você é um redator legislativo e cartorial sênior. Reescreva APENAS o trecho fornecido pelo usuário "
            "seguindo a instrução solicitada, mantendo tom estritamente impessoal, em terceira pessoa e formal. "
            "Retorne exclusivamente o trecho reescrito, sem aspas e sem comentários adicionais."
        )
        user_prompt = f"INSTRUÇÃO DE REESCRITA: {instruction}\n\nTRECHO ORIGINAL:\n{snippet}"

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.25,
        }

        with httpx.Client(timeout=60.0) as client:
            resp = client.post(f"{self.base_url}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
            refined = data["choices"][0]["message"]["content"].strip()
            return refined, f"ollama:{model}"
