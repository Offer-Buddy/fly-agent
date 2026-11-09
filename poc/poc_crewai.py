"""POC 2 · CrewAI：先把角色和任务写清楚，组队的事交给它。

跑法：先开假模型服务（python stub_server.py 8111），再跑本文件。
看点：代码读起来像一份岗位说明书——谁负责什么、交付什么、交给谁。
"""

from crewai import Agent, Crew, LLM, Process, Task

LLM_CFG = dict(model="openai/gpt-4o-mini", base_url="http://127.0.0.1:8111/v1", api_key="sk-stub")

TICKET = "我上周买的耳机左耳没声音，想退款 899 元"

classifier = Agent(
    role="工单分诊员",
    goal="判断这张工单属于退款、技术支持还是投诉",
    backstory="你在客服中心干了五年，看一句话就知道该转给谁。",
    llm=LLM(**LLM_CFG),
    allow_delegation=False,
    verbose=False,
)

advisor = Agent(
    role="政策核对员",
    goal="给出这张工单该走哪条处理口径",
    backstory="你熟悉售后退换货政策，从不凭印象回答。",
    llm=LLM(**LLM_CFG),
    allow_delegation=False,
    verbose=False,
)

writer = Agent(
    role="回复撰写员",
    goal="按口径写一句给用户看的话，并判断要不要人签字",
    backstory="你写回复只写用户关心的，不写公司术语。",
    llm=LLM(**LLM_CFG),
    allow_delegation=False,
    verbose=False,
)

t1 = Task(
    description=f"给这张工单分类。#STAGE:classify# #INPUT:{TICKET}#",
    expected_output="一段 JSON，包含 kind 与 amount 两个字段",
    agent=classifier,
)

t2 = Task(
    description="根据上一步的分类结果，查对应的处理口径。#STAGE:policy#",
    expected_output="一段 JSON，包含 policy 字段",
    agent=advisor,
    context=[t1],
)

t3 = Task(
    description="按上一步的口径写回复，并判断是否需要人工签字。#STAGE:draft#",
    expected_output="一段 JSON，包含 draft 与 need_human 字段",
    agent=writer,
    context=[t1, t2],
)

crew = Crew(
    agents=[classifier, advisor, writer],
    tasks=[t1, t2, t3],
    process=Process.sequential,
    verbose=False,
)


def main():
    result = crew.kickoff()
    print("最终输出：")
    print(result.raw)
    for i, out in enumerate(result.tasks_output, 1):
        print(f"  第 {i} 步 -> {str(out.raw)[:90]}")


if __name__ == "__main__":
    main()
