"""课程 HTML 交付前机检。"""

import re
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "docs/agent-memory.html"
text = open(path, encoding="utf-8").read()

fails = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS  " if ok else "FAIL  ") + name + (("  <- " + detail) if detail and not ok else ""))
    if not ok:
        fails.append(name)


# 1. div 配平
opens = len(re.findall(r"<div\b", text))
closes = len(re.findall(r"</div>", text))
check("1 div 开闭配平", opens == closes, f"{opens} vs {closes}")

# 2. CSS 变量都有定义
defined = set(re.findall(r"(--[a-z0-9\-]+)\s*:", text))
used = set(re.findall(r"var\((--[a-z0-9\-]+)\)", text))
missing = used - defined
check("2 无未定义 CSS 变量", not missing, str(missing))

# 3. 导航锚点一一对应
# 只比对导航块内的锚点与 section id，不绑定导航的具体 class（侧栏 / 顶部导航都适用）
# 注意不能统计全文 href="#..."：正文里的行内跳转会把结果污染
nav_blocks = re.findall(r"<nav\b.*?</nav>", text, re.S)
navs = re.findall(r'href="#([a-z0-9\-]+)"', nav_blocks[0]) if nav_blocks else []
sections = re.findall(r'<section id="([a-z0-9\-]+)"', text)
check("3 导航锚点与章节一致", navs == sections, f"nav={navs} sec={sections}")

# 4. 表格加法自洽：合计行 == 各列求和
tables = re.findall(r"(<table\b.*?</table>)", text, re.S)
bad_tables = []
for t in tables:
    rows = re.findall(r"(<tr[^>]*>.*?</tr>)", t, re.S)
    total_idx = [i for i, r in enumerate(rows) if "total-row" in r]
    if not total_idx:
        continue
    ti = total_idx[0]
    body = rows[:ti]
    total_cells = re.findall(r"<td[^>]*>(.*?)</td>", rows[ti], re.S)
    # 合计格：纯数字
    total_nums = []
    for c in total_cells:
        m = re.fullmatch(r"\s*(\d+)\s*", re.sub(r"<[^>]+>", "", c).strip())
        total_nums.append(int(m.group(1)) if m else None)
    num_cells = [n for n in total_nums if n is not None]
    if len(num_cells) < 2:
        continue
    # 每行取纯数字单元格
    col_sums = {}
    for r in body:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)
        for i, c in enumerate(cells):
            plain = re.sub(r"<[^>]+>", "", c).strip()
            if re.fullmatch(r"\d+", plain):
                col_sums[i] = col_sums.get(i, 0) + int(plain)
    # 逐格比对（合计行里的数字按顺序对应其所在列）
    total_positions = [i for i, n in enumerate(total_nums) if n is not None]
    mism = []
    for pos in total_positions:
        if total_nums[pos] != col_sums.get(pos, 0):
            mism.append(f"col{pos}: {total_nums[pos]} != {col_sums.get(pos, 0)}")
    if mism:
        bad_tables.append(mism)
check("4 表格合计行加法自洽", not bad_tables, str(bad_tables))

# 5. 宽表可横向滚动
check("5 宽表窄屏可滚动", text.count('class="tblwrap"') >= len(tables) - 1, "存在未包裹的 table")

# 6. 打印行为
check("6 打印隐藏顶栏", "@media print" in text and ".topbar" in text)

# 7. 无外部依赖
ext = re.findall(r'(?:src|href)="(https?://[^"]+)"', text)
check("7 无外部依赖", not ext, str(ext))

# 8. 无占位残留
ph = re.findall(r"TODO|______|（替换）|XXX|占位", text)
check("8 无占位残留", not ph, str(ph))

# 9. 无授课管理痕迹
words = ["课时", "分钟", "课堂", "学员", "教师", "授课", "排课", "进度表", "本课", "教学"]
hit = {w: text.count(w) for w in words if w in text}
check("9 无授课管理痕迹", not hit, str(hit))

# 10. 开篇两块 + 章末带走
takeaways = len(re.findall(r'class="takeaway"', text))
chapters = re.findall(r'<h2><span class="num">\d+</span>', text)
check("10a 章末 takeaway 数 == 正文章节数", takeaways == len(chapters), f"{takeaways} vs {len(chapters)}")
check("10b 无目录块", 'class="toc"' not in text and "文档信息" not in text)
check("10c 首块为课程目标", sections[:2] == ["goal", "map"], str(sections[:2]))

# 11. 章节号连续
nums = [int(n) for n in re.findall(r'<h2><span class="num">(\d+)</span>', text)]
check("11 章节号连续 1..N", nums == list(range(1, len(nums) + 1)), str(nums))

# 12. 每章都以 takeaway 收尾（h2 到下一个 h2 之间必有 takeaway）
blocks = re.split(r'<h2><span class="num">\d+</span>', text)[1:]
no_end = [i + 1 for i, b in enumerate(blocks) if 'class="takeaway"' not in b]
check("12 每章都有 takeaway", not no_end, str(no_end))

print()
print("FAILED: " + ", ".join(fails) if fails else "ALL PASS")
sys.exit(1 if fails else 0)
