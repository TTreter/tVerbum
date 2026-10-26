"""
Sistema de Cache Local em SQLite para o tVerbum (v2.0).
Evita re-extrair legendas e metadados do YouTube e armazena atas consolidadas por hash de configuração
para evitar chamadas duplicadas a LLMs gratuitos.
Permite compartilhamento entre tVerbum e módulos externos (como tNuntia) via TVERBUM_CACHE_PATH / TVERBUM_CACHE_DIR.
"""

import os
import json
import time
import hashlib
import sqlite3
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any


def get_cache_db_path() -> Path:
    """Determina o caminho do banco SQLite de cache a partir do ambiente ou do padrão temporário."""
    if os.getenv("TVERBUM_CACHE_PATH"):
        return Path(os.getenv("TVERBUM_CACHE_PATH"))
    if os.getenv("TVERBUM_CACHE_DIR"):
        return Path(os.getenv("TVERBUM_CACHE_DIR")) / "tverbum_video_cache.db"
    return Path(tempfile.gettempdir()) / "tverbum_video_cache.db"


CACHE_DB_PATH = get_cache_db_path()


class VideoCache:
    """Cache persistente leve baseado em SQLite nativo para vídeos, transcrições e atas consolidadas."""

    @classmethod
    def _get_conn(cls) -> sqlite3.Connection:
        db_path = get_cache_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS video_cache (
                video_id TEXT PRIMARY KEY,
                transcript TEXT,
                metadata_json TEXT,
                updated_at REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS minute_cache (
                cache_key TEXT PRIMARY KEY,
                minute_json TEXT,
                updated_at REAL
            )
            """
        )
        return conn

    @classmethod
    def get(cls, video_id: str, max_age_seconds: int = 86400 * 7) -> Optional[Dict[str, Any]]:
        """Recupera transcrição e metadados em cache para o video_id (válido por 7 dias por padrão)."""
        try:
            with cls._get_conn() as conn:
                row = conn.execute(
                    "SELECT transcript, metadata_json, updated_at FROM video_cache WHERE video_id = ?",
                    (video_id,),
                ).fetchone()
                if not row:
                    return None
                transcript, metadata_json, updated_at = row
                if time.time() - updated_at > max_age_seconds:
                    return None
                return {
                    "transcript": transcript,
                    "metadata": json.loads(metadata_json) if metadata_json else {},
                }
        except Exception:
            return None

    @classmethod
    def get_transcript(cls, video_id: str, max_age_seconds: int = 86400 * 7) -> Optional[str]:
        """Atalho para recuperar apenas o texto da transcrição em cache para o video_id."""
        cached = cls.get(video_id, max_age_seconds=max_age_seconds)
        if cached:
            return cached.get("transcript")
        return None

    @classmethod
    def set(cls, video_id: str, transcript: Optional[str], metadata: Dict[str, Any]) -> None:
        """Salva transcrição e metadados do vídeo no cache SQLite."""
        try:
            clean_meta = {k: v for k, v in metadata.items() if k != "auxiliary_captions"}
            with cls._get_conn() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO video_cache (video_id, transcript, metadata_json, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        video_id,
                        transcript,
                        json.dumps(clean_meta, ensure_ascii=False),
                        time.time(),
                    ),
                )
                conn.commit()
        except Exception:
            pass

    @classmethod
    def build_config_cache_key(cls, config: Any) -> str:
        """Gera um hash SHA-256 estável dos parâmetros da ata para reuso de cache sem gastar tokens."""
        raw_key = (
            f"{getattr(config, 'video_url', '')}|"
            f"{getattr(config, 'session_number', '')}|"
            f"{getattr(config, 'preset_id', '')}|"
            f"{getattr(config, 'detail_level', '')}|"
            f"{getattr(config, 'single_paragraph', True)}|"
            f"{getattr(config, 'paragraph_count', 1)}|"
            f"{getattr(config, 'numbers_in_words', True)}|"
            f"{getattr(config, 'allow_abbreviations', False)}|"
            f"{getattr(config, 'ai_provider', 'auto')}|"
            f"{getattr(config, 'agenda_context', '')}|"
            f"{getattr(config, 'custom_instructions', '')}"
        )
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    @classmethod
    def get_minute(cls, cache_key: str, max_age_seconds: int = 86400 * 3) -> Optional[Dict[str, Any]]:
        """Recupera uma ata previamente gerada com as mesmas configurações."""
        try:
            with cls._get_conn() as conn:
                row = conn.execute(
                    "SELECT minute_json, updated_at FROM minute_cache WHERE cache_key = ?",
                    (cache_key,),
                ).fetchone()
                if not row:
                    return None
                minute_json, updated_at = row
                if time.time() - updated_at > max_age_seconds:
                    return None
                return json.loads(minute_json) if minute_json else None
        except Exception:
            return None

    @classmethod
    def set_minute(cls, cache_key: str, minute_data: Dict[str, Any]) -> None:
        """Salva a ata gerada no cache SQLite para evitar novas chamadas à IA."""
        try:
            with cls._get_conn() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO minute_cache (cache_key, minute_json, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    (
                        cache_key,
                        json.dumps(minute_data, ensure_ascii=False),
                        time.time(),
                    ),
                )
                conn.commit()
        except Exception:
            pass

    @classmethod
    def clear(cls) -> None:
        """Limpa todo o cache de vídeos e atas."""
        try:
            with cls._get_conn() as conn:
                conn.execute("DELETE FROM video_cache")
                conn.execute("DELETE FROM minute_cache")
                conn.commit()
        except Exception:
            pass
