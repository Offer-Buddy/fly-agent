"""POC 3 · AutoGen：让几个 Agent 在一个群里轮流发言。

跑法：先开假模型服务（python stub_server.py 8111），再跑本文件。
看点：没有显式的「边」，顺序由发言规则决定——这是它和 LangGraph 最大的差别。
"""

import asyncio

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.conditions import MaxMessageTermination
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_ext.models.openai import OpenAIChatCompletionClient

TICKET = "我上周买的耳机左耳没声音，想退款 899 元"

client = OpenAIChatCompletionClient(
    model="gpt-4o-mini",
    base_url="http://127.0.0.1:8111/v1",
    api_key="sk-stub",
)

classifier = AssistantAgent(
    name="分诊员",
    model_client=client,
    description="判断工单属于哪一类",
    system_message=f"你判断工单类型，只输出 JSON。#STAGE:classify# #INPUT:{TICKET}#",
)

advisor = AssistantAgent(
    name="政策核对员",
    model_client=client,
    description="给出处理口径",
    system_message="你根据前面的分类结果给出处理口径，只输出 JSON。#STAGE:policy#",
)

writer = AssistantAgent(
    name="回复撰写员",
    model_client=client,
    description="写用户看的话",
    system_message="你写一句给用户看的话，并判断要不要人工签字，只输出 JSON。#STAGE:draft#",
)


async def main():
    team = RoundRobinGroupChat(
        [classifier, advisor, writer],
        termination_condition=MaxMessageTermination(3),
    )
    result = await team.run(task=f"处理这张工单：{TICKET}")

    for m in result.messages:
        who = getattr(m, "source", "?")
        body = str(getattr(m, "content", "")).replace("\n", " ")[:88]
        print(f"[{who}] {body}")

    print("\n最后一条就是交付：", str(result.messages[-1].content))
    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
