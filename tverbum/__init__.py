"""
=============================================================================
tVerbum - Motor Facilitador para Redação Inteligente de Atas Plenárias
=============================================================================
Pacote principal que expõe as classes, modelos de dados e funções utilitárias
para ingestão de vídeos do YouTube, processamento com IA (Google Gemini),
validação cartorial (parágrafo único, números por extenso, vedação a abreviações)
e exportação documental (PDF, DOCX, Markdown, TXT e JSON estruturado).
"""

from .models import (
    MinuteConfig,
    MinuteOutput,
    OrganizationPreset,
    ExportRequest,
    ExtractedSessionData,
    VotedMatter,
    ReviewWarning,
    TimelineEntry,
    ValidateTextRequest,
    ValidateTextResponse,
    RefineSnippetRequest,
    RefineSnippetResponse,
)
from .normalizer import TextNormalizer
from .ingest import ContentIngestor
from .ai_engine import MinuteGenerator
from .exporter import MinuteExporter
from .presets import PresetManager
from .cache import VideoCache

__version__ = "1.2.0"


def generate_minute(config: MinuteConfig) -> MinuteOutput:
    """
    Função principal de alto nível (atalho) para gerar uma ata completa
    a partir de um objeto `MinuteConfig`.

    Parâmetros:
        config (MinuteConfig): Objeto contendo a URL do vídeo, número da ata e
                               regras formais de personalização.

    Retorna:
        MinuteOutput: Objeto estruturado contendo o texto da ata (`body`),
                      metadados para banco de dados (`extracted_data`),
                      alertas de revisão (`review_warnings`) e timestamps (`timeline_map`).
    """
    generator = MinuteGenerator()
    return generator.generate(config)


__all__ = [
    "MinuteConfig",
    "MinuteOutput",
    "OrganizationPreset",
    "ExportRequest",
    "ExtractedSessionData",
    "VotedMatter",
    "ReviewWarning",
    "TimelineEntry",
    "ValidateTextRequest",
    "ValidateTextResponse",
    "RefineSnippetRequest",
    "RefineSnippetResponse",
    "TextNormalizer",
    "ContentIngestor",
    "MinuteGenerator",
    "MinuteExporter",
    "PresetManager",
    "VideoCache",
    "generate_minute",
]
