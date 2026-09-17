"""
Core engine for the text-driven game.

Responsibilities:
- Load scene files (YAML) into a dict keyed by scene id.
- Evaluate `requires` / choice `condition` expressions safely (no eval()).
- Apply choice `effect`s to game state.
- Resolve requires -> fallback chains so the player never sees a scene
  they don't qualify for.
"""

from __future__ import annotations

import operator
import re
from pathlib import Path
from typing import Optional

SCENES_DIR = Path(__file__).parent / "scenes"

_OPS = {
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
    "==": operator.eq,
    "!=": operator.ne,
}

# Matches things like "stamina > 15" or "trust>=3"
_COND_RE = re.compile(r"^\s*(\w+)\s*(>=|<=|==|!=|>|<)\s*(-?\d+(?:\.\d+)?)\s*$")
# Matches the value side of a `requires:` entry, e.g. ">10"
_REQ_RE = re.compile(r"^\s*(>=|<=|==|!=|>|<)\s*(-?\d+(?:\.\d+)?)\s*$")


def load_scenes(scenes_dir: Path = SCENES_DIR) -> dict:
    """Load every *.yaml file in scenes_dir. Each file may contain a single
    scene mapping or a list of scene mappings."""
    import yaml  # local import so this module is inspectable without the dep

    scenes: dict[str, dict] = {}
    for path in sorted(Path(scenes_dir).rglob("*.yaml")):
        data = yaml.safe_load(path.read_text())
        if data is None:
            continue
        items = data if isinstance(data, list) else [data]
        for scene in items:
            if "id" not in scene:
                raise ValueError(f"Scene in {path} is missing an 'id' field")
            scenes[scene["id"]] = scene
    return scenes


def check_condition(cond: Optional[str], state: dict) -> bool:
    """Evaluate a single 'stat OP number' string, e.g. 'stamina > 15'.
    Used for per-choice `condition:` fields. No expression -> always True."""
    if cond is None:
        return True
    m = _COND_RE.match(str(cond))
    if not m:
        raise ValueError(f"Bad condition syntax: {cond!r}")
    key, op, val = m.groups()
    return _OPS[op](state.get(key, 0), float(val))


def check_requires(requires: Optional[dict], state: dict) -> bool:
    """requires: {stat: '>10', other_stat: '<5'} — ALL entries must pass."""
    if not requires:
        return True
    for key, expr in requires.items():
        m = _REQ_RE.match(str(expr))
        if not m:
            raise ValueError(f"Bad requires syntax for {key!r}: {expr!r}")
        op, val = m.groups()
        if not _OPS[op](state.get(key, 0), float(val)):
            return False
    return True


def apply_effect(effect: Optional[dict], state: dict) -> None:
    """effect: {stat: '+5'} or {stat: '-10'} applies a delta.
    effect: {stat: 'value'} (no +/- prefix, or a string) sets it absolutely —
    lets you do things like {part1_status: 'suspended'}."""
    if not effect:
        return
    for key, delta in effect.items():
        if isinstance(delta, str) and delta[:1] in ("+", "-") and delta[1:].replace(".", "", 1).isdigit():
            # signed string ("+5", "-10") -> delta applied to current value
            state[key] = state.get(key, 0) + float(delta)
        else:
            # bare number or non-numeric value -> absolute set
            # e.g. {infection_exposure: 5} sets it to 5; {part1_status: "suspended"} sets a flag
            state[key] = delta


class Engine:
    def __init__(self, scenes: dict, start_scene: str, state: Optional[dict] = None):
        self.scenes = scenes
        self.state = state or {}
        self.radio_log: list[str] = []
        self.current_id = start_scene

    def resolve_entry(self, scene_id: str) -> dict:
        """Follow requires -> fallback chains until landing on a scene the
        player qualifies for (or one with no fallback, which is shown as-is
        with any locked choices simply absent)."""
        visited: set[str] = set()
        while True:
            if scene_id in visited:
                raise RuntimeError(f"Fallback loop detected at {scene_id!r}")
            visited.add(scene_id)
            if scene_id not in self.scenes:
                raise KeyError(f"Unknown scene id: {scene_id!r}")
            scene = self.scenes[scene_id]
            if check_requires(scene.get("requires"), self.state):
                return scene
            fallback = scene.get("fallback")
            if not fallback:
                return scene  # no escape route defined — show it anyway
            scene_id = fallback

    def enter(self, scene_id: str) -> dict:
        scene = self.resolve_entry(scene_id)
        self.current_id = scene["id"]
        for msg in scene.get("radio", []):
            self.radio_log.append(msg)
        self.radio_log = self.radio_log[-8:]
        return scene

    def available_choices(self, scene: dict) -> list[dict]:
        return [
            c for c in scene.get("choices", [])
            if check_condition(c.get("condition"), self.state)
        ]

    def choose(self, scene: dict, index: int) -> dict:
        choices = self.available_choices(scene)
        choice = choices[index]
        apply_effect(choice.get("effect"), self.state)
        return self.enter(choice["next_scene"])

