"""LLM 统一客户端（DashScope OpenAI 兼容接口）。

- chat(): 纯文本对话
- chat_with_image(): 多模态
- 所有 JSON 输出统一提取
"""
import base64
import io
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .config import settings
from .metrics import get_logger, metrics

logger = get_logger("dro.llm")

# 模块级共享连接池：所有 LLMClient 实例复用同一 httpx.Client，避免每次新建 TLS 连接。
# httpx.Client 的连接池按 origin 隔离，多供应商（不同 base_url）也能共享同一 client。
_HTTP_CLIENT = httpx.Client(
    follow_redirects=True,
    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
)

# LLM 惰性单例（线程安全）：graph 内 _get_runtime_llm 复用，避免每节点重建实例
_LLM_SINGLETON: Optional["LLMClient"] = None
_LLM_SINGLETON_LOCK = threading.Lock()


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        default_model: Optional[str] = None,
        timeout: float = 30.0,
    ):
        self.api_key = (api_key or settings.dashscope_api_key or "").strip().strip("'\"“”")
        self.base_url = (base_url or settings.dashscope_base_url).rstrip("/")
        self.default_model = default_model or settings.qwen_model
        self.timeout = timeout
        if not self.api_key:
            raise LLMError("DASHSCOPE_API_KEY 未配置，请检查 .env")

    # ------------------------------------------------------------------
    def chat(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: float = 0.1,
        timeout: Optional[float] = None,
    ) -> str:
        payload = {
            "model": model or self.default_model,
            "messages": messages,
            "temperature": temperature,
        }
        started = time.monotonic()
        model_name = model or self.default_model
        resp = _HTTP_CLIENT.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=timeout or self.timeout,
        )
        if resp.status_code != 200:
            metrics.record_llm(False, (time.monotonic() - started) * 1000, 0, model_name)
            raise LLMError(f"LLM 请求失败 {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"]
            tokens = int((data.get("usage") or {}).get("total_tokens", 0) or 0)
            metrics.record_llm(True, (time.monotonic() - started) * 1000, tokens, model_name)
            return content
        except (KeyError, IndexError) as e:
            metrics.record_llm(False, (time.monotonic() - started) * 1000, 0, model_name)
            raise LLMError(f"LLM 响应格式异常: {data}") from e

    def chat_with_image(
        self,
        image_path: str | Path,
        prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.1,
        timeout: Optional[float] = None,
    ) -> str:
        image_path = Path(image_path)
        if not image_path.exists():
            raise LLMError(f"图片不存在: {image_path}")
        img_bytes, mime = _load_image_bytes(image_path)
        img_b64 = base64.b64encode(img_bytes).decode("utf-8")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_b64}"}},
                ],
            }
        ]
        return self.chat(messages, model=model, temperature=temperature, timeout=timeout)

    def chat_json(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: float = 0.1,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """要求 LLM 输出 JSON，并尽力解析。"""
        content = self.chat(messages, model=model, temperature=temperature, timeout=timeout)
        parsed = extract_json_object(content)
        if parsed is None:
            raise LLMError(f"LLM 未返回合法 JSON: {content[:300]}")
        return parsed

    def embed_texts(
        self,
        texts: List[str],
        model: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> List[List[float]]:
        """批量文本向量化（DashScope OpenAI 兼容 /embeddings 接口）。

        返回与输入等长的向量列表，供长期偏好记忆的向量检索使用。
        """
        if isinstance(texts, str):
            texts = [texts]
        payload = {
            "model": model or settings.embedding_model,
            "input": texts,
        }
        started = time.monotonic()
        resp = _HTTP_CLIENT.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=timeout or self.timeout,
        )
        if resp.status_code != 200:
            metrics.record_llm(False, (time.monotonic() - started) * 1000)
            raise LLMError(f"Embedding 请求失败 {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        try:
            items = sorted(data["data"], key=lambda x: x.get("index", 0))
            tokens = int((data.get("usage") or {}).get("total_tokens", 0) or 0)
            metrics.record_llm(True, (time.monotonic() - started) * 1000, tokens)
            return [it["embedding"] for it in items]
        except (KeyError, IndexError, TypeError) as e:
            metrics.record_llm(False, (time.monotonic() - started) * 1000)
            raise LLMError(f"Embedding 响应格式异常: {data}") from e


def extract_json_object(content: str) -> Optional[Dict[str, Any]]:
    """从 LLM 输出中稳健提取 JSON 对象，容忍 markdown 围栏与前后杂文。"""
    if not content:
        return None
    text = content.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            candidate = text[start : end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                # 尝试修复裸键/单引号（Python dict 风格）
                fixed = candidate.replace("None", "null").replace("True", "true").replace("False", "false")
                try:
                    return json.loads(fixed)
                except json.JSONDecodeError:
                    return None
    return None


def extract_json(content: str) -> Optional[Any]:
    """兼容别名：graph/vision 使用。"""
    return extract_json_object(content)


class AsyncLLMClient(LLMClient):
    """异步版 LLM 客户端：图内使用，避免阻塞事件循环。"""

    async def achat(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: float = 0.1,
        timeout: Optional[float] = None,
    ) -> str:
        import asyncio

        return await asyncio.to_thread(self.chat, messages, model, temperature, timeout)

    async def achat_json(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: float = 0.1,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        content = await self.achat(messages, model=model, temperature=temperature, timeout=timeout)
        parsed = extract_json_object(content)
        if parsed is None:
            raise LLMError(f"LLM 未返回合法 JSON: {content[:300]}")
        return parsed

    async def achat_with_image(
        self,
        image_path: str | Path,
        prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.1,
        timeout: Optional[float] = None,
    ) -> str:
        import asyncio

        return await asyncio.to_thread(self.chat_with_image, image_path, prompt, model, temperature, timeout)


def new_llm() -> LLMClient:
    return LLMClient()


def need_deepseek_model(model: str) -> str:
    """将用户选择映射为具体模型名。"""
    return settings.deepseek_model if "deepseek" in model.lower() else settings.qwen_model


def resolve_model(model: str) -> Optional[str]:
    """把前端选择的模型映射为具体模型名；空/自动 返回 None（用客户端默认）。"""
    if not model or model.strip().lower() in {"", "auto", "自动"}:
        return None
    return need_deepseek_model(model.strip())


def get_llm() -> Optional[LLMClient]:
    """惰性 + 线程安全的 LLM 单例；无 API Key 时返回 None（调用方需兜底）。"""
    global _LLM_SINGLETON
    if _LLM_SINGLETON is not None:
        return _LLM_SINGLETON
    with _LLM_SINGLETON_LOCK:
        if _LLM_SINGLETON is None:
            try:
                _LLM_SINGLETON = LLMClient()
            except (LLMError, ValueError) as e:
                logger.warning(f"LLM 不可用: {e}")
                return None
    return _LLM_SINGLETON


def close_http_client() -> None:
    """进程退出时显式关闭共享连接池。"""
    _HTTP_CLIENT.close()


def _load_image_bytes(image_path: Path) -> Tuple[bytes, str]:
    """读取图片字节；若 Pillow 可用则降采样（最长边 1024 + JPEG），减小 base64 体积与视觉 token。

    返回 (bytes, mime)。Pillow 缺失或解码异常时回退原图字节。
    """
    raw = image_path.read_bytes()
    try:
        from PIL import Image  # 惰性导入：未安装则跳过降采样

        with Image.open(io.BytesIO(raw)) as im:
            im = im.convert("RGB")
            w, h = im.size
            scale = 1024 / max(w, h)
            if scale < 1.0:
                im = im.resize((int(w * scale), int(h * scale)))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=85)
            return buf.getvalue(), "image/jpeg"
    except Exception:  # noqa: BLE001 —— 降采样失败回退原图
        pass
    return raw, "image/png"