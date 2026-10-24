"""Seeded personas: one JSON file each in personas/. The web chat agent adds more files here."""

import json
from pathlib import Path

from .models import Persona

PERSONAS_DIR = Path(__file__).parent.parent / "personas"


def load(directory: Path = PERSONAS_DIR) -> dict[str, Persona]:
    personas = (Persona.model_validate(json.loads(f.read_text(encoding="utf-8"))) for f in sorted(directory.glob("*.json")))
    return {p.agent_id: p for p in personas}
