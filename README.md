# Dro 电商 AI 助手

Dro（Droidrun E-commerce AI Agent）是一个面向 Android 设备（真机 / 模拟器）的购物助手，能自动在淘宝、京东等 App 内完成**比价、找相似、按预算推荐**三类任务。

当前项目已切换到 **`v2/` 独立重构流程**：LangGraph 做任务编排，原子 ADB 工具做手机操作，SQLite 做默认记忆与 checkpoint。

> 旧版 `app.py` / `agent_service.py` 仍保留，但已标记为 deprecated，仅作历史参考，不作为当前推荐流程。

---

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env        # 填 DASHSCOPE_API_KEY / DEVICE_SERIAL
python -m v2                # http://localhost:8001
```

启动后可用页面：

| 地址 | 说明 |
| --- | --- |
| `http://localhost:8001/` | 任务编排控制台（创建任务、看结果与商品、查工具） |
| `http://localhost:8001/chat` | 客服式聊天界面（多轮会话、图片找相似） |
| `http://localhost:8001/docs` | FastAPI 自动接口文档 |

---

## 1. 架构

```mermaid
flowchart LR
    U[用户输入] --> I[intent_node
    意图识别]
    I -->|chat / 需澄清| R[result_node
    直接回复 / 反问澄清]
    I --> P[planner_node
    生成工具计划]
    P --> A[agent_node
    ReAct 工具循环]
    A -->|计划未完成| A
    A -->|计划完成 / 达步数上限 / 提前 finish| R
    R --> END[结束]

    A --> T[tools/ 原子工具]
    A --> M[memory 四层记忆]
    A --> C[(SqliteSaver checkpoint)]
    I --> B[budget.py 预算解析]
    R --> B
```

### 任务类型 → 意图映射

| 前端 task_type | 内部意图 | 含义 |
| --- | --- | --- |
| `比价` | `price_compare` | 跨平台（淘宝 / 京东）各采集最低价后对比 |
| `找相似` | `find_similar` | 按图片 / 关键词找同款（**传图时强制走此意图**） |
| `最优方案` | `recommend` | 按预算 + 销量 + 评分综合推荐 |
| （空） | `chat` | 闲聊 / 需求澄清，直达结果，不出工具 |

### 执行顺序

1. `intent_node` 判断意图（含记忆注入、预算解析、**需求澄清**）
2. `planner_node` 依据意图生成工具计划（LLM 失败时用确定性兜底计划）
3. `agent_node` ReAct 循环：每轮执行计划中的一个工具，支持弹窗自愈、失败重试、LLM 提前 `finish`
4. `result_node` 去重归一化采集结果、做预算标注、生成最终自然语言回复

---

## 2. 目录结构

```text
dro/
├── app.py                      # 旧版入口（deprecated）
├── agent_service.py            # 旧版核心服务（deprecated）
├── templates/index.html        # 旧版 Web UI
├── v2/                         # 当前推荐流程
│   ├── __main__.py             # python -m v2（--host / --port 可覆盖）
│   ├── api.py                  # FastAPI 接口（含 / 与 /chat 内嵌 Web UI）
│   ├── graph.py                # LangGraph 状态图 + TaskRunner
│   ├── config.py               # 配置加载（环境变量 > .env > 默认值）
│   ├── llm.py                  # DashScope 兼容接口封装（同步/异步）
│   ├── memory.py               # 四层记忆
│   ├── prompts.py              # 提示词模板
│   ├── schemas.py              # ToolResult / Product / 意图 / State 契约
│   ├── budget.py               # 预算上限解析
│   ├── adb_client.py           # ADB 原子操作封装
│   ├── vision.py               # 视觉服务（Qwen-VL 商品提取 / 弹窗检测）
│   ├── ui.py                   # uiautomator XML 元素解析
│   ├── data/                   # [自动生成] checkpoints.db / memory.db
│   └── tools/                  # 原子工具
│       ├── base.py             # register_tool 装饰器 + execute_tool 调度（超时/异常兜底）
│       ├── open_app.py         # open_app
│       ├── search_product.py   # search_product
│       ├── click_product.py    # click_product
│       ├── extract_info.py     # extract_info
│       ├── go_back.py          # go_back
│       ├── screenshot.py       # screenshot
│       ├── close_popup.py      # close_popup
│       ├── gesture.py          # swipe_up / swipe_down / swipe_left / swipe_right
│       └── clipboard.py        # paste_text
├── tests/                      # 单测 / 回归 / 评估用例
│   ├── test_graph.py
│   ├── test_memory.py
│   ├── test_tools.py
│   ├── test_v2_regression.py
│   ├── eval_cases.py
│   └── fixtures/eval_samples.json
├── docs/                       # architecture / evaluation / interview-cheatsheet
├── .env.example                # 环境变量样例
├── .gitignore
└── requirements.txt            # 依赖
```

---

## 3. v2 核心能力

### 3.1 原子工具层（12 个工具）

| 工具 | 参数 | 说明 |
| --- | --- | --- |
| `open_app` | `app_name` | 打开淘宝 / 京东（中文名或包名） |
| `search_product` | `keyword` | 在搜索框输入关键词并搜索（中文自动走剪贴板） |
| `click_product` | `index` | 点击搜索结果第 N 个商品（UI 元素定位优先，坐标估算兜底） |
| `extract_info` | — | 截图 + 视觉模型提取标题 / 价格 / 销量 / 店铺 |
| `go_back` | — | 系统返回键 |
| `screenshot` | — | 截图保存到 `trajectories/` |
| `close_popup` | — | 检测并关闭广告 / 优惠券 / 升级弹窗 |
| `swipe_up` / `swipe_down` | `times` | 上 / 下滑动（翻列表、加载更多） |
| `swipe_left` / `swipe_right` | `times` | 左 / 右滑动（切换图片 / 横向轮播） |
| `paste_text` | `text` | 剪贴板粘贴（支持中文，需先聚焦输入框） |

### 3.2 预算解析（budget.py）

- 从输入中解析价格上限：`预算200` / `200以内` / `不超过300` / `200-300`（区间取上界）等表述。
- 预算**只「标注」不「硬过滤」**：超预算商品保留并标注 `reason`，预算内最低价标注 `is_winner`，避免把「看到全部信息 + 明确标注」误做成「只给一个答案」。

### 3.3 四层记忆

- **短期记忆**：SQLite `messages` 表 + 滑动窗口
- **会话摘要**：长对话自动压缩为摘要（LLM 生成，SQLite `summaries` 表）
- **长期偏好**：记录预算 / 颜色 / 品牌等偏好；配置 Qdrant 后走向量检索（DashScope embedding），未配置时 SQLite 兜底
- **用户画像**：结构化偏好（SQLite `profiles` 表；配置 PostgreSQL 后自动同步）

### 3.4 Checkpoint 恢复

- 默认用 SQLite（`v2/data/checkpoints.db`，`SqliteSaver`）保存任务状态，失败自动降级 `MemorySaver`
- 同一个 `thread_id` 可 `resume` 从断点继续执行
- 适合长流程比价、跨平台搜索、异常恢复等任务

### 3.5 执行健壮性

- **弹窗自愈**：工具失败且命中弹窗特征时自动 `close_popup` 并重试当前步骤一次
- **ReAct 提前结束**：每步有产出且计划尚有剩余时，让 LLM 判断是否已收齐信息提前 `finish`
- **超时保护**：工具走线程池 + 超时兜底；LLM 走同步 httpx 超时
- **降级链**：LLM 不可用 → 启发式意图 + 默认计划 + 模板拼接兜底；设备断 → 自动重连

---

## 4. 安装与启动

### 4.1 环境要求

- Python 3.10+
- Android 设备 / 模拟器（淘宝、京东已安装并登录）
- ADB 可用
- DashScope API Key（Qwen-VL 视觉模型 + DeepSeek 文本模型统一用）

### 4.2 安装依赖

```bash
pip install -r requirements.txt
```

### 4.3 配置环境变量

建议在项目根目录创建 `.env`（可复制 `.env.example`）：

```properties
# 必填
DASHSCOPE_API_KEY=你的-dashscope-api-key
DEVICE_SERIAL=127.0.0.1:16416
ADB_BINARY=adb

# 可选：覆盖默认模型
# QWEN_MODEL=qwen3-vl-plus
# DEEPSEEK_MODEL=deepseek-v3.2
# LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
# EMBEDDING_MODEL=text-embedding-v3
# EMBEDDING_DIM=1024

# 可选：任务参数
# MAX_STEPS=15
# TOOL_TIMEOUT=10
# TASK_TIMEOUT=300

# 可选：服务端口
# DRO_HOST=0.0.0.0
# DRO_PORT=8001

# 可选：长期偏好向量记忆（不填则 SQLite 兜底）
# QDRANT_URL=http://localhost:6333
# QDRANT_API_KEY=
# QDRANT_COLLECTION=dro_preferences

# 可选：用户画像（不填则 SQLite 兜底）
# POSTGRES_DSN=postgresql://user:pass@localhost:5432/dro
```

### 4.4 启动服务

```bash
python -m v2                 # http://localhost:8001
python -m v2 --port 9000     # 自定义端口
python -m v2 --host 127.0.0.1
```

---

## 5. API

### 任务编排（任务控制台 `/` 配套）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/healthz` | 健康检查 |
| POST | `/v2/upload` | 上传图片（找相似用），返回本地路径 `{path, name, size}` |
| GET | `/v2/tools` | 列出已注册工具及参数 |
| POST | `/v2/tasks` | 创建任务，返回 `thread_id` |
| GET | `/v2/tasks` | 列出任务 |
| GET | `/v2/tasks/{thread_id}` | 查询任务状态（含 `final_reply` / `products` / `tool_log`） |
| POST | `/v2/tasks/{thread_id}/resume` | 恢复中断任务 |
| POST | `/v2/tasks/{thread_id}/stop` | 停止任务 |

创建任务请求体：

```json
{
  "user_input": "帮我比价蓝牙耳机，预算200以内",
  "task_type": "比价",
  "image_path": "可选，找相似时传 /v2/upload 返回的路径",
  "thread_id": "可选，不传自动生成"
}
```

### 会话式聊天（聊天界面 `/chat` 配套）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/v2/chat` | 发送消息，返回 `session_id` + `thread_id` |
| GET | `/v2/chat/sessions` | 会话列表（聊天首页侧栏） |
| GET | `/v2/chat/{session_id}/history` | 会话历史（时间正序） |
| GET | `/v2/chat/{session_id}/events` | 轮询式执行进度（增量返回工具步骤） |
| GET | `/v2/chat/{session_id}/state` | 会话终态（最终回复 / 商品 / 选项） |
| POST | `/v2/chat/{session_id}/stop` | 停止会话当前任务 |

发送消息请求体：

```json
{
  "message": "找一个100以内的拼豆玩具",
  "session_id": "可选，为空则自动创建",
  "image_path": "可选，参考图路径",
  "task_type": "可选，显式指定比价/找相似/最优方案"
}
```

---

## 6. 设备连接

### 6.1 检查连接

```bash
adb devices
```

### 6.2 模拟器 / 真机要求

- 打开开发者模式，开启 USB 调试
- 确保目标 App（淘宝 / 京东）已安装并登录
- 中文搜索依赖系统剪贴板（Android 10+；旧版 Android 需自行确认剪贴板可用）

### 6.3 常见设备串

- MuMu：`127.0.0.1:16416`
- 其它模拟器 / 真机：以 `adb devices` 实际输出为准

---

## 7. 测试

### 7.1 单元 / 回归测试

```bash
python -m unittest discover -s tests -v
```

覆盖范围：

- 工具注册、执行、超时、异常兜底（`test_tools.py`）
- 意图路由、计划、checkpoint、ReAct finish、需求澄清（`test_graph.py`）
- 四层记忆读写、预算解析与标注（`test_memory.py` / `test_v2_regression.py`）

### 7.2 评估用例

```bash
python -m tests.eval_cases
```

对应文档：`docs/evaluation.md`（10 个端到端任务，需真机/模拟器逐条跑 3 次后补数据）。

---

## 8. 项目当前状态

### 已完成

- `v2/` 独立流程：图编排（意图 → 规划 → ReAct → 结果）、12 个原子工具、四层记忆、API、测试
- 任务控制台 `/` 与客服聊天 `/chat` 两个 Web UI
- 预算解析与标注、需求澄清、弹窗自愈、checkpoint 恢复
- 默认 SQLite 零外部依赖可启动

### 待进一步增强

- 真机上 `extract_info` / `search_product` / `click_product` 的 UI 定位与视觉提取仍需针对具体页面调优
- 评估表 `docs/evaluation.md` 需真机跑 3 次/任务后补全真实数据
- Qdrant / PostgreSQL 可选存储接入后尚未在真机业务验证

---

## 9. 旧版说明

旧版实现保留在：

- `app.py`
- `agent_service.py`
- `templates/index.html`

它们目前仅作为历史版本和回退参考，**不作为当前推荐启动路径**。

---

## 10. 建议的后续工作

1. 先用 `python -m v2` 跑通 API，打开 `/` 和 `/chat` 确认页面可用
2. 再用模拟器 / 真机验证 `open_app`、`search_product`、`extract_info`、滑动工具
3. 然后按 `tests/eval_cases.py` 跑评估并补 `docs/evaluation.md`
4. 最后再考虑接入 Qdrant / PostgreSQL 作为增强存储，并在真机验证