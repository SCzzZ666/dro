# Dro v2 架构说明

本文档描述 `v2/` 目录的架构设计，对应需求文档"第 8-9 周：dro 电商助手深度重构"。

## 1. 架构总览

```
用户输入
   │
   ▼
intent_node ──(chat/需澄清)──► result_node ──► END
   │
   ▼
planner_node ──► agent_node ◄──(还有步骤)──┐
                     │                     │
                     └──(计划完成/达步数上限)──┘
                     │
                     ▼
                  result_node ──► END
```

- **记忆系统在每一步注入**：`intent_node`（历史+偏好）、`planner_node`（记忆辅助规划）、`agent_node`（ReAct 观察）、`result_node`（写入本轮结果）。
- **checkpointer**：默认 `SqliteSaver`（`v2/data/checkpoints.db`），thread_id = `{用户ID}_{时间戳}`；同一 thread_id 可 `resume_task` 从断点恢复。

## 2. 节点说明

| 节点 | 职责 | 输出 |
| --- | --- | --- |
| `intent_node` | 结合记忆把用户输入归类为 price_compare / find_similar / recommend / chat | intent, search_term, reply, choices |
| `planner_node` | 依据意图生成有序工具调用计划（LLM 失败时用确定性兜底计划） | plan |
| `agent_node` | 每轮执行计划中的一个工具；弹窗自愈（close_popup）、失败重试、超时兜底 | tool_log, collected, step 计数 |
| `result_node` | 去重归一化采集结果，LLM 生成最终回复（无 LLM 时模板拼接兜底） | products, final_reply |

### 条件边
- intent == chat：不执行工具，直达 result_node 返回回应（含澄清选项 choices）。
- agent_node：计划执行完或达到 `max_steps`（默认 15）→ result_node；否则继续循环。

## 3. 工具化执行引擎（v2/tools/）

每个手机操作是独立 `AgentTool`，统一经过 `register_tool` 注册、`execute_tool` 调度（线程池 + 超时保护 + 异常兜底返回错误字符串）。

| 工具 | 参数 | 实现要点 |
| --- | --- | --- |
| `open_app` | app_name | `adb shell monkey -p 包名 ...`，淘宝/京东包名映射 |
| `search_product` | keyword | 点击搜索框（固定坐标兜底）→ `input text` → `keyevent 66` |
| `click_product` | index | 优先 UI dump 元素定位，回退按分辨率比例坐标 |
| `extract_info` | - | `screencap` → Qwen-VL 多模态提取标题/价格/销量/店铺 |
| `go_back` | - | `keyevent 4` |
| `screenshot` | - | `screencap` + pull 到 `trajectories/` |
| `close_popup` | - | 弹窗自愈：UI 文本定位关闭按钮，回退点击右上角 |

所有工具：docstring 清晰、参数有类型标注、内部 try/except、失败返回 `ToolResult(ok=False, message=...)` 而非抛异常。

## 4. 存储策略（SQLite 默认可运行）

| 层 | 默认实现 | 可选扩展 |
| --- | --- | --- |
| 短期记忆 | SQLite `messages` 表 + 滑动窗口 | - |
| 会话摘要 | SQLite `summaries` 表（LLM 生成，截断兜底） | - |
| 长期偏好 | SQLite `preferences` 表 | Qdrant（配置 `QDRANT_URL` 自动启用向量检索，DashScope embedding） |
| 用户画像 | SQLite `profiles` 表 | PostgreSQL（配置 `POSTGRES_DSN` 自动同步） |
| 任务 checkpoint | `v2/data/checkpoints.db`（SqliteSaver） | 保持 SQLite |

设计原则：所有外部服务均为可选，未配置时主流程照常运行（SQLite 兜底），便于本地开发与面试演示。

> 长期偏好的向量检索链路（`memory.py` → `LongTermMemory`）：写入时 SQLite 恒写 + Qdrant 同步 upsert 向量（幂等点 ID = md5(user|type|value)）；检索时 `Qdrant.query_points`（按 `user_id` 过滤）优先，embedding / Qdrant 任一不可用即回退 SQLite 全量偏好。向量化走 DashScope `/embeddings`（`text-embedding-v3`，维度见 `EMBEDDING_DIM`）。

## 5. 目录结构

```
v2/
├── __init__.py       # 版本 + Windows 编码兼容
├── __main__.py       # python -m v2 入口（默认 0.0.0.0:8001）
├── api.py            # FastAPI：/v2/tasks + /v2/tasks/{id} + /resume + /v2/tools
├── config.py         # 统一配置（.env > 环境变量 > 默认值）
├── graph.py          # LangGraph 状态图 + TaskRunner（同步图 + SqliteSaver）
├── llm.py            # DashScope 兼容接口封装（同步/异步双入口）
├── memory.py         # 四层记忆（MemoryManager）
├── prompts.py        # 全部提示词模板集中管理
├── schemas.py        # ToolResult / ToolCall / Task / AgentState
├── adb_client.py     # ADB 原子操作封装（tap/swipe/screencap/dump_ui...）
├── ui.py             # uiautomator dump XML 解析（元素定位）
├── vision.py         # 视觉服务（商品信息提取 / 弹窗检测）
├── tools/            # 7 个手机操作工具
└── data/             # [自动生成] checkpoints.db / memory.db / tasks.db
```

## 6. 与旧版（app.py / agent_service.py）的区别

| 维度 | 旧版 | v2 |
| --- | --- | --- |
| 执行方式 | LLM 生成 Python 代码本地执行 | 固定工具集，LLM 只做意图/规划/结果 |
| 编排 | 单文件长 Prompt 一把跑 | LangGraph 状态图，可单步恢复 |
| 记忆 | 内存字典 | 四层持久化记忆 |
| 恢复 | 无 | SqliteSaver checkpoint + resume |
| 可测性 | 低 | 每个工具可单测，图可 mock LLM 测试 |

## 7. 快速验证

```bash
# 单元测试（不依赖手机/API Key）
python -m unittest discover -s tests -v

# 启动 v2 服务
python -m v2                     # http://localhost:8001
python -m v2 --port 9000

# 创建任务
curl -X POST http://localhost:8001/v2/tasks \
  -H "Content-Type: application/json" \
  -d '{"user_input":"帮我比价蓝牙耳机","task_type":"比价"}'
```