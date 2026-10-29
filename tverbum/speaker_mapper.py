"""
Mapeador Heurístico de Oradores e Fases Regimentais para Sessões Plenárias.
Analisa a transcrição e detecta concessões de fala da Presidência/Mesa Diretora
('Concedo a palavra ao Vereador...', 'Com a palavra o Secretário...'),
vinculando falas aos nomes oficiais de parlamentares e calculando tempos de tribuna.
"""

import re
from typing import List, Dict, Optional, Tuple, Any
from .models import TimelineEntry


class SpeakerMapper:
    """Detecta intervenções de oradores a partir do rito regimental legislativo."""

    # Expressões regimentais típicas utilizadas pela Presidência da Mesa
    SPEECH_GRANT_PATTERNS = [
        # "Concedo a palavra ao (nobre) Vereador Fulano"
        re.compile(
            r"(?:concedo|passo|tem|com)\s+a\s+palavra\s+(?:ao|à|ao\s+senhor|à\s+senhora|ao\s+nobre|à\s+nobre)?\s*(?:vereador|vereadora|secretário|secretária|relator|relatora)?\s*([A-ZÀ-Ú][a-zà-ú]+(?:\s+[A-ZÀ-Ú][a-zà-ú]+)*)",
            re.IGNORECASE,
        ),
        # "Orador inscrito: Vereador Fulano"
        re.compile(
            r"(?:orador|oradora)\s+inscrito(?:\s*:\s*|\s+é\s+o\s+vereador\s+|\s+é\s+a\s+vereadora\s+|\s+)?([A-ZÀ-Ú][a-zà-ú]+(?:\s+[A-ZÀ-Ú][a-zà-ú]+)*)",
            re.IGNORECASE,
        ),
        # "Com a palavra o Vereador Fulano" / "Pela ordem, Vereador Fulano"
        re.compile(
            r"(?:pela\s+ordem|com\s+a\s+palavra)\s*,?\s*(?:o\s+vereador|a\s+vereadora)?\s*([A-ZÀ-Ú][a-zà-ú]+(?:\s+[A-ZÀ-Ú][a-zà-ú]+)*)",
            re.IGNORECASE,
        ),
        # "Convido o Vereador Fulano a fazer uso da palavra"
        re.compile(
            r"(?:convido|chamo)\s+o\s+vereador\s+([A-ZÀ-Ú][a-zà-ú]+(?:\s+[A-ZÀ-Ú][a-zà-ú]+)*)",
            re.IGNORECASE,
        ),
    ]

    TIMESTAMP_PATTERN = re.compile(r"\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?")

    @staticmethod
    def _timestamp_to_seconds(ts: str) -> int:
        """Converte string de timestamp HH:MM:SS ou MM:SS em segundos inteiros."""
        clean = re.sub(r"[^\d:]", "", ts)
        parts = clean.split(":")
        try:
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            if len(parts) == 1 and parts[0]:
                return int(parts[0])
        except ValueError:
            return 0
        return 0

    @staticmethod
    def _seconds_to_timestamp(seconds: int) -> str:
        """Converte segundos inteiros de volta para o formato HH:MM:SS ou MM:SS."""
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    @classmethod
    def match_known_attendee(cls, candidate: str, known_attendees: List[str]) -> Optional[str]:
        """
        Cruza o nome extraído de uma frase regimental com a lista canônica oficial de parlamentares.
        Suporta correspondência por sobreposição de primeiro/último nome e apelidos.
        """
        if not candidate or not known_attendees:
            return None

        clean_cand = candidate.strip().lower()
        cand_parts = [p for p in clean_cand.split() if len(p) > 2 and p not in ("senhor", "nobre", "vereador", "vereadora")]
        if not cand_parts:
            return None

        best_match = None
        best_score = 0

        for member in known_attendees:
            member_clean = member.lower()
            # Remove partido entre parênteses para comparação
            member_clean = re.sub(r"\(.*?\)", "", member_clean).strip()
            member_parts = set(member_clean.split())

            score = sum(1 for cp in cand_parts if cp in member_parts)
            # Se for prefixo exato (ex: "Carlos" casa com "Carlos Eduardo")
            if any(member_clean.startswith(cp) for cp in cand_parts):
                score += 1

            if score > best_score:
                best_score = score
                best_match = member

        return best_match if best_score >= 1 else None

    @classmethod
    def detect_category(cls, text_context: str) -> str:
        """Determina a categoria temática da fala pelo contexto regimental."""
        low = text_context.lower()
        if "pequeno expediente" in low:
            return "Pequeno Expediente"
        if "grande expediente" in low:
            return "Grande Expediente"
        if "ordem do dia" in low or "discussão" in low or "votação" in low:
            return "Ordem do Dia"
        if "explicações pessoais" in low or "palavra livre" in low:
            return "Explicações Pessoais"
        if "leitura do expediente" in low or "correspondência" in low:
            return "Leitura do Expediente"
        if "questão de ordem" in low or "pela ordem" in low:
            return "Questão de Ordem"
        return "Tribuna"

    @classmethod
    def extract_speaker_cues(
        cls,
        transcript_text: str,
        known_attendees: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Varre a transcrição identificando anúncios regimentais de concessão de fala.
        Retorna lista ordenada cronologicamente de eventos de oradores.
        """
        if not transcript_text:
            return []

        known = known_attendees or []
        events = []
        lines = transcript_text.splitlines()

        current_timestamp = "00:00"
        current_category = "Abertura"

        for line in lines:
            line_str = line.strip()
            if not line_str:
                continue

            # Atualiza timestamp corrente se linha tiver timestamp
            ts_match = cls.TIMESTAMP_PATTERN.search(line_str)
            if ts_match:
                current_timestamp = ts_match.group(1)

            # Atualiza fase regimental geral
            cat = cls.detect_category(line_str)
            if cat != "Tribuna":
                current_category = cat

            # Testa padrões de concessão de fala
            for pat in cls.SPEECH_GRANT_PATTERNS:
                m = pat.search(line_str)
                if m:
                    cand_name = m.group(1).strip()
                    # Ignora falsos positivos comuns
                    if cand_name.lower() in ("aberta", "encerrada", "iniciada", "deus", "dia"):
                        continue

                    matched = cls.match_known_attendee(cand_name, known) or cand_name
                    events.append({
                        "timestamp": current_timestamp,
                        "speaker": matched,
                        "raw_candidate": cand_name,
                        "category": current_category,
                        "context_snippet": line_str[:120],
                    })
                    break

        return events

    @classmethod
    def enhance_timeline(
        cls,
        timeline: List[TimelineEntry],
        transcript_text: Optional[str] = None,
        known_attendees: Optional[List[str]] = None,
    ) -> Tuple[List[TimelineEntry], Dict[str, str]]:
        """
        Cruza a timeline existente com os anúncios regimentais detectados.
        Garante preenchimento de speaker, end_timestamp e category, e calcula o tempo total por orador.
        """
        known = known_attendees or []
        detected_cues = cls.extract_speaker_cues(transcript_text or "", known) if transcript_text else []

        # 1. Se timeline estiver vazia mas detectamos eventos, gera timeline básica a partir dos cues
        if not timeline and detected_cues:
            timeline = [
                TimelineEntry(
                    phase=f"{c['category']} - {c['speaker']}",
                    timestamp=c["timestamp"],
                    summary=f"Pronunciamento de {c['speaker']} no espaço do {c['category']}.",
                    speaker=c["speaker"],
                    category=c["category"],
                )
                for c in detected_cues
            ]

        # 2. Refina cada entrada da timeline
        for entry in timeline:
            # Se não tem orador preenchido, tenta extrair de phase ou summary
            if not entry.speaker:
                cand = entry.phase.split("-")[-1].strip() if "-" in entry.phase else entry.phase
                matched = cls.match_known_attendee(cand, known)
                if matched:
                    entry.speaker = matched
                else:
                    # Tenta bater timestamp aproximado com detected_cues
                    entry_secs = cls._timestamp_to_seconds(entry.timestamp)
                    for cue in detected_cues:
                        cue_secs = cls._timestamp_to_seconds(cue["timestamp"])
                        if abs(entry_secs - cue_secs) <= 60:
                            entry.speaker = cue["speaker"]
                            if not entry.category or entry.category == "Tribuna":
                                entry.category = cue["category"]
                            break

            if not entry.category or entry.category == "Tribuna":
                entry.category = cls.detect_category(f"{entry.phase} {entry.summary}")

        # 3. Calcula end_timestamp e tempos totais por orador
        speaker_seconds: Dict[str, int] = {}

        for idx, entry in enumerate(timeline):
            curr_secs = cls._timestamp_to_seconds(entry.timestamp)
            next_secs = curr_secs + 180  # fallback de 3 minutos se for o último

            if idx < len(timeline) - 1:
                following_secs = cls._timestamp_to_seconds(timeline[idx + 1].timestamp)
                if following_secs > curr_secs:
                    next_secs = following_secs

            if not entry.end_timestamp:
                entry.end_timestamp = cls._seconds_to_timestamp(next_secs)

            duration = max(0, next_secs - curr_secs)
            if entry.speaker:
                speaker_seconds[entry.speaker] = speaker_seconds.get(entry.speaker, 0) + duration

        # Converte tempos para formato amigável "Xm Ys"
        speaker_time_summary: Dict[str, str] = {}
        for spk, secs in speaker_seconds.items():
            mins = secs // 60
            s = secs % 60
            speaker_time_summary[spk] = f"{mins:02d}m {s:02d}s"

        return timeline, speaker_time_summary
