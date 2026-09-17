from engine import Engine, load_scenes
from image import show_image
import ui
from ui import console, render

STARTING_STATE = {
    "health": 35,
    "stamina": 30,
    "supplies": 0,
    "trust": 0,
    "infection_exposure": 0,
    "days": 0,
}


def main() -> None:
    scenes = load_scenes()
    engine = Engine(scenes, start_scene="P1S0", state=dict(STARTING_STATE))
    scene = engine.enter(engine.current_id)

    ui.init()
    try:
        while True:
            console.clear()  # wipe last turn's frame before drawing anything new
            show_image(scene.get("image"))

            choices = engine.available_choices(scene)
            idx = render(scene, choices, engine.state, engine.radio_log)
            if idx is None:
                break
            scene = engine.choose(scene, idx)
    finally:
        ui.shutdown()

    console.print(f"\n[bold] You are filled with determination! [/] Days survived: {int(engine.state.get('days', 0))}")


if __name__ == "__main__":
    main()
