from typing import Literal

import pydantic as pd

ThemeName = Literal[
    "textual-dark",
    "textual-light",
    "nord",
    "gruvbox",
    "catppuccin-mocha",
    "solarized-light",
    "dracula",
    "tokyo-night",
    "monokai",
    "flexokai",
    "catppuccin-latte",
    "solarized-light",
]

THEME_NAMES: tuple[ThemeName, ...] = ThemeName.__args__


class VisualConfig(pd.BaseModel):
    theme: ThemeName
