"""POC 0 · 不用框架：一个循环加三个函数。

这是对照组。它证明了「Agent 不必依赖框架」——
也标出了自己缺的东西：没有检查点、没有中断、加一个分支要动 if。
"""

from stub_model import run_pipeline

TICKETS = [
    "我上周买的耳机左耳没声音，想退款 899 元",
    "升级完固件之后连不上蓝牙了",
    "客服三天没回我，我要投诉，态度太差了",
]


def main():
    for i, t in enumerate(TICKETS, 1):
        out = run_pipeline(t)
        flag = "需人工签字" if out["need_human"] else "可直接回复"
        print(f"[{i}] {out['kind']}  {flag}")
        print(f"    口径：{out['policy']}")
        print(f"    回复：{out['draft']}")


if __name__ == "__main__":
    main()
