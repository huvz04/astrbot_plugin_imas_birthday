"""Public voice-actor profiles and portrait provenance shipped with the plugin."""
import json
from pathlib import Path

VOICE_ACTOR_CATALOGUE = json.loads((Path(__file__).parent / "data" / "seiyuu.json").read_text(encoding="utf-8"))
