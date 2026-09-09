"""LLM 统一客户端（DashScope OpenAI 兼容接口）。

- chat(): 纯文本对话
- chat_with_image(): 多模态
- 所有 JSON 输出统一提取
"""
import base64
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from .config import settings


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
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=timeout or self.timeout,
        )
        if resp.status_code != 200:
            raise LLMError(f"LLM 请求失败 {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
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
        img_b64 = base64.b64encode(image_path.read_bytes()).decode("utf-8")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
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
        resp = httpx.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=timeout or self.timeout,
        )
        if resp.status_code != 200:
            raise LLMError(f"Embedding 请求失败 {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        try:
            items = sorted(data["data"], key=lambda x: x.get("index", 0))
            return [it["embedding"] for it in items]
        except (KeyError, IndexError, TypeError) as e:
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