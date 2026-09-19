"""
Core engine for the story game.

Every scene has exactly two decisions: `left` and `right`. The player only
ever makes decisions; everything else "just happens" through stat effects,
the passage of time, and weighted random outcomes.

Responsibilities:
- Load scene files (YAML) and check them for mistakes (validate_scenes).
- Track time in hours; a new day triggers upkeep (food, sickness).
- Apply effects, clamp stats, and run global death rules.
- Pick scenes from random pools using a seed saved with the run, so a page
  refresh never changes an outcome.
- Save/restore itself as a plain dict (for SQLite persistence).

Scene id convention (so ids read like a tree):
    P1.0            root of part 1
    P1.0.L / P1.0.R child reached by the left / right decision of P1.0
    P1.1            a "hub" where branches merge (P<part>.<beat number>)
    P1.0.L.F        fallback of P1.0.L (shown when its `requires` fail)
    POOL.<name>.NN  scene inside a random pool
    POOL.<name>.NN.A / .B   result scenes of a random outcome
    POOL.<name>.empty       shown when a pool has nothing left to draw
    END.<cause>     an ending (death)
"""

from __future__ import annotations

import operator
import random
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

SCENES_DIR = Path(__file__).parent / "scenes"
START_SCENE = "P1.0"

STARTING_STATE = {
    "health": 35,
    "stamina": 30,
    "supplies": 0,
    "trust": 0,
    "infection_exposure": 0,
    "height": 70,  # meters above the ground; the story is a descent
    "hours": 10,   # game clock; 10 = 10:00 on day 0
    "days": 0,     # days survived (derived from hours)
}

# Story flags every run starts with
STARTING_FLAGS = {"kit": "none"}

CLOCK_START = datetime(2036, 7, 21, 0, 0)   # hour 0 of the game world

# (min, max) per stat; anything not listed is unlimited
LIMITS = {
    "health": (0, 50),
    "stamina": (0, 100),
    "supplies": (0, 99),
    "trust": (-20, 20),
    "infection_exposure": (0, 100),
    "height": (0, 70),
}

# Runs once each time a new in-game day begins
UPKEEP = {
    "supplies_per_day": 1,
    "starving_damage": 5,       # health lost if supplies would go below 0
    "sick_threshold": 50,       # infection_exposure at which you are "sick"
    "sick_damage": 3,
}

# Checked after every decision. First match wins. Uses check_condition syntax.
DEATH_RULES = [
    ("health <= 0", "END.wounds"),
    ("infection_exposure >= 100", "END.infection"),
    ("stamina <= 0", "END.exhaustion"),
]

# Rolled once at the start of each run and stored in `flags`
WORLD_ROLLS = {
    # Which "J" was with you on the landing when you fell. Scenes read it with
    # requires_flag: {with_you: john}
    "with_you": ["john", "jershy"],
}

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


# --------------------------------------------------------------------------
# Loading and checking scenes
# --------------------------------------------------------------------------

def load_scenes(scenes_dir: Path = SCENES_DIR) -> dict:
    """Load every *.yaml file under scenes_dir (subfolders included). Each
    file may contain a single scene mapping or a list of scene mappings."""
    import yaml  # local import so this module is inspectable without the dep

    scenes: dict[str, dict] = {}
    for path in sorted(Path(scenes_dir).rglob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if data is None:
            continue
        items = data if isinstance(data, list) else [data]
        for scene in items:
            if "id" not in scene:
                raise ValueError(f"Scene in {path} is missing an 'id' field")
            if scene["id"] in scenes:
                raise ValueError(f"Duplicate scene id {scene['id']!r} (second one in {path})")
            scenes[scene["id"]] = scene
    return scenes


def _targets(scene: dict) -> list[str]:
    """Every scene id (or 'pool:name') this scene can lead to."""
    out: list[str] = []
    for side in ("left", "right"):
        option = scene.get(side) or {}
        if "next" in option:
            out.append(option["next"])
        out.extend(o["next"] for o in option.get("outcomes", []) if "next" in o)
    if scene.get("fallback"):
        out.append(scene["fallback"])
    return out


def validate_scenes(scenes: dict, start: str = START_SCENE) -> list[str]:
    """Return a list of human-readable problems (empty list = all good)."""
    problems: list[str] = []
    pools: dict[str, list[str]] = {}
    for s in scenes.values():
        if s.get("pool"):
            pools.setdefault(s["pool"], []).append(s["id"])

    def expand(target: str) -> list[str]:
        if target.startswith("pool:"):
            name = target[5:]
            return pools.get(name, []) + [f"POOL.{name}.empty"]
        return [target]

    for sid, s in scenes.items():
        for field in ("title", "text"):
            if not s.get(field):
                problems.append(f"{sid}: missing '{field}'")

        if s.get("ending"):
            if s.get("left") or s.get("right"):
                problems.append(f"{sid}: an ending must not have left/right decisions")
        else:
            for side in ("left", "right"):
                option = s.get(side)
                if not option:
                    problems.append(f"{sid}: missing '{side}' decision")
                    continue
                if not option.get("label"):
                    problems.append(f"{sid}.{side}: missing 'label'")
                if "next" not in option and not option.get("outcomes"):
                    problems.append(f"{sid}.{side}: needs 'next' or 'outcomes'")

        try:
            check_requires(s.get("requires"), {})
        except ValueError as exc:
            problems.append(f"{sid}: {exc}")

        for target in _targets(s):
            if target.startswith("pool:") and not pools.get(target[5:]):
                problems.append(f"{sid}: pool {target[5:]!r} has no scenes")
            for real in expand(target):
                if real not in scenes:
                    problems.append(f"{sid}: points to unknown scene {real!r}")

    for _, death_scene in DEATH_RULES:
        if death_scene not in scenes:
            problems.append(f"DEATH_RULES: unknown scene {death_scene!r}")

    if start not in scenes:
        problems.append(f"start scene {start!r} does not exist")
    else:
        reached: set[str] = set()
        stack = [start] + [d for _, d in DEATH_RULES]
        while stack:
            sid = stack.pop()
            if sid in reached or sid not in scenes:
                continue
            reached.add(sid)
            for target in _targets(scenes[sid]):
                stack.extend(expand(target))
        for sid in sorted(set(scenes) - reached):
            problems.append(f"{sid}: unreachable from {start}")

    return problems


# --------------------------------------------------------------------------
# Condition / effect helpers
# --------------------------------------------------------------------------

def check_condition(cond: Optional[str], state: dict) -> bool:
    """Evaluate a single 'stat OP number' string, e.g. 'health <= 0'.
    No expression -> always True."""
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
    effect: {stat: 'value'} (no +/- prefix, or a string) sets it absolutely."""
    if not effect:
        return
    for key, delta in effect.items():
        if isinstance(delta, str) and delta[:1] in ("+", "-") and delta[1:].replace(".", "", 1).isdigit():
            state[key] = state.get(key, 0) + float(delta)
        else:
            state[key] = delta


# --------------------------------------------------------------------------
# The engine
# --------------------------------------------------------------------------

class Engine:
    def __init__(self, scenes: dict, start_scene: str, state: Optional[dict] = None, seed: int = 0):
        self.scenes = scenes
        self.state = state or {}
        self.radio_log: list[str] = []
        self.current_id = start_scene

        # saved with the run
        self.seed = seed
        self.step = 0                 # decisions made so far
        self.seen: set[str] = set()   # scenes visited this run
        self.flags: dict = {}         # world rolls + story flags
        self.path: list[str] = []     # every scene visited, in order

        # supplied fresh on each request (NOT saved in the run)
        self.history: dict[str, int] = {}   # scene id -> times seen in recent runs
        self.memories: set[str] = set()     # diary fragments unlocked in any run
        self.new_memories: list[tuple[str, str]] = []

    # ---- creating / saving ---------------------------------------------------

    @classmethod
    def new_run(cls, scenes: dict, history: Optional[dict] = None, memories=None) -> "Engine":
        engine = cls(scenes, START_SCENE, dict(STARTING_STATE), seed=random.randrange(2**32))
        engine.history = history or {}
        engine.memories = set(memories or [])
        engine.flags.update(STARTING_FLAGS)
        engine._roll_world()
        engine.enter(START_SCENE)
        return engine

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "current_id": self.current_id,
            "radio_log": self.radio_log,
            "seed": self.seed,
            "step": self.step,
            "seen": sorted(self.seen),
            "flags": self.flags,
            "path": self.path,
        }

    @classmethod
    def from_dict(cls, scenes: dict, data: dict, history: Optional[dict] = None, memories=None) -> "Engine":
        if data["current_id"] not in scenes:
            raise KeyError(data["current_id"])
        engine = cls(scenes, data["current_id"], data["state"], seed=data["seed"])
        engine.radio_log = data["radio_log"]
        engine.step = data["step"]
        engine.seen = set(data["seen"])
        engine.flags = data["flags"]
        engine.path = data["path"]
        engine.history = history or {}
        engine.memories = set(memories or [])
        return engine

    # ---- randomness -----------------------------------------------------------

    def _rng(self, tag: str) -> random.Random:
        """A fresh generator that depends only on saved data (seed + step + tag).
        Same moment in the same run always rolls the same result."""
        return random.Random(f"{self.seed}:{self.step}:{tag}")

    def _roll_world(self) -> None:
        for name, options in WORLD_ROLLS.items():
            self.flags[name] = self._rng(f"world:{name}").choice(options)
        self.state["health"] += self._rng("world:health").randint(-3, 3)

    def _draw(self, pool: str) -> str:
        """Pick one unseen, qualifying scene from a pool (history-weighted)."""
        candidates = [
            s for s in self.scenes.values()
            if s.get("pool") == pool and s["id"] not in self.seen and self.qualifies(s)
        ]
        if not candidates:
            return f"POOL.{pool}.empty"
        weights = [s.get("weight", 1) / (1 + self.history.get(s["id"], 0)) for s in candidates]
        return self._rng(f"pool:{pool}").choices(candidates, weights=weights)[0]["id"]

    # ---- entering scenes ------------------------------------------------------

    def qualifies(self, scene: dict) -> bool:
        if scene.get("once") and scene["id"] in self.seen:
            return False
        if not check_requires(scene.get("requires"), self.state):
            return False
        for flag, value in (scene.get("requires_flag") or {}).items():
            if self.flags.get(flag) != value:
                return False
        memory = scene.get("requires_memory")
        if memory and memory not in self.memories:
            return False
        return True

    def resolve_entry(self, scene_id: str) -> dict:
        """Resolve 'pool:x' into a real scene, then follow requires -> fallback
        chains until landing on a scene the player qualifies for."""
        visited: set[str] = set()
        while True:
            if scene_id.startswith("pool:"):
                scene_id = self._draw(scene_id[5:])
            if scene_id in visited:
                raise RuntimeError(f"Fallback loop detected at {scene_id!r}")
            visited.add(scene_id)
            if scene_id not in self.scenes:
                raise KeyError(f"Unknown scene id: {scene_id!r}")
            scene = self.scenes[scene_id]
            if self.qualifies(scene):
                return scene
            fallback = scene.get("fallback")
            if not fallback:
                return scene  # no escape route defined — show it anyway
            scene_id = fallback

    def enter(self, scene_id: str) -> dict:
        scene = self.resolve_entry(scene_id)
        self.current_id = scene["id"]
        self.seen.add(scene["id"])
        self.path.append(scene["id"])
        for msg in scene.get("radio", []):
            if msg not in self.radio_log:
                self.radio_log.append(msg)
        self.radio_log = self.radio_log[-50:]
        unlock = scene.get("unlocks")
        if unlock and unlock["id"] not in self.memories:
            self.memories.add(unlock["id"])
            self.new_memories.append((unlock["id"], unlock["text"]))
        return scene

    # ---- making a decision ----------------------------------------------------

    def choose(self, scene: dict, side: str) -> dict:
        """side is 'left' or 'right'."""
        option = scene[side]
        self.step += 1

        apply_effect(option.get("effect"), self.state)
        self.flags.update(option.get("sets_flag") or {})
        target = option.get("next")

        outcomes = option.get("outcomes")
        if outcomes:
            picked = self._rng("outcome").choices(
                outcomes, weights=[o.get("weight", 1) for o in outcomes]
            )[0]
            apply_effect(picked.get("effect"), self.state)
            self.flags.update(picked.get("sets_flag") or {})
            target = picked["next"]

        self._advance_time()
        self._clamp()
        death = self._check_death()
        return self.enter(death or target)

    # ---- time, limits, death --------------------------------------------------

    def _advance_time(self) -> None:
        new_day = int(self.state.get("hours", 0) // 24)
        while self.state.get("days", 0) < new_day:
            self.state["days"] = self.state.get("days", 0) + 1
            self._nightly_upkeep()

    def _nightly_upkeep(self) -> None:
        s = self.state
        s["supplies"] = s.get("supplies", 0) - UPKEEP["supplies_per_day"]
        if s["supplies"] < 0:
            s["supplies"] = 0
            s["health"] = s.get("health", 0) - UPKEEP["starving_damage"]
        if s.get("infection_exposure", 0) >= UPKEEP["sick_threshold"]:
            s["health"] = s.get("health", 0) - UPKEEP["sick_damage"]

    def _clamp(self) -> None:
        for key, (low, high) in LIMITS.items():
            if key in self.state:
                self.state[key] = max(low, min(high, self.state[key]))

    def _check_death(self) -> Optional[str]:
        for cond, scene_id in DEATH_RULES:
            if check_condition(cond, self.state):
                return scene_id
        return None

    def clock(self) -> str:
        moment = CLOCK_START + timedelta(hours=float(self.state.get("hours", 0)))
        return moment.strftime("%d-%m-%Y  %H:%M")


if __name__ == "__main__":
    found = validate_scenes(load_scenes())
    print("\n".join(found) if found else "All scenes look good.")
