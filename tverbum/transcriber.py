"""
Módulo de Transcrição de Áudio Gratuito (tVerbum v2.0+).
Integra com a API gratuita da Groq (whisper-large-v3-turbo) para transcrever áudios
sem legendas no YouTube em poucos segundos com marcações temporais [HH:MM:SS].
Inclui suporte automático a fatiamento (Audio Chunking) para sessões longas (>22MB).
"""

import os
import re
import math
import time
import shutil
import logging
import tempfile
import subprocess
from typing import Optional, Dict, Any, List, Tuple
import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("tverbum.transcriber")


def get_ffmpeg_binary() -> Optional[str]:
    """Localiza o binário do ffmpeg no sistema ou no pacote imageio_ffmpeg."""
    bin_path = shutil.which("ffmpeg")
    if bin_path:
        return bin_path
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass
    return None


def get_audio_duration_seconds(audio_path: str, ffmpeg_bin: Optional[str] = None) -> Optional[float]:
    """Obtém a duração total do arquivo de áudio em segundos via ffmpeg."""
    exe = ffmpeg_bin or get_ffmpeg_binary()
    if not exe or not os.path.exists(audio_path):
        return None

    try:
        proc = subprocess.run(
            [exe, "-i", audio_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15.0,
        )
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr)
        if match:
            h = int(match.group(1))
            m = int(match.group(2))
            s = float(match.group(3))
            return h * 3600.0 + m * 60.0 + s
    except Exception as e:
        logger.warning(f"Não foi possível obter duração do áudio via ffmpeg: {e}")
    return None


def slice_audio_chunk(
    input_path: str,
    start_sec: float,
    end_sec: float,
    output_path: str,
    ffmpeg_bin: Optional[str] = None,
) -> bool:
    """Fatia um trecho do áudio e comprime em aac mono 16kHz 32k para caber com folga no limite da Groq."""
    exe = ffmpeg_bin or get_ffmpeg_binary()
    if not exe or not os.path.exists(input_path):
        return False

    duration = max(0.1, end_sec - start_sec)
    cmd = [
        exe,
        "-y",
        "-ss", f"{start_sec:.2f}",
        "-t", f"{duration:.2f}",
        "-i", input_path,
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "aac",
        "-b:a", "32k",
        output_path,
    ]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120.0)
        return proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 100
    except Exception as e:
        logger.error(f"Erro ao fatiar áudio com ffmpeg ({start_sec}s a {end_sec}s): {e}")
        return False


class GroqWhisperTranscriber:
    """Cliente para transcrição de áudio via Whisper na API gratuita da Groq com suporte a Audio Chunking."""

    GROQ_API_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
    DEFAULT_MODEL = "whisper-large-v3-turbo"
    MAX_SINGLE_FILE_MB = 22.0
    CHUNK_DURATION_SECONDS = 600.0  # 10 minutos por fatia
    CHUNK_OVERLAP_SECONDS = 3.0    # 3 segundos de sobreposição para não cortar palavras

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("GROQ_API_KEY")

    def is_available(self) -> bool:
        """Verifica se a chave da Groq está configurada."""
        return bool(self.api_key and self.api_key.strip())

    @staticmethod
    def _format_timestamp(seconds: float) -> str:
        """Converte segundos em string de timestamp [HH:MM:SS]."""
        total_secs = max(0, int(seconds))
        hours = total_secs // 3600
        minutes = (total_secs % 3600) // 60
        secs = total_secs % 60
        return f"[{hours:02d}:{minutes:02d}:{secs:02d}]"

    def _build_whisper_prompt(self) -> str:
        """Monta o prompt com dicas de vocabulário e nomes da Câmara para orientar o Whisper."""
        whisper_prompt = "Sessão Plenária da Câmara Municipal de Vereadores. "
        try:
            from .name_library import NameLibraryManager
            entries = NameLibraryManager.get_active_entries()
            if entries:
                names_list = ", ".join(e.canonical_name for e in entries[:25])
                whisper_prompt += f"Nomes oficiais: {names_list}."
        except Exception:
            pass
        return whisper_prompt[:800]

    def _transcribe_single_file(
        self,
        audio_path: str,
        offset_seconds: float = 0.0,
        model: Optional[str] = None,
    ) -> Optional[List[Tuple[float, str]]]:
        """
        Envia um arquivo individual (ou fatia) para a Groq Whisper API.
        Retorna lista de pares (timestamp_absoluto_em_segundos, texto_do_segmento).
        """
        if not self.is_available() or not os.path.exists(audio_path):
            return None

        file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
        if file_size_mb > 25.0:
            logger.warning(f"Fatia de áudio com {file_size_mb:.1f}MB excede o teto de 25MB da Groq.")
            return None

        model_to_use = model or self.DEFAULT_MODEL
        headers = {"Authorization": f"Bearer {self.api_key}"}
        filename = os.path.basename(audio_path)
        ext = os.path.splitext(filename)[1].lower().lstrip(".")
        mime_type = f"audio/{ext}" if ext in ("mp3", "webm", "wav", "ogg") else "audio/m4a"

        whisper_prompt = self._build_whisper_prompt()

        try:
            with open(audio_path, "rb") as f:
                files = {"file": (filename, f, mime_type)}
                data = {
                    "model": model_to_use,
                    "language": "pt",
                    "response_format": "verbose_json",
                    "temperature": "0.0",
                    "prompt": whisper_prompt,
                }
                with httpx.Client(timeout=300.0) as client:
                    resp = client.post(self.GROQ_API_URL, headers=headers, files=files, data=data)
                    resp.raise_for_status()
                    result = resp.json()

            parsed_segments: List[Tuple[float, str]] = []
            segments = result.get("segments", [])
            if segments:
                for seg in segments:
                    seg_start = seg.get("start", 0.0)
                    seg_text = seg.get("text", "").strip()
                    if seg_text:
                        abs_time = offset_seconds + float(seg_start)
                        parsed_segments.append((abs_time, seg_text))
            elif result.get("text"):
                parsed_segments.append((offset_seconds, result["text"].strip()))

            return parsed_segments

        except Exception as e:
            logger.error(f"Erro ao transcrever trecho ({offset_seconds}s offset) via Groq Whisper: {e}")
            return None

    def transcribe(self, audio_path: str, model: Optional[str] = None) -> Optional[str]:
        """
        Transcreve o arquivo de áudio via Groq Whisper API.
        Caso o arquivo seja menor que 22MB, transcreve diretamente.
        Caso exceda 22MB, fatia automaticamente em blocos de 10 minutos, preservando timestamps contínuos.
        """
        if not self.is_available():
            logger.info("GROQ_API_KEY não configurada para Whisper. Pulando transcrição via Groq.")
            return None

        if not os.path.exists(audio_path):
            logger.error(f"Arquivo de áudio não encontrado para transcrição: {audio_path}")
            return None

        file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)

        # Caso 1: Arquivo leve (< 22MB) -> Transcrição direta sem fatiamento
        if file_size_mb <= self.MAX_SINGLE_FILE_MB:
            logger.info(f"Áudio de {file_size_mb:.1f}MB dentro do limite direto (<= {self.MAX_SINGLE_FILE_MB}MB). Transcrevendo...")
            segments = self._transcribe_single_file(audio_path, offset_seconds=0.0, model=model)
            if not segments:
                return None
            lines = [f"{self._format_timestamp(t)} {txt}" for t, txt in segments]
            return "\n".join(lines)

        # Caso 2: Arquivo pesado (> 22MB) -> Audio Chunking automático
        logger.info(
            f"Arquivo de áudio com {file_size_mb:.1f}MB excede {self.MAX_SINGLE_FILE_MB}MB. "
            "Iniciando fatiamento inteligente (Audio Chunking)..."
        )
        ffmpeg_bin = get_ffmpeg_binary()
        if not ffmpeg_bin:
            logger.error(
                "FFmpeg não encontrado para fatiar áudio > 22MB. "
                "Instale imageio-ffmpeg ('pip install imageio-ffmpeg') ou instale o ffmpeg no sistema."
            )
            return None

        duration = get_audio_duration_seconds(audio_path, ffmpeg_bin)
        if not duration or duration <= 0:
            logger.warning("Não foi possível determinar a duração do áudio. Tentando transcrever direto como fallback...")
            segments = self._transcribe_single_file(audio_path, offset_seconds=0.0, model=model)
            if segments:
                lines = [f"{self._format_timestamp(t)} {txt}" for t, txt in segments]
                return "\n".join(lines)
            return None

        chunk_step = self.CHUNK_DURATION_SECONDS
        num_chunks = math.ceil(duration / chunk_step)
        logger.info(
            f"Áudio com duração total de {duration / 60.0:.1f} minutos fatiado em {num_chunks} blocos de ~10 minutos."
        )

        temp_dir = tempfile.mkdtemp(prefix="tverbum_chunks_")
        all_segments: List[Tuple[float, str]] = []

        try:
            for idx in range(num_chunks):
                start_sec = idx * chunk_step
                end_sec = min(duration, (idx + 1) * chunk_step + self.CHUNK_OVERLAP_SECONDS)
                chunk_file = os.path.join(temp_dir, f"chunk_{idx:03d}.m4a")

                logger.info(
                    f"Fatiando bloco {idx + 1}/{num_chunks}: {self._format_timestamp(start_sec)} até {self._format_timestamp(end_sec)}..."
                )
                sliced = slice_audio_chunk(audio_path, start_sec, end_sec, chunk_file, ffmpeg_bin)
                if not sliced:
                    logger.error(f"Falha ao gerar fatia de áudio {idx + 1}/{num_chunks}. Prosseguindo com demais fatias.")
                    continue

                chunk_size_mb = os.path.getsize(chunk_file) / (1024 * 1024)
                logger.info(f"Fatia {idx + 1}/{num_chunks} gerada ({chunk_size_mb:.2f}MB). Enviando para Groq Whisper...")

                chunk_segments = self._transcribe_single_file(
                    chunk_file,
                    offset_seconds=start_sec,
                    model=model,
                )

                if chunk_segments:
                    all_segments.extend(chunk_segments)

                # Pequena pausa preventiva para respeitar a taxa de requisições por minuto da Groq
                time.sleep(0.5)

            if not all_segments:
                logger.error("Nenhuma fatia de áudio pôde ser transcrita com sucesso.")
                return None

            # Ordena por timestamp para garantir cronologia perfeita
            all_segments.sort(key=lambda s: s[0])

            # Deduplicação simples de trechos idênticos causados pela sobreposição
            deduped_lines: List[str] = []
            last_text = ""
            for t, txt in all_segments:
                if txt.lower() != last_text.lower():
                    deduped_lines.append(f"{self._format_timestamp(t)} {txt}")
                    last_text = txt

            logger.info(
                f"Transcrição por fatiamento concluída com sucesso: {len(deduped_lines)} segmentos extraídos."
            )
            return "\n".join(deduped_lines)

        finally:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
