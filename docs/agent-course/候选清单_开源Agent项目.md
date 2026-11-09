# 第三讲候选池 · 主流 Agent 开源框架与产品

> 数据抓取：2026-11，star 数按量级参考（同类项目一周能差几千，不要当精确值）。
> 本表只做「选谁进讲义」的决策，不是讲义正文。

## 筛选口径

四条硬标准，全过才进候选：

1. **真开源** —— 许可证可查（MIT / Apache-2.0 / GPL-3.0）。fair-code、open-core 单独标注。
2. **起得来** —— docker 一条命令、compose 一次起，或 CLI 三行内。不要求读源码才能跑。
3. **有场景** —— 能一句话说清「它替你干哪类活」。
4. **还在动** —— 近三个月有提交。维护模式的项目不进主表。

## 分四组 · 按「你要不要写代码」和「你能看见它在干什么」

| 组 | 谁适合 | 共同点 |
|---|---|---|
| 甲 · 装好就用 | 不写代码 | 一条 docker 命令，起来就是个能干活的助手 |
| 乙 · 画布搭应用 | 不写代码 | 一条命令起平台，剩下靠拖拽和填表 |
| 丙 · 写代码，但看得见 | 会写 Python | 要装要配，但自带可视化调试台，能看每一步 |
| 丁 · 纯库 | 会写 Python | 没有界面，只能靠日志和 trace |

## 甲组 · 装好就用（助手型，单机常驻）

| 项目 | Star | 许可 | 启动 | 它干什么 | 教学价值 |
|---|---|---|---|---|---|
| OpenClaw | 393k | MIT | 官方 Docker 镜像 | 个人助手，接 22 个聊天渠道，浏览器控制 + 持久记忆 + ClawHub 技能市场 | star 之王；「技能 = 插件」的活样本 |
| Hermes Agent | 199k+ | MIT | `docker run nousresearch/hermes-agent` :8642 | 自我进化：任务做完自动沉淀成 skill，跨会话记忆，gateway 暴露 OpenAI 兼容 API | 记忆 + 技能闭环最好的教材 |
| Agent Zero | 19.8k | MIT | `docker run agent0ai/agent-zero`（含 XFCE 桌面） | 给 Agent 一台完整 Linux 桌面，能操作 GUI 软件、派子智能体；行为全写在 prompt 文件里 | 「一切皆 prompt」的极端案例 |
| DeepSeek Harness (dsh) | 272k | MIT | `npx @deepseek-ai/dsh web` :3080 / 桌面版 | 编码 harness，一切皆插件（模型/工具/沙箱/界面），MCP + 子智能体 | ⚠️ developer preview、无安全审计、CVE-2026-82533 沙箱逃逸（9.4）——讲「能力与风险同源」最佳 |

## 乙组 · 画布搭应用（平台型，不写代码）

| 项目 | Star | 许可 | 启动 | 它干什么 | 教学价值 |
|---|---|---|---|---|---|
| Dify | 159.7k | Apache-2.0（open-core） | `docker compose up` | RAG + 工作流 + Agent 一体，画完能发布成 API | 平台层的标准答案 |
| MaxKB | 23.1k | GPL-3.0 | `docker run` 单容器 :8080 | 文档进去就能问，工作流 + MCP 工具，零代码嵌入第三方系统 | 单容器最干净，课程主线项目 |
| n8n | 208.8k | fair-code（非 OSI） | `docker run` :5678 | 系统之间的胶水，带 AI 节点 | 「工作流 + 模型节点」的对照组 |
| Langflow | ~47k | MIT | `docker run` :7860 | LangChain 的可视化外壳，画完能把代码拿走 | 「画布 → 代码」这条路的样本 |
| Flowise | 53.5k | Apache-2.0 | `docker run` :3001 | 低代码 Agent 画布，基于 LangChain.js | Langflow 的 Node 侧对照 |
| RAGFlow | 93.7k | Apache-2.0 | `docker compose up` | DeepDoc 深度文档解析，扫描件/PDF/表格 | 「知识这一层做到极致」的样本 |

## 丙组 · 写代码，但看得见（自带调试台）

| 项目 | Star | 许可 | 启动 | 它干什么 |
|---|---|---|---|---|
| **LangGraph** | 44k | MIT | `pip install -U "langgraph-cli[inmem]"` → `langgraph new app --template new-langgraph-project-python` → `langgraph dev` | API 起在 :2024，同时给你 **LangGraph Studio** 可视化调试台（看每一步 state 变化）+ 热重载。`langgraph dev` 是内存模式，`langgraph up` 才要 docker 和持久化存储 |
| deer-flow | 70k–89k | MIT | `make docker-init && make docker-start` :2026 | 字节 SuperAgent harness：子智能体 + 沙箱 + 技能（Markdown 定义）+ 跨会话记忆 |
| OpenHands | 88k–91k | MIT | `docker run` | 沙箱里自主编码，改仓库、跑测试 |

**LangGraph 为什么单列**：它是唯一一个「同一条链，画布项目和它都能做，但代价完全不同」的对照组。MaxKB 里点三下拿到的知识库问答，LangGraph 要自己写 state、node、edge——换来的是 interrupt、checkpoint、精确控制每一步。这正好接上第二讲「框架这一层只问三句话」和第五讲「状态层」。

## 丁组 · 纯库（无界面，第三讲只点名不实训）

| 项目 | Star | 许可 | 备注 |
|---|---|---|---|
| browser-use | 119.3k | MIT | 让模型操作真浏览器，无 API 网站的解法 |
| CrewAI | 60.2k | MIT | 角色型多智能体，上手最快 |
| ⚠️ AutoGen | 61.6k | MIT | **2025-10 起维护模式**，勿在新项目里选它 |
| Microsoft Agent Framework | 14.3k | MIT | AutoGen + Semantic Kernel 合并后的继任者，1.0 于 2026-04 发布 |
| OpenAI Agents SDK | 29k | MIT | 轻，贴近原生 tool calling |
| Google ADK | 21.3k | Apache-2.0 | Gemini 生态 |
| Pydantic AI | 19.5k | MIT | 类型安全，适合要单测的场景 |
| LlamaIndex | 51.9k | MIT | 数据/检索为中心 |

## 备选池（能用，但不进主表）

| 项目 | 为什么不进 |
|---|---|
| AutoGPT 184.9k | 先驱项目，定位已漂移成平台，讲它容易变历史课 |
| Open WebUI 156.4k / LibreChat 40k / AnythingLLM 61.5k / Big-AGI | 是「聊天 UI 层」，七模块里只覆盖模型 + 上下文，不构成完整 Agent |
| LobeHub 83.4k | Agent 工作台，偏前端编排 |
| CowAgent 45.2k | 中文超级助手，生态和文档偏薄 |
| AionUi 28k | 20+ CLI agent 的本地外壳，本身不是 Agent |
| GPT-Researcher | 单点研究型，场景太窄 |

## 与第三讲「四格坐标」的映射

坐标「横轴 = 谁在跑，纵轴 = 要不要写代码」，候选池刚好把四格填满：

- **右下（服务器 + 不写代码）**：Dify、MaxKB、n8n、Langflow、Flowise、RAGFlow
- **左上（本机 + 不写代码）**：OpenClaw、Hermes Agent、Agent Zero、dsh 桌面版
- **右上（本机 + 写代码）**：LangGraph + Studio、OpenHands
- **左下（服务器 + 写代码）**：deer-flow、CrewAI、MS Agent Framework

## 已落地：第三讲 v2（2026-11-08）

讲义已按「讲广度 + 每个项目讲清定位 / 使用场景 / 技术栈」重写，不再限定「开箱即用」。结构 9 章、6 张图、20 张项目卡：

| 章 | 内容 | 项目 |
|---|---|---|
| 1 | 四条路线（四档谱系：装好就用 → 画布搭 → 写但看得见 → 纯库） | 全景 |
| 2 | 平台型 | Dify、MaxKB、n8n、Langflow、Flowise、RAGFlow |
| 3 | 助手型 | OpenClaw、Hermes Agent、Agent Zero、DeepSeek Harness |
| 4 | 编排库（三种心智模型：画图 / 分岗位 / 交接） | LangGraph、CrewAI、MS Agent Framework、AutoGen、OpenAI Agents SDK、Google ADK、Pydantic AI |
| 5 | 垂直型 | OpenHands、deer-flow、browser-use |
| 6 | 同一件事三条路对照（步数 4/5/6，差在能改多深） | — |
| 7 | 换血与三个陷阱（星数≠健康、三种开源、预览版） | — |
| 8 | 三问选型 + 动手单 | — |
| 9 | 三讲收口 | — |

实训从「四个都起」改成「挑两个」：一个平台（MaxKB 或 Dify）+ 一个库（LangGraph），同一份文档各做一遍，加同一条「转人工」需求，交一张四项观察表。

## 备查：如果以后要加项目

优先看这三类空缺——① 中文生态的助手型（CowAgent 一类，生态还薄）；② 语音/实时类 Agent（LiveKit Agents 一类）；③ 评测与观测（Langfuse、Phoenix、DeepEval，属基础设施不算 Agent）。补之前先确认近三个月有提交。
