"""POC 1 · LangGraph：把流程画成图，状态、检查点、中断都是框架自带的概念。

跑法：python poc_langgraph.py
看点：跑到「要不要放行」时它会停下来；进程里换个 config 继续，接着走。
"""

import json
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from stub_model import complete


class Ticket(TypedDict, total=False):
    text: str
    kind: str
    amount: int
    policy: str
    draft: str
    need_human: bool
    approved: bool


def classify(state: Ticket):
    out = json.loads(complete("classify", state["text"]))
    return {"kind": out["kind"], "amount": out["amount"]}


def lookup(state: Ticket):
    out = json.loads(complete("policy", state["kind"]))
    return {"policy": out["policy"]}


def draft(state: Ticket):
    payload = json.dumps({"kind": state["kind"], "amount": state["amount"]}, ensure_ascii=False)
    out = json.loads(complete("draft", payload))
    return {"draft": out["draft"], "need_human": out["need_human"]}


def gate(state: Ticket):
    """人工闸门。interrupt 会在这里把整个图冻结，并把问题抛回给调用方。"""
    ok = interrupt({"ask": f"{state['amount']} 元退款超过 500，是否放行？", "draft": state["draft"]})
    return {"approved": bool(ok)}


def route(state: Ticket):
    return "gate" if state["need_human"] else END


builder = StateGraph(Ticket)
builder.add_node("classify", classify)
builder.add_node("lookup", lookup)
builder.add_node("draft", draft)
builder.add_node("gate", gate)
builder.add_edge(START, "classify")
builder.add_edge("classify", "lookup")
builder.add_edge("lookup", "draft")
builder.add_conditional_edges("draft", route, {"gate": "gate", END: END})
builder.add_edge("gate", END)

app = builder.compile(checkpointer=InMemorySaver())


def main():
    cfg = {"configurable": {"thread_id": "T-1"}}
    out = app.invoke({"text": "我上周买的耳机左耳没声音，想退款 899 元"}, cfg)

    print("第一步到第三步跑完，状态里已经有：")
    print("  kind =", out["kind"], "/ amount =", out["amount"])
    print("  policy =", out["policy"])

    snap = app.get_state(cfg)
    print("  图停在哪：", snap.next, "（这就是检查点）")

    print("\n把问题抛给人，等人回话：")
    print(" ", snap.tasks[0].interrupts[0].value if snap.tasks and snap.tasks[0].interrupts else "（无）")

    final = app.invoke(Command(resume=True), cfg)
    print("\n人点了同意，接着走完：")
    print("  approved =", final["approved"], "/ 回复 =", final["draft"])

    # 第二张单子不触发人工闸门，一条路走到底
    out2 = app.invoke({"text": "升级完固件之后连不上蓝牙了"}, {"configurable": {"thread_id": "T-2"}})
    print("\n第二张单子不用签字，直接出结果：", out2["draft"])


if __name__ == "__main__":
    main()
