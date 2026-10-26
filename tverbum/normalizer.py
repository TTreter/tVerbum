"""
Normalizador determinístico de texto, validador (linter) cartorial e regras estilísticas formais para atas (tVerbum v2.0).
Inclui sanitização de markdown, conversão de moedas, percentuais, ordinais e numerais para extenso.
"""

import re
from typing import List, Tuple, Optional
from num2words import num2words
from .name_library import NameLibraryManager


class TextNormalizer:
    """Aplica regras determinísticas de formatação, números por extenso, abreviações e parágrafos."""

    # Dicionário de abreviações e siglas comuns em ambientes legislativos
    ABBREVIATIONS = {
        r"\bVer\.(?=\s|$|[,\.;:])": "Vereador",
        r"\bVera\.(?=\s|$|[,\.;:])": "Vereadora",
        r"\bVers\.(?=\s|$|[,\.;:])": "Vereadores",
        r"\bPres\.(?=\s|$|[,\.;:])": "Presidente",
        r"\bSec\.(?=\s|$|[,\.;:])": "Secretário",
        r"\bSecr\.(?=\s|$|[,\.;:])": "Secretário",
        r"\bSr\.(?=\s|$|[,\.;:])": "Senhor",
        r"\bSra\.(?=\s|$|[,\.;:])": "Senhora",
        r"\bExmo\.(?=\s|$|[,\.;:])": "Excelentíssimo",
        r"\bExma\.(?=\s|$|[,\.;:])": "Excelentíssima",
        r"\bPL\b": "Projeto de Lei",
        r"\bPLC\b": "Projeto de Lei Complementar",
        r"\bPDL\b": "Projeto de Decreto Legislativo",
        r"\bPEC\b": "Proposta de Emenda à Constituição",
        r"\bArt\.(?=\s|$|[,\.;:])": "Artigo",
        r"\barts\.(?=\s|$|[,\.;:])": "artigos",
        r"\bOf\.(?=\s|$|[,\.;:])": "Ofício",
        r"\bInd\.(?=\s|$|[,\.;:])": "Indicação",
        r"\bReq\.(?=\s|$|[,\.;:])": "Requerimento",
        r"\bRequer\.(?=\s|$|[,\.;:])": "Requerimento",
    }

    MONTHS = {
        1: "janeiro", 2: "fevereiro", 3: "março", 4: "abril",
        5: "maio", 6: "junho", 7: "julho", 8: "agosto",
        9: "setembro", 10: "outubro", 11: "novembro", 12: "dezembro"
    }

    HOUR_WORDS = {
        0: "zero",
        1: "uma",
        2: "duas",
        21: "vinte e uma",
        22: "vinte e duas",
    }

    @classmethod
    def sanitize_markdown_artifacts(cls, text: str) -> str:
        """
        Sanitiza marcadores de formatação Markdown (#, **, *, __, _, -, •)
        que modelos de linguagem menores possam inadvertidamente incluir no corpo da ata.
        """
        if not text:
            return ""

        # Remove blocos de código
        text = re.sub(r"```[a-zA-Z]*\n?([\s\S]*?)```", r"\1", text)
        text = re.sub(r"`([^`]+)`", r"\1", text)

        # Remove marcadores de cabeçalho Markdown (#, ##, ###) no início da linha
        text = re.sub(r"(?m)^#{1,6}\s*", "", text)

        # Remove negrito e itálico (**texto**, *texto*, __texto__)
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
        text = re.sub(r"\*([^*]+)\*", r"\1", text)
        text = re.sub(r"__([^_]+)__", r"\1", text)
        text = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"\1", text)

        # Remove marcadores de citação (> texto)
        text = re.sub(r"(?m)^>\s*", "", text)

        # Remove marcadores de lista não ordenada (- item, * item, • item)
        text = re.sub(r"(?m)^\s*[-*•]\s+", "", text)

        return text

    @classmethod
    def expand_abbreviations(cls, text: str) -> str:
        """Expande abreviações comuns para sua forma plena."""
        for pattern, replacement in cls.ABBREVIATIONS.items():
            text = re.sub(pattern, replacement, text)
        return text

    @classmethod
    def enforce_single_paragraph(cls, text: str) -> str:
        """
        Unifica o texto em um único parágrafo contínuo (padrão legal cartorial),
        removendo quebras de linha e normalizando espaçamentos.
        """
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return ""

        joined = " ".join(lines)
        joined = re.sub(r"\s{2,}", " ", joined)

        if joined and joined[-1] not in ".!?":
            joined += "."

        return joined

    @classmethod
    def enforce_paragraph_count(cls, text: str, target_count: int = 1) -> str:
        """
        Ajusta o texto para ter exatamente `target_count` parágrafos:
        - Se target_count <= 1: retorna parágrafo único contínuo.
        - Se target_count > 1: preserva ou redistribui as sentenças em `target_count` blocos coesos.
        """
        if target_count <= 1:
            return cls.enforce_single_paragraph(text)

        existing_paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\r?\n", text) if p.strip()]
        if len(existing_paragraphs) == target_count:
            return "\n\n".join(existing_paragraphs)

        single = cls.enforce_single_paragraph(text)
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ])", single) if s.strip()]
        if len(sentences) <= target_count:
            return "\n\n".join(sentences)

        result: List[str] = []
        chunk_size = len(sentences) / target_count
        for i in range(target_count):
            start = round(i * chunk_size)
            end = round((i + 1) * chunk_size)
            chunk = " ".join(sentences[start:end]).strip()
            if chunk:
                result.append(chunk)

        return "\n\n".join(result)

    @classmethod
    def _hour_to_words(cls, hours: int) -> str:
        if hours in cls.HOUR_WORDS:
            return cls.HOUR_WORDS[hours]
        return num2words(hours, lang="pt_BR")

    @classmethod
    def format_time_in_words(cls, hours: int, minutes: int = 0) -> str:
        """Converte horário em texto formal por extenso (respeitando gênero feminino para horas)."""
        try:
            h_text = cls._hour_to_words(hours)
            h_unit = "hora" if hours == 1 else "horas"
            if minutes == 0:
                return f"{h_text} {h_unit}"
            m_text = "um" if minutes == 1 else num2words(minutes, lang="pt_BR")
            m_unit = "minuto" if minutes == 1 else "minutos"
            return f"{h_text} {h_unit} e {m_text} {m_unit}"
        except Exception:
            return f"{hours:02d} horas e {minutes:02d} minutos"

    @classmethod
    def format_date_in_words(cls, day: int, month: int, year: int, prefix: bool = True) -> str:
        """Converte data para a fórmula clássica por extenso."""
        try:
            d_text = "primeiro" if day == 1 else num2words(day, lang="pt_BR")
            m_name = cls.MONTHS.get(month, "")
            y_text = num2words(year, lang="pt_BR")
            if prefix:
                if day == 1:
                    return f"Ao primeiro dia de {m_name} de {y_text}"
                return f"Aos {d_text} dias de {m_name} de {y_text}"
            return f"{d_text} de {m_name} de {y_text}"
        except Exception:
            return f"Aos {day} dias do mês {month} de {year}"

    @classmethod
    def apply_currency_in_words(cls, text: str) -> str:
        """
        Converte valores monetários em algarismos (ex: 'R$ 150.000,00', 'R$ 1,50') para extenso.
        """
        def _replace_currency(match: re.Match) -> str:
            raw_int = match.group(1).replace(".", "")
            val_int = int(raw_int)
            raw_cent = match.group(2)
            val_cent = int(raw_cent) if raw_cent else 0

            parts = []
            if val_int > 0:
                words_int = num2words(val_int, lang="pt_BR")
                unit = "real" if val_int == 1 else "reais"
                parts.append(f"{words_int} {unit}")

            if val_cent > 0:
                words_cent = num2words(val_cent, lang="pt_BR")
                unit_cent = "centavo" if val_cent == 1 else "centavos"
                parts.append(f"{words_cent} {unit_cent}")

            if not parts:
                return "zero real"
            return " e ".join(parts)

        return re.sub(r"\bR\$\s*(\d{1,3}(?:\.\d{3})*|\d+)(?:,(\d{2}))?\b", _replace_currency, text)

    @classmethod
    def apply_percentages_in_words(cls, text: str) -> str:
        """
        Converte percentuais (ex: '10%', '5,5%') para extenso.
        """
        def _replace_percent(match: re.Match) -> str:
            val_int = int(match.group(1))
            raw_dec = match.group(2)
            words_int = num2words(val_int, lang="pt_BR")
            if raw_dec:
                val_dec = int(raw_dec)
                words_dec = num2words(val_dec, lang="pt_BR")
                return f"{words_int} vírgula {words_dec} por cento"
            return f"{words_int} por cento"

        return re.sub(r"\b(\d+)(?:,(\d+))?\s*%", _replace_percent, text)

    @classmethod
    def apply_ordinals_in_words(cls, text: str) -> str:
        """
        Converte ordinais cardinais (ex: '1º Secretário', '10ª Legislatura', '40ª Sessão') para extenso,
        preservando números de Leis, Ofícios e Projetos ('nº 48/2026').
        """
        def _replace_ordinal(match: re.Match) -> str:
            num = int(match.group(1))
            gender_symbol = match.group(2)
            is_feminine = gender_symbol == "ª"

            ord_m = num2words(num, to="ordinal", lang="pt_BR")
            if is_feminine:
                ord_res = " ".join(w[:-1] + "a" if w.endswith(("o", "os")) else w for w in ord_m.split())
                return ord_res
            return ord_m

        pattern = r"(?<!nº\s)(?<!n°\s)(?<!n\.\s)(?<!n\.º\s)(?<!n\.°\s)\b(\d{1,3})\s*([ºª°])\b"
        return re.sub(pattern, _replace_ordinal, text, flags=re.IGNORECASE)

    @classmethod
    def apply_numbers_in_words(cls, text: str) -> str:
        """
        Pós-processador determinístico que converte horários, datas, moedas, percentuais e ordinais
        para a forma formal por extenso, preservando números de proposições legislativas ('nº 48/2026').
        """
        # 1. Horários no formato 18h07, 18h07min, 18:07
        def _replace_time_hm(match: re.Match) -> str:
            h = int(match.group(1))
            m = int(match.group(2))
            if 0 <= h <= 23 and 0 <= m <= 59:
                return cls.format_time_in_words(h, m)
            return match.group(0)

        text = re.sub(r"\b([01]?\d|2[0-3])\s*[h:]\s*([0-5]\d)(?:\s*min(?:utos)?)?\b", _replace_time_hm, text)

        # 2. Horários cheios no formato '18h' ou '18 horas'
        def _replace_time_h(match: re.Match) -> str:
            h = int(match.group(1))
            if 0 <= h <= 23:
                return cls.format_time_in_words(h, 0)
            return match.group(0)

        text = re.sub(r"\b([01]?\d|2[0-3])\s*(?:h\b|horas\b)", _replace_time_h, text)

        # 3. Moedas financeiras: 'R$ 150.000,00' -> 'cento e cinquenta mil reais'
        text = cls.apply_currency_in_words(text)

        # 4. Percentuais: '10%' -> 'dez por cento'
        text = cls.apply_percentages_in_words(text)

        # 5. Ordinais: '10ª Legislatura' -> 'décima Legislatura'
        text = cls.apply_ordinals_in_words(text)

        # 6. Datas no formato DD/MM/AAAA (não precedidas por 'nº')
        def _replace_date_slash(match: re.Match) -> str:
            d, m, y = int(match.group(1)), int(match.group(2)), int(match.group(3))
            if 1 <= d <= 31 and 1 <= m <= 12 and 1900 <= y <= 2100:
                return cls.format_date_in_words(d, m, y, prefix=False)
            return match.group(0)

        text = re.sub(r"(?<!nº\s)(?<!n\.º\s)\b(0?[1-9]|[12]\d|3[01])/(0?[1-9]|1[0-2])/(\d{4})\b", _replace_date_slash, text)

        # 7. Datas parciais 'dia 13 de outubro de 2026' -> 'dia treze de outubro de dois mil e vinte e seis'
        months_pattern = "|".join(cls.MONTHS.values())

        def _replace_written_date(match: re.Match) -> str:
            prefix_word = match.group(1)
            d = int(match.group(2))
            month_name = match.group(3)
            y = int(match.group(4))
            d_text = "primeiro" if d == 1 else num2words(d, lang="pt_BR")
            y_text = num2words(y, lang="pt_BR")
            return f"{prefix_word}{d_text} de {month_name} de {y_text}"

        text = re.sub(
            rf"\b(dia\s+|Aos\s+)(\d{{1,2}})\s+(?:dias\s+)?de\s+({months_pattern})\s+de\s+(\d{{4}})\b",
            _replace_written_date,
            text,
            flags=re.IGNORECASE,
        )

        return text

    @classmethod
    def validate_and_normalize(
        cls,
        text: str,
        single_paragraph: bool = True,
        paragraph_count: int = 1,
        numbers_in_words: bool = True,
        allow_abbreviations: bool = False,
        preset_id: Optional[str] = None,
        extra_known_names: Optional[List[str]] = None,
        apply_name_library: bool = True,
    ) -> Tuple[str, List[str]]:
        """
        Valida o texto (ex: após edição manual do usuário ou geração por modelo menor),
        aplica a sanitização de markdown, correção de nomes da biblioteca e regras cartoriais determinísticas.
        Retorna (texto_normalizado, lista_de_correcoes_aplicadas).
        """
        current = text.strip()
        corrections: List[str] = []

        # 1. Sanitização de artefatos de Markdown
        sanitized = cls.sanitize_markdown_artifacts(current)
        if sanitized != current:
            corrections.append("Marcadores e formatações de markdown foram sanitizados.")
            current = sanitized

        # 2. Expansão de abreviações
        if not allow_abbreviations:
            expanded = cls.expand_abbreviations(current)
            if expanded != current:
                corrections.append("Abreviações (como Ver., PL, Art., Of.) foram expandidas para sua forma por extenso.")
                current = expanded

        # 3. Correção de nomes próprios via Biblioteca Incremental e Fonética PT-BR
        if apply_name_library:
            fixed_names, name_changes = NameLibraryManager.apply_corrections(
                current, preset_id=preset_id, extra_known_names=extra_known_names
            )
            if fixed_names != current and name_changes:
                corrections.append(f"Nomes corrigidos pela Biblioteca: {', '.join(name_changes)}.")
                current = fixed_names

        # 4. Conversão de números para extenso (horários, datas, moedas, percentuais, ordinais)
        if numbers_in_words:
            with_words = cls.apply_numbers_in_words(current)
            if with_words != current:
                corrections.append("Números, horários, datas, valores monetários e percentuais foram convertidos para extenso.")
                current = with_words

        # 5. Ajuste na estrutura de parágrafos
        target_paras = 1 if single_paragraph else max(1, paragraph_count)
        formatted_paras = cls.enforce_paragraph_count(current, target_count=target_paras)
        if formatted_paras != current:
            if target_paras == 1:
                corrections.append("Quebras de linha e espaços extras foram unificados em parágrafo único contínuo.")
            else:
                corrections.append(f"O texto foi ajustado para {target_paras} parágrafos.")
            current = formatted_paras

        return current, corrections

    @classmethod
    def build_header(
        cls,
        organization_name: str = "",
        state: str = "",
        organization_details: str = "",
        title: str = "",
    ) -> str:
        """Gera o cabeçalho institucional formatado."""
        parts = []
        if state:
            parts.append(state.strip().upper())
        if organization_name:
            parts.append(organization_name.strip().upper())
        if organization_details:
            parts.append(organization_details.strip())
        if parts:
            parts.append("")
        if title:
            parts.append(title.strip().upper())
            parts.append("")
        return "\n".join(parts)

    @classmethod
    def build_signatures(
        cls,
        secretary_title: str = "Secretário",
        president_title: str = "Presidente",
    ) -> str:
        """Gera o bloco de assinaturas formais."""
        line = "_" * 30
        return (
            f"\n\n{line}    {line}\n"
            f"          {secretary_title}                            {president_title}"
        )
