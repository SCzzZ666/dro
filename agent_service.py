import asyncio
import os
import json
import re
import ast  # 引入 ast 模块用于解析 Python 字典格式
from pathlib import Path
from urllib.parse import quote
from dotenv import load_dotenv

# DroidRun imports
try:
    from droidrun.agent.droid import DroidAgent
    from droidrun.agent.utils.llm_picker import load_llm
    from droidrun.config_manager import (
        DroidrunConfig,
        AgentConfig,
        CodeActConfig,
        ManagerConfig,
        ExecutorConfig,
        TracingConfig,
        DeviceConfig
    )
    # Monkeypatch ManagerAgent._validate_and_retry to fix the infinite loop issue
    from droidrun.agent.manager.manager_agent import ManagerAgent
except ImportError as e:
    print(f"Failed to import droidrun: {e}")
    # Define mocks or exit if critical
    raise



class AgentService:
    """
    后端核心业务逻辑类
    在此处实现 DroidrunPortal + AI 流程
    """
    def __init__(self):
        # Load env from current directory (d:\桌面\droidrun agent\dro\.env)
        env_path = Path(__file__).parent / '.env'
        if env_path.exists():
            print(f"Loading env from {env_path}")
            load_dotenv(env_path)
        else:
            # Fallback to parent directory if not found in current
            parent_env_path = Path(__file__).parent.parent / '.env'
            if parent_env_path.exists():
                print(f"Loading env from {parent_env_path}")
                load_dotenv(parent_env_path)
            else:
                print(f"Warning: .env not found at {env_path} or {parent_env_path}")
            
        # Device Serial Configuration
        # Priority: Env Var > Default
        self.device_serial = os.getenv("DEVICE_SERIAL", "127.0.0.1:16416")
        print(f"AgentService initialized with device_serial: {self.device_serial}")
        
        # 初始化会话存储 (按 session_id 隔离)
        self.sessions = {}  # {session_id: [history_messages]}
        
        # 任务控制标记 (按 session_id 隔离)
        self.active_tasks = {} # {session_id: asyncio.Task}
        
    def stop_task(self, session_id: str = None):
        """
        强制停止当前任务
        :param session_id: 指定要停止的会话ID。如果不传，则停止所有（或根据策略调整）
        """
        if session_id:
            task = self.active_tasks.get(session_id)
            if task:
                print(f"🛑 收到停止指令，正在终止会话 {session_id} 的任务...")
                task.cancel()
                # 从活跃任务中移除
                self.active_tasks.pop(session_id, None)
        else:
            # 停止所有任务 (兜底)
            print("🛑 收到全局停止指令，正在终止所有任务...")
            for sid, task in list(self.active_tasks.items()):
                task.cancel()
                self.active_tasks.pop(sid, None)

    def _get_history(self, session_id: str) -> list:
        """获取指定会话的历史记录"""
        if session_id not in self.sessions:
            self.sessions[session_id] = []
        return self.sessions[session_id]

    def _add_history(self, session_id: str, role: str, content: str):
        """添加历史记录"""
        history = self._get_history(session_id)
        history.append({"role": role, "content": content})

    def _check_device_connection(self):
        """
        强制检查 ADB 设备连接状态及 DroidRun Portal 服务状态，防止 LLM 在无设备时编造数据。
        """
        import subprocess
        import time
        
        try:
            # 1. 基础 ADB 连接检查
            # 增加 timeout=5 防止 ADB 卡死
            result = subprocess.run(["adb", "devices"], capture_output=True, text=True, check=True, timeout=5)
            output = result.stdout
            
            # 模糊匹配检查设备是否在线
            lines = output.strip().split('\n')
            connected = False
            for line in lines:
                if self.device_serial in line and "device" in line and "offline" not in line:
                    connected = True
                    break
            
            if not connected:
                # 针对网络模拟器 (IP:Port) 尝试自动重连
                if ":" in self.device_serial and self.device_serial.replace(".", "").replace(":", "").isdigit():
                    print(f"⚠️ 设备 {self.device_serial} 未在线，尝试自动重连...")
                    try:
                        subprocess.run(["adb", "disconnect", self.device_serial], capture_output=True, timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
                        
                    time.sleep(1)
                    try:
                        subprocess.run(["adb", "connect", self.device_serial], capture_output=True, timeout=10)
                    except subprocess.TimeoutExpired:
                        print(f"⚠️ 连接设备 {self.device_serial} 超时")
                else:
                    # 真机模式：无法通过命令重连，只能提示用户检查物理连接
                    print(f"⚠️ 真机设备 {self.device_serial} 未在线或未授权，请检查 USB 连接及调试权限。")

                # 再次确认连接
                check_result = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=5)
                if self.device_serial not in check_result.stdout:
                    raise Exception(f"无法连接到 ADB 设备: {self.device_serial}。请检查设备连接状态。")

                time.sleep(2)
                
                # 二次检查
                result = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=5)
                if self.device_serial not in result.stdout or "offline" in result.stdout:
                    if ":" in self.device_serial:
                         raise ConnectionError(f"设备 {self.device_serial} 连接失败。请检查模拟器是否启动。")
                    else:
                         raise ConnectionError(f"设备 {self.device_serial} 连接失败。请确保 USB 连接正常且已允许 USB 调试。")
                print(f"✅ 设备 {self.device_serial} 连接确认正常。")

            # 2. DroidRun Portal 服务检查
            # 检查无障碍服务是否开启
            cmd = ["adb", "-s", self.device_serial, "shell", "settings", "get", "secure", "enabled_accessibility_services"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if "com.droidrun.portal/com.droidrun.portal.service.DroidrunAccessibilityService" not in res.stdout:
                # 尝试自动开启
                print("⚠️ 检测到 DroidRun 无障碍服务未开启，尝试自动开启...")
                enable_cmd = [
                    "adb", "-s", self.device_serial, "shell", "settings", "put", "secure", "enabled_accessibility_services", 
                    "com.droidrun.portal/com.droidrun.portal.service.DroidrunAccessibilityService"
                ]
                subprocess.run(enable_cmd, capture_output=True, timeout=5)
                time.sleep(1)
                
                # 再次检查
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                if "com.droidrun.portal" not in res.stdout:
                    raise ConnectionError("❌ DroidRun Portal 服务未启动且无法自动开启。\n请在手机/模拟器中手动开启【无障碍服务 -> DroidRun Portal】。")
                print("✅ DroidRun 无障碍服务已自动开启。")

        except subprocess.TimeoutExpired:
            raise ConnectionError("ADB 命令执行超时，请检查 ADB 服务或模拟器状态。")
        except subprocess.CalledProcessError:
            raise ConnectionError("无法执行 adb 命令，请确保 Android SDK Platform-tools 已配置到环境变量 Path 中。")
        except FileNotFoundError:
            raise ConnectionError("找不到 adb 命令，请安装 Android SDK Platform-tools 并配置环境变量。")

    def _get_llm(self, model_selection: str):
        """
        根据用户选择配置 LLM
        """
        api_key = os.getenv("DASHSCOPE_API_KEY")
        model_selection = model_selection.lower()
        
        provider = "OpenAILike"
        model_name = "qwen3-vl-plus"
        base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        
        if "deepseek" in model_selection:
            # DeepSeek Configuration (via Aliyun DashScope)
            # 使用阿里云托管的 DeepSeek 模型，统一使用 DASHSCOPE_API_KEY
            model_name = "deepseek-v3.2" 
            print(f"Using DeepSeek model (Aliyun): {model_name}")
        else:
            print(f"Using Qwen model: {model_name}")

        if not api_key:
            raise ValueError("API Key not found. Please check .env file (DASHSCOPE_API_KEY).")
        
        # 保存供 _analyze_intent 使用
        self.current_api_config = {
            "api_key": api_key,
            "base_url": base_url,
            "model": model_name
        }

        return load_llm(
            provider_name=provider,
            model=model_name,
            temperature=0.2,
            api_base=base_url,
            api_key=api_key,
            context_window=32000,
            is_chat_model=True
        )

    async def _negotiate_requirement(self, session_id: str, current_text: str) -> dict:
        """
        需求谈判：结合历史上下文分析用户输入，直到确定具体的商品目标。
        """
        if not hasattr(self, 'current_api_config'):
            return {"status": "CONFIRMED", "search_term": current_text}

        import httpx
        
        system_prompt = """
你是一名**高情商、有品位的金牌导购顾问**。
你的核心特质是：**敏锐感知用户的购物阶段**。
*   当用户在“犹豫/闲逛/求推荐”时，你要做**参谋**，提供灵感，而不是急着下单。
*   只有当用户“目标明确/笃定”时，你才做**执行者**，迅速干活。

**状态判定逻辑 (State Machine) - 升级版**：

1.  **� NEED_INFO (咨询/探索/模糊)**
    *   **触发条件 (必须敏感)**：
        *   用户使用了**咨询类词汇**：如“有什么推荐”、“什么样的好”、“适合送礼吗”、“最近流行什么”。
        *   用户只说了**大类目**：如“买双鞋”、“看个包”。
        *   **关键修正**：即使包含了具体品牌（如“adidas拖鞋推荐”），只要带有“推荐”二字，**一律视为 NEED_INFO**！因为用户想看的是“精选集”，而不是“全量搜索结果”。
    *   **行动策略**：
        *   **Reply**：语气要亲切、专业。先肯定用户的眼光，然后抛出 **2-3 个具体的细分风格或场景**供用户选择。
        *   **Choices**：给出具体的细分方向。
    *   **示例**：
        *   User: "有什么 adidas 拖鞋推荐" -> **NEED_INFO** (不是 CONFIRMED!)
        *   Reply: "Adidas 的拖鞋可是潮人必备！您是想要经典的 **Adilette 三叶草**（百搭），还是脚感无敌的 **Yeezy Slide**（踩屎感），或者是 **Duramo** 这种它可以湿水的居家款？"
        *   Choices: ["经典三叶草", "Yeezy Slide", "居家防水款"]

2.  **� CONFIRMED (指令/笃定/明确)**
    *   **触发条件**：
        *   用户语气**果断、直接**：如“就买这个”、“去搜一下 xxx”、“比价 xxx”。
        *   用户指定了**极具辨识度**的具体型号，且**没有**询问口吻。
    *   **示例**：
        *   "帮我找一下 adidas Adilette 22" -> **CONFIRMED** (目标极明确)
        *   "对比一下 iPhone 16 和 15" -> **CONFIRMED**
        *   "买一箱农夫山泉" -> **CONFIRMED** (标品，无需废话)
    *   **行动**：生成精确的 `search_term`。

3.  **⚪ CHAT (闲聊)**
    *   判定标准：与购物无关的互动（天气、问候、哲学探讨）。

**输出格式 (JSON Only)**：
{
    "analysis": "分析用户语气：是'求推荐'(NEED_INFO) 还是 '下指令'(CONFIRMED)？",
    "status": "CONFIRMED" | "NEED_INFO" | "CHAT",
    "reply": "高情商回复。NEED_INFO 时必须提供专业建议。",
    "choices": ["细分方向A", "细分方向B"], // NEED_INFO 必填
    "search_term": "搜索词" // CONFIRMED 必填
}
"""
        # 构建消息列表：System + History + Current User Input
        messages = [{"role": "system", "content": system_prompt}]
        
        # 添加最近 10 条历史记录以防 Context 过长
        history = self._get_history(session_id)
        messages.extend(history[-10:])
        
        # 添加当前用户输入
        messages.append({"role": "user", "content": current_text})

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.current_api_config['base_url']}/chat/completions",
                    headers={"Authorization": f"Bearer {self.current_api_config['api_key']}"},
                    json={
                        "model": self.current_api_config['model'],
                        "messages": messages,
                        "temperature": 0.1
                    },
                    timeout=15.0
                )
                if response.status_code == 200:
                    res_json = response.json()
                    content = res_json['choices'][0]['message']['content']
                    # 清洗 JSON
                    content = re.sub(r'^```json\s*', '', content, flags=re.MULTILINE)
                    content = re.sub(r'^```\s*', '', content, flags=re.MULTILINE)
                    content = re.sub(r'\s*```$', '', content, flags=re.MULTILINE)
                    return json.loads(content)
        except Exception as e:
            print(f"需求分析失败，兜底执行: {e}")
        
        # 兜底
        return {"status": "CONFIRMED", "search_term": current_text}

    async def _analyze_image(self, image_path: str) -> dict:
        """
        调用多模态模型分析图片，提取视觉特征和搜索关键词。
        """
        if not hasattr(self, 'current_api_config'):
            return {"features": [], "keywords": "图片商品"}

        import base64
        import httpx

        print(f"👁️ 正在进行视觉预分析: {image_path}")

        try:
            # 读取图片并编码为 base64
            with open(image_path, "rb") as image_file:
                base64_image = base64.b64encode(image_file.read()).decode('utf-8')

            prompt = """
请分析这张电商商品图片。
1. **视觉解码**：提取 3-5 个最具辨识度的视觉特征（如颜色、材质、款式、独特设计）。
2. **生成搜索词**：将这些特征转化为电商平台的高效搜索关键词（空格分隔）。

请输出纯 JSON 格式：
{
    "features": ["特征1", "特征2", "特征3"],
    "keywords": "关键词1 关键词2 关键词3"
}
"""
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                    ]
                }
            ]

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.current_api_config['base_url']}/chat/completions",
                    headers={"Authorization": f"Bearer {self.current_api_config['api_key']}"},
                    json={
                        "model": self.current_api_config['model'], # 使用支持视觉的模型 (qwen3-vl-plus or deepseek)
                        "messages": messages,
                        "temperature": 0.1
                    },
                    timeout=30.0
                )
                
                if response.status_code == 200:
                    res_json = response.json()
                    content = res_json['choices'][0]['message']['content']
                    # 清洗 JSON
                    content = re.sub(r'^```json\s*', '', content, flags=re.MULTILINE)
                    content = re.sub(r'^```\s*', '', content, flags=re.MULTILINE)
                    content = re.sub(r'\s*```$', '', content, flags=re.MULTILINE)
                    result = json.loads(content)
                    print(f"👁️ 视觉分析结果: {result}")
                    return result
                else:
                    print(f"❌ 视觉分析请求失败: {response.status_code} - {response.text}")

        except Exception as e:
            print(f"❌ 视觉分析发生异常: {e}")

        # 兜底
        return {"features": ["未知特征"], "keywords": "同款商品"}

    async def run(self, session_id: str, model: str, product_name: str, task_type: str, image_path: str = None):
        """
        执行 Agent 任务
        """
        # 记录当前任务，以便 stop_task 可以取消它
        self.active_tasks[session_id] = asyncio.current_task()
        
        print(f"[后端日志] 启动任务: Session={session_id}, 模型={model}, 商品={product_name}, 任务={task_type}, 图片={image_path}")
        
        try:
            llm = self._get_llm(model)
        except Exception as e:
            self.active_tasks.pop(session_id, None)
            return {
                "products": [],
                "recommendation": f"LLM 初始化失败: {str(e)}",
                "show_checkout_option": False,
                "error": True
            }

        # ---------------------------------------------------------------------
        # 0. 智能需求谈判 (Intelligent Requirement Negotiation) & 视觉预分析
        # ---------------------------------------------------------------------
        final_product_name = product_name
        visual_feedback = ""
        
        # A. 如果有图片，先进行视觉预分析 (Visual Pre-analysis)
        if image_path:
            print("👁️ 检测到图片输入，开始进行视觉预分析...")
            visual_res = await self._analyze_image(image_path)
            
            features = visual_res.get("features", [])
            keywords = visual_res.get("keywords", "")
            
            if keywords and keywords != "同款商品":
                # 覆盖原来的 product_name，直接使用视觉关键词
                final_product_name = keywords
                print(f"🎯 视觉锁定关键词: {final_product_name}")
                
                # 生成反馈文案，稍后拼接到 recommendation
                features_str = "、".join(features)
                visual_feedback = f"【👀 视觉解码】我看到了：{features_str}。\n已为您生成搜索词：**{final_product_name}**。\n\n"
            else:
                visual_feedback = "【👀 视觉解码】图片分析未提取到有效特征，将尝试直接搜索。\n\n"
            
            # 【强制逻辑修正】如果有图片，必须进入“找相似”流程
            # 无论前端传什么 task_type，只要有图，就是找同款
            print("🔄 检测到图片输入，强制将任务类型修正为: [找相似]")
            task_type = "找相似"
        
        # B. 只有当没有图片且有文本输入时才进行意图识别 (Text Intent Analysis)
        elif product_name:
            print("🤔 正在分析用户需求...")
            
            # 1. 分析前，先不入库，等待分析结果决定是否需要组合上下文
            analysis_res = await self._negotiate_requirement(session_id, product_name)
            print(f"💡 需求分析结果: {analysis_res}")
            
            status = analysis_res.get("status", "CONFIRMED")
            choices = analysis_res.get("choices", [])
            
            # 2. 将当前轮次存入历史 (User Input)
            self._add_history(session_id, "user", product_name)
            
            if status == "CHAT":
                reply = analysis_res.get("reply", "你好！我是 DroidRun 购物助手。")
                self._add_history(session_id, "assistant", reply)
                self.active_tasks.pop(session_id, None)
                return {
                    "products": [],
                    "recommendation": reply,
                    "choices": choices, # 传递选项给前端
                    "show_checkout_option": False
                }
            elif status == "NEED_INFO":
                reply = analysis_res.get("reply", "请提供更多细节。")
                self._add_history(session_id, "assistant", reply)
                self.active_tasks.pop(session_id, None)
                return {
                    "products": [],
                    "recommendation": reply,
                    "choices": choices, # 传递选项给前端
                    "show_checkout_option": False
                }
            elif status == "CONFIRMED":
                # 目标明确！
                final_product_name = analysis_res.get("search_term", product_name)
                print(f"🎯 目标锁定: {final_product_name}")
        
        # 更新 TARGET_PRODUCT 为提炼后的名称 (无论是来自视觉还是文本)
        TARGET_PRODUCT = final_product_name
        # ---------------------------------------------------------------------

        # 【关键】在此处检查设备连接
        # 只有当任务确认为 CONFIRMED 且即将执行时，才需要连接设备。
        try:
            self._check_device_connection()
        except Exception as e:
            print(f"❌ 设备连接检查失败: {e}")
            sys_error_msg = f"设备连接失败: {str(e)}"
            self._add_history(session_id, "assistant", sys_error_msg)
            self.active_tasks.pop(session_id, None)
            return {
                "products": [],
                "recommendation": sys_error_msg,
                "show_checkout_option": False,
                "error": True
            }

        # Configuration
        agent_config = AgentConfig(
            reasoning=False, # Use CodeAct for speed
            codeact=CodeActConfig(vision=True)
        )
        config = DroidrunConfig(
            agent=agent_config,
            tracing=TracingConfig(enabled=False),
            device=DeviceConfig(serial=self.device_serial)
        )

        # Construct Search Query
        # 优先使用智能谈判得到的 final_product_name
        search_keyword = final_product_name
        image_instruction = ""
        if image_path:
            # 如果有图片，我们指示 Agent 关注图片
            # 如果此时 search_keyword 为空（极其罕见），则兜底
            if not search_keyword:
                search_keyword = "图片中的商品"
            image_instruction = f"（请参考上传的图片：{image_path}，虽然你主要通过搜索关键词 '{search_keyword}' 操作）"

        TARGET_PRODUCT = search_keyword
        
        # ---------------------------------------------------------------------
        # 功能关键词识别逻辑 (Feature Keywords Logic) - 强化版
        # ---------------------------------------------------------------------
        task_specific_instruction = ""
        
        # 任务类型：找相似 (Visual Similarity Search)
        if task_type == "找相似":
            task_specific_instruction = f"""
**特别任务指令：【识图找同款】 - 智能视觉溯源模式**
**设计理念**：参考阿里云“图片搜索”技术逻辑，通过**特征重组**与**属性验证**，解决关键词描述偏差，打通“视觉感知 -> 交易决策”闭环。

**当前视觉指纹 (Visual Fingerprint)**：
{features_str if 'features_str' in locals() else "请参考用户上传图片的视觉描述"}

**执行策略 (特征驱动 -> 多维验证)**：

**Step 1: 特征驱动搜索 (Feature-Driven Search)**
- **策略 A (精准)**：优先输入核心关键词 "{final_product_name}" 搜索。
- **策略 B (修正)**：如果前 3 个结果视觉差异巨大（如搜“裙子”出来“裤子”），**立即**提取视觉特征（如“碎花 泡泡袖 黄色”）重组关键词进行二次搜索。
- **动作**：输入 -> 搜索 -> 视觉扫视前 5 个商品。

**Step 2: 视觉与属性双重验证 (Dual Verification)**
- **初筛（视觉层）**：在列表页寻找主色调、轮廓、关键元素（如Logo、印花）高度一致的商品。
- **深挖（属性层）**：
  - 点击进入最相似商品的详情页。
  - **核对参数**：检查标题或参数页，确认材质（如真皮/PU）、风格（如复古/现代）是否与原图特征相符。
  - *阿里云逻辑参考*：不仅要“形似”（Look-alike），更要“神似”（Same Attributes）。

**Step 3: 交易决策闭环 (Decision Making)**
- **价值评估**：在视觉匹配的前提下，优先选择**销量高**且**价格合理**的商品（拒绝溢价严重的同款）。
- **完结**：找到最佳匹配项后，**立即停止**操作。

**输出要求 (必须严格执行)**：
请构造标准 JSON 格式返回，并将【视觉溯源报告】（Markdown 表格）**完整包含**在 `recommendation` 字段中。

**Markdown 表格模板 (填入 JSON 的 recommendation 字段)**：
### 👁️ 视觉溯源报告
| 维度 | 原图特征 | 搜到商品 | 匹配度/备注 |
| :--- | :--- | :--- | :--- |
| **视觉核心** | [如：红色 波点] | [对应特征] | ⭐⭐⭐⭐⭐ |
| **隐性属性** | [如：棉麻质感] | [标题含“棉麻”] | ✅ 属性吻合 |
| **交易价值** | -- | [如：月销1万+] | 🔥 高热度推荐 |

**综合相似度**：95%
**决策建议**：该商品在保持视觉还原的同时，具有[价格/销量]优势，建议入手。
"""

        # 任务类型：比价 (Cross-Platform Price Comparison)
        elif task_type == "比价":
            task_specific_instruction = """
**执行模式：步步为营 (Step-by-Step Reasoning)**
**严禁**一次性生成一堆命令！你必须像人类操作手机一样：**看一眼 -> 想一步 -> 做一步**。

**当前任务目标**：对比京东和淘宝的【{TARGET_PRODUCT}】价格。

**操作原则**：
1. **原子化执行**：每次回复只输出 1-3 个最确定的操作（如 `open_app` 后紧接 `wait`）。不要试图盲猜后续页面。
2. **视觉确认**：在点击任何按钮前，先问自己“我看到它了吗？”。如果页面没加载出来，就输出 `wait(2)`。
3. **异常中断**：一旦发现弹窗（如“停止运行”）、广告或误入无关页面，**立即**处理，不要强行继续主流程。

**决策流程参考 (动态执行)**：
- **阶段一：京东取价**
  - *若不在京东* -> 打开京东 -> 等待首页加载。
  - *若在首页* -> 点击搜索框 -> 输入 "{TARGET_PRODUCT}" -> 点击搜索。
  - *若在搜索结果页* -> **仔细观察**前 3 个商品 -> 排除广告/配件 -> 点击最符合的商品。
  - *若在商品详情页* -> 记录价格、规格、销量 -> 截图确认 -> 按 Home 键离场。

- **阶段二：淘宝比价**
  - *若不在淘宝* -> 打开淘宝 -> 等待。
  - *若在搜索页* -> 输入关键词 -> 搜索。
  - *若在结果页* -> 寻找与京东**同规格**的商品 -> 记录价格。

- **阶段三：结论**
  - 对比数据 -> 输出最终比价结果。

**记住：稳比快更重要。如果卡住了，就停下来思考补救措施。**
"""

        # 任务类型：最优方案 (Best Overall Choice)
        elif task_type == "最优方案":
             task_specific_instruction = """
**特别任务指令：【最优方案】 - 全网跨平台甄选系统**
⚠️ **核心原则**：口碑第一。宁可贵一点，也要质量好。
⚠️ **必须执行跨平台对比**：你必须**先后**访问【京东】和【淘宝】，货比三家。

**评分公式 (Score 0-100)**：
`Score = (口碑得分 * 0.4) + (销量得分 * 0.2) + (价格优势 * 0.2) + (平台服务加成 * 0.2) - (风险扣分)`
*   **口碑权重最高 (40%)**：好评率是核心指标。
*   **平台服务加成**：京东自营 +10分（代表物流快、售后好）。

**执行流程 (严格遵循)**：

**Step 1: 京东优选 (寻找标杆)**
- 打开京东，搜索 "{TARGET_PRODUCT}"。
- 筛选：优先看【京东自营】或【品牌旗舰店】。
- **锁定标杆**：找到 1 个好评率极高的“标杆商品”。
- **动作**：进入详情页 -> 重点看评价标签 -> 记录价格(P1)、销量、好评率 -> 截图 -> 退出。

**Step 2: 淘宝淘金 (寻找挑战者)**
- 打开淘宝，搜索 "{TARGET_PRODUCT}"。
- **精准对齐**：寻找与京东标杆商品**同品牌、同型号**的竞品。
- **筛选挑战者**：找到 1 个价格更有优势且**评分不低于 4.8** 的“挑战者”。
- **动作**：进入详情页 -> 检查是否有“追评/差评” -> 记录价格(P2)、销量、好评率 -> 截图 -> 退出。

**评分细则 (口碑一票否决)**：
- **口碑得分**：好评率 99%+ (100分) | 97%-99% (80分) | 95%-97% (60分)。
- **一票否决**：如果好评率 < 95% 或 评论区首屏出现“假货/质量差” -> **直接淘汰 (Score=0)**。

**Step 3: 巅峰对决 (Final Battle)**
- **计算价差**：`Diff = (京东价 - 淘宝价) / 京东价`
- **决策逻辑**：
  - *若 淘宝挑战者 Score = 0* -> **强制推荐京东** (避雷)。
  - *若价差 < 15%* -> **推荐京东** (买个放心)。
  - *若价差 > 30% 且 淘宝口碑分 >= 80* -> **推荐淘宝** (真香定律)。
  - *其他情况* -> **综合评分决胜** (谁 Score 高选谁)。

**输出要求**：
- 必须明确指出：“推荐【平台】的【商品名】，价格 ¥xx，好评率 xx%。”
- 必须给出理由：“虽然 [另一家] 便宜，但该商品口碑更稳，且 [物流/售后] 更有保障。”
"""

        visual_context = ""
        if image_path:
            visual_context = f"""
**【系统视觉预分析结果】**
系统已对用户上传的图片完成了分析，你**无需**也**严禁**再次分析图片文件。
请直接利用以下信息进行搜索和比对：

- **视觉特征 (Visual Features)**：{features_str}
- **搜索关键词 (Search Keywords)**：{final_product_name}

**指令：**
1. 忽略任何要求你读取 `uploads/image.png` 的旧指令。
2. 直接使用上述 **搜索关键词** 在 APP 中搜索。
3. 在比对商品时，参考上述 **视觉特征**。
"""

        goal = f"""
请作为一名**极速且精准**的电商自动化专家，完成以下跨平台任务。
目标商品: "{TARGET_PRODUCT}"
{visual_context}

-----------------------------------------------------------------------
{task_specific_instruction}
-----------------------------------------------------------------------

**⚠️ 高效执行工具箱 (High-Performance Toolkit)**
1. **对焦验证 (Focus & Verify)**：点击任何商品前，必须停留 1 秒观察标题，确保其包含搜索关键词的 80% 以上。
2. **原子化操作**：`open_app` 后必须 `wait(5)`。`click` 或 `type` 后必须 `wait(2)`。
3. **状态感知**：每次操作后，问自己“页面加载完了吗？”如果不确定，多等 1 秒。
4. **动态滑动**：如果目标不在当前屏幕，使用 `swipe('up')` 寻找，而不是盲目点击。
5. **异常自愈 (Self-Healing)**：
   - **遇到系统弹窗（如“停止运行”、“无响应”）**：**必须**优先点击“关闭应用”或“取消”，然后等待 3 秒让系统恢复，再尝试重新打开。
   - 如果 APP 闪退回桌面：执行 `system_button('home')` 刷新状态 -> 等待 3 秒 -> 重新打开 APP 继续。
   - **坚韧不拔 (Persistence)**：**严禁放弃！** 如果京东/淘宝崩溃，请保持耐心，多试几次。如果连续失败，请尝试先回到桌面 `system_button('home')` 并等待 5 秒再重试。

**可用函数**：
- `open_app(text='...')`
- `click(text='...')` 或 `click(point=(x,y))`
- `type(text='...')`
- `wait(seconds)`
- `swipe(direction='up'|'down')`
- `system_button('home')`

**🚀 任务状态机 (State Machine) - 强化版**：

请根据当前屏幕状态，精准选择下一步（每次只走一步，步步为营）：

**状态 A: 初始/桌面**
- 动作：`open_app('京东')` 或 `open_app('淘宝')`
- 后置：**必须** `wait(5)` 等待冷启动。

**状态 B: 首页/加载中**
- 动作：寻找搜索框 -> 点击。
- 兜底：若无搜索框，先 `wait(2)` 再点 (500, 150)。

**状态 C: 搜索/输入中**
- 动作：`type("{TARGET_PRODUCT}")` -> `click(text='搜索')` -> `wait(3)`

**状态 D: 商品列表页 (核心决策)**
- [比对]: 只有标题匹配度 >90% 才点击。
- [比价]: 优先点“自营/旗舰店”，进入详情页前必须 `wait(2)` 确保列表稳定。
- **动作后必须 `wait(3)` 确保进入详情页**。

**状态 E: 详情页 (精准采集)**
- 动作：等待价格元素出现 -> 采集 标题/价格/规格。
- 分支：
  - 还没比完价？ -> `system_button('home')` -> `wait(2)` -> 转状态 F。
  - 选定冠军？ -> 结束任务。

**状态 F: 切换平台中**
- 动作：`open_app('另一个App')` -> `wait(5)` -> 重复状态 B。

**输出 JSON 格式要求（必须包含在 reason 中）**：
{{
    "products": [
        {{
            "name": "商品标题（完整）",
            "price": 100.0,
            "link": "https://...",
            "platform": "京东/淘宝",
            "score": 90,
            "sales": "1000+",
            "rating": "98%",
            "is_winner": true,
            "reason": "为何选中它？(附带评分细节)"
        }}
    ],
    "recommendation": "详细点评..."
}}

**关于 Link 字段的获取**：
- 优先尝试点击“分享”->“复制链接”。
- 如果失败，留空即可，后端会补全。
"""
        # ---------------------------------------------------------------------

        config.agent.max_steps = 50
        
        agent = DroidAgent(
            goal=goal,
            llms=llm,
            config=config
        )
        
        try:
            print(f"▶️  开始执行任务: {TARGET_PRODUCT}")
            
            # 添加超时保护 (300秒)
            try:
                result = await asyncio.wait_for(agent.run(), timeout=300.0)
            except asyncio.TimeoutError:
                print("⏳ 任务执行超时 (300s)")
                self.active_tasks.pop(session_id, None)
                return {
                    "products": [],
                    "recommendation": "⚠️ 任务执行超时。可能原因：\n1. 网络连接不稳定\n2. ADB 设备响应过慢\n3. 任务过于复杂\n请尝试重试。",
                    "show_checkout_option": False,
                    "error": True
                }

            if result.success:
                print(f"✅ 任务完成: {result.reason}")
                
                # 尝试解析 JSON
                # 通常 Agent 的最后回答包含在 result.reason 中
                # 我们尝试提取 JSON 块
                try:
                    # 寻找最外层的 {}
                    json_str = result.reason
                    # 简单的清洗，去除可能的 markdown ```json ... ```
                    json_str = re.sub(r'^```json\s*', '', json_str, flags=re.MULTILINE)
                    json_str = re.sub(r'^```\s*', '', json_str, flags=re.MULTILINE)
                    json_str = re.sub(r'\s*```$', '', json_str, flags=re.MULTILINE)
                    
                    # 尝试找到第一个 { 和最后一个 }
                    start = json_str.find('{')
                    end = json_str.rfind('}')
                    
                    if start != -1 and end != -1:
                        json_str = json_str[start:end+1]
                        
                        # 尝试解析 JSON 或 Python 字典
                        try:
                            data = json.loads(json_str)
                        except json.JSONDecodeError:
                            # 如果 JSON 解析失败，尝试使用 ast.literal_eval 解析 Python 字典
                            print("⚠️ JSON 解析失败，尝试解析 Python 字典格式...")
                            try:
                                data = ast.literal_eval(json_str)
                            except Exception as e:
                                raise ValueError(f"无法解析数据: {e}")
                        
                        # ---------------------------------------------------------
                        # 格式化商品列表为文本，方便用户复制粘贴
                        # 自动生成真实可点击的搜索链接 (解决 APP 无法直接提取链接的问题)
                        # ---------------------------------------------------------
                        products = data.get("products", [])
                        if products:
                            # 使用纯文本作为标题，避免 emoji 编码问题
                            text_summary = "\n\n📋 **精选商品清单 (已生成直达链接)**\n"
                            text_summary += "----------------------------------------\n"
                            
                            for i, p in enumerate(products, 1):
                                name = p.get('name', '未知商品')
                                price = p.get('price', '未知')
                                platform = p.get('platform', '未知平台')
                                sales = p.get('sales', '未知销量')
                                rating = p.get('rating', '未知评分')
                                
                                # 智能生成真实链接
                                encoded_name = quote(name)
                                real_link = "#"
                                
                                # 优先使用 Agent 提取的链接
                                original_link = p.get('link', '')
                                if original_link and original_link.startswith('http') and len(original_link) > 10:
                                    real_link = original_link
                                else:
                                    if '京东' in platform or 'jd' in platform.lower():
                                        # 移动端适配：使用 m.jd.com 的搜索接口，确保能唤起 APP 搜索页
                                        real_link = f"https://so.m.jd.com/ware/search.action?keyword={encoded_name}"
                                    elif '淘宝' in platform or 'taobao' in platform.lower():
                                        real_link = f"https://s.taobao.com/search?q={encoded_name}"
                                    else:
                                        real_link = f"https://www.baidu.com/s?wd={encoded_name}"

                                # 更新回数据对象，供前端卡片点击
                                p['link'] = real_link

                                is_winner = "🏆 [冠军] " if p.get("is_winner") else ""
                                text_summary += f"{i}. {is_winner}**{name}**\n"
                                text_summary += f"   🏷️ 平台: {platform} | 💰 价格: {price}\n"
                                text_summary += f"   📊 销量: {sales} | ⭐ 好评: {rating}\n"
                                text_summary += f"   🔗 直达链接: {real_link}\n"
                                if p.get("reason"):
                                    text_summary += f"   📝 推荐理由: {p.get('reason')}\n"
                                text_summary += "\n"
                            
                            # 将清单追加到 recommendation 中
                            if "recommendation" in data:
                                data["recommendation"] += text_summary
                            else:
                                data["recommendation"] = text_summary

                        # 将视觉反馈添加到推荐结果的最前面
                        if "recommendation" in data:
                            data["recommendation"] = visual_feedback + data["recommendation"]

                        # 【关键修复】将最终结果存入历史记录，以便多轮对话
                        # 只存储人类可读的建议，不存储 JSON
                        self._add_history(session_id, "assistant", data.get("recommendation", "任务完成"))
                        
                        self.active_tasks.pop(session_id, None)
                        return data
                    else:
                        # 没找到 JSON 块
                        print("⚠️ 未在 result.reason 中找到 JSON 数据")
                        no_json_msg = f"任务执行成功，但未能提取到结构化数据。\nAgent 原始回复: {result.reason}"
                        self._add_history(session_id, "assistant", no_json_msg)
                        self.active_tasks.pop(session_id, None)
                        return {
                            "products": [],
                            "recommendation": no_json_msg,
                            "show_checkout_option": False
                        }

                except Exception as parse_error:
                    print(f"JSON 解析失败: {parse_error}")
                    error_msg = f"任务执行成功，但结果解析失败。Agent 回复: {result.reason[:200]}..."
                    self._add_history(session_id, "assistant", error_msg)
                    
                    self.active_tasks.pop(session_id, None)
                    return {
                        "products": [],
                        "recommendation": error_msg,
                        "show_checkout_option": False
                    }
            else:
                print(f"❌ 任务失败: {result.reason}")
                fail_msg = f"任务执行失败: {result.reason}"
                self._add_history(session_id, "assistant", fail_msg)
                
                self.active_tasks.pop(session_id, None)
                return {
                    "products": [],
                    "recommendation": fail_msg,
                    "show_checkout_option": False,
                    "error": True
                }

        except asyncio.CancelledError:
            print("🛑 任务已被用户手动停止")
            self.active_tasks.pop(session_id, None)
            return {
                "products": [],
                "recommendation": "⚠️ 任务已由用户手动终止。",
                "show_checkout_option": False
            }
                
        except Exception as e:
            print(f"❌ 任务执行异常: {e}")
            import traceback
            traceback.print_exc()
            
            error_msg = f"任务执行遇到问题: {str(e)}"
            
            # 即使出错，也要把视觉反馈带上
            final_msg = visual_feedback + error_msg
            
            self._add_history(session_id, "assistant", final_msg)
            self.active_tasks.pop(session_id, None)
            return {
                "products": [],
                "recommendation": final_msg,
                "show_checkout_option": False,
                "error": True
            }

# 创建全局单例供 app.py 调用
agent_service = AgentService()
