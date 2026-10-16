# Dro Agent API 接口文档

> 版本：V2.0 | 日期：2026-09 | 状态：评审稿
> Base URL：`http://<host>:8001`
> 关联文档：`技术选型与架构设计.md`、`数据库设计文档.md`

---

## 1. 概述

### 1.1 设计原则

| 原则 | 说明 |
|------|------|
| REST + SSE | 常规操作用 REST，任务进度与 LLM 输出用 SSE 流式推送 |
| 版本化 | 所有端点前缀 `/v2/`，破坏性变更升版本号 |
| 统一响应 | 成功返回业务数据，失败返回统一错误结构 |
| 鉴权可选 | `DRO_API_TOKEN` 环境变量设置后启用 Bearer 鉴权，缺省关闭 |
| 限流 | 默认 60 req/min/IP，可配置 |

### 1.2 通用约定

- 所有时间戳为 Unix 秒（REAL），前端自行格式化
- 所有 ID 为字符串，不假设数值
- 请求体与响应体均为 JSON（文件上传除外）
- 分页参数：`page`（从1开始）、`page_size`（默认20，最大100）

### 1.3 鉴权

设置 `DRO_API_TOKEN` 环境变量后，所有请求需携带 Header：
```
Authorization: Bearer <token>
```

未设置时鉴权中间件不启用，保持零配置可用。

### 1.4 统一错误响应

```json
{
  "error": {
    "code": "TASK_NOT_FOUND",
    "message": "任务不存在",
    "detail": "thread_id=abc123 未找到",
    "request_id": "req_01H..."
  }
}
```

| HTTP 状态码 | 含义 | 典型场景 |
|------------|------|---------|
| 400 | 请求参数错误 | 缺少必填字段、参数格式错误 |
| 401 | 未授权 | 缺少或无效的 Bearer Token |
| 404 | 资源不存在 | 任务/会话/设备不存在 |
| 409 | 状态冲突 | 任务仍在执行中无法 resume |
| 413 | 请求体过大 | 上传文件超过 8MB |
| 422 | 校验失败 | Pydantic 模型校验失败 |
| 429 | 限流 | 请求频率超限 |
| 500 | 服务器内部错误 | 未捕获异常 |

---

## 2. 健康检查

### GET /healthz

服务健康检查，用于负载均衡与容器探针。

**响应 200：**
```json
{
  "status": "ok",
  "version": "2.0.0",
  "devices_online": 2,
  "tasks_running": 1,
  "uptime_seconds": 3600
}
```

---

## 3. 任务管理

### 3.1 POST /v2/tasks

创建并启动一个任务。任务在后台异步执行，返回 `thread_id` 用于查询状态与 SSE 订阅。

**请求体：**
```json
{
  "user_input": "帮我比价蓝牙耳机，预算200以内",
  "task_type": "",
  "image_path": null,
  "thread_id": null,
  "session_id": "sess_1700000000000",
  "model": "auto"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| user_input | string | 是 | 用户自然语言输入 |
| task_type | string | 否 | 前端任务类型：比价/找相似/最优方案，为空自动识别 |
| image_path | string | 否 | 图片路径（找相似用，需先调用上传接口） |
| thread_id | string | 否 | 复用已有线程（多轮对话），为空自动生成 |
| session_id | string | 否 | 会话 ID，关联对话历史 |
| model | string | 否 | 模型选择：auto/qwen/deepseek，默认 auto |

**响应 201：**
```json
{
  "task_id": "1700000000-a1b2c3d4",
  "thread_id": "a1b2c3d4e5f6...",
  "session_id": "sess_1700000000000",
  "status": "queued"
}
```

**错误：**
- 400 `EMPTY_INPUT`：user_input 为空
- 422：参数校验失败

### 3.2 GET /v2/tasks

列出任务列表，按创建时间倒序。

**查询参数：**

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| status | string | - | 过滤状态：pending/running/succeeded/failed/cancelled |
| session_id | string | - | 过滤会话 |
| page | int | 1 | 页码 |
| page_size | int | 20 | 每页数量 |

**响应 200：**
```json
{
  "tasks": [
    {
      "task_id": "1700000000-a1b2c3d4",
      "thread_id": "a1b2c3d4...",
      "session_id": "sess_xxx",
      "user_message": "帮我比价蓝牙耳机",
      "task_type": "比价",
      "intent": "price_compare",
      "status": "succeeded",
      "step_count": 8,
      "duration_ms": 45000,
      "created_at": 1700000000.0,
      "updated_at": 1700000045.0
    }
  ],
  "total": 42,
  "page": 1,
  "page_size": 20
}
```

### 3.3 GET /v2/tasks/{thread_id}

查询单个任务的详细状态与结果。

**响应 200：**
```json
{
  "task_id": "1700000000-a1b2c3d4",
  "thread_id": "a1b2c3d4...",
  "session_id": "sess_xxx",
  "user_input": "帮我比价蓝牙耳机，预算200以内",
  "task_type": "比价",
  "intent": "price_compare",
  "status": "succeeded",
  "step_count": 8,
  "llm_calls": 5,
  "token_usage": {"prompt": 12000, "completion": 800, "total": 12800},
  "duration_ms": 45000,
  "final_reply": "经过对比，推荐...",
  "products": [
    {
      "name": "QCY HT05",
      "price": 199.0,
      "platform": "京东",
      "sales": "10万+",
      "rating": "4.8",
      "shop_name": "QCY京东自营旗舰店",
      "link": "https://item.jd.com/xxx.html",
      "deeplink": "openapp.jdmobile://virtual?params=...",
      "is_winner": true,
      "reason": "价格最低且评分最高",
      "score": 0.92
    }
  ],
  "choices": [],
  "tool_log": [
    {"name": "open_app", "ok": true, "message": "已打开京东", "duration_ms": 1200},
    {"name": "search_product", "ok": true, "message": "搜索完成", "duration_ms": 3500}
  ],
  "checkout_options": {
    "available": true,
    "winner_product_index": 0
  },
  "error": "",
  "created_at": 1700000000.0,
  "updated_at": 1700000045.0,
  "started_at": 1700000002.0,
  "finished_at": 1700000047.0
}
```

**错误：**
- 404 `TASK_NOT_FOUND`：任务不存在

### 3.4 POST /v2/tasks/{thread_id}/resume

恢复中断的任务（从 checkpoint 断点继续）。

**响应 200：**
```json
{
  "thread_id": "a1b2c3d4...",
  "status": "running",
  "resumed_from_step": 5
}
```

**错误：**
- 404 `TASK_NOT_FOUND`：任务不存在
- 409 `TASK_RUNNING`：任务仍在执行中

### 3.5 POST /v2/tasks/{thread_id}/stop

停止正在执行的任务。

**响应 200：**
```json
{
  "thread_id": "a1b2c3d4...",
  "stopped": true
}
```

> `stopped: false` 表示任务已结束或不存在，取消操作未生效。

### 3.6 POST /v2/tasks/{thread_id}/confirm

确认高风险操作（如代下单）。任务执行到高风险动作时会暂停并推送 `need_confirm` 事件，用户调用此接口确认后继续。

**请求体：**
```json
{
  "action": "confirm_order",
  "confirmed": true,
  "detail": {"address_id": "addr_001", "payment_method": "alipay"}
}
```

**响应 200：**
```json
{
  "thread_id": "a1b2c3d4...",
  "status": "running",
  "confirmed": true
}
```

---

## 4. SSE 流式接口

### 4.1 GET /v2/tasks/{thread_id}/stream

订阅任务执行进度与最终输出的 SSE 流。

**请求 Header：**
```
Accept: text/event-stream
```

**事件类型：**

| event | 说明 | data 结构 |
|-------|------|----------|
| `status` | 任务状态变更 | `{"status": "running"}` |
| `progress` | 工具执行进度 | `{"step": 3, "tool": "search_product", "ok": true, "message": "搜索完成", "duration_ms": 3500, "screenshot": "/uploads/step_3.png"}` |
| `token` | LLM 回复分片 | `{"delta": "经过"}` |
| `need_confirm` | 高风险操作需确认 | `{"action": "confirm_order", "product": "QCY HT05", "price": 199.0}` |
| `done` | 任务完成 | `{"status": "succeeded", "final_reply": "...", "products": [...]}` |
| `error` | 任务失败 | `{"code": "DEVICE_OFFLINE", "message": "设备离线"}` |

**SSE 响应示例：**
```
event: status
data: {"status": "running"}

event: progress
data: {"step": 1, "tool": "open_app", "ok": true, "message": "已打开京东", "duration_ms": 1200}

event: progress
data: {"step": 2, "tool": "search_product", "ok": true, "message": "搜索完成", "duration_ms": 3500}

event: token
data: {"delta": "经过"}

event: token
data: {"delta": "对比"}

event: done
data: {"status": "succeeded", "final_reply": "经过对比，推荐...", "products": [...]}
```

**重连：** 浏览器 `EventSource` 自动重连。服务端通过 `Last-Event-ID` Header 支持断点续传（从指定 step 开始推送）。

**降级：** 不支持 SSE 的客户端可使用 `GET /v2/tasks/{thread_id}` 轮询（建议间隔 1s）。

---

## 5. 会话管理

### 5.1 POST /v2/chat

发送一条对话消息（会话式接口，自动管理 session_id）。

**请求体：**
```json
{
  "message": "前两个哪个好评率高？",
  "session_id": "sess_1700000000000",
  "image_path": null,
  "task_type": "",
  "model": "auto"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| message | string | 是 | 用户消息 |
| session_id | string | 否 | 为空自动创建新会话 |
| image_path | string | 否 | 参考图路径 |
| task_type | string | 否 | 显式任务类型 |
| model | string | 否 | 模型选择 |

**响应 201：**
```json
{
  "session_id": "sess_1700000000000",
  "thread_id": "b2c3d4e5...",
  "status": "queued"
}
```

> 与 `/v2/tasks` 的区别：`/v2/chat` 自动管理会话，上一轮仍在执行时会自动取消旧任务；`/v2/tasks` 为底层任务接口，需手动管理 thread_id。

### 5.2 GET /v2/chat/sessions

列出最近会话。

**查询参数：** `limit`（默认20）

**响应 200：**
```json
{
  "sessions": [
    {
      "session_id": "sess_1700000000000",
      "title": "帮我比价蓝牙耳机",
      "last_ts": 1700000045.0,
      "last_role": "assistant",
      "last_content": "经过对比，推荐QCY HT05...",
      "message_count": 6,
      "status": "active"
    }
  ]
}
```

### 5.3 GET /v2/chat/{session_id}/history

获取会话完整对话历史。

**响应 200：**
```json
{
  "session_id": "sess_xxx",
  "messages": [
    {"role": "user", "content": "帮我比价蓝牙耳机", "ts": 1700000000.0},
    {"role": "assistant", "content": "经过对比，推荐...", "ts": 1700000045.0}
  ]
}
```

### 5.4 GET /v2/chat/{session_id}/stream

订阅会话级 SSE 流（封装任务流，自动关联当前活跃任务）。事件格式同 4.1。

### 5.5 POST /v2/chat/{session_id}/stop

停止会话当前执行的任务。响应同 3.5。

### 5.6 DELETE /v2/chat/{session_id}

删除会话（软删除，标记为 archived）。

**响应 200：**
```json
{"session_id": "sess_xxx", "deleted": true}
```

### 5.7 PATCH /v2/chat/{session_id}

重命名会话标题。

**请求体：** `{"title": "耳机比价"}`

---

## 6. 文件上传

### 6.1 POST /v2/upload

上传图片（以图搜图/找相似用）。

**请求：** `multipart/form-data`，字段名 `file`

**限制：**
- 格式：JPG / PNG / WebP / GIF / BMP
- 大小：≤ 8MB
- 魔数校验：拒绝伪装扩展名

**响应 201：**
```json
{
  "path": "/absolute/path/to/uploads/1700000000000_a1b2c3d4.jpg",
  "name": "1700000000000_a1b2c3d4.jpg",
  "size": 245760,
  "mime_type": "image/jpeg"
}
```

**错误：**
- 400 `INVALID_FILE_TYPE`：不支持的格式
- 400 `EMPTY_FILE`：文件内容为空
- 413 `FILE_TOO_LARGE`：超过 8MB

---

## 7. 设备管理

### 7.1 GET /v2/devices

列出设备池状态。

**响应 200：**
```json
{
  "devices": [
    {
      "device_id": "emulator-5554",
      "serial": "127.0.0.1:16416",
      "name": "MuMu模拟器-1",
      "device_type": "emulator",
      "os_version": "Android 12",
      "resolution": "1080x1920",
      "status": "busy",
      "tags": ["taobao_logged_in", "jd_logged_in"],
      "current_task_id": "1700000000-a1b2c3d4",
      "tasks_done": 156,
      "tasks_failed": 12,
      "last_heartbeat": 1700000045.0
    }
  ],
  "total": 3,
  "online": 2,
  "busy": 1
}
```

### 7.2 POST /v2/devices

注册新设备。

**请求体：**
```json
{
  "device_id": "phone-01",
  "serial": "192.168.1.10:5555",
  "name": "测试机-红米K50",
  "device_type": "real_device",
  "tags": ["taobao_logged_in"]
}
```

**响应 201：** 设备详情（同 7.1 单条结构）

**错误：**
- 409 `DEVICE_EXISTS`：device_id 已存在
- 400 `DEVICE_UNREACHABLE`：ADB 连接失败

### 7.3 DELETE /v2/devices/{device_id}

移除设备（正在执行任务时拒绝）。

**错误：**
- 409 `DEVICE_BUSY`：设备正在执行任务

### 7.4 POST /v2/devices/{device_id}/reconnect

强制重连设备。

**响应 200：** `{"device_id": "phone-01", "status": "online"}`

### 7.5 POST /v2/devices/{device_id}/clear-apps

清理设备上指定应用数据（防账号串扰）。

**请求体：** `{"packages": ["com.taobao.taobao", "com.jingdong.app.mall"]}`

---

## 8. 工具元数据

### 8.1 GET /v2/tools

列出所有已注册工具及其参数 schema。

**响应 200：**
```json
{
  "open_app": {
    "description": "启动指定应用",
    "parameters": {
      "type": "object",
      "properties": {
        "app_name": {"type": "string", "description": "应用名称"}
      },
      "required": ["app_name"]
    }
  },
  "click": {
    "description": "点击指定元素编号（SoM索引）",
    "parameters": {
      "type": "object",
      "properties": {
        "element_index": {"type": "integer", "description": "SoM标注编号"},
        "timeout": {"type": "number", "description": "超时秒数"}
      },
      "required": ["element_index"]
    }
  }
}
```

---

## 9. 模型配置

### 9.1 GET /v2/models

列出可用模型与当前配置。

**响应 200：**
```json
{
  "available": [
    {"id": "qwen-vl-plus", "name": "通义千问VL-Plus", "role": "manager", "vendor": "dashscope"},
    {"id": "qwen-vl-turbo", "name": "通义千问VL-Turbo", "role": "executor", "vendor": "dashscope"},
    {"id": "deepseek-v3", "name": "DeepSeek V3", "role": "verifier", "vendor": "dashscope"}
  ],
  "current": {
    "manager": "qwen-vl-plus",
    "executor": "qwen-vl-turbo",
    "verifier": "deepseek-v3",
    "fallback_chain": ["qwen-vl-plus", "deepseek-v3"]
  }
}
```

### 9.2 PUT /v2/models/config

更新模型路由配置（热生效，不需重启）。

**请求体：**
```json
{
  "manager": "qwen-vl-plus",
  "executor": "qwen-vl-turbo",
  "verifier": "deepseek-v3",
  "fallback_chain": ["qwen-vl-plus", "deepseek-v3"]
}
```

**响应 200：** 更新后的配置（同 9.1 current）

---

## 10. 指标与监控

### 10.1 GET /v2/metrics

Prometheus 文本格式指标端点。

**响应 200（text/plain）：**
```
# HELP dro_tasks_total Total tasks by status
# TYPE dro_tasks_total counter
dro_tasks_total{status="succeeded"} 156
dro_tasks_total{status="failed"} 12
dro_tasks_total{status="cancelled"} 3

# HELP dro_task_duration_seconds Task duration histogram
# TYPE dro_task_duration_seconds histogram
dro_task_duration_seconds_bucket{le="10"} 5
dro_task_duration_seconds_bucket{le="30"} 42
dro_task_duration_seconds_bucket{le="60"} 120
dro_task_duration_seconds_bucket{le="+Inf"} 171

# HELP dro_llm_calls_total LLM calls by model and node
# TYPE dro_llm_calls_total counter
dro_llm_calls_total{model="qwen-vl-plus",node="manager"} 342
dro_llm_calls_total{model="qwen-vl-turbo",node="executor"} 890

# HELP dro_devices_online Online device count
# TYPE dro_devices_online gauge
dro_devices_online 2
```

### 10.2 GET /v2/stats/summary

业务统计摘要（用于管理后台）。

**查询参数：** `days`（默认7）

**响应 200：**
```json
{
  "period_days": 7,
  "tasks_total": 171,
  "success_rate": 0.91,
  "avg_duration_ms": 42000,
  "avg_steps": 7.2,
  "avg_llm_calls": 5.1,
  "total_tokens": 2400000,
  "top_tools": [
    {"name": "search_product", "count": 156, "success_rate": 0.95},
    {"name": "extract_info", "count": 148, "success_rate": 0.92}
  ],
  "failure_reasons": [
    {"reason": "元素未找到", "count": 5},
    {"reason": "设备离线", "count": 3}
  ]
}
```

---

## 11. 评估接口（内部）

### 11.1 POST /v2/eval/run

批量运行评估任务集（CI/CD 调用，需管理员权限）。

**请求体：**
```json
{
  "suite": "ecommerce",
  "limit": 20,
  "device_ids": ["emulator-5554"],
  "model_config": {"manager": "qwen-vl-plus", "executor": "qwen-vl-turbo"}
}
```

**响应 202：**
```json
{
  "eval_id": "eval_01H...",
  "status": "running",
  "total": 20
}
```

### 11.2 GET /v2/eval/{eval_id}

查询评估结果。

**响应 200：**
```json
{
  "eval_id": "eval_01H...",
  "suite": "ecommerce",
  "status": "completed",
  "total": 20,
  "succeeded": 14,
  "failed": 6,
  "success_rate": 0.70,
  "avg_duration_ms": 38000,
  "avg_llm_calls": 4.8,
  "results": [
    {"task_id": "...", "case": "比价耳机", "success": true, "duration_ms": 35000},
    {"task_id": "...", "case": "找相似键盘", "success": false, "error": "元素未找到"}
  ]
}
```

---

## 12. 前端页面路由

| 路径 | 页面 | 说明 |
|------|------|------|
| `/` | 首页 | 快速任务入口 + 最近任务 |
| `/chat` | 对话页 | 会话侧栏 + 消息区 + 输入区 |
| `/console` | 控制台 | 任务列表 + 步骤时间线 + 设备状态 |
| `/settings` | 设置页 | 模型选择、设备管理、API Key |

> 生产化升级后前端抽离为独立 React 应用，FastAPI 仅提供 API；开发阶段 FastAPI 仍托管静态页面。

---

## 13. 接口变更记录

| 版本 | 日期 | 变更 |
|------|------|------|
| V1.0 | 2026-08 | 初始 v2 API（tasks/chat/upload/tools） |
| V2.0 | 2026-09 | 新增 SSE 流式接口、设备管理、模型配置、metrics、评估接口；任务表新增 llm_calls/token_usage/duration_ms 字段 |
