"""条款级分块器 —— 把一部法律的全文切成「一条一 chunk」。

这一步决定了整个 RAG 的精度上限，比换模型、换向量库影响都大。
按「第X条」切，不是按固定字数切，原因有三：

1. 法条是天然语义完整的最小单元，切一半会丢条件句（「……的，处三年以下……」）
2. ``chunk_id`` 能直接定位到条，命中后可以精确引用，答题可溯源
3. 「第358条」这类查询能走精确匹配，不必依赖向量近似

分块规则：
- 按「第X条」正则切分，支持「第二百八十七条之一」这类增补条款
- 自动维护编/章/节层级路径，写进 ``hierarchy`` 供展示与过滤
- 无条款结构的文本（宪法序言、修正案）按「一、二、三」分项兜底切分
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Optional

from app.services.rag.normalize import extract_article_num
from app.services.rag.types import (
    LawChunk,
    LawMeta,
)

# 匹配条款号：第一条、第二百三十四条、第二百八十七条之一 等
_ARTICLE_PATTERN = re.compile(r"^(第[零〇一二两三四五六七八九十百千万\d]+条(?:之[零〇一二两三四五六七八九十百千万\d]+)?)\s*")

# 匹配层级标题：第X编、第X分编、第X章、第X节
_HIERARCHY_PATTERN = re.compile(r"^(第[零〇一二两三四五六七八九十百千万\d]+(?:分编|编|章|节))\s+(.+)")

# 匹配修正案、修改决定等没有「第X条」结构的分项：一、二、三、
_ITEM_PATTERN = re.compile(r"^([一二三四五六七八九十百千万]+)、\s*(.+)")

# 文件头元信息：key: value，中英文冒号都要认（语料来自网页抓取，两种都有）
_HEADER_KV_PATTERN = re.compile(r"^([^:：]{1,12})[:：]\s*(.*)$")

# 「效力标记: 现行有效版本（sxx=3）」里的状态码
_STATUS_MARKER_PATTERN = re.compile(r"sxx\s*=\s*(\d)")

_HEADER_SCAN_LINES = 15

# 层级判定：取标题开头的「第X编/分编/章/节」，用于维护层级栈
_LEVEL_PATTERN = re.compile(r"^第[零〇一二两三四五六七八九十百千万\d]+(分编|编|章|节)")


def extract_law_name(file_path: Path) -> str:
    """从文件名提取法规简称。

    Args:
        file_path: 语料文件路径，如 ``02_民法典.txt``

    Returns:
        str: 简称，如「民法典」；文件名没有 ``NN_`` 前缀时原样返回
    """
    stem = file_path.stem
    parts = stem.split("_", 1)
    return parts[1] if len(parts) == 2 else stem


def parse_law_meta(file_path: Path) -> LawMeta:
    """解析法规文件头部的元信息。

    语料头部是抓取时留下的固定格式：

        # 中华人民共和国民法典
        来源: 国家法律法规数据库 https://flk.npc.gov.cn/
        官方标题: 中华人民共和国民法典
        效力标记: 现行有效版本（sxx=3）
        公布日期: 2020-05-28
        施行日期: 2021-01-01
        制定机关: 全国人民代表大会
        法规类别: 法律

    这些字段不是装饰：``status`` 决定检索时要不要排除历史版本，
    ``effective_date`` 决定回答「什么时候开始施行」时能不能直接答。

    Args:
        file_path: 语料文件路径

    Returns:
        LawMeta: 解析出的元信息；缺失字段用默认值兜底
    """
    short_name = extract_law_name(file_path)
    fields = _read_header_fields(file_path)

    title = fields.get("官方标题") or ""
    if not title:
        first_line = _first_line(file_path)
        title = first_line[2:].strip() if first_line.startswith("# ") else short_name

    status = 3  # 默认按现行有效处理：语料里绝大多数是现行版本
    marker = _STATUS_MARKER_PATTERN.search(fields.get("效力标记", ""))
    if marker:
        parsed = int(marker.group(1))
        if 1 <= parsed <= 4:
            status = parsed

    return LawMeta(
        title=title,
        short_name=short_name,
        status=status,
        publish_date=_parse_date(fields.get("公布日期")),
        effective_date=_parse_date(fields.get("施行日期")),
        issuer=fields.get("制定机关") or None,
        category=fields.get("法规类别") or None,
        source=fields.get("来源") or LawMeta.source,
    )


def chunk_law_file(file_path: Path) -> list[LawChunk]:
    """把单个法律文件切成条款级分块。

    Args:
        file_path: 语料文件路径（UTF-8 编码的 txt）

    Returns:
        list[LawChunk]: 该法规的全部条款分块，按原文顺序
    """
    text = file_path.read_text(encoding="utf-8")
    law_name = extract_law_name(file_path)
    lines = _strip_header(text)

    chunks: list[LawChunk] = []
    hierarchy_stack: list[str] = []  # 当前层级栈：编 > 章 > 节
    current_article: Optional[str] = None
    current_content: list[str] = []
    fallback_lines: list[str] = []  # 没有条款结构时的兜底文本

    def flush_current() -> None:
        """把当前累积的条款落成一个 chunk。"""
        nonlocal current_article, current_content
        if current_article and current_content:
            body = "\n".join(current_content).strip()
            if body:
                chunks.append(
                    LawChunk(
                        law_name=law_name,
                        article_no=current_article,
                        # content 必须带条号：BM25 与精排都读这一份
                        content=f"{current_article} {body}",
                        chunk_id=f"{law_name}_{current_article}",
                        hierarchy=" > ".join(hierarchy_stack),
                        article_num=extract_article_num(current_article),
                    )
                )
        current_article = None
        current_content = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # 层级标题：切换层级栈，本身不进正文
        hierarchy_match = _HIERARCHY_PATTERN.match(stripped)
        if hierarchy_match:
            flush_current()
            level = _level_of(stripped)
            if level == "分编":
                # 分编挂在编下面：保留编，追加分编
                hierarchy_stack = [item for item in hierarchy_stack if _level_of(item) == "编"] + [stripped]
            elif level == "编":
                # 新的一编：整栈重置
                hierarchy_stack = [stripped]
            elif level == "章":
                # 保留编/分编，丢掉旧的章与节
                hierarchy_stack = [item for item in hierarchy_stack if _level_of(item) in ("编", "分编")] + [stripped]
            elif level == "节":
                hierarchy_stack = [
                    item for item in hierarchy_stack if _level_of(item) in ("编", "分编", "章")
                ] + [stripped]
            continue

        # 条款开头：先结算上一条，再开新的
        article_match = _ARTICLE_PATTERN.match(stripped)
        if article_match:
            flush_current()
            current_article = article_match.group(1)
            remainder = stripped[article_match.end() :].strip()
            if remainder:
                current_content.append(remainder)
            continue

        # 普通正文行
        if current_article:
            current_content.append(stripped)
        else:
            fallback_lines.append(stripped)

    flush_current()

    if not chunks:
        chunks.extend(_chunk_fallback_paragraphs(law_name, fallback_lines))

    return chunks


def chunk_all_laws(laws_dir: Path | str) -> list[LawChunk]:
    """扫描目录，对全部 txt 语料分块。

    Args:
        laws_dir: 语料目录，内含 ``*.txt``

    Returns:
        list[LawChunk]: 所有法规的分块合集
    """
    laws_path = Path(laws_dir)
    if not laws_path.exists():
        raise FileNotFoundError(f"语料目录不存在：{laws_path}")

    all_chunks: list[LawChunk] = []
    for txt_file in sorted(laws_path.glob("*.txt")):
        all_chunks.extend(chunk_law_file(txt_file))
    return all_chunks


def _chunk_fallback_paragraphs(law_name: str, lines: list[str]) -> list[LawChunk]:
    """为没有「第X条」结构的文本兜底分块。

    宪法序言、修正案、修改决定都属于这一类。按「一、二、三」分项切，
    切出来的 ``article_no`` 记为「第一项」，前言记为「前言」——保留可引用的锚点，
    总比整篇塞成一个 chunk 强。

    Args:
        law_name: 法规简称
        lines: 已剥离头部的正文行

    Returns:
        list[LawChunk]: 兜底分块结果
    """
    chunks: list[LawChunk] = []
    preface: list[str] = []
    current_no: Optional[str] = None
    current_content: list[str] = []

    def flush_item() -> None:
        """结算当前分项。"""
        nonlocal current_no, current_content
        if current_no and current_content:
            article_no = f"第{current_no}项"
            body = "\n".join(current_content).strip()
            if body:
                chunks.append(
                    LawChunk(
                        law_name=law_name,
                        article_no=article_no,
                        content=f"{article_no} {body}",
                        chunk_id=f"{law_name}_{article_no}",
                        article_num=None,
                    )
                )
        current_no = None
        current_content = []

    for line in lines:
        item_match = _ITEM_PATTERN.match(line)
        if item_match:
            flush_item()
            current_no = item_match.group(1)
            current_content.append(item_match.group(2).strip())
        elif current_no:
            current_content.append(line)
        else:
            preface.append(line)

    flush_item()

    preface_text = "\n".join(preface).strip()
    if preface_text:
        chunks.insert(
            0,
            LawChunk(
                law_name=law_name,
                article_no="前言",
                content=f"前言 {preface_text}",
                chunk_id=f"{law_name}_前言",
                article_num=None,
            ),
        )

    return chunks


def _level_of(title: str) -> str:
    """判断层级标题属于哪一级。

    不能对标题串直接 ``endswith("编")``：标题是「第二编　物权」，
    末尾是法规名而不是「编」字，判断会静默失效（层级整个丢失且不报错）。

    Args:
        title: 层级标题，如「第二编　物权」「第十二章　建设用地使用权」

    Returns:
        str: ``编`` / ``分编`` / ``章`` / ``节``；无法识别时返回空串
    """
    match = _LEVEL_PATTERN.match(title)
    return match.group(1) if match else ""


def _first_line(file_path: Path) -> str:
    """读文件第一行（只读一行，避免整篇读盘）。"""
    with open(file_path, encoding="utf-8") as handle:
        return handle.readline().strip()


def _read_header_fields(file_path: Path) -> dict[str, str]:
    """读取文件头的 key: value 元信息。

    Args:
        file_path: 语料文件路径

    Returns:
        dict[str, str]: 元信息键值对；遇到第一个空行即停止
    """
    fields: dict[str, str] = {}
    with open(file_path, encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if index > _HEADER_SCAN_LINES:
                break
            stripped = line.strip()
            if not stripped:
                break
            if stripped.startswith("#"):
                continue
            match = _HEADER_KV_PATTERN.match(stripped)
            if match:
                fields[match.group(1).strip()] = match.group(2).strip()
    return fields


def _strip_header(text: str) -> list[str]:
    """剥离正文前的标题与元信息块。

    Args:
        text: 文件全文

    Returns:
        list[str]: 正文行列表
    """
    lines = text.split("\n")
    content_start = 0

    if lines and lines[0].startswith("# "):
        # 标准语料：标题行 + 若干元信息行，之后空一行进入正文
        for index, line in enumerate(lines[1:], start=1):
            if not line.strip():
                content_start = index + 1
                break
        else:
            content_start = 1
    else:
        # 兜底：逐行跳过「来源:」「抓取时间:」这类头部字段
        for index, line in enumerate(lines):
            if line.startswith("来源:") or line.startswith("抓取时间:"):
                content_start = index + 1
            elif not line.strip() and content_start > 0:
                content_start = index + 1
            else:
                break

    return lines[content_start:]


def _parse_date(raw: Optional[str]) -> Optional[date]:
    """解析 ``YYYY-MM-DD`` 日期，解析不出来返回 None。

    Args:
        raw: 原始字符串，如「2020-05-28」

    Returns:
        Optional[date]: 日期对象；格式不符时为 None
    """
    if not raw:
        return None
    try:
        return date.fromisoformat(raw.strip()[:10])
    except ValueError:
        return None
