from collections.abc import Mapping, Sequence
from typing import cast, final

from textual import on
from textual.app import ComposeResult
from textual.containers import HorizontalGroup, VerticalGroup, VerticalScroll
from textual.reactive import var
from textual.widget import Widget
from textual.widgets import Button, Label, SelectionList
from textual.widgets.selection_list import Selection
from typing_extensions import override


@final
class SelectionWithPreview(Widget):
    # maps from selection key to actual values
    data: Mapping[str, Sequence[str]]
    preview_label: Label
    selected_keys = var[list[str]]([])

    def __init__(
        self,
        data: Mapping[str, Sequence[str]],
        preview_label: Label,
        *children: Widget,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        markup: bool = True,
    ) -> None:
        super().__init__(
            *children,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
            markup=markup,
        )
        self.data = data
        self.preview_label = preview_label

    @override
    def compose(self) -> ComposeResult:
        with HorizontalGroup(classes="h100", id="outer"):
            with VerticalGroup(classes="mr1 w50"):
                yield SelectionList[str](
                    *(Selection(k, k, False) for k in self.data.keys()),
                    classes="nb",
                    id="selection_list",
                )
                with HorizontalGroup(classes="right db"):
                    yield Button(
                        "Clear", compact=True, classes="editor_button"
                    )

            with VerticalGroup(
                classes="h100",
            ):
                yield self.preview_label
                yield VerticalScroll(
                    Label(id="display", disabled=True),
                    classes="nb",
                )

    def watch_selected_keys(self):
        display = self.query_exactly_one("#display", Label)

        if len(self.selected_keys) == 0:
            display.update("[grey][i]nothing selected...")
        else:
            display.update(
                "\n".join(
                    "\n".join(self.data[key]) for key in self.selected_keys
                )
            )

    @on(SelectionList.SelectedChanged)
    def handle_selection_change(self) -> None:
        self.selected_keys = cast(
            list[str], self.query_exactly_one(SelectionList).selected
        )

    @on(Button.Pressed)
    def clear_selection(self) -> None:
        self.query_exactly_one(SelectionList).deselect_all()

    @property
    def selected_values(self) -> Sequence[str]:
        """Returns a flat list of selected values"""
        values = list[str]()
        for k in self.selected_keys:
            for val in self.data[k]:
                values.append(val)
        return values

    def restore_selection(self, values: Sequence[str]) -> None:
        """Restores the selection keys in the UI based on @param values

        :param values: find the keys to restore based on these values
        """
        keys_to_select: list[str] = []
        for value in values:
            for k, v in self.data.items():
                if value in v:
                    keys_to_select.append(k)
                    break
        elem = cast(
            SelectionList[str],  # can't have generics in isinstance call
            self.query_exactly_one("#selection_list", SelectionList),
        )
        for k in keys_to_select:
            elem.select(k)
