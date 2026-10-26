"""
Motor de Inteligência Artificial para geração, extração estruturada e refinamento de atas (tVerbum v2.0).
Suporta múltiplos provedores com failover automático: Google Gemini, Groq, OpenRouter e Ollama Local.
Devolve simultaneamente o texto formal da ata, os metadados para banco de dados, alertas de revisão humana
e o mapa cronológico de timestamps.
"""

import os
import json
import time
import uuid
import logging
from typing import Optional, List, Any, Tuple
from dotenv import load_dotenv
from google import genai
from google.genai import types

from .models import (
    MinuteConfig,
    MinuteOutput,
    AIGeneratedPayload,
    ExtractedSessionData,
    ReviewWarning,
    TimelineEntry,
    RefineSnippetRequest,
    RefineSnippetResponse,
)
from .ingest import ContentIngestor
from .normalizer import TextNormalizer
from .presets import PresetManager
from .cache import VideoCache
from .name_library import NameLibraryManager
from .providers import ProviderRouter, BaseLLMProvider

load_dotenv()
logger = logging.getLogger("tverbum.ai_engine")


class MinuteGenerator:
    """Gerador inteligente de atas formais com arquitetura multi-provedor e fallback resiliente."""

    FALLBACK_MODELS = [
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
    ]

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        provider: Optional[str] = None,
    ):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model_name = model_name or os.getenv("DEFAULT_MODEL", "gemini-3.8-flash")
        self.provider = (provider or os.getenv("AI_PROVIDER", "auto")).lower()
        self.router = ProviderRouter(primary_provider=self.provider)

        # Atualiza a chave de API no provedor Gemini se informada
        if self.api_key and "gemini" in self.router.providers:
            self.router.providers["gemini"].api_key = self.api_key

        # Instancia cliente Gemini se houver chave (para compatibilidade com upload de mídia)
        self.client: Optional[genai.Client] = None
        if self.api_key:
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception:
                pass

        # Valida se ao menos um provedor de IA está configurado e disponível
        available = self.router.list_available_providers()
        if not any(available.values()):
            raise ValueError(
                "Nenhum provedor de IA disponível. Configure ao menos uma variável de ambiente: "
                "GEMINI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY ou inicie o Ollama localmente."
            )

    def _build_system_prompt(self, config: MinuteConfig, compact: bool = False) -> str:
        """Monta as instruções do sistema calibradas com base no padrão legislativo brasileiro."""
        if config.single_paragraph or config.paragraph_count == 1:
            paragraph_rule = (
                "1. PARÁGRAFO ÚNICO OBRIGATÓRIO: O campo 'body' DEVE SER REDIGIDO ESTRITAMENTE EM UM ÚNICO PARÁGRAFO CONTÍNUO, "
                "sem nenhuma quebra de linha interna. Este é o padrão legal e notarial para evitar fraudes ou espaços vazios. "
                "Use conectivos formais de transição ('Na sequência,', 'Em seguida,', 'Logo após,', 'No espaço do pequeno expediente,', "
                "'Ingressando na ordem do dia,', 'Dando prosseguimento,', 'No espaço das explicações pessoais,', 'Esgotadas as matérias,')."
            )
        else:
            paragraph_rule = (
                f"1. QUANTIDADE EXATA DE PARÁGRAFOS ({config.paragraph_count} PARÁGRAFOS): "
                f"Divida o campo 'body' exatamente em {config.paragraph_count} parágrafos temáticos bem distribuídos, "
                "separados por quebra de linha dupla (\\n\\n)."
            )

        words_rule = (
            "2. NÚMEROS, DATAS E HORÁRIOS POR EXTENSO: No campo 'body', escreva datas ('Aos vinte e oito dias de setembro de dois mil e vinte e seis'), "
            "horários ('dezoito horas e sete minutos'), quantidade de oradores ('quatro vereadores fizeram uso da palavra'), "
            "quórum ('9 (nove) vereadores'), ordinais ('número quarenta') e numerais gerais por extenso. "
            "Exceção documental: Números oficiais de Leis, Projetos, Indicações, Pedidos e Ofícios mantêm o formato padrão "
            "(exemplo: 'Projeto de Lei nº 48/2026', 'Lei Municipal nº 1.903/2015', 'Ofício Circular nº 46/2026')."
            if config.numbers_in_words
            else "2. NÚMEROS E DATAS EM ALGARISMOS: Utilize algarismos numéricos normais para datas, horários e quantidades."
        )

        abbrev_rule = (
            "3. VEDAÇÃO A ABREVIAÇÕES: Não utilize abreviações de cargos ou proposições no campo 'body'. Escreva 'Vereador', "
            "'Vereadora', 'Senhor Presidente', 'Senhor Secretário', 'Projeto de Lei', 'Indicação', 'Pedido de Providência', "
            "'Pedido de Informação' sempre por extenso. As siglas partidárias entre parênteses na chamada nominal são permitidas (ex: '(MDB)', '(PP)', '(PDT)', '(UNIÃO)')."
            if not config.allow_abbreviations
            else "3. ABREVIAÇÕES PERMITIDAS: Pode utilizar abreviações legislativas usuais como Ver., Pres., Sec., PL."
        )

        detail_rules = {
            "resumido": (
                "4. NÍVEL DE DETALHAMENTO (RESUMIDO / SINTÉTICO): Seja conciso e direto. Registre apenas os atos essenciais, "
                "presenças, resumo de 1 frase curta por orador, matérias votadas com seus resultados e encerramento."
            ),
            "padrao": (
                "4. NÍVEL DE DETALHAMENTO (PADRÃO OFICIAL): Registre com equilíbrio e fidelidade os pronunciamentos de cada "
                "vereador no Pequeno Expediente e nas Explicações Pessoais (2 a 4 linhas por orador resumindo todos os temas "
                "abordados, cobranças, elogios e indicações), além de todas as correspondências e votações da Ordem do Dia."
            ),
            "detalhado": (
                "4. NÍVEL DE DETALHAMENTO (DETALHADO / ANALÍTICO): Faça um registro minucioso e exaustivo de cada discurso, "
                "incluindo todos os argumentos apresentados pelos oradores, apartes concedidos, justificativas de voto, "
                "detalhes das exposições de motivos e todas as correspondências lidas."
            ),
        }
        detail_rule = detail_rules.get(config.detail_level, detail_rules["padrao"])

        example_section = ""
        if not compact:
            example_section = """
EXEMPLO DE REFERÊNCIA DE QUALIDADE E ESTILO ESPERADO PARA O CAMPO 'body':
"Aos vinte e oito dias de setembro de dois mil e vinte e seis, reuniram-se nas dependências da Câmara Municipal de Vereadores de Exemplo os integrantes da 10ª Legislatura para a realização da Sessão Plenária Ordinária de número quarenta. Havendo quórum regimental e, invocando a proteção de Deus, o Senhor Presidente Carlos Eduardo da Silva declarou aberta a sessão as dezoito horas e sete minutos. Na sequência, o Senhor Secretário realizou a primeira chamada, a qual acusou a presença dos vereadores: Carlos Eduardo da Silva e Ana Paula Oliveira (PARTIDO A); Marcos Vinícius Santos e Helena Costa Ferreira (PARTIDO B); e Roberto Almeida Souza (PARTIDO C). Em seguida, procedeu-se à leitura da mensagem do dia, sendo conduzida a oração do "Pai Nosso". Logo após, foram lidas as correspondências, destacando-se o Ofício Circular nº 46/2026 do Tribunal de Contas e o Ofício nº 134/2026 do Executivo, encaminhando o Projeto de Lei nº 49/2026. A Presidência informou que a Ata nº 20 foi enviada previamente aos vereadores e considerada tacitamente aprovada. No espaço do pequeno expediente, três vereadores fizeram uso da palavra... Ingressando na ordem do dia, foi apreciado o Projeto de Lei nº 48/2026... Após a leitura dos pareceres favoráveis das comissões, o projeto foi colocado em votação e aprovado por unanimidade... Esgotadas as matérias, a sessão foi encerrada as dezenove horas e dezoito minutos, sendo a próxima sessão ordinária convocada para o dia treze de outubro de dois mil e vinte e seis (terça-feira), às dezoito horas."
"""

        prompt = f"""Você é um redator oficial e consultor legislativo sênior especializado na confecção de atas institucionais e parlamentares brasileiras.
Sua missão é analisar a sessão fornecida e retornar um objeto JSON estruturado contendo:
1. 'body': O texto formal completo do corpo da ata (sem cabeçalho institucional e sem linhas de assinatura).
2. 'extracted_data': Os dados estruturados da sessão para o banco de dados (data ISO YYYY-MM-DD, horários HH:MM, presidente, secretário, lista de presentes, ausentes e matérias votadas/lidas).
3. 'review_warnings': De 1 a 5 pontos específicos do texto ('term' deve ser uma palavra ou trecho curto exato presente em 'body', 'timestamp' o minuto aproximado no vídeo e 'reason' o motivo) onde recomenda-se que o revisor humano confira um nome próprio específico, número de ofício/lei ou valor citado.
4. 'timeline_map': Lista cronológica das fases da sessão e oradores com seus respectivos timestamps ('HH:MM:SS' ou 'MM:SS') para navegação sincronizada no vídeo.

DIRETRIZES FUNDAMENTAIS DE REDAÇÃO DO CAMPO 'body':
{paragraph_rule}
{words_rule}
{abbrev_rule}
{detail_rule}
5. NEUTRALIDADE E OBJETIVIDADE JURÍDICA: Mantenha tom estritamente impessoal, em terceira pessoa, técnico e neutro.
6. ESTRUTURA CRONOLÓGICA CANÔNICA DA SESSÃO:
   - Abertura: Data e local por extenso, menção à Legislatura e número da Sessão Plenária, quórum regimental, invocação da proteção divina ('invocando a proteção de Deus'), declaração de abertura pelo Senhor Presidente e horário exato por extenso.
   - Primeira chamada: Relação nominal dos vereadores presentes agrupados por bancada/partido político quando conhecido.
   - Atos iniciais e Expediente: Leitura da mensagem do dia e oração ('Pai Nosso' ou equivalente), leitura das correspondências e ofícios recebidos, e aprovação tácita ou votada da ata da sessão anterior.
   - Pequeno Expediente: Quantidade de vereadores inscritos e síntese fiel, individual e cronológica de cada pronunciamento.
   - Segunda chamada / Verificação de quórum para a Ordem do Dia.
   - Ordem do Dia: Matérias apreciadas e votadas (Projetos de Lei, pareceres das comissões, resultado da votação como 'aprovado por unanimidade'), seguidas da leitura das demais matérias encaminhadas às comissões ou apresentadas (Projetos de Lei, Indicações, Pedidos de Providência, Pedidos de Informação com seus respectivos números e autores).
   - Explicações Pessoais: Manifestações finais dos vereadores que fizeram uso da palavra.
   - Encerramento: Declaração de encerramento com horário por extenso e convocação para a próxima sessão.
{example_section}"""
        return prompt

    @staticmethod
    def _prepare_transcript_for_inference(transcript_text: str, max_chars: int = 120000) -> str:
        """
        Garante que transcrições muito longas (>3 horas) caibam no contexto de modelos gratuitos.
        Se exceder max_chars, condensa repetições e preserva as marcações temporais chave.
        """
        if not transcript_text or len(transcript_text) <= max_chars:
            return transcript_text

        lines = transcript_text.splitlines()
        if len(lines) <= 500:
            return transcript_text[:max_chars]

        # Mantém cabeçalho e rodapé cronológicos integrais e condensa o meio mantendo timestamps
        keep_lines: List[str] = []
        for i, line in enumerate(lines):
            # Sempre mantém primeiras 100 linhas e últimas 100 linhas
            if i < 100 or i >= len(lines) - 100:
                keep_lines.append(line)
            # Para o miolo, inclui se tiver menção a votação, ordem do dia, presença ou a cada 2 linhas
            elif any(k in line.lower() for k in ("votação", "projeto", "aprovado", "rejeitado", "chamada", "ordem do dia", "expediente")) or (i % 2 == 0):
                keep_lines.append(line)

        condensed = "\n".join(keep_lines)
        return condensed[:max_chars]

    def _wait_for_file_active(self, uploaded_file: Any, timeout_seconds: int = 180) -> Any:
        """Aguarda até que um arquivo de áudio/vídeo enviado à Gemini File API fique no estado ACTIVE."""
        if not self.client:
            return uploaded_file

        start = time.time()
        file_name = getattr(uploaded_file, "name", None)
        if not file_name:
            return uploaded_file

        while time.time() - start < timeout_seconds:
            state = getattr(uploaded_file, "state", None)
            state_name = getattr(state, "name", str(state)) if state else "ACTIVE"
            if "ACTIVE" in state_name:
                return uploaded_file
            if "FAILED" in state_name:
                raise RuntimeError(f"O processamento do arquivo de mídia falhou na API Gemini: {state_name}")
            time.sleep(2)
            uploaded_file = self.client.files.get(name=file_name)

        return uploaded_file

    @staticmethod
    def _parse_ai_payload(raw_text: str) -> AIGeneratedPayload:
        """Converte o texto retornado pela IA em AIGeneratedPayload com máxima tolerância a falhas."""
        return BaseLLMProvider.parse_ai_payload(raw_text)

    def generate(self, config: MinuteConfig) -> MinuteOutput:
        """Executa o pipeline completo de geração da ata e extração de metadados estruturados."""
        # 0. Aplica perfil institucional pré-configurado se informado
        config = PresetManager.apply_preset_to_config(config)

        # 1. Ingestão de conteúdo e metadados do YouTube (com Cache SQLite)
        mode, content, meta = ContentIngestor.ingest(
            config.video_url,
            mode_preference=config.ingestion_mode,
            bypass_cache=config.bypass_cache,
        )

        # 1.1. Limpeza Pré-IA da transcrição usando a Biblioteca Incremental de Nomes
        if mode == "captions" and isinstance(content, str):
            content, _ = NameLibraryManager.apply_corrections(
                content,
                preset_id=config.preset_id,
                extra_known_names=config.known_attendees,
            )

        # Captura da transcrição para reuso por ferramentas de imprensa e redes sociais (tNuntia)
        raw_transcript_text: Optional[str] = None
        if mode == "captions" and isinstance(content, str):
            raw_transcript_text = content
        elif meta.get("auxiliary_captions"):
            raw_transcript_text, _ = NameLibraryManager.apply_corrections(
                meta.get("auxiliary_captions"),
                preset_id=config.preset_id,
                extra_known_names=config.known_attendees,
            )
            meta["auxiliary_captions"] = raw_transcript_text
        else:
            video_id = meta.get("video_id")
            if video_id:
                raw_transcript_text = VideoCache.get_transcript(video_id)
                if raw_transcript_text:
                    raw_transcript_text, _ = NameLibraryManager.apply_corrections(
                        raw_transcript_text,
                        preset_id=config.preset_id,
                        extra_known_names=config.known_attendees,
                    )

        # 2. Monta o prompt do sistema (suporta modo compacto para economia de TPM)
        use_compact = getattr(config, "compact_prompt", False) or (getattr(config, "ai_provider", "auto") in ("groq", "ollama"))
        system_instruction = self._build_system_prompt(config, compact=use_compact)

        # 3. Informações contextuais fornecidas pelo usuário + metadados automáticos do YouTube + Biblioteca de Nomes
        context_details = []
        glossary_section = NameLibraryManager.build_prompt_glossary(
            preset_id=config.preset_id,
            extra_known_names=config.known_attendees,
        )
        if glossary_section:
            context_details.append(glossary_section)
        if config.organization_name:
            context_details.append(f"- Órgão/Entidade: {config.organization_name}")
        if config.session_type:
            context_details.append(f"- Tipo de Sessão: {config.session_type}")
        if config.session_number:
            context_details.append(f"- Número da Ata: {config.session_number}")
        if config.legislature:
            context_details.append(f"- Legislatura: {config.legislature}")
        if config.plenary_session_number:
            context_details.append(f"- Número da Sessão Plenária: {config.plenary_session_number}")
        if config.president_name:
            context_details.append(f"- Presidente da Sessão: {config.president_name}")
        if config.secretary_name:
            context_details.append(f"- Secretário da Sessão: {config.secretary_name}")
        if config.known_attendees:
            context_details.append(
                f"- Lista Oficial de Vereadores/Membros e Partidos (use esta grafia exata): {', '.join(config.known_attendees)}"
            )

        if meta.get("video_title"):
            context_details.append(f"- Título do Vídeo no YouTube: {meta['video_title']}")
        if meta.get("upload_date"):
            context_details.append(f"- Data de Publicação/Transmissão do Vídeo: {meta['upload_date']}")
        if meta.get("channel"):
            context_details.append(f"- Canal Oficial: {meta['channel']}")
        if meta.get("video_description"):
            context_details.append(f"- Descrição do Vídeo no YouTube:\n{meta['video_description']}")

        if config.agenda_context:
            context_details.append(
                f"- PAUTA OFICIAL / ORDEM DO DIA / MATÉRIAS E CORRESPONDÊNCIAS (use para precisão exata de números e ementas):\n{config.agenda_context}"
            )
        if config.custom_instructions:
            context_details.append(f"- Instruções Adicionais do Usuário: {config.custom_instructions}")

        user_prompt_header = (
            "DADOS DE CONTEXTO E ANCORAGEM DA SESSÃO:\n"
            + "\n".join(context_details)
            + "\n\nAnalise a sessão a seguir e retorne o JSON estruturado com o corpo da ata ('body'), "
            "'extracted_data', 'review_warnings' e 'timeline_map':\n"
        )

        uploaded_file = None
        ai_payload = AIGeneratedPayload(body="")
        used_model = self.model_name
        used_provider = getattr(config, "ai_provider", "auto") or "auto"
        target_model = getattr(config, "ai_model", None)

        try:
            # Caso 1: Transcrição textual (modo mais frugal e compatível com 100% dos modelos gratuitos)
            if mode == "captions":
                prepared_content = self._prepare_transcript_for_inference(content)
                user_content = [f"{user_prompt_header}\nTRANSCRIÇÃO DA SESSÃO:\n{prepared_content}"]
                ai_payload, used_provider, used_model = self.router.generate_structured(
                    contents=user_content,
                    system_instruction=system_instruction,
                    user_prompt_header="",
                    preferred_provider=used_provider,
                    model_name=target_model or self.model_name,
                )

            # Caso 2: URL direta do YouTube (multimodal Gemini)
            elif mode == "youtube_direct":
                # Se o provedor não for Gemini ou Gemini não estiver disponível, tenta obter transcrição
                if used_provider not in ("auto", "gemini") or not self.client:
                    aux_captions = meta.get("auxiliary_captions") or (
                        VideoCache.get_transcript(meta.get("video_id", ""))
                    )
                    if aux_captions:
                        user_content = [f"{user_prompt_header}\nTRANSCRIÇÃO DA SESSÃO:\n{aux_captions}"]
                        ai_payload, used_provider, used_model = self.router.generate_structured(
                            contents=user_content,
                            system_instruction=system_instruction,
                            preferred_provider=used_provider,
                            model_name=target_model,
                        )
                    else:
                        raise RuntimeError(
                            f"O provedor '{used_provider}' requer transcrição de áudio/texto e não suporta vídeo multimodal direto."
                        )
                else:
                    yt_part = types.Part.from_uri(file_uri=content, mime_type="video/mp4")
                    aux_captions = meta.get("auxiliary_captions", "")
                    prompt_parts: List[Any] = [yt_part, user_prompt_header]
                    if aux_captions:
                        prompt_parts.append(f"\nTRANSCRIÇÃO AUXILIAR DE APOIO:\n{aux_captions[:50000]}")
                    try:
                        ai_payload, used_provider, used_model = self.router.generate_structured(
                            contents=prompt_parts,
                            system_instruction=system_instruction,
                            preferred_provider="gemini",
                            model_name=target_model or self.model_name,
                        )
                    except Exception as yt_err:
                        # Fallback de áudio se YouTube Direct multimodal falhar
                        logger.warning(f"YouTube Direct falhou no Gemini ({yt_err}). Tentando áudio...")
                        audio_path = ContentIngestor.download_audio_stream(content)
                        if not audio_path:
                            raise
                        mode = "audio"
                        content = audio_path
                        meta["method"] = "audio_download_fallback"
                        if self.client:
                            uploaded_file = self.client.files.upload(file=content)
                            uploaded_file = self._wait_for_file_active(uploaded_file)
                            ai_payload, used_provider, used_model = self.router.generate_structured(
                                contents=[user_prompt_header, uploaded_file],
                                system_instruction=system_instruction,
                                preferred_provider="gemini",
                                model_name=target_model or self.model_name,
                            )
                        else:
                            raise yt_err

            # Caso 3: Arquivo de áudio local
            else:
                if self.client and used_provider in ("auto", "gemini"):
                    uploaded_file = self.client.files.upload(file=content)
                    uploaded_file = self._wait_for_file_active(uploaded_file)
                    user_content = [user_prompt_header, uploaded_file]
                    ai_payload, used_provider, used_model = self.router.generate_structured(
                        contents=user_content,
                        system_instruction=system_instruction,
                        preferred_provider="gemini",
                        model_name=target_model or self.model_name,
                    )
                else:
                    raise RuntimeError("Upload de arquivos brutos de áudio é restrito ao provedor Gemini.")

        finally:
            if uploaded_file and hasattr(uploaded_file, "name") and self.client:
                try:
                    self.client.files.delete(name=uploaded_file.name)
                except Exception:
                    pass
            if mode == "audio" and os.path.exists(content):
                try:
                    os.remove(content)
                except Exception:
                    pass

        # 4. Pós-processamento Determinístico (Corpo da ata + Metadados + Linha do Tempo)
        payload_name_fixes = NameLibraryManager.sanitize_ai_payload(
            ai_payload,
            preset_id=config.preset_id,
            extra_known_names=config.known_attendees,
        )

        processed_body, corrections = TextNormalizer.validate_and_normalize(
            text=ai_payload.body,
            single_paragraph=config.single_paragraph,
            paragraph_count=config.paragraph_count,
            numbers_in_words=config.numbers_in_words,
            allow_abbreviations=config.allow_abbreviations,
            preset_id=config.preset_id,
            extra_known_names=config.known_attendees,
        )
        if payload_name_fixes:
            fix_msg = f"Nomes corrigidos pela Biblioteca: {', '.join(payload_name_fixes)}."
            if not any("Nomes corrigidos" in c for c in corrections):
                corrections.insert(0, fix_msg)

        # Preenche data ISO de fallback se o vídeo tiver upload_date_iso e a IA não tiver extraído
        if not ai_payload.extracted_data.session_date_iso and meta.get("upload_date_iso"):
            ai_payload.extracted_data.session_date_iso = meta["upload_date_iso"]

        # 5. Montagem do documento final
        session_title = f"ATA Nº {config.session_number.upper()} - {config.session_type.upper()}"

        header_text = None
        if config.include_header:
            header_text = TextNormalizer.build_header(
                organization_name=config.organization_name,
                state=config.state,
                organization_details=config.organization_details,
                title=session_title,
            )

        signatures_text = None
        if config.include_signatures:
            signatures_text = TextNormalizer.build_signatures()

        doc_parts = []
        if header_text:
            doc_parts.append(header_text)
        else:
            doc_parts.append(session_title + "\n")

        doc_parts.append(processed_body)

        if signatures_text:
            doc_parts.append(signatures_text)

        full_text = "\n".join(doc_parts)

        meta.pop("auxiliary_captions", None)
        meta["word_count"] = len(processed_body.split())
        meta["character_count"] = len(processed_body)
        meta["paragraph_count"] = len([p for p in processed_body.split("\n\n") if p.strip()])
        meta["provider_used"] = used_provider
        meta["model_used"] = used_model
        meta["post_processing_corrections"] = corrections

        return MinuteOutput(
            id=str(uuid.uuid4()),
            title=session_title,
            header=header_text,
            organization_name=config.organization_name,
            state=config.state,
            organization_details=config.organization_details,
            logo_url=config.logo_url,
            body=processed_body,
            signatures=signatures_text,
            full_text=full_text,
            raw_transcript=raw_transcript_text if config.include_raw_transcript else None,
            extracted_data=ai_payload.extracted_data,
            review_warnings=ai_payload.review_warnings,
            timeline_map=ai_payload.timeline_map,
            metadata=meta,
        )

    def refine_snippet(self, request: RefineSnippetRequest) -> RefineSnippetResponse:
        """Reescreve ou refina rapidamente apenas um trecho selecionado da ata."""
        provider_preference = getattr(request, "ai_provider", "auto") or "auto"
        model_preference = getattr(request, "ai_model", None)

        refined_raw, used_provider, used_model = self.router.refine_snippet(
            snippet=request.snippet,
            instruction=request.instruction,
            preferred_provider=provider_preference,
            model_name=model_preference,
        )

        normalized_snippet, _ = TextNormalizer.validate_and_normalize(
            text=refined_raw,
            single_paragraph=True,
            paragraph_count=1,
            numbers_in_words=request.numbers_in_words,
            allow_abbreviations=request.allow_abbreviations,
        )

        return RefineSnippetResponse(
            original_snippet=request.snippet,
            refined_snippet=normalized_snippet,
        )
