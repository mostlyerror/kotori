from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Input, Label


class NoteInput(ModalScreen[str]):
    DEFAULT_CSS = """
    NoteInput { align: center middle; }
    NoteInput > Container {
        width: 60;
        height: 7;
        border: solid $accent;
        padding: 1 2;
        background: $surface;
        layout: vertical;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss('')", "Cancel", show=True),
    ]

    def __init__(self, symbol: str, **kwargs):
        super().__init__(**kwargs)
        self.symbol = symbol

    def compose(self) -> ComposeResult:
        with Container():
            yield Label(f"Add note for {self.symbol}")
            yield Input(placeholder="Type note and press Enter...", id="note-input")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)
