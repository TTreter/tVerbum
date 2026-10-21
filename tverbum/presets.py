"""
=============================================================================
Gerenciador de Perfis Institucionais (Presets) - tVerbum
=============================================================================
Permite cadastrar câmaras municipais, conselhos ou organizações para evitar
redigitar cabeçalhos, mesa diretora e lista de membros a cada ata.
Os perfis personalizados salvos localmente ficam em `presets_data.json` (ignorado pelo Git).
"""

import json
from pathlib import Path
from typing import Dict, List, Optional
from .models import OrganizationPreset, MinuteConfig

PRESETS_FILE = Path(__file__).parent / "presets_data.json"

# Perfil genérico demonstrativo (substitua ou cadastre seus próprios perfis via API / presets_data.json)
DEFAULT_PRESETS: Dict[str, OrganizationPreset] = {
    "camara_exemplo": OrganizationPreset(
        id="camara_exemplo",
        name="Câmara Municipal de Exemplo (Modelo Demonstrativo)",
        organization_name="CÂMARA MUNICIPAL DE EXEMPLO",
        state="ESTADO DE EXEMPLO",
        organization_details=(
            "Site Oficial: camaraexemplo.leg.br | Email: contato@camaraexemplo.leg.br\n"
            "Rua das Palmeiras, nº 100, Centro, CEP: 00.000-000, Cidade Exemplo / UF"
        ),
        legislature="10ª Legislatura",
        president_name="Carlos Eduardo da Silva",
        secretary_name="Ana Paula Oliveira",
        known_attendees=[
            "Carlos Eduardo da Silva (PARTIDO A)",
            "Ana Paula Oliveira (PARTIDO A)",
            "Marcos Vinícius Santos (PARTIDO B)",
            "Helena Costa Ferreira (PARTIDO B)",
            "Roberto Almeida Souza (PARTIDO C)",
        ],
    )
}


class PresetManager:
    """Gerencia a leitura, gravação e aplicação de perfis institucionais."""

    @classmethod
    def list_presets(cls) -> List[OrganizationPreset]:
        """Retorna todos os perfis disponíveis (padrão + salvos localmente em JSON)."""
        presets = dict(DEFAULT_PRESETS)
        if PRESETS_FILE.exists():
            try:
                raw = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
                for item in raw:
                    p = OrganizationPreset(**item)
                    presets[p.id] = p
            except Exception:
                pass
        return list(presets.values())

    @classmethod
    def get_preset(cls, preset_id: str) -> Optional[OrganizationPreset]:
        """Busca um perfil específico pelo seu ID."""
        for p in cls.list_presets():
            if p.id == preset_id:
                return p
        return None

    @classmethod
    def save_preset(cls, preset: OrganizationPreset) -> OrganizationPreset:
        """Salva ou atualiza um perfil no arquivo local `presets_data.json`."""
        current = {p.id: p for p in cls.list_presets()}
        current[preset.id] = preset
        data = [p.model_dump() for p in current.values()]
        PRESETS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return preset

    @classmethod
    def apply_preset_to_config(cls, config: MinuteConfig) -> MinuteConfig:
        """Preenche campos em branco de `MinuteConfig` utilizando os dados do perfil selecionado."""
        if not config.preset_id:
            return config
        preset = cls.get_preset(config.preset_id)
        if not preset:
            return config

        if not config.organization_name:
            config.organization_name = preset.organization_name
        if not config.state:
            config.state = preset.state
        if not config.organization_details:
            config.organization_details = preset.organization_details
        if not config.legislature:
            config.legislature = preset.legislature
        if not config.president_name:
            config.president_name = preset.president_name
        if not config.secretary_name:
            config.secretary_name = preset.secretary_name
        if not config.known_attendees and preset.known_attendees:
            config.known_attendees = list(preset.known_attendees)
        if not config.logo_url and preset.logo_url:
            config.logo_url = preset.logo_url
        return config
