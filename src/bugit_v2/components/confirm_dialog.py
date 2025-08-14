from collections.abc import Sequence
from typing import Generic, TypeVar, cast, final

from textual.app import ComposeResult
from textual.containers import Center, HorizontalGroup
from textual.screen import ModalScreen
from textual.widgets import Button, Label
from typing_extensions import override

T = TypeVar("T", bound=str)  # 3.10 syntax


@final
class ConfirmScreen(Generic[T], ModalScreen[T]):

    CSS = """
    ConfirmScreen {
        align: center middle;
        background: $background 90%
    }
    """

    def __init__(
        self,
        prompt: str,
        choices: Sequence[tuple[str, T]],  # (display name, value)
        focus_id_on_mount: str | None = None,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        self.prompt = prompt
        self.choices = choices
        self.focus_id_on_mount = focus_id_on_mount

        super().__init__(name, id, classes)

    @override
    def compose(self) -> ComposeResult:
        yield Center(Label(self.prompt, classes="wa"))
        with Center():
            with HorizontalGroup(classes="center wa"):
                for display_name, value in self.choices[:-1]:
                    yield Button(display_name, id=value, classes="mr1")
                yield Button(self.choices[-1][0], id=self.choices[-1][1])

    def on_mount(self) -> None:
        if self.focus_id_on_mount:
            self.query_exactly_one(
                f"#{self.focus_id_on_mount}", Button
            ).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        assert event.button.id
        self.dismiss(cast(T, event.button.id))
