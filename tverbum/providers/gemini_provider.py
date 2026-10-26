"""
Adaptador de Provedor para Google Gemini (Google GenAI SDK) no tVerbum.
Utiliza Structured Outputs nativos e cascata de modelos de fallback da camada gratuita.
"""

import os
from typing import Any, List, Optional, Tuple
from google import genai
from google.genai import types

from ..models import AIGeneratedPayload
from .base import BaseLLMProvider


class GeminiProvider(BaseLLMProvider):
    """Adaptador para Google Gemini utilizando o SDK google-genai."""

    name: str = "gemini"

    DEFAULT_MODELS = [
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
    ]

    def __init__(self, api_key: Optional[str] = None, default_model: Optional[str] = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.default_model = default_model or os.getenv("DEFAULT_MODEL", "gemini-3.8-flash")
        self._client: Optional[genai.Client] = None
        if self.api_key:
            self._client = genai.Client(api_key=self.api_key)

    def is_available(self) -> bool:
        """Retorna True se a chave do Gemini estiver configurada."""
        return bool(self.api_key and self.api_key.strip())

    @property
    def client(self) -> genai.Client:
        if not self._client:
            self.api_key = os.getenv("GEMINI_API_KEY")
            if not self.api_key:
                raise ValueError("GEMINI_API_KEY não configurada no ambiente.")
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def generate_structured(
        self,
        contents: List[Any],
        system_instruction: str,
        user_prompt_header: str = "",
        model_name: Optional[str] = None,
    ) -> Tuple[AIGeneratedPayload, str]:
        """Gera conteúdo estruturado com Structured Outputs e fallback automático entre modelos Gemini."""
        preferred_model = model_name or self.default_model
        models_to_try = [preferred_model] + [m for m in self.DEFAULT_MODELS if m != preferred_model]
        last_error: Optional[Exception] = None

        for model in models_to_try:
            try:
                response = self.client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        temperature=0.2,
                        response_mime_type="application/json",
                        response_schema=AIGeneratedPayload,
                    ),
                )
                raw_text = (response.text or "").strip()
                payload = self.parse_ai_payload(raw_text)
                return payload, f"gemini:{model}"
            except Exception as e:
                last_error = e
                err_str = str(e)
                # Se for erro temporário de cota/disponibilidade, tenta o próximo modelo Gemini
                if any(code in err_str for code in ("503", "429", "UNAVAILABLE", "404", "NOT_FOUND", "RESOURCE_EXHAUSTED")):
                    continue
                raise e

        raise RuntimeError(f"GeminiProvider falhou após tentar modelos {models_to_try}: {last_error}")

    def refine_snippet(
        self,
        snippet: str,
        instruction: str,
        model_name: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Refina um trecho selecionado usando Gemini."""
        system_instruction = (
            "Você é um redator legislativo e cartorial sênior. Reescreva APENAS o trecho fornecido pelo usuário "
            "seguindo a instrução solicitada, mantendo tom estritamente impessoal, em terceira pessoa e formal. "
            "Retorne exclusivamente o trecho reescrito, sem aspas e sem comentários adicionais."
        )
        prompt = f"INSTRUÇÃO DE REESCRITA: {instruction}\n\nTRECHO ORIGINAL:\n{snippet}"

        preferred_model = model_name or self.default_model
        models_to_try = [preferred_model] + [m for m in self.DEFAULT_MODELS if m != preferred_model]
        last_error: Optional[Exception] = None

        for model in models_to_try:
            try:
                res = self.client.models.generate_content(
                    model=model,
                    contents=[prompt],
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        temperature=0.25,
                    ),
                )
                refined = (res.text or snippet).strip()
                return refined, f"gemini:{model}"
            except Exception as e:
                last_error = e
                if any(code in str(e) for code in ("503", "429", "UNAVAILABLE", "404", "RESOURCE_EXHAUSTED")):
                    continue
                raise e

        raise RuntimeError(f"GeminiProvider: falha ao refinar trecho: {last_error}")
