"""
Sistema de Cache Local em SQLite para o tVerbum.
Evita re-extrair legendas e metadados do YouTube quando o mesmo vídeo é processado novamente
com diferentes parâmetros de formatação.
"""

import os
import json
import time
import sqlite3
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any

CACHE_DB_PATH = Path(tempfile.gettempdir()) / "tverbum_video_cache.db"


class VideoCache:
    """Cache persistente leve baseado em SQLite nativo."""

    @classmethod
    def _get_conn(cls) -> sqlite3.Connection:
        conn = sqlite3.connect(str(CACHE_DB_PATH))
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
    def clear(cls) -> None:
        """Limpa todo o cache de vídeos."""
        try:
            with cls._get_conn() as conn:
                conn.execute("DELETE FROM video_cache")
                conn.commit()
        except Exception:
            pass
