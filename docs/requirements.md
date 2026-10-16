六、第四阶段：项目升级（第8-10周）

### 第8-9周：dro电商助手深度重构

这两周把你最有想法但代码最粗糙的dro项目，从demo升级成一个能经得起面试追问的Agent项目。

**第8周 Day 1：架构设计**

先做架构设计，不要急着写代码。在纸上或用draw.io画新架构图：用户输入→意图识别节点→规划节点→工具执行循环（ReAct）→结果生成节点，记忆系统在每一步注入。在项目里新建 `v2/` 目录，旧代码保留不动，新代码全写在v2里，这样出问题可以回退。v2里的文件结构：`graph.py`（LangGraph图定义和State）、`tools/`（每个手机操作一个工具文件）、`memory.py`（把第7周的MemoryManager搬过来适配）、`prompts.py`（所有提示词模板集中管理）、`__main__.py`（入口）。

**第8周 Day 2：工具化执行引擎**

原来dro是"LLM生成Python代码在本地执行"，这种方式不稳定且危险，彻底废弃。每个手机操作写成一个独立的Tool类，放在 `tools/` 目录里：`open_app.py`（工具名open_app，参数app_name: str，内部用adb shell monkey -p 包名 -c android.intent.category.LAUNCHER 1打开App，支持淘宝和京东的包名映射）、`search_product.py`（参数keyword: str，内部用adb input tap点击搜索框坐标→adb input text输入关键词→adb input keyevent 66回车，返回"已搜索关键词"）、`click_product.py`（参数index: int，点击第index个商品的坐标，返回"已点击第N个商品"）、`extract_info.py`（无参数，截图→用Qwen-VL多模态API分析屏幕内容，提取商品标题、价格、销量、店铺名，返回结构化JSON字符串）、`go_back.py`（无参数，adb input keyevent 4返回）、`screenshot.py`（无参数，adb screencap截图保存到trajectories目录，返回截图路径）。每个工具都用你第6周写的register_tool装饰器注册，都有清晰的docstring描述，内部都有try/except错误处理，失败时返回错误信息字符串而不是抛异常。单独测试每个工具：在Python交互环境里import后直接调用，确认每个都能正常控制手机。

**第8周 Day 3：定义 State 和节点**

State用TypedDict，字段包括 `messages: list`（对话历史）、`intent: str`（识别出的意图：price_compare/find_similar/recommend/chat）、`task_plan: list`（规划的执行步骤）、`tool_calls: list`（当前待执行的工具调用）、`memory_context: dict`（记忆系统注入的上下文）。写4个节点：`intent_node`（把用户输入发给LLM，让它从4种意图里选一个，写入State）、`planner_node`（根据意图生成执行计划，比如比价任务的计划是["打开淘宝","搜索商品","提取前3个价格","打开京东","搜索商品","提取前3个价格","对比返回"]，写入State）、`agent_node`（ReAct循环，根据计划调用工具执行，这就是你第5周写的ReAct逻辑，工具用上面写的6个手机操作工具）、`result_node`（执行完计划后，整理工具返回的结果，生成自然语言的最终回答，比如"淘宝最低价199元，京东最低价209元，淘宝更便宜10元"）。

**第8周 Day 4：连边和条件分支**

START→intent_node，intent_node后加条件边：如果intent是chat（闲聊）直接到result_node（不需要执行工具），否则到planner_node；planner_node→agent_node；agent_node后加条件边：如果还有未执行的工具调用就回到agent_node（ReAct循环），如果计划执行完或达到最大步数（比如15步）就到result_node；result_node→END。用LangGraph的SqliteSaver做checkpointer，thread_id用用户ID+时间戳生成。编译后测试：说"你好"，识别为chat→直接result_node返回问候；说"帮我找100元以内的蓝牙耳机"，识别为recommend→planner生成计划→agent循环调用open_app→search_product→click_product→extract_info→result_node返回推荐结果。在每一步打印State，确认整个流程按预期走。

**第9周 Day 1：接入四层记忆**

把第7周的MemoryManager集成到v2里，在graph的每个节点执行前都调用memory.build_prompt获取上下文注入。具体来说：短期记忆用滑动窗口管理对话历史；会话摘要在长对话时自动生成；长期记忆把用户的购物偏好（预算、品牌、颜色、尺码）存入Qdrant，每次对话检索注入；用户画像存在PostgreSQL，结构化存储偏好和预算。在agent_node的system prompt里加入记忆上下文，比如"用户预算200以内，喜欢红色，之前买过索尼耳机"。测试：第一轮说"我预算200以内，喜欢红色"，系统提取存入长期记忆和画像；重启后说"推荐耳机"，agent_node的prompt里能看到预算和颜色偏好，推荐结果会考虑这些因素。

**第9周 Day 2：长期任务恢复**

因为用了SqliteSaver，每个任务的每一步State都存在checkpoints.db里。写一个 `resume_task(thread_id)` 函数，用同一个thread_id调用 `graph.invoke(None, config={"configurable": {"thread_id": thread_id}})`，LangGraph会自动从最后一个checkpoint继续执行。测试：执行一个比价任务，在agent_node执行到第3步时强制Ctrl+C终止程序，重新启动后调用resume_task传同一个thread_id，程序能从第3步接着执行而不是从头开始——这就是岗位要求里的"长期任务恢复"。

**第9周 Day 3：异常处理与自愈**

手机操作经常遇到意外：App弹出广告弹窗、页面加载慢、网络超时。在agent_node里加入异常检测：每次extract_info后用Qwen-VL判断屏幕上是否有弹窗（"关闭""跳过""取消"按钮），如果有就自动调用一个新工具 `close_popup()`（尝试点击常见的关闭按钮坐标）；如果工具执行超时（超过10秒），返回超时信息给LLM让它决定重试或跳过；加入最大步数限制（15步），超过就强制结束并返回已收集到的部分结果。测试：手动在手机上弹出一个弹窗，Agent执行时能检测到并自动关闭。

**第9周 Day 4：评估体系**

准备10个典型任务测试用例，写在 `tests/eval_cases.py` 里：1.简单比价"找iPhone 15在淘宝和京东的最低价"；2.找同款（上传图片）"找这个杯子的同款"；3.按预算推荐"200以内推荐降噪耳机"；4.多轮对话"先找耳机，再对比前两个的价格"；5.模糊需求"买个杯子"（应该反问澄清而不是直接执行）；6.异常恢复（执行中手动切出App，看能否回来）；7.长期记忆"按照我上次的预算推荐"；8.用户画像更新"我喜欢红色"（画像应更新）；9.跨平台"在京东找自营的，在淘宝找便宜的"；10.任务中断恢复（执行到一半重启程序）。每个任务手动跑3次，记录在 `docs/evaluation.md` 里：任务描述、是否成功、执行步数、耗时、失败原因、改进方向。统计整体成功率（目标是10个任务里至少6个能成功或部分成功）。

**第9周 Day 5：根据评估结果优化**

把失败案例分类：是工具坐标不准导致的（改用UI元素定位或让Qwen-VL识别坐标）、是LLM规划不合理导致的（优化planner的prompt）、是记忆没注入导致的（检查memory.build_prompt的逻辑）、是App弹窗导致的（加强弹窗检测）。至少解决2个失败案例，重新跑评估，记录改进前后的对比。

**第9周 Day 6：重写 README**

新README必须包含：项目一句话介绍、新架构图（LangGraph节点和边的流程图）、核心特性（四层记忆、工具化执行、checkpoint恢复、异常自愈）、工具列表（6个手机操作工具各自的功能和参数）、评估结果（10个任务的成功率表格）、失败案例与改进（至少3个真实遇到的问题：现象→排查→根因→解决方案→效果，比如"一开始LLM生成的点击坐标经常偏移，因为用了固定坐标，改用Qwen-VL实时识别元素位置后准确率从40%提升到80%""任务执行中淘宝弹出优惠券弹窗导致流程中断，加入弹窗检测和自动关闭工具后恢复率提升到90%""多轮对话中早期偏好丢失，加入会话摘要和长期记忆后重启仍能记住用户预算"）、快速启动说明（怎么连手机、怎么配置API Key、怎么运行）、架构图和运行截图。

**第9周 Day 7：推 GitHub**

把v2目录的代码整理好，旧代码标记为deprecated，推到GitHub，确保陌生人clone下来能跑起来。

**这两周验收标准**：dro项目从一个单文件44KB的demo，变成一个基于LangGraph、有四层记忆、有工具化执行引擎、有checkpoint恢复、有评估报告、有完整文档的正经Agent项目，你能在面试中讲清楚每一个技术决策和至少3个失败改进案例。