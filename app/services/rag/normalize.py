"""条号归一化 —— 让用户嘴里的「第358条」和库里的「第三百五十八条」对上。

两个方向都得处理，缺一个就有一半查询查不准：

1. **查询方向**（查询前）：把「第358条」转成中文数字。库里存的是中文，
   BM25 分词后是「第三」「三百」「百五」「五十」「十八」这类 bigram，
   阿拉伯数字一个都对不上，纯向量也常常输给字面完全不同的条文。
2. **入库方向**（建库时）：把中文条号转成阿拉伯数字写进 ``article_num``，
   「第 358 条」走这个整数列是 0.04 ms 的索引命中，而不是全表扫描。

还有一件容易被忽略的事：**法名要按最长匹配猜**。
「劳动合同法」里含有「合同法」，「民事诉讼法」里含有「诉讼法」，
按最短匹配猜会把查询引到错的法上。
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

# 中文数字字符表。两 = 二，法律文本里两种写法都有
_CN_DIGITS: dict[str, int] = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CN_UNITS: dict[str, int] = {"十": 10, "百": 100, "千": 1000, "万": 10000}

# 阿拉伯数字条号：第358条、第 12 条
_ARABIC_ARTICLE_RE = re.compile(r"第\s*(\d+)\s*条")

# 任意条号（中文或阿拉伯）：第三百五十八条、第358条、第二百八十七条之一
_ANY_ARTICLE_RE = re.compile(r"第\s*([0-9零〇一二两三四五六七八九十百千万]+)\s*条")

# 把中文条号里的「第X条」整段取出，用来从 chunk 的 article_no 还原数字
_ARTICLE_NO_RE = re.compile(r"第([0-9零〇一二两三四五六七八九十百千万]+)条")


def cn_to_int(text: str) -> Optional[int]:
    """把中文数字转成整数。

    Args:
        text: 中文数字串，如「三百五十八」「十」「一千零一」；也接受纯阿拉伯数字

    Returns:
        Optional[int]: 解析成功返回整数，含无法识别的字符时返回 None

    Examples:
        >>> cn_to_int("三百五十八")
        358
        >>> cn_to_int("十")
        10
        >>> cn_to_int("一千零一")
        1001
    """
    if not text:
        return None
    if text.isdigit():
        return int(text)

    total = 0  # 万以上的部分
    section = 0  # 当前「万」段内累计
    number = 0  # 待乘单位的数字

    for char in text:
        if char in _CN_DIGITS:
            number = _CN_DIGITS[char]
        elif char in _CN_UNITS:
            unit = _CN_UNITS[char]
            if unit == 10000:
                # 「万」是分段单位：把当前段结算后整体乘上去
                section = (section + number) * unit
                total += section
                section = 0
            else:
                # 「十」「百」「千」直接乘：单独一个「十」读作 10，所以 number 为 0 时按 1 算
                section += (number or 1) * unit
            number = 0
        else:
            return None

    return total + section + number


def int_to_cn(number: int) -> str:
    """把整数转成中文数字（法律条号口径）。

    Args:
        number: 阿拉伯数字，如 358

    Returns:
        str: 中文数字，如「三百五十八」

    Examples:
        >>> int_to_cn(358)
        '三百五十八'
        >>> int_to_cn(10)
        '十'
        >>> int_to_cn(1001)
        '一千零一'
    """
    if number <= 0:
        return str(number)

    digits = "零一二三四五六七八九"
    units = ["", "十", "百", "千"]

    if number >= 10000:
        # 法条不会到这个量级，超过万位直接退回阿拉伯数字，避免造出奇怪的读法
        return str(number)

    parts: list[str] = []
    chars = str(number)
    length = len(chars)
    for index, char in enumerate(chars):
        digit = int(char)
        unit_index = length - index - 1
        if digit == 0:
            # 末尾的零不读；中间连续的零只补一个「零」
            if parts and parts[-1] != "零":
                parts.append("零")
            continue
        parts.append(digits[digit] + units[unit_index])

    result = "".join(parts).rstrip("零")
    # 「一十」在中文里读「十」：第十条，不是第十条里的「一十」
    if result.startswith("一十"):
        result = result[1:]
    return result


def extract_article_num(article_no: str) -> Optional[int]:
    """从条号字符串里取出阿拉伯数字。

    Args:
        article_no: 条号，如「第三百五十八条」「第358条」「第二百八十七条之一」；
            「前言」「第一项」这类没有条号结构的返回 None

    Returns:
        Optional[int]: 条号数字；「之一」这类增补条款按主条号计
    """
    match = _ARTICLE_NO_RE.search(article_no or "")
    if not match:
        return None
    return cn_to_int(match.group(1))


def normalize_query(query: str) -> str:
    """把查询里的阿拉伯条号改成中文，对齐库里的写法。

    Args:
        query: 用户原始查询，如「民法典第358条讲什么」

    Returns:
        str: 归一化后的查询，如「民法典第三百五十八条讲什么」
    """

    def _replace(match: re.Match[str]) -> str:
        return f"第{int_to_cn(int(match.group(1)))}条"

    return _ARABIC_ARTICLE_RE.sub(_replace, query)


def extract_query_articles(query: str) -> list[int]:
    """取出查询里提到的所有条号数字。

    Args:
        query: 用户查询

    Returns:
        list[int]: 去重后的条号数字，按出现顺序；没提条号时为空列表
    """
    numbers: list[int] = []
    for match in _ANY_ARTICLE_RE.finditer(query):
        value = cn_to_int(match.group(1))
        # 超过一万的「条号」几乎肯定是误匹配（比如「第一万条」这种不存在的写法）
        if value and value < 10000 and value not in numbers:
            numbers.append(value)
    return numbers


def guess_law_names(query: str, known_names: Iterable[str], limit: int = 3) -> list[str]:
    """从查询里猜出用户指的是哪几部法。

    按名称长度从长到短匹配，避免「劳动合同法」被「合同法」抢先命中。

    Args:
        query: 用户查询
        known_names: 库里已知的法规简称（或全称）
        limit: 最多返回几个

    Returns:
        list[str]: 命中的法规名，按名称长度降序
    """
    hits = [name for name in known_names if name and name in query]
    # 长名优先：先匹配到的长名如果包含短名，短名就是噪声
    hits.sort(key=len, reverse=True)
    result: list[str] = []
    for name in hits:
        if any(name != chosen and name in chosen for chosen in result):
            continue
        result.append(name)
        if len(result) >= limit:
            break
    return result
