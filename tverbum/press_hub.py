"""
Hub de Comunicação, Imprensa e Multi-Publicação (Sinergia tVerbum + tNuntia).
Transforma atas formais consolidadas em múltiplos subprodutos legislativos:
1. Releases jornalísticos com manchete, lide e corpo para o Portal da Câmara;
2. Resumo Cidadão em linguagem simples para Transparência Pública e listas de WhatsApp;
3. Dossiês parlamentares individualizados por vereador para prestação de contas;
4. Exportação em padrão LexML Brasil (XML) e formato para Diário Oficial dos Municípios.
"""

import re
import html
from typing import Dict, List, Optional, Any
from .models import (
    MinuteOutput,
    VotedMatter,
    TimelineEntry,
)


class PressHub:
    """Central de geração de produtos de imprensa e comunicação a partir de atas."""

    @classmethod
    def generate_news_release(
        cls,
        minute: MinuteOutput,
        tone: str = "jornalistico",
    ) -> Dict[str, Any]:
        """Gera matéria jornalística com manchete, lide e destaques para o portal institucional."""
        ext = minute.extracted_data
        matters = ext.voted_matters or []
        org = minute.organization_name or "Câmara Municipal"
        date_str = ext.session_date_iso or "recente sessão"

        # Identifica deliberação mais relevante para manchete
        lead_matter = matters[0].matter if matters else "pauta legislativa"
        lead_summary = matters[0].summary if matters else "deliberações de interesse público"

        headline = f"{org} delibera sobre {lead_matter} em {date_str}"
        subtitle = f"Sessão Plenária contou com aprovação de matérias estratégicas e manifestações na tribuna"

        lead_p = (
            f"Em sessão realizada em {date_str}, a {org} reuniu seus parlamentares sob a presidência de "
            f"{ext.president or 'sua Mesa Diretora'} para a apreciação da ordem do dia. "
            f"Ao todo, {(ext.present_members or []) and len(ext.present_members)} parlamentares participaram dos debates "
            f"que resultaram em deliberações sobre {lead_summary}."
        )

        body_paragraphs = [lead_p]

        if matters:
            matters_text = "Dentre as principais proposições deliberadas pelo plenário, destacam-se: " + "; ".join(
                f"{m.matter} ({m.result or 'Aprovado'}), de autoria de {m.author or 'autoria legislativa'}, que {m.summary}"
                for m in matters[:5]
            ) + "."
            body_paragraphs.append(matters_text)

        # Destaca pronunciamentos da tribuna
        spk_times = getattr(ext, "speaker_time_summary", {}) or {}
        if spk_times:
            speakers_p = (
                f"No espaço destinado aos pronunciamentos parlamentares, fizeram uso da palavra os vereadores: "
                f"{', '.join(spk_times.keys())}, que abordaram temas relacionados à infraestrutura local, "
                f"saúde, serviços públicos e demandas das diversas comunidades do município."
            )
            body_paragraphs.append(speakers_p)

        closing_p = (
            f"A íntegra das deliberações, documentos oficiais e registros em vídeo encontram-se disponíveis "
            f"para consulta da população nos canais oficiais de transparência da instituição."
        )
        body_paragraphs.append(closing_p)

        highlights = [
            f"Apreciação de {len(matters)} proposição(ões) na Ordem do Dia;"
            if matters else "Realização de sessão plenária ordinária com deliberações legislativas;",
            f"Presença de {len(ext.present_members)} parlamentares em plenário;"
            if ext.present_members else "Quórum regimental verificado;",
            f"Uso da tribuna por {len(spk_times)} orador(es)."
            if spk_times else "Manifestações regimentais registradas.",
        ]

        tags = [
            "Poder Legislativo",
            org,
            "Sessão Plenária",
            "Ordem do Dia",
            "Transparência Pública",
        ]

        return {
            "headline": headline,
            "subtitle": subtitle,
            "lead": lead_p,
            "body_paragraphs": body_paragraphs,
            "full_article_text": "\n\n".join(body_paragraphs),
            "highlights": highlights,
            "suggested_tags": tags,
        }

    @classmethod
    def generate_citizen_summary(cls, minute: MinuteOutput) -> Dict[str, Any]:
        """Gera resumo em linguagem cidadã e acessível, com versão especial para WhatsApp."""
        ext = minute.extracted_data
        matters = ext.voted_matters or []
        org = minute.organization_name or "Câmara Municipal"
        date_str = ext.session_date_iso or "sessão recente"

        approved = []
        for m in matters:
            res_low = (m.result or "").lower()
            if "aprovad" in res_low or not res_low:
                approved.append(f"• *{m.matter}*: {m.summary or 'Proposição aprovada em plenário.'}")

        quick_take = (
            f"Na sessão de {date_str}, os vereadores da {org} aprovaram matérias importantes "
            f"para o município e cobraram melhorias para os bairros e serviços essenciais."
        )

        # Formato dinâmico especial para WhatsApp
        wa_lines = [
            f"🏛️ *RESUMO CIDADÃO - {org.upper()}*",
            f"📅 *Data:* {date_str}",
            "",
            "🔍 *O que aconteceu de mais importante na sessão de hoje:*",
            quick_take,
            "",
        ]

        if approved:
            wa_lines.append("✅ *O QUE FOI APROVADO:*")
            wa_lines.extend(approved[:6])
            wa_lines.append("")

        spk_times = getattr(ext, "speaker_time_summary", {}) or {}
        if spk_times:
            wa_lines.append("🎙️ *VEREADORES QUE USARAM A TRIBUNA:*")
            for spk, tm in spk_times.items():
                wa_lines.append(f"• {spk} ({tm})")
            wa_lines.append("")

        wa_lines.append("📲 *Compartilhe com a comunidade e acompanhe os trabalhos da sua cidade!*")
        whatsapp_formatted = "\n".join(wa_lines)

        return {
            "title": f"Resumo Cidadão: O que foi decidido na Câmara em {date_str}",
            "quick_take": quick_take,
            "approved_matters": approved,
            "neighborhood_requests": [
                f"Cobranças de infraestrutura e serviços nos bairros registradas na tribuna por {len(spk_times)} vereador(es)."
            ] if spk_times else ["Acompanhamento das demandas da comunidade."],
            "whatsapp_formatted": whatsapp_formatted,
        }

    @classmethod
    def generate_councilor_dossier(
        cls,
        minute: MinuteOutput,
        councilor_name: str,
    ) -> Dict[str, Any]:
        """Gera dossiê das atividades de um parlamentar específico na sessão gravada."""
        ext = minute.extracted_data
        spk_times = getattr(ext, "speaker_time_summary", {}) or {}
        timeline = minute.timeline_map or []
        clean_target = councilor_name.strip().lower()

        # Localiza tempo de fala
        matched_speaking_time = None
        for spk, tm in spk_times.items():
            if clean_target in spk.lower() or spk.lower() in clean_target:
                matched_speaking_time = tm
                break

        # Localiza trechos na timeline
        matched_timeline = []
        for entry in timeline:
            spk = (entry.speaker or "").lower()
            if clean_target in spk or clean_target in entry.phase.lower():
                matched_timeline.append(f"[{entry.timestamp}] {entry.phase}: {entry.summary}")

        # Localiza matérias de sua autoria
        authored_matters = []
        for m in ext.voted_matters:
            if clean_target in (m.author or "").lower():
                authored_matters.append(f"{m.matter} - {m.summary} ({m.result})")

        # Texto para redes sociais do vereador
        caption_lines = [
            f"🏛️ *TRABALHO PARLAMENTAR NA CÂMARA MUNICIPAL*",
            f"Hoje na Sessão Plenária defendi os interesses da nossa comunidade na tribuna.",
        ]
        if matched_speaking_time:
            caption_lines.append(f"⏱️ Tempo de fala: {matched_speaking_time}")
        if authored_matters:
            caption_lines.append(f"📋 Matéria em destaque: {authored_matters[0]}")
        caption_lines.append("\nSeguimos firmes fiscalizando e propondo melhorias para o nosso povo! 💪")
        caption_lines.append("#TrabalhoPeloPovo #MandatoAtuante #Transparência #PoderLegislativo")

        return {
            "councilor_name": councilor_name,
            "speaking_time": matched_speaking_time or "Não registrado na tribuna",
            "timeline_entries": matched_timeline,
            "authored_matters": authored_matters,
            "social_media_caption": "\n".join(caption_lines),
        }

    @classmethod
    def export_lexml(cls, minute: MinuteOutput) -> str:
        """Exporta os metadados e o conteúdo da ata no padrão nacional LexML Brasil (XML)."""
        ext = minute.extracted_data
        doc_id = minute.id or "ata-plenaria"
        org = html.escape(minute.organization_name or "Camara Municipal")
        state = html.escape(minute.state or "Brasil")
        title = html.escape(minute.title or "Ata de Sessao")
        date_iso = ext.session_date_iso or "2026-01-01"
        hash_val = html.escape(minute.integrity_hash or "")
        status_val = html.escape(minute.document_status or "rascunho_ia")

        matters_xml = []
        for m in ext.voted_matters:
            matters_xml.append(
                f'        <ItemProposicao id="{html.escape(m.matter)}">\n'
                f"          <Identificacao>{html.escape(m.matter)}</Identificacao>\n"
                f"          <Autor>{html.escape(m.author or '')}</Autor>\n"
                f"          <Ementa>{html.escape(m.summary or '')}</Ementa>\n"
                f"          <Resultado>{html.escape(m.result or '')}</Resultado>\n"
                f"        </ItemProposicao>"
            )
        matters_block = "\n".join(matters_xml) if matters_xml else "        <!-- Nenhuma proposição -->"

        xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<LexML xmlns="http://www.lexml.gov.br/1.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://www.lexml.gov.br/1.0 http://projeto.lexml.gov.br/esquemas/lexml.xsd">
  <Documento id="{doc_id}">
    <Metadados>
      <Identificacao>
        <Autoridade>{org}</Autoridade>
        <Localidade>{state}</Localidade>
        <TipoDocumento>Ata de Sessão Plenária</TipoDocumento>
        <DataSessao>{date_iso}</DataSessao>
        <Presidente>{html.escape(ext.president or '')}</Presidente>
        <Secretario>{html.escape(ext.secretary or '')}</Secretario>
      </Identificacao>
      <IntegridadeCartorial>
        <ChaveSHA256>{hash_val}</ChaveSHA256>
        <StatusDocumental>{status_val}</StatusDocumental>
        <LegislacaoReferencia>Lei Federal 14.063/2020</LegislacaoReferencia>
      </IntegridadeCartorial>
    </Metadados>
    <Conteudo>
      <Titulo>{title}</Titulo>
      <TextoIntegral>
<![CDATA[
{minute.full_text}
]]>
      </TextoIntegral>
      <DeliberacoesOrdemDoDia>
{matters_block}
      </DeliberacoesOrdemDoDia>
    </Conteudo>
  </Documento>
</LexML>"""
        return xml_content.strip()

    @classmethod
    def export_official_gazette(cls, minute: MinuteOutput) -> str:
        """Gera versão diagramada e padronizada para diagramação em Diário Oficial dos Municípios."""
        org = (minute.organization_name or "CÂMARA MUNICIPAL").upper()
        state = (minute.state or "").upper()
        status_label = minute.document_status.replace("_", " ").upper()
        doc_hash = minute.integrity_hash or "NÃO ASSINADO"

        lines = [
            "================================================================================",
            f"{org}",
            f"{state}" if state else "",
            f"{minute.title.upper()}",
            "================================================================================",
            "",
            minute.body,
            "",
            "--------------------------------------------------------------------------------",
            "MESA DIRETORA:",
            f"Presidente: {minute.extracted_data.president or 'Registrado em Ata'}",
            f"Secretário: {minute.extracted_data.secretary or 'Registrado em Ata'}",
            "",
            "AUTENTICIDADE E VALIDAÇÃO ELETRÔNICA (LEI Nº 14.063/2020):",
            f"Status Documental: {status_label}",
            f"Código Verificador SHA-256: {doc_hash}",
            "================================================================================",
        ]
        return "\n".join(l for l in lines if l is not None)
