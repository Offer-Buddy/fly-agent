"""工单分诊：三个 POC 共用的「假模型」。

真模型在这里只干一件事：给定阶段，返回一段 JSON 文本。
用规则模拟它，换来三件事：离线可跑、结果可复现、对比的只有框架结构本身。
换成真模型时只改 complete()，其余代码一行不动。

为什么它要「自己从提示里找字段」：CrewAI 和 AutoGen 都会在提示外面裹一层，
真正传进来的是一整段对话。所以这里按阶段各自去文本里找线索，而不是等一个干净入参。
"""

import json
import re

KB = {
    "refund": "7 天内无理由可退；单笔超过 500 元需主管签字后放行。",
    "tech": "先确认固件版本并重置；仍不恢复的走换新，不直接退款。",
    "complaint": "24 小时内升级客服主管处理，并在 48 小时内回访一次。",
}

KIND_RE = re.compile(r'"kind"\s*:\s*"(refund|tech|complaint)"')
KIND_LOOSE = re.compile(r"\b(refund|tech|complaint)\b")
AMOUNT_RE = re.compile(r'"amount"\s*:\s*(\d+)')


def _amount(text):
    m = re.search(r"(\d{2,6})\s*元", text)
    return int(m.group(1)) if m else 0


def _pick_kind(text):
    m = KIND_RE.search(text) or KIND_LOOSE.search(text)
    if m:
        return m.group(1)
    if any(k in text for k in ("退款", "退货", "退钱", "不要了")):
        return "refund"
    if any(k in text for k in ("投诉", "态度", "太差", "举报")):
        return "complaint"
    return "tech"


def classify(text):
    return {"kind": _pick_kind(text), "amount": _amount(text)}


def complete(stage, payload):
    """返回一段 JSON 字符串，模拟模型输出。"""
    text = payload or ""

    if stage == "classify":
        out = classify(text)
        return json.dumps(out, ensure_ascii=False)

    if stage == "policy":
        return json.dumps({"policy": KB[_pick_kind(text)]}, ensure_ascii=False)

    if stage == "draft":
        kind = _pick_kind(text)
        m = AMOUNT_RE.search(text)
        amount = int(m.group(1)) if m else _amount(text)
        body = {
            "refund": f"可以退，{amount} 元将按原路退回。",
            "tech": "先按固件重置走一遍，不行我们直接换新。",
            "complaint": "已记录并升级主管，24 小时内会有专人联系你。",
        }[kind]
        need = kind == "refund" and amount >= 500
        return json.dumps({"draft": body, "need_human": need}, ensure_ascii=False)

    return json.dumps({"error": "unknown stage"}, ensure_ascii=False)


def run_pipeline(text):
    """不依赖任何框架的参考实现，也当对照组的标准答案。"""
    step1 = classify(text)
    step2 = json.loads(complete("policy", json.dumps(step1, ensure_ascii=False)))
    step3 = json.loads(complete("draft", json.dumps({**step1, **step2}, ensure_ascii=False)))
    return {**step1, **step2, **step3}


if __name__ == "__main__":
    print(json.dumps(run_pipeline("我上周买的耳机左耳没声音，想退款 899 元"), ensure_ascii=False, indent=2))
