import os
import shutil
import uuid
from fastapi import FastAPI, File, UploadFile, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from agent_service import agent_service  # 引入新创建的业务逻辑模块

# 创建上传目录
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# 模拟 AI Agent 执行逻辑（替换成你的真实逻辑）
def run_agent(model: str, text_query: str, image_path: str = None):
    # TODO: 这里调用你的 DroidrunPortal + AI 流程
    # 示例返回
    return {
        "products": [
            {
                "name": "示例商品 A",
                "price": 89.9,
                "link": "https://example.com/item1",
                "rating": 4.7,
                "sales": 1200
            },
            {
                "name": "示例商品 B（最便宜）",
                "price": 69.9,
                "link": "https://example.com/item2",
                "rating": 4.5,
                "sales": 800
            }
        ],
        "recommendation": "最便宜方案",
        "show_checkout_option": True
    }

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    session_id = request.cookies.get("session_id")
    response = templates.TemplateResponse("index.html", {"request": request})
    if not session_id:
        session_id = str(uuid.uuid4())
        response.set_cookie(key="session_id", value=session_id, httponly=True)
    return response

# app.py 中添加
active_tasks = {}

@app.post("/stop")
async def stop_task(request: Request):
    # 从 Cookie 获取 session_id
    session_id = request.cookies.get("session_id")
    # 如果是多用户，需要通过 session_id 找到对应的 task 并 cancel
    agent_service.stop_task(session_id) 
    return {"status": "stopped"}


@app.post("/search")
async def search(
    request: Request,
    model: str = Form(...),
    product_name: str = Form(""),
    task_type: str = Form(""),
    # 为了兼容旧版或者其他调用，保留可选的 text_query，但优先使用拆分参数拼接
    text_query: str = Form(""), 
    image: UploadFile = File(None)
):
    # 构造最终查询参数，不再进行字符串拼接
    target_product = product_name if product_name else text_query
    
    # Session 管理
    session_id = request.cookies.get("session_id")
    if not session_id:
        session_id = str(uuid.uuid4())
        print(f"为新用户创建 Session: {session_id}")
    
    img_path = None
    if image and image.filename:
        # 安全处理文件名（简单版）
        safe_filename = "".join(c for c in image.filename if c.isalnum() or c in "._-")
        img_path = UPLOAD_DIR / safe_filename
        with open(img_path, "wb") as f:
            shutil.copyfileobj(image.file, f)
        print(f"图片已保存至: {img_path}")

    # 调用真实业务逻辑，传递独立的参数
    result = await agent_service.run(session_id, model, target_product, task_type, str(img_path) if img_path else None)

    # 渲染结果页面（也可以返回 JSON，但这里为了简单直接渲染 HTML）
    response = templates.TemplateResponse("index.html", {
        "request": request,
        "result": result,
        "model": model,
        # 将拆分的参数回传给模板以保持状态
        "product_name": product_name,
        "task_type": task_type,
        "text_query": text_query # 保留完整query供调试或显示
    })
    
    # 确保 Cookie 被设置
    response.set_cookie(key="session_id", value=session_id, httponly=True)
    return response