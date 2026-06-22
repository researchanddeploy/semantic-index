"""Config loader for the semantic index.

Loads config.yaml from the semantic-index base directory and returns it as a dict.
"""

from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    """Load and return the semantic-index configuration.

    Args:
        path: Optional override path to config file. Defaults to
              config.yaml located at the repository root (see CONFIG_PATH).

    Returns:
        Parsed configuration dictionary.

    Raises:
        FileNotFoundError: If config file does not exist.
        yaml.YAMLError: If config file is not valid YAML.
    """
    config_file = Path(path) if path else CONFIG_PATH

    if not config_file.exists():
        raise FileNotFoundError(f"Config file not found: {config_file}")

    with open(config_file, "r") as f:
        config = yaml.safe_load(f)

    if config is None:
        return {}

    return config


def get_semantic_config(path: Path | str | None = None) -> dict[str, Any]:
    """Convenience function that returns the 'semantic_index' section of config.

    Returns:
        The semantic_index sub-dict from config.yaml.
    """
    config = load_config(path)
    return config.get("semantic_index", config)
