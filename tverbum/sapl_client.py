"""
Cliente de Integração com o SAPL (Sistema de Apoio ao Processo Legislativo - Interlegis / Senado Federal).
Consome a API REST pública (Django REST Framework) de Câmaras Municipais brasileiras para importar:
- Nomes oficiais de parlamentares e partidos (alimentando a Biblioteca de Nomes);
- Mesa Diretora (Presidente e Secretários);
- Matérias em pauta na Ordem do Dia e Expediente;
- Votações, presenças e links de transmissão oficial.
"""

import re
import logging
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import urlparse
import httpx

from .name_library import NameLibraryManager
from .models import NameEntry

logger = logging.getLogger("tverbum.sapl")


class SaplClient:
    """Cliente HTTP para comunicação com instâncias do SAPL (Interlegis)."""

    def __init__(self, base_url: str, timeout: float = 8.0):
        self.base_url = self._normalize_base_url(base_url)
        self.timeout = timeout

    @staticmethod
    def _normalize_base_url(url: str) -> str:
        """Garante formato canônico da URL base sem barra final."""
        cleaned = url.strip().rstrip("/")
        if not cleaned.startswith("http://") and not cleaned.startswith("https://"):
            cleaned = f"https://{cleaned}"
        return cleaned

    def test_connection(self) -> Dict[str, Any]:
        """Testa se a URL informada possui a API REST do SAPL ativa e acessível."""
        api_url = f"{self.base_url}/api/"
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                resp = client.get(api_url)
                if resp.status_code == 200:
                    data = resp.json() if "application/json" in resp.headers.get("content-type", "") else {}
                    return {
                        "online": True,
                        "base_url": self.base_url,
                        "endpoints_count": len(data) if isinstance(data, dict) else 0,
                        "message": "Conexão com a API do SAPL estabelecida com sucesso.",
                    }
                return {
                    "online": False,
                    "base_url": self.base_url,
                    "status_code": resp.status_code,
                    "error": f"Servidor respondeu com status {resp.status_code}.",
                }
        except Exception as e:
            return {
                "online": False,
                "base_url": self.base_url,
                "error": f"Não foi possível conectar ao SAPL: {str(e)}",
            }

    def get_parlamentares(self, only_active: bool = True) -> List[Dict[str, Any]]:
        """
        Recupera a lista de parlamentares cadastrados no SAPL.
        Retorna dicionários com id, nome_completo, nome_parlamentar e status ativo.
        """
        url = f"{self.base_url}/api/parlamentares/parlamentar/?page_size=100"
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                resp = client.get(url)
                if resp.status_code != 200:
                    logger.warning(f"Falha ao consultar parlamentares no SAPL ({resp.status_code})")
                    return []

                data = resp.json()
                results = data.get("results", []) if isinstance(data, dict) else []

                parlamentares: List[Dict[str, Any]] = []
                for p in results:
                    ativo = p.get("ativo", True)
                    if only_active and not ativo:
                        continue

                    parlamentares.append({
                        "id": p.get("id"),
                        "nome_completo": (p.get("nome_completo") or "").strip(),
                        "nome_parlamentar": (p.get("nome_parlamentar") or "").strip(),
                        "ativo": ativo,
                        "partido": p.get("partido"),
                    })
                return parlamentares
        except Exception as e:
            logger.error(f"Erro ao consultar parlamentares no SAPL ({self.base_url}): {e}")
            return []

    def sync_names_to_library(self, preset_id: Optional[str] = None) -> List[NameEntry]:
        """
        Sincroniza os vereadores cadastrados no SAPL diretamente na Biblioteca de Nomes do tVerbum.
        Cadastra o nome oficial completo e inclui o nome parlamentar (nome de urna) como alias conhecido.
        """
        parlamentares = self.get_parlamentares(only_active=True)
        synced_entries: List[NameEntry] = []

        for p in parlamentares:
            full_name = p.get("nome_completo")
            if not full_name:
                continue

            parliamentary_name = p.get("nome_parlamentar")
            aliases = []
            if parliamentary_name and parliamentary_name.lower() != full_name.lower():
                aliases.append(parliamentary_name)

            slug = NameLibraryManager.slugify(full_name)
            entry = NameEntry(
                id=slug,
                canonical_name=full_name,
                aliases=aliases,
                category="parlamentar",
                preset_id=preset_id,
                auto_phonetic=True,
            )
            saved = NameLibraryManager.save_entry(entry)
            synced_entries.append(saved)

        logger.info(f"{len(synced_entries)} nomes de parlamentares sincronizados do SAPL para o tVerbum.")
        return synced_entries

    def find_session(self, session_number: int, year: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Localiza uma sessão plenária pelo seu número ordinal e ano de realização."""
        url = f"{self.base_url}/api/sessao/sessaoplenaria/?page_size=50"
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                resp = client.get(url)
                if resp.status_code != 200:
                    return None

                data = resp.json()
                results = data.get("results", []) if isinstance(data, dict) else []

                for s in results:
                    s_num = s.get("numero")
                    s_date = s.get("data_inicio") or ""

                    if s_num == session_number:
                        if year is None or s_date.startswith(str(year)):
                            return s
                return None
        except Exception as e:
            logger.error(f"Erro ao buscar sessão {session_number} no SAPL: {e}")
            return None

    def get_mesa_diretora(self, session_id: int) -> Dict[str, str]:
        """Obtém os nomes do Presidente e Secretário da sessão a partir da mesa diretora cadastrada."""
        url = f"{self.base_url}/api/sessao/integrantemesa/?sessao_plenaria={session_id}&page_size=30"
        mesa_info = {"president": "", "secretary": ""}
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                resp = client.get(url)
                if resp.status_code != 200:
                    return mesa_info

                data = resp.json()
                results = data.get("results", []) if isinstance(data, dict) else []

                for item in results:
                    desc = item.get("__str__", "")
                    # Padrão típico do SAPL: "Presidente - Nome do Parlamentar" ou "1º Secretário - Nome"
                    if "-" in desc:
                        cargo, nome = desc.split("-", 1)
                        cargo = cargo.strip().lower()
                        nome = nome.strip()
                        if "presidente" in cargo and "vice" not in cargo:
                            mesa_info["president"] = nome
                        elif "secret" in cargo and not mesa_info["secretary"]:
                            mesa_info["secretary"] = nome
                return mesa_info
        except Exception as e:
            logger.warning(f"Erro ao consultar mesa diretora da sessão {session_id} no SAPL: {e}")
            return mesa_info

    def get_ordem_dia_matters(self, session_id: int) -> List[Dict[str, Any]]:
        """Recupera as proposições constantes na Ordem do Dia da sessão com seus resultados."""
        url = f"{self.base_url}/api/sessao/ordemdia/?sessao_plenaria={session_id}&page_size=100"
        matters: List[Dict[str, Any]] = []
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                resp = client.get(url)
                if resp.status_code != 200:
                    return matters

                data = resp.json()
                results = data.get("results", []) if isinstance(data, dict) else []

                for item in results:
                    desc = item.get("__str__", "")
                    obs = item.get("observacao", "")
                    res = item.get("resultado", "")

                    # Tenta extrair a identificação da matéria da string padrão do SAPL
                    # Ex: "Ordem do Dia/Expediente: 1 - Projeto de Lei nº 48 de 2026 em ..."
                    matter_label = desc
                    if "-" in desc:
                        parts = desc.split("-", 1)
                        if len(parts) > 1:
                            matter_label = parts[1].split(" em ")[0].strip()

                    matters.append({
                        "id": item.get("id"),
                        "materia": matter_label,
                        "observacao": obs,
                        "resultado": res,
                    })
                return matters
        except Exception as e:
            logger.warning(f"Erro ao consultar ordem do dia da sessão {session_id} no SAPL: {e}")
            return matters

    def fetch_full_session_context(
        self,
        session_identifier: str,
        year: Optional[int] = None,
        sync_names: bool = True,
        preset_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Método agregador: localiza a sessão no SAPL e extrai todo o contexto para o tVerbum:
        - Número e data oficial;
        - Link de áudio/vídeo se cadastrado no SAPL;
        - Presidente e Secretário;
        - Texto completo da Ordem do Dia (para ancorar a IA sem alucinações);
        - Lista oficial de vereadores.
        """
        # Extrai número e ano do identificador (ex: "24/2026" ou "24")
        match = re.search(r"(\d+)(?:[/-](\d{4}))?", str(session_identifier).strip())
        if not match:
            raise ValueError(f"Identificador de sessão inválido: '{session_identifier}'. Use '21/2026' ou '21'.")

        session_num = int(match.group(1))
        session_year = int(match.group(2)) if match.group(2) else year

        # 1. Localiza a sessão no SAPL
        sess_data = self.find_session(session_num, session_year)
        if not sess_data:
            raise ValueError(
                f"Sessão plenária número {session_num} (ano: {session_year or 'todos'}) não encontrada no SAPL ({self.base_url})."
            )

        sess_id = sess_data.get("id")

        # 2. Mesa Diretora
        mesa = self.get_mesa_diretora(sess_id)

        # 3. Ordem do Dia
        ordem_matters = self.get_ordem_dia_matters(sess_id)

        # Monta texto estruturado da pauta para ancorar a IA
        pauta_lines = []
        for idx, m in enumerate(ordem_matters, start=1):
            line = f"{idx}. {m['materia']}"
            if m.get("observacao"):
                line += f" - Ementa: {m['observacao']}"
            if m.get("resultado"):
                line += f" (Resultado registrado no SAPL: {m['resultado']})"
            pauta_lines.append(line)
        agenda_text = "\n".join(pauta_lines)

        # 4. Sincroniza parlamentares
        parlamentares = self.get_parlamentares(only_active=True)
        known_attendees = [p["nome_completo"] for p in parlamentares if p.get("nome_completo")]

        if sync_names:
            try:
                self.sync_names_to_library(preset_id=preset_id)
            except Exception as e:
                logger.warning(f"Não foi possível persistir nomes na biblioteca local: {e}")

        # Formata data ISO
        data_inicio = sess_data.get("data_inicio") or ""

        return {
            "status": "ok",
            "sapl_session_id": sess_id,
            "session_number": f"{session_num}/{session_year}" if session_year else str(session_num),
            "session_date_iso": data_inicio,
            "start_time": sess_data.get("hora_inicio"),
            "end_time": sess_data.get("hora_fim"),
            "video_url": sess_data.get("url_video") or sess_data.get("url_audio"),
            "president_name": mesa.get("president", ""),
            "secretary_name": mesa.get("secretary", ""),
            "agenda_context": agenda_text,
            "voted_matters_count": len(ordem_matters),
            "voted_matters_raw": ordem_matters,
            "known_attendees": known_attendees,
            "session_type": "Sessão Ordinária" if sess_data.get("tipo") == 1 else "Sessão Extraordinária",
        }
