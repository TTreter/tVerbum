"""
Adaptador de Provedor para OpenRouter (com foco nos modelos :free) no tVerbum.
Permite acesso gratuito e redundante a múltiplos modelos de ponta (Gemini 2.0, LLaMA 3.3 70B, Qwen 2.5).
"""

import os
from typing import Any, List, Optional, Tuple
import httpx

from ..models import AIGeneratedPayload
from .base import BaseLLMProvider


class OpenRouterProvider(BaseLLMProvider):
    """Adaptador para a API OpenRouter com suporte a modelos :free."""

    name: str = "openrouter"

    FREE_MODELS = [
        "google/gemini-2.0-flash-exp:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "deepseek/deepseek-chat:free",
        "qwen/qwen-2.5-72b-instruct:free",
    ]

    def __init__(self, api_key: Optional[str] = None, default_model: Optional[str] = None):
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        self.default_model = default_model or os.getenv(
            "OPENROUTER_DEFAULT_MODEL", "google/gemini-2.0-flash-exp:free"
        )
        self.base_url = "https://openrouter.ai/api/v1"

    def is_available(self) -> bool:
        """Verifica se a chave OPENROUTER_API_KEY está configurada."""
        return bool(self.api_key and self.api_key.strip())

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
        """Gera ata formal estruturada via OpenRouter."""
        if not self.is_available():
            raise ValueError("OPENROUTER_API_KEY não configurada.")

        preferred = model_name or self.default_model
        models_to_try = [preferred] + [m for m in self.FREE_MODELS if m != preferred]

        user_text = self._extract_text_content(contents, user_prompt_header)
        full_system = system_instruction + self.get_structured_prompt_instructions()

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/TTreter/tVerbum",
            "X-Title": "tVerbum",
            "Content-Type": "application/json",
        }

        last_error: Optional[Exception] = None
        for model in models_to_try:
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
                with httpx.Client(timeout=120.0) as client:
                    resp = client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        raw_text = data["choices"][0]["message"]["content"]
                        parsed = self.parse_ai_payload(raw_text)
                        return parsed, f"openrouter:{model}"
                    elif resp.status_code in (429, 502, 503, 504):
                        last_error = RuntimeError(f"OpenRouter HTTP {resp.status_code}: {resp.text}")
                        continue
                    else:
                        resp.raise_for_status()
            except Exception as e:
                last_error = e
                err_str = str(e)
                if any(code in err_str for code in ("429", "502", "503", "504", "timeout", "timed out")):
                    continue
                raise e

        raise RuntimeError(f"OpenRouterProvider falhou após tentar modelos {models_to_try}: {last_error}")

    def refine_snippet(
        self,
        snippet: str,
        instruction: str,
        model_name: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Refina trecho via OpenRouter."""
        if not self.is_available():
            raise ValueError("OPENROUTER_API_KEY não configurada.")

        model = model_name or self.default_model
        system_instruction = (
            "Você é um redator legislativo e cartorial sênior. Reescreva APENAS o trecho fornecido pelo usuário "
            "seguindo a instrução solicitada, mantendo tom estritamente impessoal, em terceira pessoa e formal. "
            "Retorne exclusivamente o trecho reescrito, sem aspas e sem comentários adicionais."
        )
        user_prompt = f"INSTRUÇÃO DE REESCRITA: {instruction}\n\nTRECHO ORIGINAL:\n{snippet}"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/TTreter/tVerbum",
            "X-Title": "tVerbum",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.25,
        }

        with httpx.Client(timeout=45.0) as client:
            resp = client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            refined = data["choices"][0]["message"]["content"].strip()
            return refined, f"openrouter:{model}"
