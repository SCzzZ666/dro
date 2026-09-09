"""评估用例：10 个典型任务（需求文档第 9 周 Day 4）。

每个用例包含：编号、名称、用户输入、task_type、期望行为、评估要点。
可配合 docs/evaluation.md 记录手动运行 3 次的结果。
"""

EVAL_CASES = [
    {
        "id": 1,
        "name": "简单比价",
        "user_input": "找 iPhone 15 在淘宝和京东的最低价",
        "task_type": "比价",
        "expect": "intent=price_compare；两平台各提取至少一个价格并对比",
        "check": ["intent == price_compare", "collected 覆盖 2 个平台"],
        "pass_rate": None,  # 手动运行填写
        "note": "",
    },
    {
        "id": 2,
        "name": "找同款（上传图片）",
        "user_input": "找这个杯子的同款",
        "task_type": "找相似",
        "image_path": "uploads/example.jpg",
        "expect": "intent=find_similar（有图强制走找相似）",
        "check": ["intent == find_similar", "计划包含 search_product"],
        "pass_rate": None,
        "note": "",
    },
    {
        "id": 3,
        "name": "按预算推荐",
        "user_input": "200以内推荐降噪耳机",
        "task_type": "最优方案",
        "expect": "intent=recommend；记忆写入 budget=200",
        "check": ["intent == recommend", "long_term 含 budget"],
        "pass_rate": None,
        "note": "",
    },
    {
        "id": 4,
        "name": "多轮对话",
        "user_input": "先找耳机，再对比前两个的价格",
        "task_type": "",
        "expect": "复用 thread_id 时保留上下文",
        "check": ["两次 invoke 同一 thread_id", "第二次包含历史记忆"],
        "pass_rate": None,
        "note": "",
    },
    {
        "id": 5,
        "name": "模糊需求",
        "user_input": "买个杯子",
        "task_type": "",
        "expect": "应反问澄清而不是立即执行（chat/need_clarify）",
        "check": ["intent == chat 或 need_clarify == true", "reply 非空"],
        "pass_rate": None,
        "note": "",
    },
    {
        "id": 6,
        "name": "异常恢复（弹窗）",
        "user_input": "比价耳机",
        "task_type": "比价",
        "expect": "extract_info 检测到 popup 时自动调用 close_popup",
        "check": ["tool_log 含 close_popup", "自愈后重试当前步骤"],
        "pass_rate": None,
        "note": "需真机/模拟器手工弹窗",
    },
    {
        "id": 7,
        "name": "长期记忆",
        "user_input": "按照我上次的预算推荐",
        "task_type": "最优方案",
        "expect": "重启后仍能读到上次保存的偏好",
        "check": ["build_prompt 含上次 budget"],
        "pass_rate": None,
        "note": "重启服务后再验证",
    },
    {
        "id": 8,
        "name": "用户画像更新",
        "user_input": "我喜欢红色",
        "task_type": "",
        "expect": "画像新增 color=红色",
        "check": ["profile 含 color"],
        "pass_rate": None,
        "note": "",
    },
    {
        "id": 9,
        "name": "跨平台",
        "user_input": "在京东找自营的，在淘宝找便宜的",
        "task_type": "比价",
        "expect": "计划包含京东+淘宝各一轮",
        "check": ["plan 含 2 次 open_app（两个平台）"],
        "pass_rate": None,
        "note": "",
    },
    {
        "id": 10,
        "name": "任务中断恢复",
        "user_input": "比价耳机",
        "task_type": "比价",
        "expect": "执行到一半停止进程，重启后用同一 thread_id 恢复",
        "check": ["resume_task 后 step_index 从断点继续"],
        "pass_rate": None,
        "note": "",
    },
]


def summary() -> str:
    """打印评估用例清单。"""
    lines = [f"{c['id']:>2}. {c['name']}: {c['user_input']}" for c in EVAL_CASES]
    return "\n".join(lines)


if __name__ == "__main__":
    print(summary())