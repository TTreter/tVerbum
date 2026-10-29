"""
API FastAPI do tVerbum (v1.2) e servidor web integrado para testes e consumo por aplicações web (JS/Python).
Inclui endpoints de validação pós-edição, reescrita de trecho com IA e entrega do Web Component <tverbum-editor>.
"""

import uuid
import json
import hashlib
import asyncio
import tempfile
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from fastapi import FastAPI, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, Response, StreamingResponse

from .models import (
    MinuteConfig,
    MinuteOutput,
    UpdateMinuteRequest,
    ExportRequest,
    OrganizationPreset,
    NameEntry,
    QuickLearnNameRequest,
    ValidateTextRequest,
    ValidateTextResponse,
    RefineSnippetRequest,
    RefineSnippetResponse,
    GenerationJob,
    SaplConnectionRequest,
    SaplSyncNamesRequest,
    SaplFetchSessionRequest,
    AgendaExtractRequest,
    AgendaExtractResponse,
    DetectSpeakersRequest,
    DetectSpeakersResponse,
    NewsReleaseRequest,
    NewsReleaseResponse,
    CitizenSummaryRequest,
    CitizenSummaryResponse,
    CouncilorDossierRequest,
    CouncilorDossierResponse,
    VotedMatter,
)
from .ai_engine import MinuteGenerator
from .normalizer import TextNormalizer
from .exporter import MinuteExporter
from .presets import PresetManager
from .name_library import NameLibraryManager
from .cache import VideoCache
from .limiter import RateLimiter
from .sapl_client import SaplClient
from .agenda_parser import AgendaParser
from .speaker_mapper import SpeakerMapper
from .press_hub import PressHub

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
    elif fmt in ("lexml", "xml"):
        file_path = _TEMP_DIR / f"{safe_title}_{unique_suffix}_lexml.xml"
        file_path.write_text(PressHub.export_lexml(minute), encoding="utf-8")
        return FileResponse(
            path=str(file_path),
            filename=f"{safe_title}_lexml.xml",
            media_type="application/xml; charset=utf-8",
        )
    elif fmt in ("gazette", "diario"):
        file_path = _TEMP_DIR / f"{safe_title}_{unique_suffix}_gazette.txt"
        file_path.write_text(PressHub.export_official_gazette(minute), encoding="utf-8")
        return FileResponse(
            path=str(file_path),
            filename=f"{safe_title}_diario_oficial.txt",
            media_type="text/plain; charset=utf-8",
        )
    else:
        raise HTTPException(
            status_code=400,
            detail="Formato não suportado. Use 'pdf', 'docx', 'txt', 'md', 'lexml' ou 'gazette'.",
        )


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


# =============================================================================
# Rotas de Integração com o SAPL (Interlegis / Senado Federal)
# =============================================================================


@app.post("/api/sapl/test")
async def test_sapl_connection(request: SaplConnectionRequest):
    """Testa se a URL da Câmara Municipal possui a API REST do SAPL ativa e comunicável."""
    client = SaplClient(request.base_url)
    return client.test_connection()


@app.post("/api/sapl/sync-names")
async def sync_sapl_names(request: SaplSyncNamesRequest):
    """
    Importa os nomes de todos os vereadores cadastrados no SAPL da Câmara e
    adiciona/atualiza na Biblioteca Incremental de Nomes do tVerbum automaticamente.
    """
    client = SaplClient(request.base_url)
    synced = client.sync_names_to_library(preset_id=request.preset_id)
    return {
        "status": "ok",
        "synced_count": len(synced),
        "entries": synced,
        "message": f"{len(synced)} nomes de parlamentares sincronizados com a Biblioteca de Nomes.",
    }


@app.post("/api/sapl/fetch-session")
async def fetch_sapl_session(request: SaplFetchSessionRequest):
    """
    Importa todos os dados oficiais da sessão a partir da API do SAPL:
    - Data, horário e link do vídeo da transmissão;
    - Presidente e Secretário da sessão;
    - Pauta oficial completa da Ordem do Dia (para ancorar a IA sem alucinações de leis);
    - Vereadores em exercício.
    """
    client = SaplClient(request.base_url)
    try:
        data = client.fetch_full_session_context(
            session_identifier=request.session_number,
            year=request.year,
            sync_names=request.sync_names,
            preset_id=request.preset_id,
        )
        return data
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao consultar o SAPL: {str(e)}")


# =============================================================================
# Rotas de Extração e Análise de Pauta / Ordem do Dia (PDF, Word e Texto)
# =============================================================================


@app.post("/api/agenda/extract", response_model=AgendaExtractResponse)
async def extract_agenda_from_text(request: AgendaExtractRequest):
    """
    Analisa um texto colado de pauta / ordem do dia e extrai proposições legislativas,
    autores e ementas para ancoragem do modelo de IA.
    """
    raw_matters = AgendaParser.parse_propositions_from_text(request.text)
    matters = [
        VotedMatter(
            matter=m["matter"],
            author=m.get("author", ""),
            summary=m.get("summary", ""),
            result="Em pauta",
        )
        for m in raw_matters
    ]
    formatted = AgendaParser.format_agenda_context(raw_matters)
    return AgendaExtractResponse(
        status="ok",
        matters_count=len(matters),
        matters=matters,
        formatted_context=formatted,
    )


@app.post("/api/agenda/upload", response_model=AgendaExtractResponse)
async def extract_agenda_from_file(file: UploadFile = File(...)):
    """
    Recebe um arquivo PDF (.pdf) ou Word (.docx) contendo a Ordem do Dia / Pauta oficial,
    lê seu conteúdo e extrai deterministicamente todas as matérias legislativas.
    """
    ext = Path(file.filename or "").suffix.lower()
    if ext not in (".pdf", ".docx", ".doc"):
        raise HTTPException(status_code=400, detail="Envie um arquivo PDF (.pdf) ou Word (.docx).")

    content_bytes = await file.read()
    if len(content_bytes) == 0:
        raise HTTPException(status_code=400, detail="Arquivo vazio.")

    if ext == ".pdf":
        text = AgendaParser.extract_text_from_pdf(content_bytes)
    else:
        text = AgendaParser.extract_text_from_docx(content_bytes)

    raw_matters = AgendaParser.parse_propositions_from_text(text)
    matters = [
        VotedMatter(
            matter=m["matter"],
            author=m.get("author", ""),
            summary=m.get("summary", ""),
            result="Em pauta",
        )
        for m in raw_matters
    ]
    formatted = AgendaParser.format_agenda_context(raw_matters)
    return AgendaExtractResponse(
        status="ok",
        matters_count=len(matters),
        matters=matters,
        formatted_context=formatted,
    )


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
    Devolve o objeto MinuteOutput salvo no banco de dados SQLite persistente (ou em cache de memória),
    permitindo que sistemas externos (como tNuntia) consultem atas geradas anteriormente sem reprocessar.
    Aceita 'latest' para obter a última sessão gerada.
    """
    target_id = _LATEST_MINUTE_ID if minute_id == "latest" else minute_id

    # 1. Consulta armazenamento persistente SQLite
    if minute_id == "latest":
        db_minute = VideoCache.get_latest_session()
    else:
        db_minute = VideoCache.get_session(target_id) if target_id else None

    if db_minute:
        return MinuteOutput(**db_minute)

    # 2. Fallback para memória em tempo de execução
    minute = _MINUTES_STORE.get(target_id) if target_id else None
    if not minute:
        raise HTTPException(status_code=404, detail="Sessão/Ata não encontrada para o minute_id informado.")
    return minute


@app.put("/api/session/{minute_id}", response_model=MinuteOutput)
async def update_session_by_id(minute_id: str, payload: UpdateMinuteRequest):
    """
    Atualiza o título, o corpo da ata e/ou o status no ciclo de vida notarial.
    Recalcula o hash SHA-256 de integridade e persiste as alterações no banco SQLite.
    """
    data = VideoCache.get_session(minute_id)
    if not data:
        cached = _MINUTES_STORE.get(minute_id)
        if cached:
            data = cached.model_dump()
        else:
            raise HTTPException(status_code=404, detail="Sessão não encontrada para atualização.")

    if payload.title is not None:
        data["title"] = payload.title.strip()
    if payload.body is not None:
        data["body"] = payload.body.strip()
    if payload.document_status is not None:
        data["document_status"] = payload.document_status

    doc_parts = []
    if data.get("header"):
        doc_parts.append(data["header"])
    elif data.get("title"):
        doc_parts.append(data["title"])
    if data.get("body"):
        doc_parts.append(data["body"])
    if data.get("signatures"):
        doc_parts.append(data["signatures"])

    full_text = "\n\n".join(doc_parts)
    data["full_text"] = full_text
    data["integrity_hash"] = hashlib.sha256(full_text.encode("utf-8")).hexdigest()

    meta = data.get("metadata") or {}
    meta["word_count"] = len(data["body"].split()) if data.get("body") else 0
    meta["character_count"] = len(data["body"]) if data.get("body") else 0
    data["metadata"] = meta

    VideoCache.save_session(minute_id, data)
    output_obj = MinuteOutput(**data)
    _MINUTES_STORE[minute_id] = output_obj
    return output_obj


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

        # 3. Salva no cache SQLite e persistência relacional de sessões
        VideoCache.set_minute(cache_key, output.model_dump())
        VideoCache.save_session(output.id, output.model_dump(), config.model_dump())
        return output
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _run_async_generation_task(job_id: str, config: MinuteConfig):
    """Executa a geração em background e atualiza o progresso em tempo real no SQLite."""
    global _LATEST_MINUTE_ID
    try:
        VideoCache.save_job(
            job_id,
            status="downloading",
            progress_percent=15,
            step_description="Localizando mídia da sessão e verificando legendas/áudio...",
        )

        # 1. Checa cache
        cache_key = VideoCache.build_config_cache_key(config)
        if not config.bypass_cache:
            cached_minute_dict = VideoCache.get_minute(cache_key)
            if cached_minute_dict:
                cached_minute = MinuteOutput(**cached_minute_dict)
                cached_minute.metadata["cache_hit"] = True
                _MINUTES_STORE[cached_minute.id] = cached_minute
                _LATEST_MINUTE_ID = cached_minute.id
                VideoCache.save_session(cached_minute.id, cached_minute.model_dump(), config.model_dump())
                VideoCache.save_job(
                    job_id,
                    status="completed",
                    progress_percent=100,
                    step_description="Ata recuperada instantaneamente do cache local SQLite.",
                    result_data=cached_minute.model_dump(),
                )
                return

        VideoCache.save_job(
            job_id,
            status="transcribing",
            progress_percent=40,
            step_description="Transcrevendo falas e cronometragens com Groq Whisper...",
        )

        generator = MinuteGenerator(provider=config.ai_provider)

        VideoCache.save_job(
            job_id,
            status="generating_ai",
            progress_percent=70,
            step_description="Redigindo texto formal da ata com IA e estruturando dados...",
        )

        output = RateLimiter.execute_with_backoff(
            func=lambda: generator.generate(config),
            provider=config.ai_provider or "auto",
        )

        VideoCache.save_job(
            job_id,
            status="normalizing",
            progress_percent=90,
            step_description="Aplicando normalizador determinístico e biblioteca fonética de nomes...",
        )

        _MINUTES_STORE[output.id] = output
        _LATEST_MINUTE_ID = output.id
        VideoCache.set_minute(cache_key, output.model_dump())
        VideoCache.save_session(output.id, output.model_dump(), config.model_dump())

        VideoCache.save_job(
            job_id,
            status="completed",
            progress_percent=100,
            step_description="Ata gerada com sucesso e pronta para revisão oficial.",
            result_data=output.model_dump(),
        )
    except Exception as e:
        logger.error(f"Erro ao processar job {job_id}: {e}", exc_info=True)
        VideoCache.save_job(
            job_id,
            status="failed",
            progress_percent=100,
            step_description="Falha durante o processamento da sessão.",
            error_message=str(e),
        )


@app.post("/api/generate-async", response_model=GenerationJob)
async def generate_minute_async_endpoint(config: MinuteConfig, background_tasks: BackgroundTasks):
    """
    Inicia a geração da ata em segundo plano (Background Task) sem risco de timeout HTTP 504.
    Retorna imediatamente o `job_id` para acompanhamento de status via `/api/jobs/{job_id}/stream`.
    """
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    VideoCache.save_job(
        job_id,
        status="queued",
        progress_percent=0,
        step_description="Tarefa agendada na fila de processamento...",
    )
    background_tasks.add_task(_run_async_generation_task, job_id, config)
    job_data = VideoCache.get_job(job_id)
    return GenerationJob(**job_data)


@app.get("/api/jobs/{job_id}", response_model=GenerationJob)
async def get_job_status(job_id: str):
    """Retorna o status atual, percentual de progresso e resultado de uma tarefa assíncrona."""
    job_data = VideoCache.get_job(job_id)
    if not job_data:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada para o job_id informado.")
    return GenerationJob(**job_data)


@app.get("/api/jobs/{job_id}/stream")
async def stream_job_progress(job_id: str):
    """
    Endpoint SSE (Server-Sent Events) para transmissão do progresso em tempo real
    para a interface web ou sistemas externos via `EventSource`.
    """
    async def event_generator():
        last_progress = -1
        last_step = ""
        while True:
            job = VideoCache.get_job(job_id)
            if not job:
                yield f"event: error\ndata: {json.dumps({'error': 'Job não encontrado'})}\n\n"
                break

            if job["progress_percent"] != last_progress or job["step_description"] != last_step:
                last_progress = job["progress_percent"]
                last_step = job["step_description"]
                payload = {
                    "job_id": job["job_id"],
                    "status": job["status"],
                    "progress_percent": job["progress_percent"],
                    "step_description": job["step_description"],
                    "result": job.get("result"),
                    "error_message": job.get("error_message"),
                }
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

            if job["status"] in ("completed", "failed"):
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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


@app.post("/api/speakers/detect", response_model=DetectSpeakersResponse)
async def detect_speakers_endpoint(payload: DetectSpeakersRequest):
    """
    Detecta concessões regimentais de fala na transcrição da sessão e apura
    os tempos estimados de tribuna de cada orador.
    """
    cues = SpeakerMapper.extract_speaker_cues(
        payload.transcript,
        known_attendees=payload.known_attendees,
    )
    _, time_summary = SpeakerMapper.enhance_timeline(
        timeline=[],
        transcript_text=payload.transcript,
        known_attendees=payload.known_attendees,
    )
    return DetectSpeakersResponse(
        detected_cues=cues,
        speaker_time_summary=time_summary,
    )


def _resolve_minute_from_request(minute_id: Optional[str], minute_obj: Optional[MinuteOutput]) -> MinuteOutput:
    if minute_obj:
        return minute_obj
    target_id = _LATEST_MINUTE_ID if minute_id in ("latest", None) else minute_id
    if target_id:
        cached = VideoCache.get_session(target_id)
        if cached:
            return MinuteOutput(**cached)
        if target_id in _MINUTES_STORE:
            return _MINUTES_STORE[target_id]
    raise HTTPException(status_code=404, detail="Ata não encontrada para gerar o produto de imprensa.")


@app.post("/api/generate-news-release", response_model=NewsReleaseResponse)
async def generate_news_release_endpoint(payload: NewsReleaseRequest):
    """Gera release jornalístico oficial para publicação no portal da Câmara."""
    minute = _resolve_minute_from_request(payload.minute_id, payload.minute)
    data = PressHub.generate_news_release(minute, tone=payload.tone)
    return NewsReleaseResponse(**data)


@app.post("/api/generate-citizen-summary", response_model=CitizenSummaryResponse)
async def generate_citizen_summary_endpoint(payload: CitizenSummaryRequest):
    """Gera resumo executivo simples para o cidadão e versão para transmissão no WhatsApp."""
    minute = _resolve_minute_from_request(payload.minute_id, payload.minute)
    data = PressHub.generate_citizen_summary(minute)
    return CitizenSummaryResponse(**data)


@app.post("/api/generate-councilor-dossier", response_model=CouncilorDossierResponse)
async def generate_councilor_dossier_endpoint(payload: CouncilorDossierRequest):
    """Gera dossiê com os pronunciamentos, matérias e post de rede social de um vereador."""
    minute = _resolve_minute_from_request(payload.minute_id, payload.minute)
    data = PressHub.generate_councilor_dossier(minute, payload.councilor_name)
    return CouncilorDossierResponse(**data)


@app.post("/api/export/lexml")
async def export_lexml_endpoint(payload: ExportRequest):
    """Exporta a ata no padrão nacional LexML Brasil (XML)."""
    xml_str = PressHub.export_lexml(payload.minute)
    return Response(content=xml_str, media_type="application/xml")


@app.post("/api/export/official-gazette")
async def export_official_gazette_endpoint(payload: ExportRequest):
    """Exporta a ata diagramada para publicação no Diário Oficial dos Municípios."""
    gazette_text = PressHub.export_official_gazette(payload.minute)
    return Response(content=gazette_text, media_type="text/plain; charset=utf-8")
