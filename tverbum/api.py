"""
API FastAPI do tVerbum (v1.2) e servidor web integrado para testes e consumo por aplicações web (JS/Python).
Inclui endpoints de validação pós-edição, reescrita de trecho com IA e entrega do Web Component <tverbum-editor>.
"""

import uuid
import tempfile
from pathlib import Path
from typing import Dict, List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, Response

from .models import (
    MinuteConfig,
    MinuteOutput,
    ExportRequest,
    OrganizationPreset,
    ValidateTextRequest,
    ValidateTextResponse,
    RefineSnippetRequest,
    RefineSnippetResponse,
)
from .ai_engine import MinuteGenerator
from .normalizer import TextNormalizer
from .exporter import MinuteExporter
from .presets import PresetManager
from .cache import VideoCache

app = FastAPI(
    title="tVerbum API",
    description="Motor facilitador para geração, revisão assistida e validação de atas plenárias via IA.",
    version="1.2.0",
)

# Permitir CORS para facilitar integração com Next.js, React, Vue, PHP, Django ou apps locais
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Armazenamento indexado por ID
_MINUTES_STORE: Dict[str, MinuteOutput] = {}
_LATEST_MINUTE_ID: Optional[str] = None
_TEMP_DIR = Path(tempfile.gettempdir()) / "tverbum_downloads"
_TEMP_DIR.mkdir(parents=True, exist_ok=True)


def _export_minute_to_response(minute: MinuteOutput, fmt: str) -> FileResponse:
    """Auxiliar para exportar qualquer objeto MinuteOutput para FileResponse (.pdf, .docx, .txt, .md)."""
    safe_title = "".join(c for c in minute.title if c.isalnum() or c in ("-", "_", " ")).strip()
    safe_title = safe_title.replace(" ", "_") or "Ata_Sessao"
    unique_suffix = uuid.uuid4().hex[:6]

    fmt = fmt.lower().strip()
    if fmt == "pdf":
        file_path = _TEMP_DIR / f"{safe_title}_{unique_suffix}.pdf"
        MinuteExporter.to_pdf(minute, str(file_path))
        return FileResponse(
            path=str(file_path),
            filename=f"{safe_title}.pdf",
            media_type="application/pdf",
        )
    elif fmt == "docx":
        file_path = _TEMP_DIR / f"{safe_title}_{unique_suffix}.docx"
        MinuteExporter.to_docx(minute, str(file_path))
        return FileResponse(
            path=str(file_path),
            filename=f"{safe_title}.docx",
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    elif fmt == "txt":
        file_path = _TEMP_DIR / f"{safe_title}_{unique_suffix}.txt"
        MinuteExporter.to_txt(minute, str(file_path))
        return FileResponse(
            path=str(file_path),
            filename=f"{safe_title}.txt",
            media_type="text/plain; charset=utf-8",
        )
    elif fmt == "md":
        file_path = _TEMP_DIR / f"{safe_title}_{unique_suffix}.md"
        MinuteExporter.to_markdown(minute, str(file_path))
        return FileResponse(
            path=str(file_path),
            filename=f"{safe_title}.md",
            media_type="text/markdown; charset=utf-8",
        )
    else:
        raise HTTPException(status_code=400, detail="Formato não suportado. Use 'pdf', 'docx', 'txt' ou 'md'.")


@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    """Entrega a interface web moderna e ultraleve de testes."""
    index_path = Path(__file__).parent / "web" / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Arquivo index.html não encontrado.")
    return HTMLResponse(content=index_path.read_text(encoding="utf-8"))


@app.get("/tverbum-editor.js")
async def serve_editor_component():
    """
    Entrega o Web Component standalone `<tverbum-editor>` para ser importado em qualquer
    aplicação web externa via `<script src="http://localhost:8000/tverbum-editor.js"></script>`.
    """
    js_path = Path(__file__).parent / "web" / "tverbum-editor.js"
    if not js_path.exists():
        raise HTTPException(status_code=404, detail="Arquivo tverbum-editor.js não encontrado.")
    return Response(
        content=js_path.read_text(encoding="utf-8"),
        media_type="application/javascript; charset=utf-8",
    )


@app.get("/api/presets", response_model=List[OrganizationPreset])
async def list_presets():
    """Lista todos os perfis institucionais (Câmaras/Conselhos) cadastrados."""
    return PresetManager.list_presets()


@app.post("/api/presets", response_model=OrganizationPreset)
async def save_preset(preset: OrganizationPreset):
    """Salva ou atualiza um perfil institucional."""
    return PresetManager.save_preset(preset)


@app.get("/api/transcription/{video_id}")
async def get_video_transcription(video_id: str):
    """
    Recupera a transcrição e metadados diretamente do VideoCache.
    Caso não exista no cache, tenta extrair via ContentIngestor.get_transcript_text(video_id)
    e salva no cache antes de devolver.
    """
    from .ingest import ContentIngestor

    clean_id = ContentIngestor.extract_video_id(video_id) or video_id.strip()
    cached = VideoCache.get(clean_id)
    if cached and cached.get("transcript"):
        return {
            "video_id": clean_id,
            "found": True,
            "transcript": cached["transcript"],
            "metadata": cached.get("metadata", {}),
        }

    transcript = ContentIngestor.get_transcript_text(clean_id)
    canonical_url = ContentIngestor.get_canonical_youtube_url(clean_id)
    meta = ContentIngestor.extract_video_metadata(canonical_url)
    meta["video_id"] = clean_id
    meta["url"] = canonical_url

    if transcript:
        VideoCache.set(clean_id, transcript, meta)
        return {
            "video_id": clean_id,
            "found": True,
            "transcript": transcript,
            "metadata": meta,
        }

    return {
        "video_id": clean_id,
        "found": False,
        "transcript": None,
        "metadata": meta,
    }


@app.get("/api/session/{minute_id}", response_model=MinuteOutput)
async def get_session_by_id(minute_id: str):
    """
    Devolve o objeto MinuteOutput armazenado em memória no dicionário _MINUTES_STORE[minute_id],
    permitindo que sistemas externos (como tNuntia) consultem atas geradas anteriormente sem reprocessar.
    Aceita 'latest' para obter a última sessão gerada.
    """
    target_id = _LATEST_MINUTE_ID if minute_id == "latest" else minute_id
    minute = _MINUTES_STORE.get(target_id) if target_id else None
    if not minute:
        raise HTTPException(status_code=404, detail="Sessão/Ata não encontrada para o minute_id informado.")
    return minute


@app.post("/api/generate", response_model=MinuteOutput)
async def generate_minute_endpoint(config: MinuteConfig):
    """
    Endpoint principal: gera o texto da ata e devolve o JSON enriquecido com:
    - `body`: texto formal da ata
    - `extracted_data`: data ISO, horários, presentes, matérias votadas (pronto para banco de dados)
    - `review_warnings`: termos sugeridos para conferência rápida
    - `timeline_map`: timestamps clicáveis da sessão no YouTube
    """
    global _LATEST_MINUTE_ID
    try:
        generator = MinuteGenerator()
        output = generator.generate(config)
        _MINUTES_STORE[output.id] = output
        _LATEST_MINUTE_ID = output.id
        return output
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/validate-text", response_model=ValidateTextResponse)
async def validate_and_normalize_text(request: ValidateTextRequest):
    """
    Validador e normalizador cartorial pós-edição:
    Aplica deterministamente a expansão de abreviações, conversão de horas/datas para extenso
    e formatação de parágrafo único sobre o texto que o usuário editou manualmente no sistema.
    """
    normalized, corrections = TextNormalizer.validate_and_normalize(
        text=request.text,
        single_paragraph=request.single_paragraph,
        paragraph_count=request.paragraph_count,
        numbers_in_words=request.numbers_in_words,
        allow_abbreviations=request.allow_abbreviations,
    )
    return ValidateTextResponse(
        original_text=request.text,
        normalized_text=normalized,
        is_compliant=(len(corrections) == 0),
        corrections_applied=corrections,
        word_count=len(normalized.split()),
        character_count=len(normalized),
    )


@app.post("/api/refine-snippet", response_model=RefineSnippetResponse)
async def refine_snippet_endpoint(request: RefineSnippetRequest):
    """
    Reescreve rapidamente apenas uma frase ou trecho selecionado pelo usuário durante a revisão,
    sem precisar regerar a ata inteira.
    """
    try:
        generator = MinuteGenerator()
        return generator.refine_snippet(request)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/export")
async def export_custom_minute(request: ExportRequest):
    """
    Exporta o documento da ata (incluindo eventuais edições feitas pelo usuário na tela)
    para PDF (.pdf), Word (.docx), Markdown (.md) ou Texto (.txt).
    """
    return _export_minute_to_response(request.minute, request.format)


@app.delete("/api/cache")
async def clear_video_cache():
    """Limpa o cache local SQLite de transcrições e metadados de vídeos."""
    VideoCache.clear()
    return {"status": "ok", "message": "Cache de vídeos limpo com sucesso."}


@app.get("/api/download/{minute_id}/{format}")
async def download_minute_by_id(minute_id: str, format: str):
    """Baixa uma ata específica pelo seu ID único."""
    minute = _MINUTES_STORE.get(minute_id)
    if not minute:
        raise HTTPException(status_code=404, detail="Ata não encontrada pelo ID informado.")
    return _export_minute_to_response(minute, format)


@app.get("/api/download/{format}")
async def download_latest_minute(format: str):
    """Mantido por retrocompatibilidade: baixa a última ata gerada."""
    if not _LATEST_MINUTE_ID or _LATEST_MINUTE_ID not in _MINUTES_STORE:
        raise HTTPException(status_code=400, detail="Nenhuma ata foi gerada ainda nesta sessão.")
    return _export_minute_to_response(_MINUTES_STORE[_LATEST_MINUTE_ID], format)
