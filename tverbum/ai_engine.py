"""
Motor de Inteligência Artificial para geração, extração estruturada e refinamento de atas (tVerbum v1.2).
Utiliza Structured Outputs do Google Gemini para devolver simultaneamente o texto formal da ata,
os metadados para banco de dados, alertas de revisão humana e o mapa de timestamps.
"""

import os
import json
import time
import uuid
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

load_dotenv()


class MinuteGenerator:
    """Gerador inteligente de atas formais utilizando Google Gemini."""

    FALLBACK_MODELS = [
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
    ]

    def __init__(self, api_key: Optional[str] = None, model_name: Optional[str] = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Chave de API do Gemini não encontrada. Defina a variável GEMINI_API_KEY no arquivo .env "
                "ou passe diretamente para o gerador."
            )
        self.model_name = model_name or os.getenv("DEFAULT_MODEL", "gemini-3.8-flash")
        self.client = genai.Client(api_key=self.api_key)

    def _build_system_prompt(self, config: MinuteConfig) -> str:
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

EXEMPLO DE REFERÊNCIA DE QUALIDADE E ESTILO ESPERADO PARA O CAMPO 'body':
"Aos vinte e oito dias de setembro de dois mil e vinte e seis, reuniram-se nas dependências da Câmara Municipal de Vereadores de Exemplo os integrantes da 10ª Legislatura para a realização da Sessão Plenária Ordinária de número quarenta. Havendo quórum regimental e, invocando a proteção de Deus, o Senhor Presidente Carlos Eduardo da Silva declarou aberta a sessão as dezoito horas e sete minutos. Na sequência, o Senhor Secretário realizou a primeira chamada, a qual acusou a presença dos vereadores: Carlos Eduardo da Silva e Ana Paula Oliveira (PARTIDO A); Marcos Vinícius Santos e Helena Costa Ferreira (PARTIDO B); e Roberto Almeida Souza (PARTIDO C). Em seguida, procedeu-se à leitura da mensagem do dia, sendo conduzida a oração do "Pai Nosso". Logo após, foram lidas as correspondências, destacando-se o Ofício Circular nº 46/2026 do Tribunal de Contas e o Ofício nº 134/2026 do Executivo, encaminhando o Projeto de Lei nº 49/2026. A Presidência informou que a Ata nº 20 foi enviada previamente aos vereadores e considerada tacitamente aprovada. No espaço do pequeno expediente, três vereadores fizeram uso da palavra... Ingressando na ordem do dia, foi apreciado o Projeto de Lei nº 48/2026... Após a leitura dos pareceres favoráveis das comissões, o projeto foi colocado em votação e aprovado por unanimidade... Esgotadas as matérias, a sessão foi encerrada as dezenove horas e dezoito minutos, sendo a próxima sessão ordinária convocada para o dia treze de outubro de dois mil e vinte e seis (terça-feira), às dezoito horas."
"""
        return prompt

    def _wait_for_file_active(self, uploaded_file: Any, timeout_seconds: int = 180) -> Any:
        """Aguarda até que um arquivo de áudio/vídeo enviado à Gemini File API fique no estado ACTIVE."""
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

    def _generate_structured_with_fallback(
        self, contents: List[Any], system_instruction: str
    ) -> Tuple[AIGeneratedPayload, str]:
        """Executa generate_content com Structured JSON Schema e fallback automático de modelos."""
        models_to_try = [self.model_name] + [m for m in self.FALLBACK_MODELS if m != self.model_name]
        last_error: Optional[Exception] = None

        for model in models_to_try:
            try:
                response = self.client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        temperature=0.2,
                        response_mime_type="application/json",
                        response_schema=AIGeneratedPayload,
                    ),
                )
                raw_text = (response.text or "").strip()
                parsed = self._parse_ai_payload(raw_text)
                return (parsed, model)
            except Exception as e:
                last_error = e
                err_str = str(e)
                if any(code in err_str for code in ("503", "429", "UNAVAILABLE", "404", "NOT_FOUND")):
                    continue
                raise e

        raise RuntimeError(f"Falha ao gerar conteúdo após tentar múltiplos modelos Gemini: {last_error}")

    @staticmethod
    def _parse_ai_payload(raw_text: str) -> AIGeneratedPayload:
        """Converte o JSON retornado pelo Gemini em AIGeneratedPayload com fallback seguro."""
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
        try:
            data = json.loads(cleaned)
            if isinstance(data, dict) and "body" in data:
                return AIGeneratedPayload(**data)
        except Exception:
            pass
        # Fallback caso o modelo retorne texto puro
        return AIGeneratedPayload(body=raw_text)

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

        # Captura da transcrição para reuso por ferramentas de imprensa e redes sociais (tNuntia)
        raw_transcript_text: Optional[str] = None
        if mode == "captions" and isinstance(content, str):
            raw_transcript_text = content
        elif meta.get("auxiliary_captions"):
            raw_transcript_text = meta.get("auxiliary_captions")
        else:
            video_id = meta.get("video_id")
            if video_id:
                raw_transcript_text = VideoCache.get_transcript(video_id)

        # 2. Monta o prompt do sistema
        system_instruction = self._build_system_prompt(config)

        # 3. Informações contextuais fornecidas pelo usuário + metadados automáticos do YouTube
        context_details = []
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

        try:
            if mode == "captions":
                user_content = [f"{user_prompt_header}\nTRANSCRIÇÃO DA SESSÃO:\n{content}"]
                ai_payload, used_model = self._generate_structured_with_fallback(user_content, system_instruction)

            elif mode == "youtube_direct":
                yt_part = types.Part.from_uri(file_uri=content, mime_type="video/mp4")
                aux_captions = meta.get("auxiliary_captions", "")
                prompt_parts: List[Any] = [yt_part, user_prompt_header]
                if aux_captions:
                    prompt_parts.append(f"\nTRANSCRIÇÃO AUXILIAR DE APOIO:\n{aux_captions[:50000]}")
                try:
                    ai_payload, used_model = self._generate_structured_with_fallback(prompt_parts, system_instruction)
                except Exception:
                    audio_path = ContentIngestor.download_audio_stream(content)
                    if not audio_path:
                        raise
                    mode = "audio"
                    content = audio_path
                    meta["method"] = "audio_download_fallback"
                    uploaded_file = self.client.files.upload(file=content)
                    uploaded_file = self._wait_for_file_active(uploaded_file)
                    ai_payload, used_model = self._generate_structured_with_fallback(
                        [user_prompt_header, uploaded_file], system_instruction
                    )

            else:
                uploaded_file = self.client.files.upload(file=content)
                uploaded_file = self._wait_for_file_active(uploaded_file)
                user_content = [user_prompt_header, uploaded_file]
                ai_payload, used_model = self._generate_structured_with_fallback(user_content, system_instruction)

        finally:
            if uploaded_file and hasattr(uploaded_file, "name"):
                try:
                    self.client.files.delete(name=uploaded_file.name)
                except Exception:
                    pass
            if mode == "audio" and os.path.exists(content):
                try:
                    os.remove(content)
                except Exception:
                    pass

        # 4. Pós-processamento Determinístico
        processed_body, corrections = TextNormalizer.validate_and_normalize(
            text=ai_payload.body,
            single_paragraph=config.single_paragraph,
            paragraph_count=config.paragraph_count,
            numbers_in_words=config.numbers_in_words,
            allow_abbreviations=config.allow_abbreviations,
        )

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
        system_instruction = (
            "Você é um redator legislativo e cartorial sênior. Reescreva APENAS o trecho fornecido pelo usuário "
            "seguindo a instrução solicitada, mantendo tom estritamente impessoal, em terceira pessoa e formal. "
            "Retorne exclusivamente o trecho reescrito, sem aspas e sem comentários adicionais."
        )
        prompt = (
            f"INSTRUÇÃO DE REESCRITA: {request.instruction}\n\n"
            f"TRECHO ORIGINAL:\n{request.snippet}"
        )

        models_to_try = [self.model_name] + [m for m in self.FALLBACK_MODELS if m != self.model_name]
        refined_raw = request.snippet
        for model in models_to_try:
            try:
                res = self.client.models.generate_content(
                    model=model,
                    contents=[prompt],
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        temperature=0.25,
                    ),
                )
                refined_raw = (res.text or request.snippet).strip()
                break
            except Exception as e:
                if any(code in str(e) for code in ("503", "429", "UNAVAILABLE", "404")):
                    continue
                raise e

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
