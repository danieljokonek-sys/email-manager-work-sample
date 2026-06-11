"""
Business entity definitions loaded from config.yaml.
"""
from dataclasses import dataclass, field
from typing import Optional
import yaml


@dataclass
class Entity:
    key: str
    name: str
    description: str
    keywords: list[str]
    accounts_receivable_from: list[str]
    accounts_payable_to: list[str]
    partner: Optional[str] = None

    def matches_text(self, text: str) -> bool:
        """Quick keyword check on lowercased text."""
        lower = text.lower()
        return any(kw.lower() in lower for kw in self.keywords)


def load_entities(config: dict) -> dict[str, Entity]:
    entities = {}
    for key, cfg in config.get("entities", {}).items():
        entities[key] = Entity(
            key=key,
            name=cfg["name"],
            description=cfg["description"],
            keywords=cfg.get("keywords", []),
            accounts_receivable_from=cfg.get("accounts_receivable_from", []),
            accounts_payable_to=cfg.get("accounts_payable_to", []),
            partner=cfg.get("partner"),
        )
    return entities


def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)
