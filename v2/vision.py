"""视觉分析服务：截图 -> Qwen-VL 多模态识别屏幕内容 / 弹窗检测 / 商品信息提取。

所有 LLM 调用走 DashScope OpenAI 兼容接口，统一在 LLM 层处理。
"""
import base64
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import settings
from .llm import LLMClient


class VisionService:
    """多模态屏幕理解服务。"""

    def __init__(self, llm: Optional[LLMClient] = None):
        self._llm = llm

    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = LLMClient(settings.dashscope_api_key, settings.dashscope_base_url, settings.qwen_model)
        return self._llm

    def analyze_image(self, image_path: str | Path, prompt: str) -> Dict[str, Any]:
        """用 VL 分析一张图片，返回结构化 JSON dict。"""
        try:
            content = self.llm.chat_with_image(image_path, prompt)
        except Exception as e:
            return {"error": f"视觉分析失败: {e}"}
        return _parse_json(content) or {"raw": content[:500]}

    def extract_product_info(self, image_path: str | Path) -> Dict[str, Any]:
        """从商品截图提取 标题/价格/销量/店铺。"""
        prompt = """
分析这张电商 APP 截图。
- 如果当前是搜索结果列表：提取前 3 个商品的标题、价格、销量、店铺名。
- 如果当前是商品详情页：提取标题、价格、销量、好评数/好评率、店铺名。
输出严格 JSON：
{
  "screen_type": "search_list" | "detail" | "other",
  "products": [
    {"title": "...", "price": 0.0, "sales": "...", "shop": "..."}
  ]
}
"""
        return self.analyze_image(image_path, prompt)

    def detect_popup(self, image_path: str | Path) -> Dict[str, Any]:
        """判断屏幕上是否有弹窗/广告，以及可点的关闭按钮。"""
        prompt = """
判断这张手机截图中是否存在弹窗、广告遮罩、升级弹窗、优惠券弹窗等遮挡内容。
输出严格 JSON：
{
  "has_popup": true/false,
  "popup_type": "coupon" | "ad" | "update" | "system_dialog" | "" ,
  "close_button": {"text": "关闭", "estimate_position": {"x": 0-100, "y": 0-100}}
}
如果没有弹窗，has_popup 为 false，close_button 为空对象。
"""
        return self.analyze_image(image_path, prompt)

    def find_element_position(self, image_path: str | Path, target_text: str) -> Dict[str, Any]:
        """在截图中定位目标元素（如搜索框），返回相对坐标百分比。"""
        prompt = f"""
在这张手机截图中定位目标元素: "{target_text}"。
输出严格 JSON：
{{"found": true/false, "position": {{"x": 0-100, "y": 0-100}}, "confidence": 0.0-1.0}}
"""
        return self.analyze_image(image_path, prompt)


def _parse_json(content: str) -> Optional[Dict[str, Any]]:
    """从 LLM 输出中提取 JSON 对象（兼容 markdown 代码块/多余文本）。"""
    if not content:
        return None
    content = re.sub(r"^```json\s*", "", content.strip(), flags=re.MULTILINE)
    content = re.sub(r"^```\s*", "", content, flags=re.MULTILINE)
    content = re.sub(r"\s*```$", "", content, flags=re.MULTILINE)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(content[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None


vision = VisionService()