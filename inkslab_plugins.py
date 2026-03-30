#!/usr/bin/python3
"""
Shared plugin registry for InkSlab.

This is the first step toward a modular architecture without rewriting the
existing TCG product. The current built-in TCG modes are represented as
internal plugins so the daemon and web UI can share a single source of truth.
"""

from dataclasses import asdict, dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class PluginDefinition:
    """Defines an installable/enableable InkSlab mode."""
    plugin_id: str
    name: str
    kind: str
    card_library_path: Optional[str] = None
    accent_color: Optional[str] = None
    download_script: Optional[str] = None
    description: str = ""
    builtin: bool = True
    settings_keys: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


BUILTIN_PLUGINS: Dict[str, PluginDefinition] = {
    "pokemon": PluginDefinition(
        plugin_id="pokemon",
        name="Pokemon",
        kind="tcg",
        card_library_path="/home/pi/pokemon_cards",
        accent_color="#36A5CA",
        download_script="download_cards_pokemon.py",
        description="Pokemon card slideshow mode.",
        settings_keys=["collection_only", "slab_header_mode", "rotation_angle", "color_saturation"],
    ),
    "mtg": PluginDefinition(
        plugin_id="mtg",
        name="Magic: The Gathering",
        kind="tcg",
        card_library_path="/home/pi/mtg_cards",
        accent_color="#6BCCBD",
        download_script="download_cards_mtg.py",
        description="Magic: The Gathering card slideshow mode.",
        settings_keys=["collection_only", "slab_header_mode", "rotation_angle", "color_saturation"],
    ),
    "lorcana": PluginDefinition(
        plugin_id="lorcana",
        name="Disney Lorcana",
        kind="tcg",
        card_library_path="/home/pi/lorcana_cards",
        accent_color="#C084FC",
        download_script="download_cards_lorcana.py",
        description="Disney Lorcana card slideshow mode.",
        settings_keys=["collection_only", "slab_header_mode", "rotation_angle", "color_saturation"],
    ),
    "custom": PluginDefinition(
        plugin_id="custom",
        name="Custom",
        kind="tcg",
        card_library_path="/home/pi/custom_cards",
        accent_color="#F59E0B",
        description="User-uploaded custom image slideshow mode.",
        settings_keys=["collection_only", "slab_header_mode", "rotation_angle", "color_saturation"],
    ),
}


def get_plugins() -> Dict[str, PluginDefinition]:
    """Return all currently available built-in plugins."""
    return BUILTIN_PLUGINS


def get_plugin(plugin_id: str) -> Optional[PluginDefinition]:
    """Return a plugin definition by ID, or None if missing."""
    return BUILTIN_PLUGINS.get(plugin_id)


def get_plugin_payload() -> Dict[str, Dict[str, object]]:
    """Return plugin metadata in a JSON-friendly shape for the web UI."""
    return {plugin_id: plugin.to_dict() for plugin_id, plugin in BUILTIN_PLUGINS.items()}


def get_card_libraries() -> Dict[str, str]:
    """Return plugin -> card library path for TCG-backed plugins."""
    return {
        plugin_id: plugin.card_library_path
        for plugin_id, plugin in BUILTIN_PLUGINS.items()
        if plugin.card_library_path
    }


def get_plugin_ids() -> List[str]:
    """Return valid plugin IDs."""
    return list(BUILTIN_PLUGINS.keys())


def normalize_active_plugin(value: Optional[str], default: str = "pokemon") -> str:
    """Normalize a requested active plugin ID to a known built-in plugin."""
    if value in BUILTIN_PLUGINS:
        return str(value)
    return default
