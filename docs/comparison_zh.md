# 与同类对比

> [English](comparison.md)

以下两张表帮助快速判断 muselab 是否适合当前需求，或哪个替代工具更为合适。

**核验日期：2026-09-08。** 本文比较产品定位，不是跨产品性能或完整能力基准。
本次更新核验了 CloudCLI 的公开仓库与包依赖；其他产品条目尚未完成按版本的全面核验。
功能和订阅使用方式可能随版本、套餐、服务商与模型变化。模型可配置不代表 SDK
全部能力兼容，请验证所需的工具和工作流。

CloudCLI（原 Claude Code UI，仓库名 `claudecodeui`）官方提供一条命令自托管启动：
`npx @cloudcli-ai/cloudcli`，要求 Node.js 22+。其包声明了
`@anthropic-ai/claude-agent-sdk` 和 `@openai/codex-sdk` 依赖；仅凭依赖列表
不能判断每条执行路径使用的运行时。来源：[官方仓库](https://github.com/siteboon/claudecodeui)、
[package.json](https://github.com/siteboon/claudecodeui/blob/main/package.json)。

## vs. 通用 chat UI

|  | muselab | CloudCLI / claudecodeui | LobeChat | AnythingLLM | Claude Code CLI |
|---|---|---|---|---|---|
| 定位 | 本地工作目录 + 可执行 Agent | 编程 Agent 的网页／手机界面 | 多模型对话 + 插件市场 | 文档问答；更广泛的 Agent 范围未核验 | 终端编程 agent |
| 自托管 | ✅ | ✅ | ✅ | ✅ | 不适用（本地执行） |
| 浏览器访问 | ✅ | ✅ | ✅ | ✅ | ❌ |
| HTML / PDF / 图片预览 | ✅ | ⚠️ | ⚠️ | ⚠️ | ❌ |
| 浏览器内真实 PTY 终端 | ✅ 多终端 + Profile | ✅ 多标签 | ❌ | ❌ | n/a（本身运行于终端） |
| Agent / SDK 集成 | Claude Agent SDK；能力需按服务商／模型验证 | 声明 Claude Agent SDK 与 Codex SDK 依赖；能力需按服务商／模型验证 | 未核验 | 未核验 | 不适用（CLI） |
| 复用 Claude Pro 订阅 | ✅ | ✅ | ❌ | ❌ | ✅ |
| 安装入口 | curl \| bash | npx（一条命令；Node.js 22+） | docker compose | docker | brew / npm |

需要 **IDE 完整功能**，推荐 claudecodeui 或 code-server。
需要 **插件市场**，推荐 LobeChat。
需要 **基于爬取内容的 RAG**，推荐 AnythingLLM。

muselab 的终端与文件、预览和会话使用同一个工作目录。可以同时保留多个真实 PTY
会话，并通过 Profile 在新建终端时自动执行固定命令；切换页面不会终止其中的进程。

同类搜索中经常出现的其他名字：

- [Open WebUI](https://github.com/open-webui/open-webui) —— 本地模型（Ollama）及 OpenAI 兼容端点的首选自托管 chat UI，自带 RAG 与工具体系。以本地模型对话为核心时选它；需要在自己文件上跑 Claude Code agent loop（Read / Grep / Edit / Bash、Skills、MCP）时选 muselab。
- [LibreChat](https://github.com/danny-avila/LibreChat) —— 多提供商对话，带多用户鉴权和 agent 框架。需要面向团队的共享对话门户时选它；muselab 刻意设计为单用户（见[边界](#边界)）。
- **Obsidian / Logseq AI 插件** —— 笔记应用内嵌 AI。它们主要围绕笔记库工作；muselab 的 Agent 可作用于已登记的本地工作目录（任意文件类型），并能通过工具与终端执行多步骤任务，而不仅仅是生成文字。

## vs. 其他 Claude harness

|  | muselab | Claude Code CLI | Claude Desktop | CloudCLI / claudecodeui | claude-code-router |
|---|---|---|---|---|---|
| 官方 **Claude Agent SDK** 集成 | 直接使用 | 不适用（CLI） | 内部 SDK 使用情况未获公开核验 | 声明该依赖；另含 Codex SDK | 未核验 |
| 浏览器 web UI | ✅ | ❌ TTY | ❌ 桌面 | ✅ | ❌ |
| 文件 + 预览 + 真实终端 | ✅ 一体化 | ⚠️ 终端为主 | ⚠️ 无真实终端 | ✅ | ❌ |
| 非 Claude 模型兼容性 | 经服务商兼容层；所需 SDK 能力须按服务商／模型验证 | 未核验 | 未核验 | 按服务商／模型验证；SDK 依赖不代表功能完全一致 | 按服务商／模型验证 |
| 自托管友好度 | ✅ | 不适用（本地执行） | 不适用（桌面应用） | ✅ | ✅ |
| 开源 | ✅ MIT | ❌ | ❌ | ✅ AGPL-3.0 | ✅ MIT |

最简概括：muselab 把 Agent loop 放进一个可自托管、可从浏览器访问的本地工作台。

任何已授权的本地目录都可以作为工作区；安装器不采集个人资料，也不创建预设目录。

## 边界

- 单用户、单 token —— 两人共用即共享全部数据；团队共享场景请使用独立实例或其他多用户产品
- 不是完整 IDE：内置终端适合在工作目录执行命令和辅助 Agent 工作，但不提供完整的
  代码导航、调试器与 IDE 插件生态；重度软件开发仍建议使用 claudecodeui 或
  Claude Code
- 不是 RAG：工作目录文件按需 Read / Grep，不预先向量化；爬虫式文档问答用 [AnythingLLM](https://github.com/Mintplex-Labs/anything-llm)
- 不带插件市场，也不预装 Skill payload；会动态发现用户、workspace、plugin 和经审核生成的 Skills，如需应用内 marketplace 可用 [LobeChat](https://github.com/lobehub/lobe-chat)
