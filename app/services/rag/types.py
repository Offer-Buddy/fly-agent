"""法律知识库的数据结构。

这里的 ``LawChunk`` 是检索链路里流转的最小单元，**不依赖任何存储实现**：
pgvector 读写、BM25 建索引、精排打分都只认这个 dataclass。
把「数据长什么样」和「数据存在哪」分开，后面换向量库时才不用动检索逻辑。
"""

from dataclasses import (
    dataclass,
    field,
)
from datetime import date
from typing import Optional


@dataclass(slots=True)
class LawMeta:
    """一部法规的元信息。

    Attributes:
        title: 官方全称，如「中华人民共和国民法典」
        short_name: 简称，如「民法典」，也是 ``chunk_id`` 的前缀
        status: 效力状态，1 历史版本 / 2 已修改 / 3 现行有效 / 4 尚未生效
        publish_date: 公布日期
        effective_date: 施行日期
        issuer: 制定机关
        category: 法规类别，如「法律」「行政法规」
        version_id: 官方版本标识（bbbs），用于判断有没有出新版
        source: 数据来源
    """

    title: str
    short_name: str
    status: int = 3
    publish_date: Optional[date] = None
    effective_date: Optional[date] = None
    issuer: Optional[str] = None
    category: Optional[str] = None
    version_id: Optional[str] = None
    source: str = "flk.npc.gov.cn"


@dataclass(slots=True)
class LawChunk:
    """一条法条分块 —— 检索的最小单元。

    Attributes:
        law_name: 法规简称，如「民法典」
        article_no: 条号，如「第三百五十八条」；无条款结构的文本用「前言」「第一项」
        content: 条号 + 正文。**必须含条号**，否则「第358条怎么规定的」这类查询永远召回不到
        chunk_id: 全局唯一标识，格式 ``{法规简称}_{条号}``
        hierarchy: 层级路径，如「第三编 合同 > 第一章 一般规定」
        article_num: 条号转成的阿拉伯数字，精确条号查询走这一列
        score: 精排分数，检索时回填，不落库
        metadata: 预留扩展位
    """

    law_name: str
    article_no: str
    content: str
    chunk_id: str
    hierarchy: str = ""
    article_num: Optional[int] = None
    score: float = 0.0
    metadata: dict = field(default_factory=dict)

    @property
    def citation(self) -> str:
        """返回可直接写进答案的引用形式，如「民法典 第三百五十八条」。"""
        return f"{self.law_name} {self.article_no}"
