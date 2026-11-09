"""评测用的数据模型。

注意：``description`` 不是文档，它会随 JSON Schema 一起发给评测模型，
作为「该怎么打分」的指令，因此保持英文。
"""

from pydantic import (
    BaseModel,
    Field,
)


class ScoreSchema(BaseModel):
    """评测打分的结构化输出。

    Attributes:
        score: 0~1 之间的分数
        reasoning: 一句话说明打分理由
    """

    score: float = Field(description="provide a score between 0 and 1")
    reasoning: str = Field(description="provide a one sentence reasoning")
