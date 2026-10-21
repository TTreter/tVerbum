"""
Módulo de ingestão de conteúdo multimídia e metadados do YouTube (tVerbum).
Inclui cache local SQLite para evitar extrações repetidas do mesmo vídeo.
"""

import re
import os
import tempfile
from typing import Optional, Tuple, Dict, Any
from youtube_transcript_api import YouTubeTranscriptApi
import yt_dlp

from .cache import VideoCache


class ContentIngestor:
    """Extrai metadados, transcrições e áudios de links do YouTube com suporte a cache local."""

    @staticmethod
    def extract_video_id(url: str) -> Optional[str]:
        """Extrai o ID de 11 caracteres do vídeo a partir de múltiplos formatos de URL do YouTube."""
        url = url.strip()

        patterns = [
            r"(?:v=|\/)([0-9A-Za-z_-]{11}).*",
            r"youtu\.be\/([0-9A-Za-z_-]{11})",
            r"youtube\.com\/live\/([0-9A-Za-z_-]{11})",
            r"youtube\.com\/shorts\/([0-9A-Za-z_-]{11})",
            r"youtube\.com\/embed\/([0-9A-Za-z_-]{11})",
        ]

        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)

        if re.match(r"^[0-9A-Za-z_-]{11}$", url):
            return url

        return None

    @classmethod
    def get_canonical_youtube_url(cls, url_or_id: str) -> str:
        """Retorna a URL canônica do YouTube (necessária para Part.from_uri no Gemini)."""
        vid = cls.extract_video_id(url_or_id)
        if vid:
            return f"https://www.youtube.com/watch?v={vid}"
        return url_or_id.strip()

    @classmethod
    def extract_video_metadata(cls, video_url: str) -> Dict[str, Any]:
        """
        Extrai título, data de publicação, canal, duração e descrição do vídeo no YouTube
        sem baixar a mídia. Isso ancora a data real da sessão e a pauta na descrição.
        """
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "skip_download": True,
            "extract_flat": False,
        }
        meta: Dict[str, Any] = {}
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
                if info:
                    meta["video_title"] = info.get("title", "")
                    meta["channel"] = info.get("uploader") or info.get("channel", "")
                    meta["duration_seconds"] = info.get("duration", 0)
                    upload_date = info.get("upload_date", "")  # YYYYMMDD
                    if upload_date and len(upload_date) == 8:
                        meta["upload_date"] = f"{upload_date[6:8]}/{upload_date[4:6]}/{upload_date[0:4]}"
                        meta["upload_date_iso"] = f"{upload_date[0:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
                    description = info.get("description", "")
                    if description:
                        meta["video_description"] = description[:3000]
        except Exception:
            pass
        return meta

    @classmethod
    def get_transcript_text(cls, video_id: str) -> Optional[str]:
        """
        Tenta obter as legendas oficiais ou automáticas em português via youtube-transcript-api.
        Retorna o texto com marcações temporais aproximadas.
        """
        try:
            api = YouTubeTranscriptApi()
            transcript_list = api.list(video_id)

            transcript = None
            try:
                transcript = transcript_list.find_transcript(["pt", "pt-BR"])
            except Exception:
                try:
                    transcript = transcript_list.find_generated_transcript(["pt", "pt-BR"])
                except Exception:
                    for t in transcript_list:
                        if t.is_translatable:
                            transcript = t.translate("pt")
                            break

            if transcript:
                raw_data = transcript.fetch().to_raw_data()
                lines = []
                for item in raw_data:
                    seconds = int(item.get("start", 0))
                    hours = seconds // 3600
                    minutes = (seconds % 3600) // 60
                    secs = seconds % 60
                    time_str = f"[{hours:02d}:{minutes:02d}:{secs:02d}]"
                    text = item.get("text", "").strip()
                    if text:
                        lines.append(f"{time_str} {text}")

                return "\n".join(lines)
        except Exception:
            return None

    @classmethod
    def download_audio_stream(cls, video_url: str, output_dir: Optional[str] = None) -> Optional[str]:
        """
        Baixa o stream de áudio do vídeo sem necessidade de recodificação (ffmpeg).
        Usa o formato nativo m4a/opus de menor tamanho para processamento rápido pela IA.
        """
        if not output_dir:
            output_dir = tempfile.gettempdir()

        ydl_opts = {
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "outtmpl": os.path.join(output_dir, "tverbum_%(id)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=True)
                filename = ydl.prepare_filename(info)
                if os.path.exists(filename):
                    return filename
        except Exception:
            return None

    @classmethod
    def ingest(
        cls,
        video_url: str,
        mode_preference: str = "auto",
        bypass_cache: bool = False,
    ) -> Tuple[str, str, Dict[str, Any]]:
        """
        Ingere o vídeo selecionando a estratégia configurada e utilizando cache SQLite local.
        """
        video_id = cls.extract_video_id(video_url)
        if not video_id:
            raise ValueError(f"Não foi possível identificar o ID do vídeo a partir da URL: {video_url}")

        canonical_url = cls.get_canonical_youtube_url(video_id)

        # 0. Verifica Cache Local SQLite
        cached = None if bypass_cache else VideoCache.get(video_id)
        if cached:
            metadata: Dict[str, Any] = dict(cached.get("metadata", {}))
            metadata["video_id"] = video_id
            metadata["url"] = canonical_url
            metadata["cache_hit"] = True
            cached_transcript = cached.get("transcript")

            if mode_preference in ("auto", "captions") and cached_transcript and len(cached_transcript) > 100:
                metadata["method"] = "youtube_captions_cached"
                metadata["length_chars"] = len(cached_transcript)
                return ("captions", cached_transcript, metadata)

            if mode_preference == "youtube_direct":
                if cached_transcript:
                    metadata["auxiliary_captions"] = cached_transcript
                metadata["method"] = "youtube_direct_multimodal"
                return ("youtube_direct", canonical_url, metadata)
        else:
            metadata = {
                "video_id": video_id,
                "url": canonical_url,
                "cache_hit": False,
            }
            yt_meta = cls.extract_video_metadata(canonical_url)
            metadata.update(yt_meta)

        # Busca transcrição de legendas se ainda não estiver em cache
        transcript = cached.get("transcript") if cached else cls.get_transcript_text(video_id)
        if not cached:
            VideoCache.set(video_id, transcript, metadata)

        # Modo 1: YouTube Direct explícito
        if mode_preference == "youtube_direct":
            if transcript:
                metadata["auxiliary_captions"] = transcript
            metadata["method"] = "youtube_direct_multimodal"
            return ("youtube_direct", canonical_url, metadata)

        # Modo 2: Apenas Legendas explícito
        if mode_preference == "captions":
            if transcript and len(transcript.strip()) > 50:
                metadata["method"] = "youtube_captions"
                metadata["length_chars"] = len(transcript)
                return ("captions", transcript, metadata)
            raise RuntimeError("Este vídeo não possui legendas disponíveis no YouTube. Use o modo 'auto' ou 'youtube_direct'.")

        # Modo 3: Download de Áudio explícito
        if mode_preference == "audio":
            audio_path = cls.download_audio_stream(canonical_url)
            if audio_path and os.path.exists(audio_path):
                metadata["method"] = "audio_download"
                metadata["audio_file"] = audio_path
                return ("audio", audio_path, metadata)
            raise RuntimeError("Não foi possível baixar o stream de áudio do vídeo informado.")

        # Modo 4: Automático Inteligente ('auto')
        if transcript and len(transcript.strip()) > 100:
            metadata["method"] = "youtube_captions_plus_meta"
            metadata["length_chars"] = len(transcript)
            return ("captions", transcript, metadata)

        metadata["method"] = "youtube_direct_multimodal"
        return ("youtube_direct", canonical_url, metadata)
