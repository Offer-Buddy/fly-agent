"""法律知识库单测：分块、条号归一化、RRF 融合。

只测**纯逻辑**，不加载模型、不连数据库——这样 CI 里 1 秒能跑完，
也不会因为模型文件缺失而红一片。检索链路的端到端验证走
``scripts/eval_retrieval.py``。
"""

from __future__ import annotations

import sys
from pathlib import Path

# Windows 上 psycopg 的异步池不支持 ProactorEventLoop；本模块虽不连库，
# 但导入 app.services 会建连接池对象，先切好循环策略避免偶发告警。
import asyncio

if sys.platform == "win32" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.services.rag.chunker import (
    chunk_law_file,
    parse_law_meta,
)
from app.services.rag.hybrid import HybridRetriever
from app.services.rag.keyword import tokenize
from app.services.rag.normalize import (
    cn_to_int,
    extract_article_num,
    extract_query_articles,
    guess_law_names,
    int_to_cn,
    normalize_query,
)
from app.services.rag.types import LawChunk

SAMPLE_LAW = """# 中华人民共和国测试法
来源: 国家法律法规数据库 https://flk.npc.gov.cn/
官方标题: 中华人民共和国测试法
效力标记: 现行有效版本（sxx=3）
公布日期: 2020-05-28
施行日期: 2021-01-01
制定机关: 全国人民代表大会
法规类别: 法律

第一编　总　　则
第一章　基本规定
第一条　为了测试分块器，制定本法。
第二条　本法所称测试，指对分块逻辑的验证。
第二章　具体规定
第三条　条文可以有多个自然段。
第二段内容应当并入同一条。
第二编　分　　则
第四条　新的一编应重置层级。
第五条之一　增补条款也要能切出来。
"""


def _write_sample(tmp_path: Path) -> Path:
    """把样例法规写到临时文件。

    Args:
        tmp_path: pytest 提供的临时目录

    Returns:
        Path: 文件路径
    """
    file_path = tmp_path / "99_测试法.txt"
    file_path.write_text(SAMPLE_LAW, encoding="utf-8")
    return file_path


def test_chunk_splits_by_article(tmp_path: Path) -> None:
    """按条切分，且正文带条号。"""
    chunks = chunk_law_file(_write_sample(tmp_path))
    assert [chunk.article_no for chunk in chunks] == [
        "第一条",
        "第二条",
        "第三条",
        "第四条",
        "第五条之一",
    ]
    assert chunks[0].chunk_id == "测试法_第一条"
    # content 必须含条号，否则条号类查询召回不到
    assert chunks[0].content.startswith("第一条")
    assert chunks[0].law_name == "测试法"


def test_chunk_keeps_hierarchy(tmp_path: Path) -> None:
    """层级路径要跟着编/章走，换编要重置。"""
    chunks = chunk_law_file(_write_sample(tmp_path))
    assert "总　　则" in chunks[0].hierarchy
    assert "第一章" in chunks[0].hierarchy
    # 第三条已进入第二章，章要换、编要保留
    assert "第二章" in chunks[2].hierarchy and "总　　则" in chunks[2].hierarchy
    # 第四条进入新的一编，旧的编章都不能留
    assert "分　　则" in chunks[3].hierarchy
    assert "总　　则" not in chunks[3].hierarchy


def test_chunk_merges_paragraphs(tmp_path: Path) -> None:
    """同一条的多个自然段应并进同一个 chunk。"""
    chunks = chunk_law_file(_write_sample(tmp_path))
    third = next(chunk for chunk in chunks if chunk.article_no == "第三条")
    assert "第二段内容应当并入同一条" in third.content


def test_parse_law_meta(tmp_path: Path) -> None:
    """文件头元信息要能解析成结构化字段。"""
    meta = parse_law_meta(_write_sample(tmp_path))
    assert meta.title == "中华人民共和国测试法"
    assert meta.short_name == "测试法"
    assert meta.status == 3
    assert meta.effective_date is not None and meta.effective_date.year == 2021
    assert meta.issuer == "全国人民代表大会"


def test_article_num_filled(tmp_path: Path) -> None:
    """article_num 要转成阿拉伯数字，增补条款按主条号计。"""
    chunks = chunk_law_file(_write_sample(tmp_path))
    by_no = {chunk.article_no: chunk for chunk in chunks}
    assert by_no["第四条"].article_num == 4
    assert by_no["第五条之一"].article_num == 5


def test_cn_number_round_trip() -> None:
    """中文数字与阿拉伯数字互转。"""
    cases = {1: "一", 10: "十", 20: "二十", 100: "一百", 358: "三百五十八", 1201: "一千二百零一"}
    for number, chinese in cases.items():
        assert int_to_cn(number) == chinese
        assert cn_to_int(chinese) == number


def test_normalize_query_converts_arabic_article() -> None:
    """查询里的「第358条」要转成中文。"""
    assert normalize_query("民法典第358条怎么规定") == "民法典第三百五十八条怎么规定"
    assert normalize_query("第1条") == "第一条"


def test_extract_article_num_variants() -> None:
    """各种条号写法都要能取出数字。"""
    assert extract_article_num("第三百五十八条") == 358
    assert extract_article_num("第二百八十七条之一") == 287
    assert extract_article_num("前言") is None
    assert extract_query_articles("刑法第20条和民法典第1179条") == [20, 1179]


def test_guess_law_names_prefers_longest() -> None:
    """法名按最长匹配，避免「劳动合同法」被「合同法」抢走。"""
    names = ["合同法", "劳动合同法", "劳动法"]
    assert guess_law_names("劳动合同法第38条", names) == ["劳动合同法"]


def test_tokenize_unigram_bigram() -> None:
    """中文切单字 + 相邻双字，英文整体小写。"""
    tokens = tokenize("居住权 OK")
    assert "居" in tokens and "居住" in tokens and "住权" in tokens
    assert "ok" in tokens


def _chunk(chunk_id: str) -> LawChunk:
    """构造测试用分块。

    Args:
        chunk_id: 分块标识

    Returns:
        LawChunk: 分块对象
    """
    return LawChunk(law_name=chunk_id.split("_")[0], article_no="第一条", content=chunk_id, chunk_id=chunk_id)


def test_rrf_favours_consensus() -> None:
    """两路都命中的结果应排在只有一路命中的前面。"""
    retriever = HybridRetriever()
    list_a = [(_chunk("甲_1"), 1.0), (_chunk("乙_1"), 0.9), (_chunk("丙_1"), 0.8)]
    list_b = [(_chunk("乙_1"), 5.0), (_chunk("丁_1"), 4.0), (_chunk("甲_1"), 3.0)]

    fused = retriever._rrf_fuse([list_a, list_b], k=60)
    order = [chunk.chunk_id for chunk in fused]

    # 乙与甲两路都有：乙在 A 排 2、B 排 1；甲在 A 排 1、B 排 3 —— 分差极小，但都在
    # 只有一路命中的丙、丁之前
    assert set(order[:2]) == {"甲_1", "乙_1"}
    assert order[0] == "乙_1"
