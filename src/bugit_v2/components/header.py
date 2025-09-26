from typing import final, override

from textual.app import ComposeResult, RenderResult
from textual.content import Content
from textual.events import Click
from textual.reactive import Reactive
from textual.widget import Widget
from textual.widgets import Static


@final
class HeaderIcon(Widget):
    """Display an 'icon' on the left of the header."""

    DEFAULT_CSS = """
    HeaderIcon {
        dock: left;
        padding: 0 1;
        width: 8;
        content-align: left middle;
        background: $primary 10%;
        margin-right: 1
    }

    HeaderIcon:hover {
        background: $foreground 10%;
    }
    """

    icon = Reactive("[b]>_ CMD")
    """The character to use as the icon within the header."""

    def on_mount(self) -> None:
        if self.app.ENABLE_COMMAND_PALETTE:
            self.tooltip = "Open the command palette"
        else:
            self.disabled = True

    async def on_click(self, event: Click) -> None:
        """Launch the command palette when icon is clicked."""
        event.stop()
        await self.run_action("app.command_palette")

    @override
    def render(self) -> RenderResult:
        """Render the header icon.

        Returns:
            The rendered icon.
        """
        return self.icon


@final
class RightAlignTitle(Static):
    DEFAULT_CSS = """
    RightAlignTitle {
        dock: right;
        padding: 0 1;
        width: auto;
        content-align: right middle;
    }
    """


@final
class HeaderTitle(Static):
    """Display the title / subtitle in the header."""

    DEFAULT_CSS = """
    HeaderTitle {
        text-wrap: nowrap;
        text-overflow: ellipsis;
        content-align: center middle;
        width: 100%;
    }
    """


@final
class SimpleHeader(Widget):
    """A header widget with icon and clock."""

    DEFAULT_CSS = """
    SimpleHeader {
        dock: top;
        width: 100%;
        background: $panel;
        color: $foreground;
        height: 1;
    }
    """

    DEFAULT_CLASSES = ""

    def __init__(
        self,
        *children: Widget,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ):
        super().__init__(*children, name=name, id=id, classes=classes)

    @override
    def compose(self) -> ComposeResult:
        yield HeaderIcon()
        for c in self.children:
            yield c
        yield RightAlignTitle()

    def format_title(self) -> Content:
        return self.app.format_title(self.screen_title, self.screen_sub_title)

    @property
    def screen_title(self) -> str:
        screen_title = self.screen.title
        title = screen_title if screen_title is not None else self.app.title
        return title

    @property
    def screen_sub_title(self) -> str:
        screen_sub_title = self.screen.sub_title
        sub_title = (
            screen_sub_title
            if screen_sub_title is not None
            else self.app.sub_title
        )
        return sub_title

    def on_mount(self) -> None:
        def set_title():
            self.query_exactly_one(RightAlignTitle).update(self.format_title())

        self.watch(self.app, "title", set_title)
        self.watch(self.app, "sub_title", set_title)
        self.watch(self.screen, "title", set_title)
        self.watch(self.screen, "sub_title", set_title)
