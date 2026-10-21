"""
=============================================================================
Modelos de Dados e Esquemas de Validação (Pydantic v2) - tVerbum
=============================================================================
Este módulo define todos os contratos de entrada e saída utilizados pelo tVerbum,
incluindo:
- Configuração de parâmetros da ata (`MinuteConfig`);
- Perfis institucionais reutilizáveis (`OrganizationPreset`);
- Estruturas extraídas para integração com bancos de dados externos (`ExtractedSessionData`);
- Alertas de revisão humana (`ReviewWarning`) e linha do tempo do vídeo (`TimelineEntry`);
- Payloads das rotas de validação cartorial e reescrita assistida.
"""

from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field, model_validator


class OrganizationPreset(BaseModel):
    """
    Representa um perfil institucional pré-configurado (ex: dados fixos de uma Câmara Municipal).
    Evita a necessidade de reenviar cabeçalho, lista de vereadores e partidos a cada requisição.
    """

    id: str = Field(..., description="Identificador único do perfil (ex: 'camara_exemplo')")
    name: str = Field(..., description="Nome amigável de exibição do perfil")
    organization_name: str = Field(default="", description="Nome oficial da instituição para o cabeçalho")
    state: str = Field(default="", description="Estado da federação (ex: 'ESTADO DO RIO GRANDE DO SUL')")
    organization_details: str = Field(default="", description="Endereço, site, e-mail e CEP institucionais")
    legislature: str = Field(default="", description="Identificação da Legislatura atual (ex: '9ª Legislatura')")
    president_name: str = Field(default="", description="Nome do Presidente da Mesa Diretora")
    secretary_name: str = Field(default="", description="Nome do Secretário da Mesa Diretora")
    known_attendees: List[str] = Field(
        default_factory=list,
        description="Lista nominal de vereadores/membros com seus respectivos partidos para ancoragem de grafia",
    )
    logo_url: Optional[str] = Field(default=None, description="Caminho, URL ou Data-URI Base64 do brasão oficial")


class VotedMatter(BaseModel):
    """
    Representa uma proposição ou matéria apreciada, votada ou lida na Ordem do Dia.
    Útil para popular tabelas de tramitação/votação no sistema principal.
    """

    matter: str = Field(..., description="Identificação da matéria (ex: 'Projeto de Lei nº 48/2026')")
    author: str = Field(default="", description="Autoria da proposição (ex: 'Poder Executivo', 'Vereadora Ana Paula')")
    summary: str = Field(default="", description="Breve ementa ou assunto da matéria")
    result: str = Field(default="", description="Resultado da deliberação (ex: 'Aprovado por unanimidade', 'Encaminhado às comissões')")


class ReviewWarning(BaseModel):
    """
    Alerta gerado pela IA apontando um trecho específico do texto que merece conferência
    visual rápida pelo secretário/revisor humano (ex: nomes próprios incomuns ou números falados rápido).
    """

    term: str = Field(..., description="Trecho ou termo exato presente no texto da ata que deve ser destacado")
    timestamp: str = Field(default="", description="Minuto aproximado no vídeo do YouTube (ex: '00:18:40')")
    reason: str = Field(..., description="Motivo da recomendação de conferência")


class TimelineEntry(BaseModel):
    """
    Marcador temporal da sessão plenária para permitir que o revisor clique e ouça
    exatamente o momento correspondente no vídeo do YouTube.
    """

    phase: str = Field(..., description="Fase da sessão ou nome do orador (ex: 'Pequeno Expediente - Ver. Marcos Santos')")
    timestamp: str = Field(..., description="Tempo no vídeo no formato HH:MM:SS ou MM:SS")
    summary: str = Field(..., description="Resumo curto do que ocorreu neste momento")
    speaker: Optional[str] = Field(default="", description="Nome do orador ou participante da fala")
    end_timestamp: Optional[str] = Field(default="", description="Timestamp aproximado de término (HH:MM:SS ou MM:SS)")
    category: Optional[str] = Field(default="", description="Categoria temática (ex: 'Pequeno Expediente', 'Ordem do Dia', 'Votação', 'Explicações Pessoais')")


class ExtractedSessionData(BaseModel):
    """
    Dados estruturados extraídos automaticamente da sessão pela IA.
    Prontos para serem salvos diretamente em colunas do banco de dados do sistema web consumidor.
    """

    session_date_iso: Optional[str] = Field(default=None, description="Data da sessão no formato ISO YYYY-MM-DD")
    start_time: Optional[str] = Field(default=None, description="Horário de abertura no formato HH:MM")
    end_time: Optional[str] = Field(default=None, description="Horário de encerramento no formato HH:MM")
    next_session_info: Optional[str] = Field(default=None, description="Data e horário da próxima sessão convocada")
    president: Optional[str] = Field(default=None, description="Nome do Presidente que conduziu a sessão")
    secretary: Optional[str] = Field(default=None, description="Nome do Secretário da sessão")
    present_members: List[str] = Field(default_factory=list, description="Lista nominal dos vereadores/membros presentes")
    absent_members: List[str] = Field(default_factory=list, description="Lista nominal dos ausentes")
    voted_matters: List[VotedMatter] = Field(default_factory=list, description="Lista estruturada de matérias da Ordem do Dia")


class AIGeneratedPayload(BaseModel):
    """
    Esquema de resposta estruturada (Structured Output) exigido diretamente da API Google Gemini.
    Garante que o modelo devolva em uma única chamada o texto da ata e os metadados auxiliares.
    """

    body: str = Field(..., description="O texto formal do corpo da ata seguindo todas as regras estilísticas")
    extracted_data: ExtractedSessionData = Field(default_factory=ExtractedSessionData)
    review_warnings: List[ReviewWarning] = Field(default_factory=list)
    timeline_map: List[TimelineEntry] = Field(default_factory=list)


class MinuteConfig(BaseModel):
    """
    Parâmetros de configuração enviados pelo usuário ou sistema externo para gerar uma ata.
    """

    # --- Parâmetros Obrigatórios ---
    video_url: str = Field(
        ...,
        description="URL do vídeo da sessão no YouTube (suporta links watch, youtu.be, live e embed)",
    )
    session_number: str = Field(
        ...,
        description="Identificação formal da ata/sessão (ex: '21/2026')",
    )

    # --- Perfil Institucional Opcional ---
    preset_id: Optional[str] = Field(
        default=None,
        description="ID de um perfil salvo (ex: 'camara_exemplo') para autopreencher dados fixos da Câmara",
    )

    # --- Metadados Institucionais ---
    session_type: str = Field(
        default="Sessão Ordinária",
        description="Tipo da sessão ('Sessão Ordinária', 'Sessão Extraordinária', 'Sessão Solene', 'Audiência Pública')",
    )
    organization_name: str = Field(
        default="",
        description="Nome da entidade, câmara ou conselho (ex: 'Câmara Municipal de Exemplo')",
    )
    state: str = Field(
        default="",
        description="Estado da federação (ex: 'ESTADO DO RIO GRANDE DO SUL')",
    )
    organization_details: str = Field(
        default="",
        description="Dados de contato do cabeçalho oficial (site, e-mail, endereço, CEP)",
    )
    logo_url: Optional[str] = Field(
        default=None,
        description="Caminho local, URL ou Base64 do brasão/logotipo para inclusão no PDF e DOCX",
    )
    legislature: str = Field(
        default="",
        description="Número da Legislatura (ex: '9ª Legislatura') se aplicável",
    )
    plenary_session_number: str = Field(
        default="",
        description="Número cardinal/ordinal da sessão plenária (ex: 'número quarenta') se diferente do número da ata",
    )
    president_name: str = Field(
        default="",
        description="Nome do Presidente da sessão",
    )
    secretary_name: str = Field(
        default="",
        description="Nome do Secretário da sessão",
    )

    # --- Regras Estilísticas e Cartoriais ---
    single_paragraph: bool = Field(
        default=True,
        description="Se True, gera o texto em parágrafo único e contínuo (padrão legal para evitar espaços em branco/fraudes)",
    )
    paragraph_count: int = Field(
        default=1,
        ge=1,
        le=30,
        description="Quantidade exata de parágrafos desejada para o corpo da ata (1 = parágrafo único contínuo)",
    )
    numbers_in_words: bool = Field(
        default=True,
        description="Se True, escreve datas, horários, quantidades e numerais estritamente por extenso",
    )
    allow_abbreviations: bool = Field(
        default=False,
        description="Se False, expande termos e siglas de cargos/leis para sua forma completa (ex: 'Vereador' em vez de 'Ver.')",
    )
    detail_level: Literal["resumido", "padrao", "detalhado"] = Field(
        default="padrao",
        description="Nível de detalhe no registro dos discursos: 'resumido', 'padrao' ou 'detalhado'",
    )
    ingestion_mode: Literal["auto", "youtube_direct", "captions", "audio"] = Field(
        default="auto",
        description=(
            "Estratégia de leitura do vídeo: "
            "'auto' (usa cache/legendas + metadados ou vídeo direto), "
            "'youtube_direct' (envia URL do YouTube nativamente ao Gemini multimodal), "
            "'captions' (extrai transcrição de legendas), "
            "'audio' (baixa stream de áudio leve e envia à Gemini File API)"
        ),
    )
    bypass_cache: bool = Field(
        default=False,
        description="Se True, ignora o cache local SQLite e força nova consulta ao YouTube",
    )
    include_raw_transcript: bool = Field(
        default=False,
        description="Se True, inclui a transcrição integral no MinuteOutput para reuso por ferramentas de imprensa e redes sociais",
    )

    # --- Estrutura Documental ---
    include_header: bool = Field(
        default=True,
        description="Se True, inclui cabeçalho institucional e título formal no texto consolidado",
    )
    include_signatures: bool = Field(
        default=True,
        description="Se True, inclui linhas de assinatura para Secretário e Presidente",
    )

    # --- Contexto Extra e Ancoragem Anti-Alucinação ---
    known_attendees: List[str] = Field(
        default_factory=list,
        description="Lista de nomes conhecidos de vereadores/participantes e partidos (ex: 'Carlos Eduardo da Silva (PARTIDO A)')",
    )
    agenda_context: Optional[str] = Field(
        default=None,
        description="Texto com a Pauta/Ordem do Dia/Ofícios para garantir precisão absoluta nos números de leis",
    )
    custom_instructions: Optional[str] = Field(
        default=None,
        description="Instruções livres adicionais para calibrar a geração",
    )

    @model_validator(mode="after")
    def sync_paragraph_settings(self) -> "MinuteConfig":
        """Mantém coerência entre a flag booleana `single_paragraph` e o inteiro `paragraph_count`."""
        if not self.single_paragraph and self.paragraph_count == 1:
            self.paragraph_count = 5
        elif self.paragraph_count > 1:
            self.single_paragraph = False
        return self


class MinuteOutput(BaseModel):
    """
    Objeto JSON final retornado pelo tVerbum contendo o texto da ata e todos os dados
    estruturados para uso pelo sistema web principal.
    """

    id: str = Field(default="", description="UUID único da ata gerada")
    title: str = Field(description="Título formal da ata (ex: 'ATA Nº 21/2026 - SESSÃO ORDINÁRIA')")
    header: Optional[str] = Field(default=None, description="Cabeçalho institucional formatado")
    organization_name: str = Field(default="", description="Nome da organização")
    state: str = Field(default="", description="Estado da federação")
    organization_details: str = Field(default="", description="Contatos e endereço do cabeçalho")
    logo_url: Optional[str] = Field(default=None, description="Logo/Brasão utilizado")
    body: str = Field(description="Texto principal do corpo da ata (pronto para revisão e banco de dados)")
    signatures: Optional[str] = Field(default=None, description="Bloco de assinaturas")
    full_text: str = Field(description="Texto consolidado completo (cabeçalho + corpo + assinaturas)")
    raw_transcript: Optional[str] = Field(
        default=None,
        description="Transcrição completa e cronológica da sessão extraída do YouTube",
    )

    # --- Dados Estruturados para Integração com Sistemas Externos ---
    extracted_data: ExtractedSessionData = Field(
        default_factory=ExtractedSessionData,
        description="Metadados estruturados extraídos da sessão (data ISO, horários, presentes, matérias votadas)",
    )
    review_warnings: List[ReviewWarning] = Field(
        default_factory=list,
        description="Termos recomendados para conferência humana rápida",
    )
    timeline_map: List[TimelineEntry] = Field(
        default_factory=list,
        description="Mapa de timestamps da sessão para sincronização com o vídeo do YouTube",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Estatísticas técnicas: método de ingestão, cache_hit, contagem de palavras, modelo utilizado",
    )


class ExportRequest(BaseModel):
    """Payload enviado à rota `/api/export` para converter uma ata (mesmo após edição na tela) em arquivo físico."""

    format: Literal["docx", "pdf", "txt", "md"] = Field(default="docx")
    minute: MinuteOutput


class ValidateTextRequest(BaseModel):
    """Payload enviado à rota `/api/validate-text` para normalizar um texto editado manualmente pelo usuário."""

    text: str = Field(..., description="Texto do corpo da ata editado pelo usuário")
    single_paragraph: bool = Field(default=True, description="Se deve forçar parágrafo único contínuo")
    paragraph_count: int = Field(default=1, ge=1, le=30)
    numbers_in_words: bool = Field(default=True, description="Se deve converter horas e datas para extenso")
    allow_abbreviations: bool = Field(default=False, description="Se permite abreviações como Ver., PL, Art.")


class ValidateTextResponse(BaseModel):
    """Resposta do validador cartorial indicando o texto normalizado e quais correções foram aplicadas."""

    original_text: str
    normalized_text: str
    is_compliant: bool = Field(description="True se o texto enviado já estava 100% dentro das regras")
    corrections_applied: List[str] = Field(
        default_factory=list,
        description="Lista descritiva das correções automáticas realizadas",
    )
    word_count: int = 0
    character_count: int = 0


class RefineSnippetRequest(BaseModel):
    """Payload enviado à rota `/api/refine-snippet` para reescrever apenas uma frase selecionada no editor."""

    snippet: str = Field(..., description="Trecho selecionado pelo usuário")
    instruction: str = Field(
        default="Tornar mais formal, impessoal e no padrão legislativo cartorial, mantendo números por extenso.",
        description="Instrução de reescrita (ex: 'Resumir', 'Formalizar', 'Expandir')",
    )
    numbers_in_words: bool = Field(default=True)
    allow_abbreviations: bool = Field(default=False)


class RefineSnippetResponse(BaseModel):
    """Resposta contendo o trecho original e o trecho reescrito pela IA."""

    original_snippet: str
    refined_snippet: str
