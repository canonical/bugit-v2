from typing import final

from rich.console import RenderableType
from textual.content import ContentText
from textual.widgets import Button
from textual.widgets.button import ButtonVariant


@final
class CompactButton(Button):
    DEFAULT_CSS = """
    CompactButton {
        width: auto;
        height: auto;
        min-width: 1;
        min-height: 1;
        padding: 0;
        border: none;
    }
    """

    def __init__(
        self,
        label: ContentText | None = None,
        variant: ButtonVariant = "default",
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        tooltip: RenderableType | None = None,
        action: str | None = None,
        flat: bool = False,
    ):
        super().__init__(
            label,
            variant,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
            tooltip=tooltip,
            action=action,
            compact=True,
            flat=flat,
        )
