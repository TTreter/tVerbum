"""
Módulo de Transcrição de Áudio Gratuito (tVerbum v2.0).
Integra com a API gratuita da Groq (whisper-large-v3-turbo) para transcrever áudios
sem legendas no YouTube em poucos segundos com marcações temporais [HH:MM:SS].
"""

import os
import logging
from typing import Optional, Dict, Any, List
import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("tverbum.transcriber")


class GroqWhisperTranscriber:
    """Cliente para transcrição de áudio via Whisper na API gratuita da Groq."""

    GROQ_API_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
    DEFAULT_MODEL = "whisper-large-v3-turbo"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("GROQ_API_KEY")

    def is_available(self) -> bool:
        """Verifica se a chave da Groq está configurada."""
        return bool(self.api_key and self.api_key.strip())

    @staticmethod
    def _format_timestamp(seconds: float) -> str:
        """Converte segundos em string de timestamp [HH:MM:SS]."""
        total_secs = int(seconds)
        hours = total_secs // 3600
        minutes = (total_secs % 3600) // 60
        secs = total_secs % 60
        return f"[{hours:02d}:{minutes:02d}:{secs:02d}]"

    def transcribe(self, audio_path: str, model: Optional[str] = None) -> Optional[str]:
        """
        Envia o arquivo de áudio para a Groq Whisper API e devolve o texto estruturado com timestamps.
        Suporta formatos: .m4a, .mp3, .webm, .wav, .ogg (até 25MB).
        """
        if not self.is_available():
            logger.info("GROQ_API_KEY não configurada para Whisper. Pulando transcrição via Groq.")
            return None

        if not os.path.exists(audio_path):
            logger.error(f"Arquivo de áudio não encontrado para transcrição: {audio_path}")
            return None

        file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
        if file_size_mb > 25.0:
            logger.warning(
                f"Arquivo de áudio possui {file_size_mb:.1f}MB, excedendo o limite de 25MB da API Groq."
            )
            return None

        model_to_use = model or self.DEFAULT_MODEL
        headers = {"Authorization": f"Bearer {self.api_key}"}

        filename = os.path.basename(audio_path)
        ext = os.path.splitext(filename)[1].lower().lstrip(".")
        mime_type = f"audio/{ext}" if ext in ("mp3", "webm", "wav", "ogg") else "audio/m4a"

        try:
            with open(audio_path, "rb") as f:
                files = {"file": (filename, f, mime_type)}
                data = {
                    "model": model_to_use,
                    "language": "pt",
                    "response_format": "verbose_json",
                    "temperature": "0.0",
                }

                logger.info(f"Enviando áudio ({file_size_mb:.1f}MB) para Groq Whisper ({model_to_use})...")
                with httpx.Client(timeout=300.0) as client:
                    resp = client.post(self.GROQ_API_URL, headers=headers, files=files, data=data)
                    resp.raise_for_status()
                    result = resp.json()

            # Processa os segmentos cronológicos com timestamps
            segments = result.get("segments", [])
            if segments:
                lines = []
                for seg in segments:
                    start_sec = seg.get("start", 0.0)
                    text = seg.get("text", "").strip()
                    if text:
                        time_str = self._format_timestamp(start_sec)
                        lines.append(f"{time_str} {text}")
                return "\n".join(lines)

            # Fallback caso não venha no formato de segmentos
            return result.get("text", "").strip() or None

        except Exception as e:
            logger.error(f"Falha ao transcrever áudio com Groq Whisper: {e}")
            return None
