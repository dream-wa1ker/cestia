from flask import Flask, jsonify, render_template, request

import db
from engine import Engine, load_scenes, validate_scenes

app = Flask(__name__)
SCENES = load_scenes()   # loaded once at startup (restart Flask after editing YAML)
db.init_db()

for problem in validate_scenes(SCENES):
    print(f"[scene check] {problem}")


# ---- helpers ------------------------------------------------------------------

def persist(engine: Engine) -> None:
    """Save the run, and copy any newly unlocked diary memories to the database."""
    for memory_id, text in engine.new_memories:
        db.add_memory(memory_id, text)
    engine.new_memories.clear()
    db.write_save(engine.to_dict())


def new_engine() -> Engine:
    engine = Engine.new_run(SCENES, history=db.scene_history(), memories=db.load_memories())
    persist(engine)
    return engine


def get_engine() -> Engine:
    """Rebuild the engine from the saved run (or start a new one)."""
    data = db.load_save()
    if data is None:
        return new_engine()
    try:
        return Engine.from_dict(SCENES, data, history=db.scene_history(), memories=db.load_memories())
    except (KeyError, TypeError, ValueError):
        return new_engine()   # old or incompatible save -> start fresh


def payload(engine: Engine) -> dict:
    scene = engine.scenes[engine.current_id]
    image = scene.get("image")
    has_radio = bool(engine.flags.get("has_radio"))   # set by the scene where you pick it up
    has_diary = bool(engine.flags.get("has_diary"))
    return {
        "scene_id": scene["id"],
        "title": scene["title"],
        "text": [p.strip() for p in scene["text"].split("\n\n") if p.strip()],
        "image": f"/static/{image}" if image else None,
        "ending": bool(scene.get("ending")),
        "left": (scene.get("left") or {}).get("label"),
        "right": (scene.get("right") or {}).get("label"),
        "state": {
            k: int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
            for k, v in engine.state.items()
        },
        "clock": engine.clock(),
        "has_radio": has_radio,
        "has_diary": has_diary,
        "radio": engine.radio_log if has_radio else [],
        "diary": list(db.load_memories().values()) if has_diary else [],
        "best": db.best_days(),
        "runs": db.run_count(),
    }


# ---- routes -------------------------------------------------------------------

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/scene")
def api_scene():
    # Read-only: never changes the game, so refreshing is always safe.
    return jsonify(payload(get_engine()))


@app.post("/api/choose")
def api_choose():
    body = request.get_json(silent=True) or {}
    engine = get_engine()

    # Stale request (double swipe, old tab)? Send back the true current scene.
    if body.get("scene_id") != engine.current_id:
        return jsonify(payload(engine)), 409

    scene = engine.scenes[engine.current_id]
    side = body.get("side")
    if scene.get("ending") or side not in ("left", "right"):
        return jsonify(error="invalid choice"), 400

    new_scene = engine.choose(scene, side)
    persist(engine)

    if new_scene.get("ending"):
        db.record_run(
            days=int(engine.state.get("days", 0)),
            cause=new_scene["id"],
            seed=engine.seed,
            path=engine.path,
        )
    return jsonify(payload(engine))


@app.post("/api/restart")
def api_restart():
    db.delete_save()
    return jsonify(payload(new_engine()))
