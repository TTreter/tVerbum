"""
API FastAPI do tVerbum (v1.2) e servidor web integrado para testes e consumo por aplicações web (JS/Python).
Inclui endpoints de validação pós-edição, reescrita de trecho com IA e entrega do Web Component <tverbum-editor>.
"""

import uuid
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Any
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, Response

from .models import (
    MinuteConfig,
    MinuteOutput,
    ExportRequest,
    OrganizationPreset,
    NameEntry,
    QuickLearnNameRequest,
    ValidateTextRequest,
    ValidateTextResponse,
    RefineSnippetRequest,
    RefineSnippetResponse,
)
from .ai_engine import MinuteGenerator
from .normalizer import TextNormalizer
from .exporter import MinuteExporter
from .presets import PresetManager
from .name_library import NameLibraryManager
from .cache import VideoCache
from .limiter import RateLimiter

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


ALLOWED_MEDIA_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac",
    ".mp4", ".mkv", ".webm", ".mov", ".avi"
}


@app.post("/api/upload-media")
async def upload_media_file(file: UploadFile = File(...)):
    """
    Recebe um arquivo de áudio ou vídeo local (.mp3, .wav, .m4a, .mp4, etc.),
    salva no servidor temporário e retorna o caminho absoluto para uso em MinuteConfig.
    """
    original_name = file.filename or "uploaded_media.mp3"
    ext = Path(original_name).suffix.lower()
    if ext not in ALLOWED_MEDIA_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Formato de arquivo '{ext}' não suportado. Use: {', '.join(sorted(ALLOWED_MEDIA_EXTENSIONS))}",
        )

    safe_name = "".join(c for c in Path(original_name).stem if c.isalnum() or c in ("-", "_")).strip() or "media"
    unique_filename = f"{uuid.uuid4().hex[:8]}_{safe_name}{ext}"
    dest_path = _TEMP_DIR / unique_filename

    total_bytes = 0
    with open(dest_path, "wb") as buffer:
        while chunk := await file.read(1024 * 1024):  # blocos de 1MB
            buffer.write(chunk)
            total_bytes += len(chunk)

    size_mb = round(total_bytes / (1024 * 1024), 2)
    is_video = ext in {".mp4", ".mkv", ".webm", ".mov", ".avi"}

    return {
        "status": "ok",
        "filename": original_name,
        "file_path": str(dest_path),
        "size_mb": size_mb,
        "media_type": "video" if is_video else "audio",
        "message": "Arquivo enviado com sucesso. Utilize este 'file_path' para gerar a ata.",
    }


@app.get("/api/presets", response_model=List[OrganizationPreset])
async def list_presets():
    """Lista todos os perfis institucionais (Câmaras/Conselhos) cadastrados."""
    return PresetManager.list_presets()


@app.post("/api/presets", response_model=OrganizationPreset)
async def save_preset(preset: OrganizationPreset):
    """Salva ou atualiza um perfil institucional."""
    return PresetManager.save_preset(preset)


@app.get("/api/names", response_model=List[NameEntry])
async def list_name_library(preset_id: Optional[str] = None, category: Optional[str] = None):
    """Lista os nomes oficiais e variações salvas na Biblioteca Incremental de Nomes."""
    return NameLibraryManager.list_entries(preset_id=preset_id, category=category)


@app.post("/api/names", response_model=NameEntry)
async def save_name_entry(entry: NameEntry):
    """Adiciona ou atualiza um nome oficial e seus apelidos/erros conhecidos na Biblioteca."""
    return NameLibraryManager.save_entry(entry)


@app.post("/api/names/quick-learn", response_model=NameEntry)
async def quick_learn_name(request: QuickLearnNameRequest):
    """
    Ensina rapidamente um nome ao sistema ('wrong_text' -> 'correct_name') a partir de uma revisão.
    Se o nome oficial já existir na biblioteca, apenas adiciona a variação incorreta aos aliases dele.
    """
    try:
        return NameLibraryManager.quick_learn(
            wrong_text=request.wrong_text,
            correct_name=request.correct_name,
            category=request.category,
            preset_id=request.preset_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/names/{entry_id}")
async def delete_name_entry(entry_id: str):
    """Remove um registro da Biblioteca de Nomes pelo seu ID."""
    removed = NameLibraryManager.delete_entry(entry_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Registro não encontrado na Biblioteca de Nomes.")
    return {"status": "ok", "deleted_id": entry_id}


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


@app.get("/api/providers")
async def get_providers_status(test: bool = False):
    """
    Retorna a lista de provedores suportados (Gemini, Groq, OpenRouter, Ollama),
    o status de disponibilidade de cada um e o resultado de diagnóstico de latência opcional.
    """
    from .providers import ProviderRouter
    router = ProviderRouter()
    status = router.list_available_providers()
    primary = router.primary_provider

    results: Dict[str, Any] = {
        "primary_provider": primary,
        "providers": status,
    }
    if test:
        test_results = {}
        for name, is_avail in status.items():
            if is_avail:
                test_results[name] = router.test_provider(name)
            else:
                test_results[name] = {"provider": name, "success": False, "error": "Credenciais não configuradas"}
        results["diagnostics"] = test_results
    return results


@app.post("/api/generate", response_model=MinuteOutput)
async def generate_minute_endpoint(config: MinuteConfig):
    """
    Endpoint principal: gera o texto da ata e devolve o JSON enriquecido com:
    - `body`: texto formal da ata
    - `extracted_data`: data ISO, horários, presentes, matérias votadas (pronto para banco de dados)
    - `review_warnings`: termos sugeridos para conferência rápida
    - `timeline_map`: timestamps clicáveis da sessão no YouTube
    Inclui controle de taxa com backoff exponencial e reutilização de cache SQLite.
    """
    global _LATEST_MINUTE_ID
    try:
        # 1. Verifica cache local de atas idênticas para evitar consumo desnecessário de cotas gratuitas
        cache_key = VideoCache.build_config_cache_key(config)
        if not config.bypass_cache:
            cached_minute_dict = VideoCache.get_minute(cache_key)
            if cached_minute_dict:
                cached_minute = MinuteOutput(**cached_minute_dict)
                cached_minute.metadata["cache_hit"] = True
                _MINUTES_STORE[cached_minute.id] = cached_minute
                _LATEST_MINUTE_ID = cached_minute.id
                return cached_minute

        # 2. Executa geração protegida por controle de taxa (RateLimiter)
        generator = MinuteGenerator(provider=config.ai_provider)
        output = RateLimiter.execute_with_backoff(
            func=lambda: generator.generate(config),
            provider=config.ai_provider or "auto",
        )
        _MINUTES_STORE[output.id] = output
        _LATEST_MINUTE_ID = output.id

        # 3. Salva no cache SQLite para consultas futuras
        VideoCache.set_minute(cache_key, output.model_dump())
        return output
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/validate-text", response_model=ValidateTextResponse)
async def validate_and_normalize_text(request: ValidateTextRequest):
    """
    Validador e normalizador cartorial pós-edição:
    Aplica deterministamente a expansão de abreviações, correção de nomes da biblioteca,
    conversão de horas/datas para extenso e formatação de parágrafo único.
    """
    normalized, corrections = TextNormalizer.validate_and_normalize(
        text=request.text,
        single_paragraph=request.single_paragraph,
        paragraph_count=request.paragraph_count,
        numbers_in_words=request.numbers_in_words,
        allow_abbreviations=request.allow_abbreviations,
        preset_id=request.preset_id,
        apply_name_library=request.apply_name_library,
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
    sem precisar regerar a ata inteira, protegido por controle de taxa.
    """
    try:
        generator = MinuteGenerator(provider=getattr(request, "ai_provider", "auto"))
        return RateLimiter.execute_with_backoff(
            func=lambda: generator.refine_snippet(request),
            provider=getattr(request, "ai_provider", "auto") or "auto",
        )
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
