import pydantic as pd
from textual.theme import BUILTIN_THEMES

_FALLBACK_THEME = "solarized-light"


class VisualConfig(pd.BaseModel):
    theme: str

    @pd.field_validator("theme")
    @classmethod
    def validate_theme(cls, v: str) -> str:
        if v not in BUILTIN_THEMES:
            return _FALLBACK_THEME
        return v
