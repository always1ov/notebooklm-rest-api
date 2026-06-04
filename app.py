# Google NotebookLM REST API wrapper
# Namhyeon Go <gnh1201@catswords.re.kr>
# https://github.com/gnh1201/notebooklm-rest-api
import asyncio
import os
import uuid
import tempfile
from typing import Any, Optional, Literal, Dict

import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from playwright.async_api import async_playwright

from notebooklm import NotebookLMClient, RPCError  # notebooklm-py :contentReference[oaicite:2]{index=2}


# ----------------------------
# Default prompts per artifact type (Chinese output)
# ----------------------------
DEFAULT_ARTIFACT_PROMPTS: Dict[str, Dict[str, Any]] = {
    "slide_deck": {
        "instructions": (
            "请用中文生成幻灯片。要求："
            "1. 标题简洁有力，每页聚焦一个核心主题；"
            "2. 每页要点不超过5条，语言精炼；"
            "3. 结构清晰（背景→核心内容→结论/行动建议）；"
            "4. 关键数据和结论需有来源支撑；"
            "5. 风格专业，适合商务演示。"
        ),
    },
    "infographic": {
        "instructions": (
            "请用中文生成信息图。要求："
            "1. 视觉层次分明，重点信息突出；"
            "2. 每个要点文字不超过15字，简洁直观；"
            "3. 关键数据用图表或数字展示；"
            "4. 逻辑流程清晰，从问题到结论；"
            "5. 适合社交媒体或汇报分享。"
        ),
    },
    "report": {
        "instructions": (
            "请用中文生成报告。要求："
            "1. 结构完整：摘要→背景→分析→结论→建议；"
            "2. 语言专业准确，避免模糊表述；"
            "3. 所有核心观点需有源材料支撑；"
            "4. 结论明确，建议可落地执行；"
            "5. 篇幅适中，重点突出。"
        ),
    },
    "audio": {
        "instructions": (
            "请用中文生成音频脚本。要求："
            "1. 语言自然流畅，符合口语习惯；"
            "2. 开场30秒内吸引听众注意；"
            "3. 内容有逻辑递进，易于理解；"
            "4. 重要概念给出通俗解释；"
            "5. 结尾有清晰总结和行动建议。"
        ),
    },
    "quiz": {
        "instructions": (
            "请用中文生成测验题目。要求："
            "1. 覆盖源材料的核心知识点；"
            "2. 题目表述清晰无歧义；"
            "3. 选项设计合理，干扰项有逻辑；"
            "4. 每题附带简短答案解析；"
            "5. 难度适中，兼顾理解与记忆。"
        ),
    },
    "flashcards": {
        "instructions": (
            "请用中文生成闪卡。要求："
            "1. 问题简洁，聚焦单一知识点；"
            "2. 答案精准简短，不超过50字；"
            "3. 覆盖源材料所有核心概念；"
            "4. 适合快速复习和记忆强化；"
            "5. 难易穿插排列。"
        ),
    },
    "mind_map": {
        "instructions": (
            "请用中文生成思维导图。要求："
            "1. 中心主题明确，一句话概括；"
            "2. 一级分支3-6个，覆盖核心维度；"
            "3. 关键词精炼，每节点不超过8字；"
            "4. 层次不超过3级，避免过度复杂；"
            "5. 分支间逻辑关系清晰。"
        ),
    },
    "data_table": {
        "instructions": (
            "请用中文生成数据表格。要求："
            "1. 列标题清晰，准确反映数据含义；"
            "2. 数据准确，来源可追溯；"
            "3. 按重要性或时间排序；"
            "4. 数值统一单位；"
            "5. 包含汇总行或关键统计。"
        ),
    },
    "video": {
        "instructions": (
            "请用中文生成视频脚本。要求："
            "1. 开场直击主题，前10秒抓住注意力；"
            "2. 内容分段清晰，每段一个核心要点；"
            "3. 语言生动，适合视频呈现；"
            "4. 包含画面描述建议；"
            "5. 结尾有明确总结和引导。"
        ),
    },
}

DEFAULT_CHAT_PREFIX = "请用中文回答。"

DEFAULT_TRANSCRIBE_PROMPT = (
    "请将这段录音逐字转录为中文简体，"
    "不得使用繁体字，"
    "不得总结或改写，"
    "保持原始语序，直接输出全文。"
)


def _get_artifact_opts(artifact_type: str) -> Dict[str, Any]:
    env_key = f"PROMPT_{artifact_type.upper()}"
    custom_prompt = os.environ.get(env_key, "").strip()
    if custom_prompt:
        return {"instructions": custom_prompt}
    return DEFAULT_ARTIFACT_PROMPTS.get(artifact_type, {})


def _get_output_format(artifact_type: str, user_format: Optional[str]) -> str:
    if user_format:
        return user_format
    return os.environ.get(f"OUTPUT_FORMAT_{artifact_type.upper()}", "json")


def _get_chat_prefix() -> str:
    return os.environ.get("CHAT_LANGUAGE_PREFIX", DEFAULT_CHAT_PREFIX)


# ----------------------------
# Config / Security
# ----------------------------
API_KEY = os.environ.get("NOTEBOOKLM_REST_API_KEY", "")
AUTH_STORAGE_PATH = os.environ.get("NOTEBOOKLM_STORAGE_PATH")
WATCH_FOLDER = os.environ.get("WATCH_FOLDER", "/uploads")
TRANSCRIPTIONS_FOLDER = os.environ.get("TRANSCRIPTIONS_FOLDER", "/transcriptions")
DOWNSTREAM_WEBHOOK_URL = os.environ.get("DOWNSTREAM_WEBHOOK_URL", "")
NOTIFY_WEBHOOK_URL = os.environ.get("NOTIFY_WEBHOOK_URL", "")
# Comma-separated host paths mounted into the container to watch for new audio files.
WATCH_PATHS: list[str] = [p.strip() for p in os.environ.get("WATCH_PATHS", "").split(",") if p.strip()]
WATCH_POLL_SECONDS = int(os.environ.get("WATCH_POLL_SECONDS", "30"))
WATCH_MIN_AGE_SECONDS = int(os.environ.get("WATCH_MIN_AGE_SECONDS", "60"))
AUDIO_EXTENSIONS = {".mp3"}
# Single-worker queue — files are processed strictly one at a time in order
_transcribe_queue: asyncio.Queue = asyncio.Queue()
# In-memory set of files currently queued or being processed (prevents duplicate queuing)
_in_flight: set[str] = set()


async def _notify(event: str, message: str, detail: str = "") -> None:
    if not NOTIFY_WEBHOOK_URL:
        return
    payload = {"event": event, "message": message, "detail": detail}
    try:
        async with httpx.AsyncClient() as http:
            await http.post(NOTIFY_WEBHOOK_URL, json=payload, timeout=10)
    except Exception as e:
        print(f"Notify webhook failed: {e}")


def require_api_key(x_api_key: Optional[str] = None):
    # Minimal API-key gate. Put this behind a real gateway (Cloudflare, Nginx, etc.) for production.
    if API_KEY:
        # FastAPI header parsing without extra imports (keep simple):
        # Prefer: from fastapi import Header; def require_api_key(x_api_key: str = Header(None)) ...
        # but we keep it minimal and rely on query param fallback too.
        if x_api_key != API_KEY:
            raise HTTPException(status_code=401, detail="Invalid API key")


async def get_client() -> NotebookLMClient:
    """
    Creates a client using notebooklm-py's supported auth precedence:
    - explicit path to from_storage()
    - NOTEBOOKLM_AUTH_JSON
    - NOTEBOOKLM_HOME/storage_state.json
    - ~/.notebooklm/storage_state.json
    :contentReference[oaicite:3]{index=3}
    """
    try:
        if AUTH_STORAGE_PATH:
            return await NotebookLMClient.from_storage(AUTH_STORAGE_PATH)
        return await NotebookLMClient.from_storage()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to initialize NotebookLM client: {e}")


def map_rpc_error(e: RPCError) -> HTTPException:
    msg = str(e)
    if "401" in msg or "403" in msg or "auth" in msg.lower():
        asyncio.create_task(_notify("session_expired", "NotebookLM 登录已过期，请重新登录", msg))
        return HTTPException(status_code=401, detail=msg)
    if "rate" in msg.lower() or "429" in msg:
        return HTTPException(status_code=502, detail=msg)
    return HTTPException(status_code=502, detail=msg)


# ----------------------------
# Models
# ----------------------------
class NotebookCreateReq(BaseModel):
    title: str


class NotebookRenameReq(BaseModel):
    new_title: str


class SourceAddUrlReq(BaseModel):
    url: str
    wait: bool = True


class SourceAddTextReq(BaseModel):
    title: str
    content: str


class SourceAddYoutubeReq(BaseModel):
    url: str
    wait: bool = True


class ChatAskReq(BaseModel):
    question: str
    # optional persona fields could be added if you want


class ArtifactGenerateReq(BaseModel):
    # A simple unified generator:
    # audio/video/report/quiz/flashcards/slide_deck/infographic/data_table/mind_map
    type: Literal[
        "audio",
        "video",
        "report",
        "quiz",
        "flashcards",
        "slide_deck",
        "infographic",
        "data_table",
        "mind_map",
    ]
    # Options are passed through as-is to the underlying generate_* calls where applicable.
    # (The library supports many per-type options; keep this generic.)
    options: Dict[str, Any] = {}


class TaskPollResp(BaseModel):
    ok: bool
    status: Any


class TranscribeWebhookReq(BaseModel):
    filepath: Optional[str] = None          # full path inside container
    filename: Optional[str] = None          # filename only — looked up in WATCH_FOLDER
    prompt: Optional[str] = None
    downstream_webhook_url: Optional[str] = None  # overrides DOWNSTREAM_WEBHOOK_URL env var


# ----------------------------
# Session auto-refresh background task
# ----------------------------
SESSION_REFRESH_SECONDS = int(os.environ.get("SESSION_REFRESH_HOURS", "2")) * 3600


async def _do_session_refresh() -> tuple[bool, str]:
    """Refresh the Playwright session then verify it with a real API call.
    Returns (success, message)."""
    storage_path = AUTH_STORAGE_PATH or os.path.expanduser("~/.notebooklm/storage_state.json")
    if not os.path.exists(storage_path):
        msg = f"storage_state.json not found: {storage_path}"
        print(f"Session refresh skipped: {msg}")
        await _notify("session_file_missing", "storage_state.json 不存在，请重新登录", storage_path)
        return False, msg

    # Step 1: Playwright page visit — renews Google cookies
    proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
    proxy = {"server": proxy_url} if proxy_url else None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, proxy=proxy)
            ctx = await browser.new_context(storage_state=storage_path)
            page = await ctx.new_page()
            await page.goto(
                "https://notebooklm.google.com/",
                wait_until="networkidle",
                timeout=60000,
            )
            await ctx.storage_state(path=storage_path)
            await browser.close()
        print("Session page visit succeeded, verifying with API call...")
    except Exception as e:
        msg = str(e)
        print(f"Session refresh (page visit) failed: {msg}")
        await _notify("session_refresh_failed", "NotebookLM 登录刷新失败（页面访问错误），请手动重新登录", msg)
        return False, msg

    # Step 2: Verify the refreshed session actually works
    try:
        client = await NotebookLMClient.from_storage(storage_path)
        async with client:
            await client.notebooks.list()
        print("Session verified successfully.")
        return True, "ok"
    except Exception as e:
        msg = str(e)
        print(f"Session refresh (verification) failed: {msg}")
        await _notify(
            "session_expired",
            "NotebookLM 登录已过期，页面刷新成功但 API 验证失败，请重新登录",
            msg,
        )
        return False, msg


async def _session_refresh_loop():
    await asyncio.sleep(30)
    while True:
        await _do_session_refresh()
        await asyncio.sleep(SESSION_REFRESH_SECONDS)


# ----------------------------
# Audio file watcher background task
# ----------------------------
def _scan_audio_files(paths: list[str]) -> set[str]:
    found: set[str] = set()
    for base in paths:
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            for f in files:
                if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS:
                    found.add(os.path.join(root, f))
    return found


def _is_file_stable(path: str) -> bool:
    """Returns True only if the file hasn't been modified for WATCH_MIN_AGE_SECONDS.
    Prevents processing partially-written files that are still being converted."""
    try:
        return (time.time() - os.path.getmtime(path)) >= WATCH_MIN_AGE_SECONDS
    except OSError:
        return False


async def _audio_watch_loop():
    if not WATCH_PATHS:
        return
    await asyncio.sleep(10)  # let app finish startup
    os.makedirs(TRANSCRIPTIONS_FOLDER, exist_ok=True)
    print(f"Audio watcher: started, watching {WATCH_PATHS}")

    while True:
        await asyncio.sleep(WATCH_POLL_SECONDS)
        for path in sorted(_scan_audio_files(WATCH_PATHS)):
            if path in _in_flight:
                continue  # already queued or processing
            if not _is_file_stable(path):
                print(f"Audio watcher: skipped (still writing) → {path}")
                continue
            print(f"Audio watcher: queued → {path}")
            _in_flight.add(path)
            await _transcribe_queue.put((path, None, DOWNSTREAM_WEBHOOK_URL))


async def _transcribe_worker():
    """Single worker — pulls from the queue and processes one file at a time."""
    while True:
        audio_path, prompt, downstream_url = await _transcribe_queue.get()
        try:
            await _transcribe_and_notify(audio_path, prompt, downstream_url)
        finally:
            _in_flight.discard(audio_path)
            _transcribe_queue.task_done()


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [
        asyncio.create_task(_session_refresh_loop()),
        asyncio.create_task(_transcribe_worker()),
    ]
    if WATCH_PATHS:
        tasks.append(asyncio.create_task(_audio_watch_loop()))
    yield
    for t in tasks:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass


# ----------------------------
# App
# ----------------------------
app = FastAPI(title="NotebookLM REST API (powered by notebooklm-py)", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
async def docs_ui():
    base = ""  # relative, works behind any domain
    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NotebookLM REST API</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0f1117;color:#e2e8f0;line-height:1.6}
header{background:#1a1d2e;border-bottom:1px solid #2d3148;padding:20px 32px;display:flex;align-items:center;gap:12px}
header h1{font-size:1.3rem;font-weight:600;color:#a78bfa}
header span{font-size:.8rem;color:#64748b;margin-left:auto}
.container{max-width:900px;margin:0 auto;padding:32px 24px}
.section{margin-bottom:40px}
.section-title{font-size:.7rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#64748b;margin-bottom:16px;padding-bottom:8px;border-bottom:1px solid #1e2130}
.card{background:#1a1d2e;border:1px solid #2d3148;border-radius:8px;margin-bottom:12px;overflow:hidden}
.card-header{display:flex;align-items:center;gap:10px;padding:12px 16px;cursor:pointer;user-select:none}
.card-header:hover{background:#1e2235}
.method{font-size:.7rem;font-weight:700;padding:2px 8px;border-radius:4px;min-width:52px;text-align:center}
.get{background:#064e3b;color:#34d399}.post{background:#1e3a5f;color:#60a5fa}
.delete{background:#4c1d1d;color:#f87171}.patch{background:#3b2a00;color:#fbbf24}
.path{font-family:'Cascadia Code','Fira Code',monospace;font-size:.9rem;color:#e2e8f0}
.desc{font-size:.82rem;color:#94a3b8;margin-left:auto}
.card-body{padding:16px;border-top:1px solid #2d3148;display:none}
.card-body.open{display:block}
.card-body p{font-size:.85rem;color:#94a3b8;margin-bottom:12px}
pre{background:#0f1117;border:1px solid #2d3148;border-radius:6px;padding:12px;font-size:.8rem;overflow-x:auto;position:relative}
code{font-family:'Cascadia Code','Fira Code',monospace;color:#a5f3fc}
.copy-btn{position:absolute;top:8px;right:8px;background:#2d3148;border:none;color:#94a3b8;padding:3px 8px;border-radius:4px;cursor:pointer;font-size:.72rem}
.copy-btn:hover{background:#3d4268;color:#e2e8f0}
.tag{display:inline-block;font-size:.7rem;padding:1px 6px;border-radius:3px;margin-right:4px;background:#1e2235;color:#94a3b8;border:1px solid #2d3148}
.status{display:flex;align-items:center;gap:8px;font-size:.82rem;padding:10px 16px;background:#0f1117;border-top:1px solid #2d3148}
#status-dot{width:8px;height:8px;border-radius:50%;background:#64748b}
#status-dot.ok{background:#34d399}#status-dot.err{background:#f87171}
</style>
</head>
<body>
<header>
  <h1>NotebookLM REST API</h1>
  <div class="status" style="margin-left:auto;background:transparent;padding:0">
    <div id="status-dot"></div>
    <span id="status-text" style="font-size:.8rem;color:#64748b">检查中...</span>
  </div>
</header>
<div class="container">

<div class="section">
<div class="section-title">系统</div>
<div class="card">
  <div class="card-header" onclick="toggle(this)">
    <span class="method get">GET</span><span class="path">/health</span>
    <span class="desc">健康检查</span>
  </div>
  <div class="card-body">
    <pre><code>curl https://notebooklm.always1ov.com/health</code><button class="copy-btn" onclick="copy(this)">复制</button></pre>
  </div>
</div>
<div class="card">
  <div class="card-header" onclick="toggle(this)">
    <span class="method post">POST</span><span class="path">/v1/scan</span>
    <span class="desc">立即扫描并入队所有待转录音频</span>
  </div>
  <div class="card-body">
    <p>扫描 WATCH_PATHS 目录，把所有稳定的 mp3 文件立即加入转录队列，不用等下次轮询。</p>
    <pre><code>curl -X POST https://notebooklm.always1ov.com/v1/scan</code><button class="copy-btn" onclick="copy(this)">复制</button></pre>
  </div>
</div>
<div class="card">
  <div class="card-header" onclick="toggle(this)">
    <span class="method post">POST</span><span class="path">/v1/refresh-session</span>
    <span class="desc">立即刷新 Google 登录 session（不等定时器）</span>
  </div>
  <div class="card-body">
    <p>遇到登录过期时使用。上传新的 storage_state.json 后，调用此接口让服务立即重新读取并刷新 session，无需重启容器。</p>
    <pre><code>curl -X POST https://notebooklm.always1ov.com/v1/refresh-session</code><button class="copy-btn" onclick="copy(this)">复制</button></pre>
  </div>
</div>
</div>

<div class="section">
<div class="section-title">笔记本</div>
<div class="card">
  <div class="card-header" onclick="toggle(this)">
    <span class="method get">GET</span><span class="path">/v1/notebooks</span>
    <span class="desc">列出所有笔记本（同时验证登录状态）</span>
  </div>
  <div class="card-body">
    <pre><code>curl https://notebooklm.always1ov.com/v1/notebooks</code><button class="copy-btn" onclick="copy(this)">复制</button></pre>
  </div>
</div>
<div class="card">
  <div class="card-header" onclick="toggle(this)">
    <span class="method post">POST</span><span class="path">/v1/notebooks</span>
    <span class="desc">创建笔记本</span>
  </div>
  <div class="card-body">
    <pre><code>curl -X POST https://notebooklm.always1ov.com/v1/notebooks \\
  -H "Content-Type: application/json" \\
  -d '{"title":"我的笔记本"}'</code><button class="copy-btn" onclick="copy(this)">复制</button></pre>
  </div>
</div>
<div class="card">
  <div class="card-header" onclick="toggle(this)">
    <span class="method delete">DELETE</span><span class="path">/v1/notebooks/{id}</span>
    <span class="desc">删除笔记本</span>
  </div>
  <div class="card-body">
    <pre><code>curl -X DELETE https://notebooklm.always1ov.com/v1/notebooks/{notebook_id}</code><button class="copy-btn" onclick="copy(this)">复制</button></pre>
  </div>
</div>
</div>

<div class="section">
<div class="section-title">来源</div>
<div class="card">
  <div class="card-header" onclick="toggle(this)">
    <span class="method post">POST</span><span class="path">/v1/notebooks/{id}/sources/text</span>
    <span class="desc">添加文字来源</span>
  </div>
  <div class="card-body">
    <pre><code>curl -X POST https://notebooklm.always1ov.com/v1/notebooks/{id}/sources/text \\
  -H "Content-Type: application/json" \\
  -d '{"title":"标题","content":"内容..."}'</code><button class="copy-btn" onclick="copy(this)">复制</button></pre>
  </div>
</div>
<div class="card">
  <div class="card-header" onclick="toggle(this)">
    <span class="method post">POST</span><span class="path">/v1/notebooks/{id}/sources/url</span>
    <span class="desc">添加网页来源</span>
  </div>
  <div class="card-body">
    <pre><code>curl -X POST https://notebooklm.always1ov.com/v1/notebooks/{id}/sources/url \\
  -H "Content-Type: application/json" \\
  -d '{"url":"https://example.com","wait":true}'</code><button class="copy-btn" onclick="copy(this)">复制</button></pre>
  </div>
</div>
<div class="card">
  <div class="card-header" onclick="toggle(this)">
    <span class="method post">POST</span><span class="path">/v1/notebooks/{id}/sources/file</span>
    <span class="desc">上传文件来源</span>
  </div>
  <div class="card-body">
    <pre><code>curl -X POST https://notebooklm.always1ov.com/v1/notebooks/{id}/sources/file \\
  -F "upload=@/path/to/file.pdf"</code><button class="copy-btn" onclick="copy(this)">复制</button></pre>
  </div>
</div>
</div>

<div class="section">
<div class="section-title">对话</div>
<div class="card">
  <div class="card-header" onclick="toggle(this)">
    <span class="method post">POST</span><span class="path">/v1/notebooks/{id}/chat/ask</span>
    <span class="desc">对笔记本内容提问（自动中文回答）</span>
  </div>
  <div class="card-body">
    <pre><code>curl -X POST https://notebooklm.always1ov.com/v1/notebooks/{id}/chat/ask \\
  -H "Content-Type: application/json" \\
  -d '{"question":"这段内容讲了什么？"}'</code><button class="copy-btn" onclick="copy(this)">复制</button></pre>
  </div>
</div>
</div>

<div class="section">
<div class="section-title">转录</div>
<div class="card">
  <div class="card-header" onclick="toggle(this)">
    <span class="method post">POST</span><span class="path">/v1/transcribe</span>
    <span class="desc">上传音频 → 一步获取中文简体转录文字</span>
  </div>
  <div class="card-body">
    <p>上传音频文件，自动创建临时笔记本，等待 30 秒索引后转录，完成后删除笔记本。</p>
    <pre><code>curl -X POST https://notebooklm.always1ov.com/v1/transcribe \\
  -F "upload=@recording.mp3"</code><button class="copy-btn" onclick="copy(this)">复制</button></pre>
    <p style="margin-top:12px">可选参数：</p>
    <pre><code>-F "source_wait_seconds=60"   # 等待时间（默认30秒）
-F "prompt=自定义转录提示词"
-F "keep_notebook=true"       # 保留笔记本不删除</code><button class="copy-btn" onclick="copy(this)">复制</button></pre>
  </div>
</div>
<div class="card">
  <div class="card-header" onclick="toggle(this)">
    <span class="method post">POST</span><span class="path">/v1/webhook/transcribe</span>
    <span class="desc">手动入队指定文件转录</span>
  </div>
  <div class="card-body">
    <p>将 WATCH_PATHS 中的指定文件立即加入转录队列。</p>
    <pre><code>curl -X POST https://notebooklm.always1ov.com/v1/webhook/transcribe \\
  -H "Content-Type: application/json" \\
  -d '{"filename":"recording.mp3"}'</code><button class="copy-btn" onclick="copy(this)">复制</button></pre>
  </div>
</div>
</div>

<div class="section">
<div class="section-title">生成内容</div>
<div class="card">
  <div class="card-header" onclick="toggle(this)">
    <span class="method post">POST</span><span class="path">/v1/notebooks/{id}/artifacts/generate</span>
    <span class="desc">生成内容（报告/思维导图/测验等）</span>
  </div>
  <div class="card-body">
    <p>type 可选：<span class="tag">report</span><span class="tag">mind_map</span><span class="tag">quiz</span><span class="tag">flashcards</span><span class="tag">slide_deck</span><span class="tag">infographic</span><span class="tag">data_table</span><span class="tag">audio</span><span class="tag">video</span></p>
    <pre><code>curl -X POST https://notebooklm.always1ov.com/v1/notebooks/{id}/artifacts/generate \\
  -H "Content-Type: application/json" \\
  -d '{"type":"report","options":{}}'</code><button class="copy-btn" onclick="copy(this)">复制</button></pre>
  </div>
</div>
<div class="card">
  <div class="card-header" onclick="toggle(this)">
    <span class="method get">GET</span><span class="path">/v1/notebooks/{id}/artifacts/download</span>
    <span class="desc">下载生成的内容文件</span>
  </div>
  <div class="card-body">
    <pre><code>curl "https://notebooklm.always1ov.com/v1/notebooks/{id}/artifacts/download?type=report" \\
  -o report.md</code><button class="copy-btn" onclick="copy(this)">复制</button></pre>
  </div>
</div>
</div>

</div>
<script>
function toggle(el){el.nextElementSibling.classList.toggle('open')}
function copy(btn){
  const code=btn.previousElementSibling||btn.parentElement.querySelector('code');
  navigator.clipboard.writeText(code.innerText).then(()=>{
    btn.textContent='已复制';setTimeout(()=>btn.textContent='复制',1500)
  })
}
fetch('/health').then(r=>r.json()).then(d=>{
  document.getElementById('status-dot').className='ok';
  document.getElementById('status-text').textContent='服务正常';
}).catch(()=>{
  document.getElementById('status-dot').className='err';
  document.getElementById('status-text').textContent='无法连接';
})
</script>
</body>
</html>"""
    return HTMLResponse(html)


@app.post("/v1/scan")
async def manual_scan():
    """Immediately scan WATCH_PATHS and queue all stable mp3 files not already in flight."""
    if not WATCH_PATHS:
        return {"ok": False, "detail": "WATCH_PATHS not configured"}
    queued = []
    skipped_in_flight = []
    skipped_unstable = []
    for path in sorted(_scan_audio_files(WATCH_PATHS)):
        if path in _in_flight:
            skipped_in_flight.append(path)
        elif not _is_file_stable(path):
            skipped_unstable.append(path)
        else:
            _in_flight.add(path)
            await _transcribe_queue.put((path, None, DOWNSTREAM_WEBHOOK_URL))
            queued.append(path)
    return {
        "ok": True,
        "queued": queued,
        "skipped_in_flight": skipped_in_flight,
        "skipped_unstable": skipped_unstable,
        "queue_size": _transcribe_queue.qsize(),
    }


@app.post("/v1/refresh-session")
async def manual_refresh_session():
    """Immediately trigger a Playwright session refresh without waiting for the scheduled interval."""
    ok, msg = await _do_session_refresh()
    return {"ok": ok, "detail": msg}


# ----------------------------
# Notebooks
# ----------------------------
@app.get("/v1/notebooks")
async def list_notebooks():
    client = await get_client()
    async with client:
        try:
            nbs = await client.notebooks.list()
            return {"ok": True, "items": [nb.model_dump() if hasattr(nb, "model_dump") else nb.__dict__ for nb in nbs]}
        except RPCError as e:
            raise map_rpc_error(e)


@app.post("/v1/notebooks")
async def create_notebook(req: NotebookCreateReq):
    client = await get_client()
    async with client:
        try:
            nb = await client.notebooks.create(req.title)
            return {"ok": True, "notebook": nb.model_dump() if hasattr(nb, "model_dump") else nb.__dict__}
        except RPCError as e:
            raise map_rpc_error(e)


@app.get("/v1/notebooks/{notebook_id}")
async def get_notebook(notebook_id: str):
    client = await get_client()
    async with client:
        try:
            nb = await client.notebooks.get(notebook_id)
            return {"ok": True, "notebook": nb.model_dump() if hasattr(nb, "model_dump") else nb.__dict__}
        except RPCError as e:
            raise map_rpc_error(e)


@app.delete("/v1/notebooks/{notebook_id}")
async def delete_notebook(notebook_id: str):
    client = await get_client()
    async with client:
        try:
            ok = await client.notebooks.delete(notebook_id)
            return {"ok": True, "deleted": bool(ok)}
        except RPCError as e:
            raise map_rpc_error(e)


@app.patch("/v1/notebooks/{notebook_id}/rename")
async def rename_notebook(notebook_id: str, req: NotebookRenameReq):
    client = await get_client()
    async with client:
        try:
            nb = await client.notebooks.rename(notebook_id, req.new_title)
            return {"ok": True, "notebook": nb.model_dump() if hasattr(nb, "model_dump") else nb.__dict__}
        except RPCError as e:
            raise map_rpc_error(e)


@app.get("/v1/notebooks/{notebook_id}/summary")
async def get_notebook_summary(notebook_id: str):
    client = await get_client()
    async with client:
        try:
            summary = await client.notebooks.get_summary(notebook_id)
            return {"ok": True, "summary": summary}
        except RPCError as e:
            raise map_rpc_error(e)


@app.get("/v1/notebooks/{notebook_id}/description")
async def get_notebook_description(notebook_id: str):
    client = await get_client()
    async with client:
        try:
            desc = await client.notebooks.get_description(notebook_id)
            return {"ok": True, "description": desc.model_dump() if hasattr(desc, "model_dump") else desc.__dict__}
        except RPCError as e:
            raise map_rpc_error(e)


# ----------------------------
# Sources
# ----------------------------
@app.get("/v1/notebooks/{notebook_id}/sources")
async def list_sources(notebook_id: str):
    client = await get_client()
    async with client:
        try:
            items = await client.sources.list(notebook_id)
            return {"ok": True, "items": [s.model_dump() if hasattr(s, "model_dump") else s.__dict__ for s in items]}
        except RPCError as e:
            raise map_rpc_error(e)


@app.post("/v1/notebooks/{notebook_id}/sources/url")
async def add_source_url(notebook_id: str, req: SourceAddUrlReq):
    client = await get_client()
    async with client:
        try:
            src = await client.sources.add_url(notebook_id, req.url, wait=req.wait)
            return {"ok": True, "source": src.model_dump() if hasattr(src, "model_dump") else src.__dict__}
        except TypeError:
            # some versions may not accept wait=; fall back
            try:
                src = await client.sources.add_url(notebook_id, req.url)
                return {"ok": True, "source": src.model_dump() if hasattr(src, "model_dump") else src.__dict__}
            except RPCError as e:
                raise map_rpc_error(e)
        except RPCError as e:
            raise map_rpc_error(e)


@app.post("/v1/notebooks/{notebook_id}/sources/youtube")
async def add_source_youtube(notebook_id: str, req: SourceAddYoutubeReq):
    client = await get_client()
    async with client:
        try:
            src = await client.sources.add_youtube(notebook_id, req.url, wait=req.wait)
            return {"ok": True, "source": src.model_dump() if hasattr(src, "model_dump") else src.__dict__}
        except TypeError:
            try:
                src = await client.sources.add_youtube(notebook_id, req.url)
                return {"ok": True, "source": src.model_dump() if hasattr(src, "model_dump") else src.__dict__}
            except RPCError as e:
                raise map_rpc_error(e)
        except RPCError as e:
            raise map_rpc_error(e)


@app.post("/v1/notebooks/{notebook_id}/sources/text")
async def add_source_text(notebook_id: str, req: SourceAddTextReq):
    client = await get_client()
    async with client:
        try:
            src = await client.sources.add_text(notebook_id, req.title, req.content)
            return {"ok": True, "source": src.model_dump() if hasattr(src, "model_dump") else src.__dict__}
        except RPCError as e:
            raise map_rpc_error(e)


@app.post("/v1/notebooks/{notebook_id}/sources/file")
async def add_source_file(
    notebook_id: str,
    upload: UploadFile = File(...),
    mime_type: Optional[str] = Form(None),
):
    # Save to temp file first
    suffix = os.path.splitext(upload.filename or "")[1] or ".bin"
    tmp_path = os.path.join(tempfile.gettempdir(), f"nb_{uuid.uuid4().hex}{suffix}")
    with open(tmp_path, "wb") as f:
        f.write(await upload.read())

    client = await get_client()
    async with client:
        try:
            src = await client.sources.add_file(notebook_id, tmp_path, mime_type=mime_type)
            return {"ok": True, "source": src.model_dump() if hasattr(src, "model_dump") else src.__dict__}
        except RPCError as e:
            raise map_rpc_error(e)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


@app.get("/v1/notebooks/{notebook_id}/sources/{source_id}/fulltext")
async def get_source_fulltext(notebook_id: str, source_id: str):
    client = await get_client()
    async with client:
        try:
            ft = await client.sources.get_fulltext(notebook_id, source_id)
            return {"ok": True, "fulltext": ft.model_dump() if hasattr(ft, "model_dump") else ft.__dict__}
        except RPCError as e:
            raise map_rpc_error(e)


@app.get("/v1/notebooks/{notebook_id}/sources/{source_id}/guide")
async def get_source_guide(notebook_id: str, source_id: str):
    client = await get_client()
    async with client:
        try:
            guide = await client.sources.get_guide(notebook_id, source_id)
            return {"ok": True, "guide": guide}
        except RPCError as e:
            raise map_rpc_error(e)


@app.delete("/v1/notebooks/{notebook_id}/sources/{source_id}")
async def delete_source(notebook_id: str, source_id: str):
    client = await get_client()
    async with client:
        try:
            ok = await client.sources.delete(notebook_id, source_id)
            return {"ok": True, "deleted": bool(ok)}
        except RPCError as e:
            raise map_rpc_error(e)


# ----------------------------
# Chat
# ----------------------------
@app.post("/v1/notebooks/{notebook_id}/chat/ask")
async def chat_ask(notebook_id: str, req: ChatAskReq):
    client = await get_client()
    async with client:
        try:
            prefix = _get_chat_prefix()
            question = req.question
            if prefix and not question.startswith(prefix):
                question = prefix + question
            result = await client.chat.ask(notebook_id, question)
            # result.answer is shown in docs :contentReference[oaicite:5]{index=5}
            if hasattr(result, "model_dump"):
                return {"ok": True, "result": result.model_dump()}
            return {"ok": True, "result": getattr(result, "__dict__", {"answer": getattr(result, "answer", None)})}
        except RPCError as e:
            raise map_rpc_error(e)


# ----------------------------
# Artifacts: list / generate / poll / download
# ----------------------------
@app.get("/v1/notebooks/{notebook_id}/artifacts")
async def list_artifacts(notebook_id: str, type: Optional[str] = None):
    client = await get_client()
    async with client:
        try:
            items = await client.artifacts.list(notebook_id, type=type) if type else await client.artifacts.list(notebook_id)
            return {"ok": True, "items": [a.model_dump() if hasattr(a, "model_dump") else a.__dict__ for a in items]}
        except RPCError as e:
            raise map_rpc_error(e)


@app.post("/v1/notebooks/{notebook_id}/artifacts/generate")
async def generate_artifact(notebook_id: str, req: ArtifactGenerateReq):
    client = await get_client()
    async with client:
        try:
            t = req.type
            # Merge env/default prompts with user options (user options take precedence)
            opts = {**_get_artifact_opts(t), **(req.options or {})}

            if t == "audio":
                status = await client.artifacts.generate_audio(notebook_id, **opts)
            elif t == "video":
                status = await client.artifacts.generate_video(notebook_id, **opts)
            elif t == "report":
                status = await client.artifacts.generate_report(notebook_id, **opts)
            elif t == "quiz":
                status = await client.artifacts.generate_quiz(notebook_id, **opts)
            elif t == "flashcards":
                status = await client.artifacts.generate_flashcards(notebook_id, **opts)
            elif t == "slide_deck":
                status = await client.artifacts.generate_slide_deck(notebook_id, **opts)
            elif t == "infographic":
                status = await client.artifacts.generate_infographic(notebook_id, **opts)
            elif t == "data_table":
                status = await client.artifacts.generate_data_table(notebook_id, **opts)
            elif t == "mind_map":
                # mind_map may return dict directly in docs :contentReference[oaicite:6]{index=6}
                out = await client.artifacts.generate_mind_map(notebook_id, **opts)
                return {"ok": True, "type": t, "result": out}
            else:
                raise HTTPException(status_code=400, detail=f"Unsupported artifact type: {t}")

            # GenerationStatus commonly contains task_id :contentReference[oaicite:7]{index=7}
            payload = status.model_dump() if hasattr(status, "model_dump") else getattr(status, "__dict__", {})
            return {"ok": True, "type": t, "status": payload}
        except RPCError as e:
            raise map_rpc_error(e)


@app.get("/v1/notebooks/{notebook_id}/artifacts/tasks/{task_id}")
async def poll_task(notebook_id: str, task_id: str, wait: bool = False):
    client = await get_client()
    async with client:
        try:
            if wait:
                status = await client.artifacts.wait_for_completion(notebook_id, task_id)
            else:
                status = await client.artifacts.poll_status(notebook_id, task_id)

            payload = status.model_dump() if hasattr(status, "model_dump") else getattr(status, "__dict__", status)
            return {"ok": True, "status": payload}
        except RPCError as e:
            raise map_rpc_error(e)


@app.get("/v1/notebooks/{notebook_id}/artifacts/download")
async def download_artifact(
    notebook_id: str,
    type: Literal[
        "audio",
        "video",
        "infographic",
        "slide_deck",
        "report",
        "mind_map",
        "data_table",
        "quiz",
        "flashcards",
    ],
    artifact_id: Optional[str] = None,
    output_format: Optional[Literal["json", "markdown", "html"]] = None,
):
    """
    Downloads the *first completed* artifact of the given type unless artifact_id is provided.
    notebooklm-py provides type-specific download_* methods. :contentReference[oaicite:8]{index=8}
    """
    suffix_map = {
        "audio": ".mp4",
        "video": ".mp4",
        "infographic": ".png",
        "slide_deck": ".pdf",
        "report": ".md",
        "mind_map": ".json",
        "data_table": ".csv",
        "quiz": ".json" if (output_format in (None, "json")) else (".md" if output_format == "markdown" else ".html"),
        "flashcards": ".json" if (output_format in (None, "json")) else (".md" if output_format == "markdown" else ".html"),
    }
    out_path = os.path.join(tempfile.gettempdir(), f"nlm_{uuid.uuid4().hex}{suffix_map[type]}")

    client = await get_client()
    async with client:
        try:
            if type == "audio":
                await client.artifacts.download_audio(notebook_id, out_path, artifact_id=artifact_id)
            elif type == "video":
                await client.artifacts.download_video(notebook_id, out_path, artifact_id=artifact_id)
            elif type == "infographic":
                await client.artifacts.download_infographic(notebook_id, out_path, artifact_id=artifact_id)
            elif type == "slide_deck":
                await client.artifacts.download_slide_deck(notebook_id, out_path, artifact_id=artifact_id)
            elif type == "report":
                await client.artifacts.download_report(notebook_id, out_path, artifact_id=artifact_id)
            elif type == "mind_map":
                await client.artifacts.download_mind_map(notebook_id, out_path, artifact_id=artifact_id)
            elif type == "data_table":
                await client.artifacts.download_data_table(notebook_id, out_path, artifact_id=artifact_id)
            elif type == "quiz":
                await client.artifacts.download_quiz(
                    notebook_id, out_path, artifact_id=artifact_id,
                    output_format=_get_output_format("quiz", output_format)
                )
            elif type == "flashcards":
                await client.artifacts.download_flashcards(
                    notebook_id, out_path, artifact_id=artifact_id,
                    output_format=_get_output_format("flashcards", output_format)
                )
            else:
                raise HTTPException(status_code=400, detail=f"Unsupported type: {type}")

            filename = os.path.basename(out_path)
            return FileResponse(out_path, filename=filename)
        except RPCError as e:
            # Clean up file if partially created
            try:
                if os.path.exists(out_path):
                    os.remove(out_path)
            except OSError:
                pass
            raise map_rpc_error(e)


# ----------------------------
# Transcribe: upload audio → Chinese simplified transcript in one shot
# ----------------------------
@app.post("/v1/transcribe")
async def transcribe_audio(
    upload: UploadFile = File(...),
    notebook_id: Optional[str] = Form(None),
    prompt: Optional[str] = Form(None),
    keep_notebook: bool = Form(False),
    source_wait_seconds: int = Form(30),
):
    """
    Upload an audio file and get a Chinese simplified transcription.

    - If notebook_id is omitted, a temporary notebook is created and deleted after transcription
      (unless keep_notebook=true).
    - prompt overrides the default transcription instruction.
    - source_wait_seconds controls how long to wait for NotebookLM to index the audio (default 8 s).
    """
    suffix = os.path.splitext(upload.filename or "")[1] or ".mp3"
    tmp_path = os.path.join(tempfile.gettempdir(), f"tr_{uuid.uuid4().hex}{suffix}")
    with open(tmp_path, "wb") as f:
        f.write(await upload.read())

    created_notebook_id: Optional[str] = None
    client = await get_client()
    try:
        async with client:
            try:
                # Create a temp notebook if none supplied
                if not notebook_id:
                    nb = await client.notebooks.create(f"录音转录_{uuid.uuid4().hex[:8]}")
                    created_notebook_id = getattr(nb, "id", None) or (nb.model_dump() if hasattr(nb, "model_dump") else nb.__dict__).get("id")
                    notebook_id = created_notebook_id

                # Upload audio as source
                await client.sources.add_file(notebook_id, tmp_path)

                # Wait for NotebookLM to index the source
                await asyncio.sleep(max(1, source_wait_seconds))

                # Ask for transcription
                q = prompt or os.environ.get("TRANSCRIBE_PROMPT", DEFAULT_TRANSCRIBE_PROMPT)
                result = await client.chat.ask(notebook_id, q)

                answer = getattr(result, "answer", None)
                result_data = result.model_dump() if hasattr(result, "model_dump") else getattr(result, "__dict__", {"answer": answer})

                # Clean up temp notebook unless caller wants to keep it
                returned_notebook_id = notebook_id if keep_notebook else None
                if created_notebook_id and not keep_notebook:
                    try:
                        await client.notebooks.delete(created_notebook_id)
                    except Exception:
                        pass

                return {
                    "ok": True,
                    "transcription": answer,
                    "result": result_data,
                    "notebook_id": returned_notebook_id,
                }
            except RPCError as e:
                raise map_rpc_error(e)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


# ----------------------------
# Webhook: upstream notifies → transcribe → save txt → push downstream
# ----------------------------
async def _transcribe_and_notify(audio_path: str, prompt: Optional[str], downstream_url: str):
    filename = os.path.basename(audio_path)
    stem = os.path.splitext(filename)[0]
    os.makedirs(TRANSCRIPTIONS_FOLDER, exist_ok=True)
    txt_path = os.path.join(TRANSCRIPTIONS_FOLDER, f"{stem}.txt")

    transcription: Optional[str] = None
    error: Optional[str] = None

    print(f"Transcribing: {filename}")
    try:
        q = prompt or os.environ.get("TRANSCRIBE_PROMPT", DEFAULT_TRANSCRIBE_PROMPT)
        client = await NotebookLMClient.from_storage(AUTH_STORAGE_PATH) if AUTH_STORAGE_PATH else await NotebookLMClient.from_storage()
        async with client:
            nb = await client.notebooks.create(f"tr_{uuid.uuid4().hex[:8]}")
            nb_id = getattr(nb, "id", None) or (nb.model_dump() if hasattr(nb, "model_dump") else nb.__dict__).get("id")
            await client.sources.add_file(nb_id, audio_path)
            await asyncio.sleep(30)
            result = await client.chat.ask(nb_id, q)
            transcription = getattr(result, "answer", None)
            try:
                await client.notebooks.delete(nb_id)
            except Exception:
                pass
    except Exception as e:
        error = str(e)
        print(f"Transcribe error [{filename}]: {e}")
        if any(k in error.lower() for k in ("401", "403", "auth", "expired", "invalid")):
            await _notify("session_expired", f"转录失败（登录已过期）：{filename}", error)

    # Persist to txt and delete source MP3 on success
    if transcription:
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(transcription)
        try:
            os.remove(audio_path)
            print(f"Deleted source file: {filename}")
        except OSError as e:
            print(f"Could not delete source file [{filename}]: {e}")

    # Push to downstream — always include filename and filepath so downstream
    # knows exactly which audio file this result belongs to
    if downstream_url:
        payload = {
            "filename": filename,
            "filepath": audio_path,
            "txt_path": txt_path,
            "transcription": transcription,
            "error": error,
        }
        try:
            async with httpx.AsyncClient() as http:
                await http.post(downstream_url, json=payload, timeout=30)
            print(f"Downstream notified: {filename}")
        except Exception as e:
            print(f"Downstream webhook push failed [{filename}]: {e}")


@app.post("/v1/webhook/transcribe")
async def webhook_transcribe(req: TranscribeWebhookReq):
    """
    Receive upstream notification, enqueue the audio file for sequential transcription,
    save result to /transcriptions/<name>.txt, then POST to downstream webhook.
    """
    if req.filepath:
        audio_path = req.filepath
    elif req.filename:
        audio_path = os.path.join(WATCH_FOLDER, req.filename)
    else:
        raise HTTPException(status_code=400, detail="filepath or filename is required")

    if not os.path.exists(audio_path):
        raise HTTPException(status_code=404, detail=f"File not found: {audio_path}")

    downstream_url = req.downstream_webhook_url or DOWNSTREAM_WEBHOOK_URL
    await _transcribe_queue.put((audio_path, req.prompt, downstream_url))
    queue_size = _transcribe_queue.qsize()
    return {"ok": True, "accepted": True, "file": audio_path, "queue_size": queue_size}
