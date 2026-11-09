"""评测指标加载。

指标不写在代码里：``metrics/prompts/`` 下每个 ``.md`` 文件就是一个指标，
文件名即指标名，文件内容即该指标的系统提示词。加指标只要放一个 md 文件。
"""

import os

metrics = []

PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "prompts")

for file in os.listdir(PROMPTS_DIR):
    if file.endswith(".md"):
        metrics.append({"name": file.replace(".md", ""), "prompt": open(os.path.join(PROMPTS_DIR, file), "r").read()})
