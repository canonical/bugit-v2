import pydantic as pd


class VisualConfig(pd.BaseModel):
    theme: str
