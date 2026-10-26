"""
Biblioteca Incremental e Normalizador Fonético de Nomes Próprios (tVerbum v2.1).
Corrige deterministamente erros de grafia em nomes de vereadores, autoridades, servidores,
cidadãos e localidades causados pelo reconhecimento de voz (YouTube / Whisper) e LLMs.
"""

import os
import re
import json
import unicodedata
from pathlib import Path
from difflib import SequenceMatcher
from typing import List, Dict, Optional, Tuple

from .models import NameEntry, AIGeneratedPayload
from .presets import PresetManager


def get_names_db_path() -> Path:
    """Retorna o caminho do arquivo JSON da biblioteca de nomes (configurável via TVERBUM_NAMES_PATH)."""
    custom = os.getenv("TVERBUM_NAMES_PATH")
    if custom:
        return Path(custom)
    return Path(__file__).parent / "name_library_data.json"


DEFAULT_NAME_ENTRIES: List[NameEntry] = [
    NameEntry(
        id="carlos_eduardo_da_silva",
        canonical_name="Carlos Eduardo da Silva",
        aliases=["Karlos Eduardo da Silva", "Carlos E. da Silva"],
        category="parlamentar",
        preset_id="camara_exemplo",
        auto_phonetic=True,
    ),
    NameEntry(
        id="ana_paula_oliveira",
        canonical_name="Ana Paula Oliveira",
        aliases=["Anna Paula Oliveira", "Ana Paula de Oliveira"],
        category="parlamentar",
        preset_id="camara_exemplo",
        auto_phonetic=True,
    ),
]


class NameLibraryManager:
    """Gerencia o glossário de nomes oficiais, variações conhecidas e correção fonética PT-BR."""

    PARTICLES = {"de", "da", "do", "dos", "das", "e"}

    ROLE_PREFIXES = (
        "Vereador",
        "Vereadora",
        "Presidente",
        "Secretário",
        "Secretária",
        "Senhor",
        "Senhora",
        "Prefeito",
        "Prefeita",
        "Vice-Presidente",
        "Vice-Prefeito",
        "Assessor",
        "Assessora",
        "Diretor",
        "Diretora",
        "Procurador",
        "Procuradora",
    )

    @classmethod
    def slugify(cls, text: str) -> str:
        """Gera um ID limpo (slug) a partir de um nome próprio."""
        norm = unicodedata.normalize("NFKD", text.strip().lower())
        ascii_text = "".join(c for c in norm if not unicodedata.combining(c))
        slug = re.sub(r"[^a-z0-9]+", "_", ascii_text).strip("_")
        return slug or "nome_entry"

    @classmethod
    def phonetic_signature(cls, text: str) -> str:
        """
        Gera uma assinatura fonética adaptada ao português brasileiro e sobrenomes comuns no Brasil
        (alemães, italianos, poloneses, lusitanos) para detectar erros de transcrição por áudio.
        Exemplos:
          - 'Thiago Tretter' == 'Tiago Treter' -> 'tiago treter'
          - 'João Luís Schneider' == 'João Luiz Chneider' -> 'joao luis xneider'
          - 'Wagner Scherer' == 'Vagner Xerer' -> 'vagner xerer'
        """
        if not text:
            return ""

        # Remove acentos e converte para minúsculas
        norm = unicodedata.normalize("NFKD", text.strip().lower())
        s = "".join(c for c in norm if not unicodedata.combining(c))

        # Remove partículas de ligação para comparação fonética estável
        words = [w for w in re.split(r"\s+", s) if w and w not in cls.PARTICLES]
        s = " ".join(words)

        # Substituições fonéticas de dígrafos e consoantes homófonas
        replacements = [
            (r"sch", "x"),
            (r"sh", "x"),
            (r"ch", "x"),
            (r"th", "t"),
            (r"ph", "f"),
            (r"ck", "k"),
            (r"qu([ei])", r"k\1"),
            (r"gu([ei])", r"g\1"),
            (r"sc([ei])", r"s\1"),
            (r"c([ei])", r"s\1"),
            (r"ss", "s"),
            (r"ç", "s"),
            (r"z", "s"),
            (r"w", "v"),
            (r"y", "i"),
            (r"h", ""),
            (r"c", "k"),
        ]
        for pat, rep in replacements:
            s = re.sub(pat, rep, s)

        # Colapsa consoantes/vogais duplicadas (ex: 'tt' -> 't', 'll' -> 'l', 'nn' -> 'n')
        s = re.sub(r"([a-z])\1+", r"\1", s)
        return s.strip()

    @classmethod
    def _ensure_file(cls) -> Path:
        path = get_names_db_path()
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            data = [e.model_dump() for e in DEFAULT_NAME_ENTRIES]
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    @classmethod
    def list_entries(
        cls,
        preset_id: Optional[str] = None,
        category: Optional[str] = None,
    ) -> List[NameEntry]:
        """Lista as entradas salvas na biblioteca de nomes, com filtro opcional por preset ou categoria."""
        path = cls._ensure_file()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            entries = [NameEntry(**item) for item in raw]
        except Exception:
            entries = list(DEFAULT_NAME_ENTRIES)

        if preset_id:
            entries = [e for e in entries if e.preset_id is None or e.preset_id == "" or e.preset_id == preset_id]
        if category:
            entries = [e for e in entries if e.category.lower() == category.lower()]
        return entries

    @classmethod
    def save_entry(cls, entry: NameEntry) -> NameEntry:
        """Adiciona ou atualiza um nome na biblioteca persistente."""
        entry.canonical_name = entry.canonical_name.strip()
        if not entry.id:
            entry.id = cls.slugify(entry.canonical_name)

        # Limpa aliases duplicados ou idênticos ao nome canônico
        clean_aliases = []
        seen = {entry.canonical_name.lower()}
        for alias in entry.aliases:
            a_clean = alias.strip()
            if a_clean and a_clean.lower() not in seen:
                clean_aliases.append(a_clean)
                seen.add(a_clean.lower())
        entry.aliases = clean_aliases

        entries = cls.list_entries()
        updated = False
        for i, existing in enumerate(entries):
            if existing.id == entry.id or existing.canonical_name.lower() == entry.canonical_name.lower():
                # Mescla aliases anteriores se for atualização pelo mesmo nome canônico
                merged_aliases = list(existing.aliases)
                for a in entry.aliases:
                    if a.lower() not in {x.lower() for x in merged_aliases} and a.lower() != entry.canonical_name.lower():
                        merged_aliases.append(a)
                entry.aliases = merged_aliases
                entries[i] = entry
                updated = True
                break

        if not updated:
            entries.append(entry)

        path = cls._ensure_file()
        path.write_text(
            json.dumps([e.model_dump() for e in entries], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return entry

    @classmethod
    def quick_learn(
        cls,
        wrong_text: str,
        correct_name: str,
        category: str = "parlamentar",
        preset_id: Optional[str] = None,
    ) -> NameEntry:
        """
        Aprende rapidamente uma nova regra de correção ('wrong_text' -> 'correct_name').
        Se o nome correto já existir na biblioteca, apenas adiciona 'wrong_text' aos seus aliases.
        """
        wrong_clean = wrong_text.strip()
        correct_clean = correct_name.strip()
        if not correct_clean:
            raise ValueError("O nome correto não pode ser vazio.")

        entries = cls.list_entries()
        target_entry: Optional[NameEntry] = None

        for existing in entries:
            if existing.canonical_name.lower() == correct_clean.lower():
                target_entry = existing
                break

        if target_entry:
            if wrong_clean and wrong_clean.lower() != correct_clean.lower():
                if wrong_clean.lower() not in {a.lower() for a in target_entry.aliases}:
                    target_entry.aliases.append(wrong_clean)
            return cls.save_entry(target_entry)

        new_entry = NameEntry(
            id=cls.slugify(correct_clean),
            canonical_name=correct_clean,
            aliases=[wrong_clean] if (wrong_clean and wrong_clean.lower() != correct_clean.lower()) else [],
            category=category,
            preset_id=preset_id,
            auto_phonetic=True,
        )
        return cls.save_entry(new_entry)

    @classmethod
    def delete_entry(cls, entry_id: str) -> bool:
        """Remove uma entrada da biblioteca de nomes pelo ID."""
        entries = cls.list_entries()
        filtered = [e for e in entries if e.id != entry_id]
        if len(filtered) == len(entries):
            return False
        path = cls._ensure_file()
        path.write_text(
            json.dumps([e.model_dump() for e in filtered], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True

    @staticmethod
    def extract_clean_name_from_attendee(attendee_str: str) -> str:
        """Remove a sigla partidária entre parênteses, ex: 'Carlos Silva (MDB)' -> 'Carlos Silva'."""
        return re.sub(r"\s*\([^)]*\)\s*$", "", attendee_str).strip()

    @classmethod
    def get_active_entries(
        cls,
        preset_id: Optional[str] = None,
        extra_known_names: Optional[List[str]] = None,
    ) -> List[NameEntry]:
        """
        Retorna todas as entradas ativas da biblioteca combinadas com os nomes do Preset
        e nomes extras informados na requisição.
        """
        entries = list(cls.list_entries(preset_id=preset_id))
        known_canonical = {e.canonical_name.lower() for e in entries}

        # Adiciona nomes do Preset institucional se informado
        names_to_merge: List[str] = []
        if preset_id:
            preset = PresetManager.get_preset(preset_id)
            if preset:
                if preset.president_name:
                    names_to_merge.append(preset.president_name)
                if preset.secretary_name:
                    names_to_merge.append(preset.secretary_name)
                names_to_merge.extend(preset.known_attendees or [])

        if extra_known_names:
            names_to_merge.extend(extra_known_names)

        for raw_name in names_to_merge:
            clean_name = cls.extract_clean_name_from_attendee(raw_name)
            if clean_name and len(clean_name) > 2 and clean_name.lower() not in known_canonical:
                entries.append(
                    NameEntry(
                        id=cls.slugify(clean_name),
                        canonical_name=clean_name,
                        aliases=[],
                        category="parlamentar",
                        preset_id=preset_id,
                        auto_phonetic=True,
                    )
                )
                known_canonical.add(clean_name.lower())

        return entries

    @classmethod
    def apply_corrections(
        cls,
        text: str,
        preset_id: Optional[str] = None,
        extra_known_names: Optional[List[str]] = None,
    ) -> Tuple[str, List[str]]:
        """
        Aplica a correção de nomes no texto em duas passadas:
        1. Substituição determinística exata de todos os aliases/erros conhecidos;
        2. Casamento fonético e fuzzy matching em nomes próprios e após cargos (ex: 'Vereador ...').
        Retorna (texto_corrigido, lista_de_correcoes_feitas).
        """
        if not text or not text.strip():
            return text, []

        entries = cls.get_active_entries(preset_id=preset_id, extra_known_names=extra_known_names)
        if not entries:
            return text, []

        current = text
        applied_changes: List[str] = []

        # ---------------------------------------------------------------------
        # PASSO 1: Substituição Exata de Aliases (De -> Para)
        # ---------------------------------------------------------------------
        alias_pairs: List[Tuple[str, str]] = []
        for entry in entries:
            for alias in entry.aliases:
                if alias.strip() and alias.strip().lower() != entry.canonical_name.lower():
                    alias_pairs.append((alias.strip(), entry.canonical_name))

        # Ordena pelos aliases mais longos primeiro para evitar substituições parciais
        alias_pairs.sort(key=lambda x: len(x[0]), reverse=True)

        for alias, canonical in alias_pairs:
            pattern = re.compile(rf"(?<!\w){re.escape(alias)}(?!\w)", flags=re.IGNORECASE)
            if pattern.search(current):
                current = pattern.sub(canonical, current)
                change_desc = f"'{alias}' → '{canonical}'"
                if change_desc not in applied_changes:
                    applied_changes.append(change_desc)

        # ---------------------------------------------------------------------
        # PASSO 2: Casamento Fonético e Fuzzy Matching em Nomes Próprios
        # ---------------------------------------------------------------------
        phonetic_entries = [e for e in entries if e.auto_phonetic and len(e.canonical_name) >= 4]
        if not phonetic_entries:
            return current, applied_changes

        # 2.1. Correção de sobrenomes ou nomes curtos após títulos (ex: "Vereador Tretter" -> "Vereador Treter")
        roles_regex = "|".join(re.escape(r) for r in cls.ROLE_PREFIXES)
        # Captura o título seguido de 1 a 5 palavras capitalizadas (incluindo partículas de/da/do)
        candidate_pattern = re.compile(
            rf"\b({roles_regex})\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç]+(?:\s+(?:de|da|do|dos|das|e|[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç]+)){{0,4}})\b"
        )

        def _replace_role_followed_name(match: re.Match) -> str:
            role = match.group(1)
            candidate_name = match.group(2)

            # Se já é idêntico a um nome canônico, mantém
            for entry in phonetic_entries:
                if candidate_name == entry.canonical_name:
                    return match.group(0)

            cand_words = [w for w in candidate_name.split() if w.lower() not in cls.PARTICLES]
            cand_phon = cls.phonetic_signature(candidate_name)

            best_replacement: Optional[str] = None
            best_score = 0.0

            for entry in phonetic_entries:
                canon = entry.canonical_name
                canon_words = [w for w in canon.split() if w.lower() not in cls.PARTICLES]
                canon_phon = cls.phonetic_signature(canon)

                # Caso A: Mesmo número de palavras principais (nome completo)
                if len(cand_words) == len(canon_words) and len(cand_words) >= 1:
                    phon_ratio = SequenceMatcher(None, cand_phon, canon_phon).ratio()
                    str_ratio = SequenceMatcher(None, candidate_name.lower(), canon.lower()).ratio()
                    score = max(phon_ratio, str_ratio)
                    if (phon_ratio >= 0.88 or str_ratio >= 0.85) and score > best_score:
                        best_score = score
                        best_replacement = canon

                # Caso B: Candidato tem apenas Nome + Último Sobrenome ou Primeiro + Segundo Nome de um nome maior
                elif 1 <= len(cand_words) < len(canon_words):
                    # Compara com os primeiros N tokens ou primeiro + último token do nome canônico
                    sub_variants = [
                        " ".join(canon_words[: len(cand_words)]),
                        " ".join([canon_words[0], canon_words[-1]]) if len(cand_words) == 2 else canon_words[-1],
                    ]
                    for sub in sub_variants:
                        sub_phon = cls.phonetic_signature(sub)
                        phon_ratio = SequenceMatcher(None, cand_phon, sub_phon).ratio()
                        str_ratio = SequenceMatcher(None, " ".join(cand_words).lower(), sub.lower()).ratio()
                        score = max(phon_ratio, str_ratio)
                        if (phon_ratio >= 0.90 or str_ratio >= 0.86) and score > best_score:
                            best_score = score
                            best_replacement = sub

            if best_replacement and best_replacement != candidate_name:
                change_desc = f"'{candidate_name}' → '{best_replacement}'"
                if change_desc not in applied_changes:
                    applied_changes.append(change_desc)
                return f"{role} {best_replacement}"

            return match.group(0)

        current = candidate_pattern.sub(_replace_role_followed_name, current)

        # 2.2. Varredura geral para nomes completos (2 ou mais palavras principais) em qualquer posição do texto
        multiword_entries = [
            e for e in phonetic_entries
            if len([w for w in e.canonical_name.split() if w.lower() not in cls.PARTICLES]) >= 2
        ]
        if multiword_entries:
            general_cap_pattern = re.compile(
                r"\b([A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç]+(?:\s+(?:de|da|do|dos|das|e|[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç]+)){1,4})\b"
            )

            def _replace_general_name(match: re.Match) -> str:
                candidate = match.group(1)
                for entry in multiword_entries:
                    if candidate == entry.canonical_name:
                        return candidate

                cand_words = [w for w in candidate.split() if w.lower() not in cls.PARTICLES]
                if len(cand_words) < 2:
                    return candidate

                cand_phon = cls.phonetic_signature(candidate)
                best_canon: Optional[str] = None
                best_score = 0.0

                for entry in multiword_entries:
                    canon = entry.canonical_name
                    canon_words = [w for w in canon.split() if w.lower() not in cls.PARTICLES]
                    if len(cand_words) != len(canon_words):
                        continue

                    canon_phon = cls.phonetic_signature(canon)
                    phon_ratio = SequenceMatcher(None, cand_phon, canon_phon).ratio()
                    str_ratio = SequenceMatcher(None, candidate.lower(), canon.lower()).ratio()
                    score = max(phon_ratio, str_ratio)
                    if (phon_ratio >= 0.90 or str_ratio >= 0.87) and score > best_score:
                        best_score = score
                        best_canon = canon

                if best_canon and best_canon != candidate:
                    change_desc = f"'{candidate}' → '{best_canon}'"
                    if change_desc not in applied_changes:
                        applied_changes.append(change_desc)
                    return best_canon

                return candidate

            current = general_cap_pattern.sub(_replace_general_name, current)

        return current, applied_changes

    @classmethod
    def sanitize_ai_payload(
        cls,
        payload: AIGeneratedPayload,
        preset_id: Optional[str] = None,
        extra_known_names: Optional[List[str]] = None,
    ) -> List[str]:
        """
        Propaga a correção de nomes por todos os campos do AIGeneratedPayload:
        - body
        - extracted_data (president, secretary, present_members, absent_members, voted_matters)
        - timeline_map (phase, speaker, summary)
        Retorna a lista consolidada de todas as correções de nomes realizadas.
        """
        all_changes: List[str] = []

        def _record(changes: List[str]) -> None:
            for c in changes:
                if c not in all_changes:
                    all_changes.append(c)

        # 1. Corpo da ata
        if payload.body:
            payload.body, ch = cls.apply_corrections(payload.body, preset_id, extra_known_names)
            _record(ch)

        # 2. Dados extraídos para banco de dados
        ed = payload.extracted_data
        if ed:
            if ed.president:
                ed.president, ch = cls.apply_corrections(ed.president, preset_id, extra_known_names)
                _record(ch)
            if ed.secretary:
                ed.secretary, ch = cls.apply_corrections(ed.secretary, preset_id, extra_known_names)
                _record(ch)
            if ed.present_members:
                new_present = []
                for m in ed.present_members:
                    fixed_m, ch = cls.apply_corrections(m, preset_id, extra_known_names)
                    _record(ch)
                    new_present.append(fixed_m)
                ed.present_members = new_present
            if ed.absent_members:
                new_absent = []
                for m in ed.absent_members:
                    fixed_m, ch = cls.apply_corrections(m, preset_id, extra_known_names)
                    _record(ch)
                    new_absent.append(fixed_m)
                ed.absent_members = new_absent
            if ed.voted_matters:
                for vm in ed.voted_matters:
                    if vm.author:
                        vm.author, ch = cls.apply_corrections(vm.author, preset_id, extra_known_names)
                        _record(ch)
                    if vm.summary:
                        vm.summary, ch = cls.apply_corrections(vm.summary, preset_id, extra_known_names)
                        _record(ch)

        # 3. Linha do tempo
        if payload.timeline_map:
            for tl in payload.timeline_map:
                if tl.phase:
                    tl.phase, ch = cls.apply_corrections(tl.phase, preset_id, extra_known_names)
                    _record(ch)
                if tl.speaker:
                    tl.speaker, ch = cls.apply_corrections(tl.speaker, preset_id, extra_known_names)
                    _record(ch)
                if tl.summary:
                    tl.summary, ch = cls.apply_corrections(tl.summary, preset_id, extra_known_names)
                    _record(ch)

        return all_changes

    @classmethod
    def build_prompt_glossary(
        cls,
        preset_id: Optional[str] = None,
        extra_known_names: Optional[List[str]] = None,
    ) -> str:
        """
        Gera uma seção de ancoragem para o prompt do LLM listando os nomes oficiais e os erros
        que devem ser evitados.
        """
        entries = cls.get_active_entries(preset_id=preset_id, extra_known_names=extra_known_names)
        if not entries:
            return ""

        lines = []
        for e in entries:
            if e.aliases:
                lines.append(f"  * Escreva '{e.canonical_name}' (NUNCA escreva: {', '.join(e.aliases)})")
            else:
                lines.append(f"  * {e.canonical_name} ({e.category})")

        return (
            "BIBLIOTECA DE NOMES OFICIAIS (Respeite rigorosamente esta grafia para pessoas e localidades):\n"
            + "\n".join(lines)
        )
