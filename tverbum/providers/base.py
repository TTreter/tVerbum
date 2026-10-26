"""
Interface base para Provedores de Modelos de Linguagem (LLM) no tVerbum.
Permite alternância transparente entre Gemini, Groq, OpenRouter e Ollama Local.
"""

from abc import ABC, abstractmethod
import json
import re
from typing import Any, Dict, List, Optional, Tuple, Type
from ..models import AIGeneratedPayload, ExtractedSessionData


class BaseLLMProvider(ABC):
    """Classe base abstrata para todos os adaptadores de provedores de IA."""

    name: str = "base"

    @abstractmethod
    def is_available(self) -> bool:
        """Verifica se o provedor possui credenciais ou endpoint válidos para ser acionado."""
        pass

    @abstractmethod
    def generate_structured(
        self,
        contents: List[Any],
        system_instruction: str,
        user_prompt_header: str = "",
        model_name: Optional[str] = None,
    ) -> Tuple[AIGeneratedPayload, str]:
        """
        Executa a inferência para gerar o payload estruturado (corpo da ata, dados extraídos, avisos e timeline).
        Retorna uma tupla contendo o objeto AIGeneratedPayload e a identificação do modelo utilizado.
        """
        pass

    @abstractmethod
    def refine_snippet(
        self,
        snippet: str,
        instruction: str,
        model_name: Optional[str] = None,
    ) -> Tuple[str, str]:
        """
        Reescreve ou refina um trecho específico do texto.
        Retorna uma tupla com o texto refinado e a identificação do modelo utilizado.
        """
        pass

    @classmethod
    def parse_ai_payload(cls, raw_text: str) -> AIGeneratedPayload:
        """
        Converte texto bruto contendo JSON em um objeto AIGeneratedPayload com máxima tolerância a falhas.
        Trata marcações markdown (```json ... ```), texto explicativo antes ou depois, e recupera o 'body'.
        """
        cleaned = (raw_text or "").strip()
        if not cleaned:
            return AIGeneratedPayload(body="")

        # Remove blocos de código markdown se existirem
        if "```" in cleaned:
            # Procura especificamente pelo bloco ```json ... ``` ou ``` ... ```
            match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
            if match:
                cleaned = match.group(1).strip()

        # Tenta parse direto de JSON
        try:
            data = json.loads(cleaned)
            if isinstance(data, dict):
                return AIGeneratedPayload(**data)
        except Exception:
            pass

        # Tenta encontrar o maior bloco {...} no texto
        first_brace = cleaned.find("{")
        last_brace = cleaned.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            candidate = cleaned[first_brace : last_brace + 1]
            try:
                data = json.loads(candidate)
                if isinstance(data, dict):
                    return AIGeneratedPayload(**data)
            except Exception:
                pass

        # Fallback de emergência: se o JSON estiver corrompido, utiliza o texto cru como o 'body' da ata
        return AIGeneratedPayload(body=raw_text.strip())

    @classmethod
    def get_structured_prompt_instructions(cls) -> str:
        """
        Instruções de formatação JSON para provedores que não possuem enforcement nativo de schema.
        Garante que modelos abertos (Llama, Qwen, DeepSeek) devolvam exatamente a estrutura esperada.
        """
        return (
            "\n\nATENÇÃO: Sua resposta DEVE SER EXCLUSIVAMENTE UM OBJETO JSON VÁLIDO, sem texto antes ou depois, "
            "com a seguinte estrutura exata:\n"
            "{\n"
            '  "body": "Texto formal contínuo da ata...",\n'
            '  "extracted_data": {\n'
            '    "session_date_iso": "YYYY-MM-DD ou null",\n'
            '    "start_time": "HH:MM ou null",\n'
            '    "end_time": "HH:MM ou null",\n'
            '    "next_session_info": "data e hora da próxima sessão ou null",\n'
            '    "president": "Nome do Presidente ou null",\n'
            '    "secretary": "Nome do Secretário ou null",\n'
            '    "present_members": ["Nome Vereador (PARTIDO)"],\n'
            '    "absent_members": [],\n'
            '    "voted_matters": [{"matter": "PL nº...", "author": "...", "summary": "...", "result": "..."}]\n'
            "  },\n"
            '  "review_warnings": [{"term": "termo no body", "timestamp": "MM:SS", "reason": "motivo"}],\n'
            '  "timeline_map": [{"phase": "Fase", "timestamp": "MM:SS", "summary": "Resumo", "speaker": "...", "category": "..."}]\n'
            "}\n"
        )
