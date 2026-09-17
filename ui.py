from __future__ import annotations

from typing import Optional

from rich import box
from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text

console = Console()

HIDDEN_STATS = {"days"}
_alt_screen_active = False


def _format_stats(state: dict) -> str:
    parts = []
    for key, value in state.items():
        if key in HIDDEN_STATS:
            continue
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        parts.append(f"[dim]{key}[/dim] {value}")
    return "   ".join(parts)


def _build_layout(scene: dict, choices: list[dict], state: dict, radio_log: list[str]) -> Layout:
    root = Layout()
    choices_height = max(3, (len(choices) if choices else 1) + 2)

    root.split_column(
        Layout(name="top", ratio=1),
        Layout(name="choices", size=choices_height),
    )
    # side-by-side: story (wide) | radio (narrow sidebar)
    root["top"].split_row(
        Layout(name="main", ratio=2),
        Layout(name="radio", ratio=1),
    )

    day = state.get("days", 0)
    day = int(day) if isinstance(day, float) and day.is_integer() else day
    subtitle = f"[bold]Day {day}[/bold]   {_format_stats(state)}"

    root["main"].update(
        Panel(
            Text(scene.get("text", "").strip(), overflow="fold"),
            title=f"[bold cyan]Cestia[/]",
            subtitle=subtitle,
            box=box.ROUNDED,
            border_style="cyan",
            padding=(1, 2),
        )
    )

    radio_text = Text(
        "\n".join(radio_log[-8:]) if radio_log else "( silence )",
        style="green",
        overflow="fold",
    )
    root["radio"].update(
        Panel(
            radio_text,
            title="[bold red]\u25c9 RADIO[/]",
            box=box.HEAVY,
            border_style="red",
            padding=(1, 1),
        )
    )

    if choices:
        lines = "\n".join(
            f"[bold yellow]{i}[/bold yellow]  {c['label']}" for i, c in enumerate(choices, 1)
        )
    else:
        lines = "[bold red]The story ends here.[/]"
    root["choices"].update(
        Panel(
            Text.from_markup(lines),
            title="[bold]Choose[/]",
            box=box.ROUNDED,
            border_style="bright_white",
            padding=(0, 2),
        )
    )

    return root


def init() -> None:
    """Enter the alternate screen buffer once. Nothing drawn afterward can
    ever bleed into normal scrollback, no matter what overflows."""
    global _alt_screen_active
    if not _alt_screen_active:
        console.set_alt_screen(True)
        console.show_cursor(False)
        _alt_screen_active = True


def shutdown() -> None:
    global _alt_screen_active
    if _alt_screen_active:
        console.show_cursor(True)
        console.set_alt_screen(False)
        _alt_screen_active = False


def render(
    scene: dict,
    choices: list[dict],
    state: dict,
    radio_log: list[str],
    reserved_rows: int = 0,
) -> Optional[int]:
    """reserved_rows: rows already consumed above this frame (e.g. by an
    image) that the layout must NOT also try to use — see caveat below."""
    layout = _build_layout(scene, choices, state, radio_log)

    width, height = console.size
    # Steal 1 row for the input prompt + whatever the caller already used,
    # so frame height is *guaranteed* to never exceed the real terminal.
    budget = max(3, height - 1 - reserved_rows)
    console.size = (width, budget)
    console.print(layout)
    console.size = (width, height)

    if not choices:
        return None

    console.show_cursor(True)
    try:
        while True:
            raw = console.input("[bold]>[/bold] ")
            if raw.isdigit() and 1 <= int(raw) <= len(choices):
                return int(raw) - 1
            console.print(f"[red]Enter a number 1-{len(choices)}[/]")
    finally:
        console.show_cursor(False)
