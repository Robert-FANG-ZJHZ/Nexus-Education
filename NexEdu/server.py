"""本地 Web 服务（零第三方依赖，基于 http.server）。

对外接口：
- GET  /                前端页面
- GET  /api/status     模型/Key 状态
- GET  /api/experts    专家列表
- POST /api/chat       单专家对话（ndjson 流式，可携带启用的技能；自动存档到历史会话）
- POST /api/lesson     一键备课闭环（ndjson 流式，可携带启用的技能）
- GET  /api/skills     技能列表（**仅教师自建**；内置技能不进工坊，由专家自动调用）
- POST /api/skills     新建技能 {name, desc, prompt}
- POST /api/skills/delete  删除技能 {id}（内置技能拒绝删除，返回 400）
- GET  /api/skills/groups   内置技能分组索引 + 内置/自建计数（前端只取计数）
- GET  /api/sessions           历史会话列表
- GET  /api/sessions/{id}      会话详情（含消息）
- DELETE /api/sessions/{id}    删除会话
- GET  /api/kb/status          知识库构建状态
- GET  /api/kb/list            知识库概览（种子 + 用户本地文档列表）
- POST /api/kb/add?name=xx     上传文档（body 为原始文件字节）
- POST /api/kb/remove          删除用户本地文档 {name}
- GET  /api/lessons/list           教案沙盒列表
- GET  /api/lessons/get?id=xx      教案详情（Markdown）
- GET  /api/lessons/download?id=xx 下载教案 Word
- POST /api/lessons/save           保存教案到本地沙盒 {title, content, meta}
- POST /api/lessons/remove         删除教案 {id}
- GET  /api/lessons/open-folder    在资源管理器打开沙盒目录
- POST /api/export/docx            直接导出 Word（不保存，返回文件字节）
- POST /api/export/svg             直接导出图表 SVG（bar/line/pie，零依赖生成）
- POST /api/export/svg             直接导出图表 SVG（bar/line/pie，零依赖生成）
- POST /api/shutdown  请求退出（页面关闭时由前端 sendBeacon 触发）

流式协议：每行一个 JSON 事件
  {"type":"section","expert":"nexaim","title":"..."}
  {"type":"delta","text":"..."}
  {"type":"done"}
"""

import hmac
import json
import logging
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import (agents, config, guard, jobs, knowledge, lessons, memory,
               metrics, package, planner, safety, sessions, skills, triggers,
               workflow)
from . import mcp as mcpool
from . import __version__

_INDEX = os.path.join(config.WEB_DIR, "index.html")
_FAVICON = os.path.join(config.WEB_DIR, "favicon.ico")

# 本地访问令牌：每次进程启动随机生成，前端从 /api/status 换取后随请求回传。
# 配合 Host 校验，阻断外部网页对本服务的跨站调用（CSRF / DNS rebinding）。
_TOKEN = secrets.token_hex(16)
_FREE_PATHS = ("/api/status",)          # 仅用于换取 token，不返回任何敏感内容
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1", config.SERVER_HOST.lower()}

# 页面票据：只有真正加载过本地页面的客户端才持有（脚本/爬虫没有经过页面加载）。
# 与 Host 白名单 + 令牌 + 同源校验一起构成纵深，防止第三方程序批量调接口搬走
# 知识库、技能、工作流与专家目录。
_PAGE_TICKET_TTL = 12 * 3600
_PAGE_TICKET_MAX = 8
_PAGE_TICKETS = {}
_PAGE_LOCK = threading.Lock()
_BLOCK_STAT = {"blocked": 0, "rate": 0}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024   # 知识库单文件上限

_log = logging.getLogger("nexedu")

# 最近一次页面加载时间与关闭请求时间（区分「窗口关闭」与「页面刷新」）
_last_page_load = time.time()
_shutdown_at = None


def _touch_page_load():
    global _last_page_load
    _last_page_load = time.time()


def _json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def _clip_text(s, n):
    """单行截断，用于标题/记忆摘要。"""
    s = (s or "").strip().replace("\n", " ")
    return s[:n] + ("…" if len(s) > n else "")


def _issue_page_ticket():
    """签发页面票据（轮换 + 限量，避免被长期持有后慢慢爬）。"""
    t = secrets.token_hex(16)
    now = time.time()
    with _PAGE_LOCK:
        for k, ts in list(_PAGE_TICKETS.items()):
            if now - ts > _PAGE_TICKET_TTL:
                _PAGE_TICKETS.pop(k, None)
        if len(_PAGE_TICKETS) >= _PAGE_TICKET_MAX:
            _PAGE_TICKETS.pop(min(_PAGE_TICKETS, key=_PAGE_TICKETS.get), None)
        _PAGE_TICKETS[t] = now
    return t


def _ticket_ok(t):
    if not t:
        return False
    with _PAGE_LOCK:
        ts = _PAGE_TICKETS.get(t)
    return bool(ts) and (time.time() - ts) <= _PAGE_TICKET_TTL


def _note_blocked(kind):
    with _PAGE_LOCK:
        _BLOCK_STAT[kind] = _BLOCK_STAT.get(kind, 0) + 1


def block_stat():
    """被拦截的访问统计（供排障与状态展示）。"""
    with _PAGE_LOCK:
        return dict(_BLOCK_STAT)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 120                # keep-alive 空闲连接回收（防止线程常驻）

    # HTTP/1.1 keep-alive：同一连接循环处理多个请求；断连即结束
    def handle(self):
        while True:
            try:
                self.handle_one_request()
            except (ConnectionError, TimeoutError):
                break
            if self.close_connection:
                break

    def handle_one_request(self):
        self._chunked = False
        super().handle_one_request()

    # ---------- 基础响应 ----------
    def _cors(self):
        """同源场景无需 CORS；仅当请求源为本机地址时才回显，不再使用 *。"""
        origin = self.headers.get("Origin")
        if not origin:
            return
        host = origin.split("//", 1)[-1].split(":", 1)[0].strip().lower()
        if host in _ALLOWED_HOSTS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _authorized(self):
        """本机访问令牌校验（阻断外部网页的跨站调用）。"""
        host = (self.headers.get("Host") or "").strip().lower()
        if host.startswith("["):            # IPv6 形式 [::1]:8520
            host = host[1:host.find("]")]
        else:
            host = host.split(":", 1)[0]
        if host not in _ALLOWED_HOSTS:
            return False
        if self.path.split("?")[0] in _FREE_PATHS:
            return True                     # 仅用于换取 token
        return hmac.compare_digest(self.headers.get("X-NexEdu-Token") or "",
                                   _TOKEN)

    def _guard(self):
        """鉴权不通过时响应 403；返回是否放行。"""
        if self._authorized():
            return True
        self._send_json({"error": "未授权的访问：缺少本地访问令牌"}, 403)
        return False

    # ---- 防爬取：来源校验 + 页面票据 + 数据接口限流 ----
    def _deny(self, why):
        _note_blocked("blocked")
        _log.warning("拦截访问（%s）：%s（UA=%s）", why, self.path,
                     (self.headers.get("User-Agent") or "")[:80])
        self._send_json({"error": "本服务仅允许本机的 NexEdu 界面访问"}, 403)
        return False

    def _client_guard(self):
        """只放行「本机 NexEdu 界面」发起的请求。

        - 浏览器明确标记为跨站（Sec-Fetch-Site: cross-site）→ 直接拒绝，
          带了票据也不放行：票据只发给本机页面，不该出现在跨站请求里；
        - 同源 → 放行；
        - 没有来源标识的（脚本、爬虫、curl）→ 必须持有页面加载时下发的票据。
        """
        site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if site == "cross-site":
            return self._deny("跨站页面")
        if site in ("same-origin", "same-site", "none"):
            return True
        # 无 Sec-Fetch-Site（脚本 / 爬虫 / 老浏览器）：看 Referer / Origin
        for key in ("Referer", "Origin"):
            val = (self.headers.get(key) or "").strip()
            if not val:
                continue
            host = val.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0]
            if host.strip().lower() in _ALLOWED_HOSTS:
                return True
            return self._deny("来源非本机")
        if _ticket_ok(self.headers.get("X-NexEdu-Page") or ""):
            return True
        return self._deny("无页面票据")

    def _api_rate_guard(self):
        """数据/资产类接口限流：防止按 id 遍历搬走知识库与内置资产。"""
        p = self.path.split("?")[0]
        if p.startswith("/api/kb/chunk"):
            ok, why = guard.check_api_rate("kb", 60)
        elif p.startswith(("/api/kb/", "/api/skills", "/api/experts",
                           "/api/package", "/api/lessons/list", "/api/sessions",
                           "/api/profile", "/api/metrics", "/api/kg", "/api/embed",
                           "/api/evals", "/api/classroom", "/api/options")):
            ok, why = guard.check_api_rate("asset", 120)
        else:
            return True
        if ok:
            return True
        _note_blocked("rate")
        self._send_json({"error": why}, 429)
        return False

    def _sec_headers(self):
        """安全响应头：禁嗅探、禁嵌套、禁缓存、限定资源加载来源。"""
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data:; "
                         "style-src 'self' 'unsafe-inline'; "
                         "script-src 'self' 'unsafe-inline'; "
                         "frame-ancestors 'none'")

    def _send_json(self, obj, status=200):
        body = _json_bytes(obj)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self._sec_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, content):
        body = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._sec_headers()
        self.end_headers()
        self.wfile.write(body)

    def _start_ndjson(self, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self._sec_headers()
        # HTTP/1.1 用 chunked 分帧，客户端可准确判定流结束（HTTP/1.0 靠关连接）
        self._chunked = self.request_version >= "HTTP/1.1"
        if self._chunked:
            self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

    def _send_binary(self, data, filename, content_type):
        """二进制文件下载（Word 导出等）。"""
        from urllib.parse import quote
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.send_header(
            "Content-Disposition",
            "attachment; filename*=UTF-8''" + quote(filename))
        self._sec_headers()
        self.end_headers()
        self.wfile.write(data)

    def _emit(self, ev):
        line = json.dumps(ev, ensure_ascii=False) + "\n"
        try:
            data = line.encode("utf-8")
            if self._chunked:
                self.wfile.write(("%x\r\n" % len(data)).encode("ascii"))
                self.wfile.write(data)
                self.wfile.write(b"\r\n")
            else:
                self.wfile.write(data)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            raise

    def _end_ndjson(self):
        """流结束标记：chunked 模式补终止块；HTTP/1.0 靠连接关闭判定结束。"""
        try:
            if getattr(self, "_chunked", False):
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            self._chunked = False
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ---------- 路由 ----------
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._serve_index()
            return
        if self.path == "/favicon.ico":
            self._serve_favicon()
            return
        if self.path.startswith("/assets/"):
            self._serve_asset()
            return
        if not self._client_guard():
            return
        if not self._api_rate_guard():
            return
        if not self._guard():
            return
        if self.path == "/api/status":
            self._send_json({
                "configured": bool(config.get_api_key()),
                "model": config.get_model(),
                "provider": config.get_provider(),
                "version": __version__,
                "token": _TOKEN,          # 前端换取后随请求回传
                "key_storage": config.credential_path(),
                # 配置文件位置：配置已迁至用户目录，源码/exe 共用同一份
                "settings_storage": config.active_settings_path(),
                "workspace_dir": config.WORKSPACE_DIR,
                "local_endpoint": config.is_local_base_url(config.get_base_url()),
            })
        elif self.path == "/api/models":
            self._send_json({"presets": config.MODEL_PRESETS})
        elif self.path == "/api/settings":
            self._send_json(config.current())
        elif self.path == "/api/experts":
            data = [{
                "id": e["id"], "name": e["name"], "cn": e["cn"],
                "desc": e["desc"], "outputs": e["outputs"],
                "stage": e.get("stage", ""), "tags": e.get("tags", []),
                "prompt_source": agents.prompt_source(e["id"]),
            } for e in agents.list_experts()]
            self._send_json(data)
        elif self.path == "/api/skills/groups":
            # 内置技能分组概览（只读索引，不进技能工坊）
            from . import skilllib
            self._send_json({
                "groups": [{"group": g, "ids": ids}
                           for g, ids in skilllib.groups()],
                "counts": skills.count_summary(),
            })
        elif self.path == "/api/metrics":
            self._send_json(metrics.summary())
        elif self.path == "/api/evals":
            from . import evals
            self._send_json(evals.run())
        elif self.path == "/api/evolve":
            self._handle_evolve({})
        elif self.path == "/api/profile":
            self._handle_profile({})
        elif self.path == "/api/classroom":
            self._handle_classroom({})
        elif self.path == "/api/tasks":
            self._handle_tasks({})
        elif self.path == "/api/autonomy":
            self._handle_autonomy({})
        elif self.path == "/api/web":
            self._handle_web({})
        elif self.path == "/api/vision":
            self._handle_vision({})
        elif self.path == "/api/speech":
            from . import speech
            self._send_json(speech.status())
        elif self.path.startswith("/api/kg"):
            from . import kg
            self._send_json(kg.status())
        elif self.path == "/api/embed":
            from . import embed
            self._send_json(embed.stats())
        elif self.path == "/api/cache":
            self._handle_cache({})
        elif self.path == "/api/skills":
            self._send_json(skills.list_skills())
        elif self.path.startswith("/api/kb/chunk"):
            self._handle_kb_chunk()
        elif self.path == "/api/kb/status":
            self._send_json(knowledge.status())
        elif self.path == "/api/kb/list":
            st = knowledge.status()
            self._send_json({
                "seed_chunks": st.get("seed_chunks", 0),
                "local_chunks": st.get("local_chunks", 0),
                "total_chunks": st.get("total_chunks", 0),
                "phase": st.get("phase"),
                "docs": knowledge.list_local(),
            })
        elif self.path == "/api/sessions":
            self._send_json(sessions.list_sessions())
        elif self.path.startswith("/api/sessions/"):
            data = sessions.get_session(self.path[len("/api/sessions/"):])
            if data is None:
                self._send_json({"error": "会话不存在"}, 404)
            else:
                self._send_json(data)
        elif self.path == "/api/lessons/list":
            self._send_json(lessons.list_lessons())
        elif self.path.startswith("/api/lessons/get"):
            self._handle_lesson_get()
        elif self.path.startswith("/api/lessons/download"):
            self._handle_lesson_download()
        elif self.path == "/api/lessons/open-folder":
            ok = lessons.open_folder()
            self._send_json({"ok": ok} if ok
                            else {"ok": False, "error": "无法打开目录"}, 200 if ok else 500)
        elif self.path == "/api/lessons/open-output":
            ok = lessons.open_output_folder()
            self._send_json({"ok": ok} if ok
                            else {"ok": False, "error": "无法打开目录"}, 200 if ok else 500)
        elif self.path == "/api/workflows":
            self._send_json({"workflows": workflow.list_workflows()})
        elif self.path == "/api/workflow/runs":
            self._send_json({"runs": workflow.list_runs()})
        elif self.path.startswith("/api/workflow/run"):
            self._handle_workflow_run_get()
        elif self.path == "/api/jobs":
            self._send_json({"jobs": jobs.MANAGER.list(),
                             "running": jobs.MANAGER.active()})
        elif self.path.startswith("/api/jobs/events"):
            self._handle_job_events()
        elif self.path == "/api/mcp":
            self._send_json(mcpool.POOL.status())
        elif self.path == "/api/triggers":
            self._send_json(triggers.status())
        elif self.path == "/api/package":
            self._send_json(package.export_package())
        else:
            self._send_json({"error": "not found"}, 404)

    def do_DELETE(self):
        if not self._client_guard():
            return
        if not self._guard():
            return
        if self.path.startswith("/api/sessions/"):
            sid = self.path[len("/api/sessions/"):]
            ok = sessions.delete_session(sid)
            # 删对话即删产出：这条对话里生成的本地文件（Word/PPT/Markdown）
            # 一并清掉，避免教师以为删干净了、磁盘上却还留着
            files = lessons.delete_by_session(sid) if ok else 0
            self._send_json({"ok": ok, "files": files} if ok
                            else {"error": "删除失败"}, 200 if ok else 400)
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        # 回调触发：外部系统 POST 数据进来，用触发器自带 token 鉴权
        # （必须放在统一守卫之前——外部系统拿不到本地进程令牌）
        if self.path.startswith("/api/hook/"):
            self._handle_webhook()
            return
        if not self._client_guard():
            return
        if not self._guard():
            return
        # 文档上传：body 是原始文件字节（非 JSON），需在 JSON 解析前分流
        if self.path.startswith("/api/kb/add"):
            self._handle_kb_add()
            return
        if self.path.startswith("/api/cache/upload"):
            self._handle_cache_upload()
            return
        if self.path.startswith("/api/maintenance/restore"):
            self._handle_maintenance_restore()
            return
        if self.path.startswith("/api/maintenance/backup"):
            self._handle_maintenance_backup()
            return
        if self.path.startswith("/api/prompts/pack"):
            self._handle_prompts_pack()
            return
        if self.path.startswith("/api/prompts/import"):
            self._handle_prompts_import()
            return

        try:
            body = self._read_body()
            payload = json.loads(body) if body else {}
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "请求体不是合法 JSON"}, 400)
            return

        if self.path == "/api/chat":
            self._handle_chat(payload)
        elif self.path == "/api/agent":
            self._handle_agent(payload)
        elif self.path == "/api/lesson":
            self._handle_lesson(payload)
        elif self.path == "/api/settings":
            self._handle_settings_save(payload)
        elif self.path == "/api/settings/test":
            self._handle_settings_test(payload)
        elif self.path == "/api/skills":
            self._handle_skill_create(payload)
        elif self.path == "/api/skills/delete":
            self._handle_skill_delete(payload)
        elif self.path == "/api/feedback":
            self._handle_feedback(payload)
        elif self.path == "/api/evolve":
            self._handle_evolve(payload)
        elif self.path == "/api/evals":
            self._handle_evals(payload)
        elif self.path == "/api/profile":
            self._handle_profile(payload)
        elif self.path == "/api/classroom":
            self._handle_classroom(payload)
        elif self.path == "/api/verify":
            self._handle_verify(payload)
        elif self.path == "/api/options":
            self._handle_options(payload)
        elif self.path == "/api/tasks":
            self._handle_tasks(payload)
        elif self.path == "/api/tasks/run":
            self._handle_task_run(payload)
        elif self.path == "/api/autonomy":
            self._handle_autonomy(payload)
        elif self.path == "/api/web":
            self._handle_web(payload)
        elif self.path == "/api/vision":
            self._handle_vision(payload)
        elif self.path == "/api/plan":
            self._handle_plan(payload)
        elif self.path == "/api/cache":
            self._handle_cache(payload)
        elif self.path == "/api/pick-folder":
            self._handle_pick_folder()
        elif self.path == "/api/kb/remove":
            self._handle_kb_remove(payload)
        elif self.path == "/api/kb/failed/clear":
            self._handle_kb_failed_clear(payload)
        elif self.path == "/api/lessons/save":
            self._handle_lesson_save(payload)
        elif self.path == "/api/lessons/remove":
            self._handle_lesson_remove(payload)
        elif self.path == "/api/export/docx":
            self._handle_export_docx(payload)
        elif self.path == "/api/export/pptx":
            self._handle_export_pptx(payload)
        elif self.path == "/api/export/svg":
            self._handle_export_svg(payload)
        elif self.path == "/api/workflow/run":
            self._handle_workflow_run(payload)
        elif self.path == "/api/workflow/resume":
            self._handle_workflow_resume(payload)
        elif self.path == "/api/jobs/run":
            self._handle_job_run(payload)
        elif self.path == "/api/jobs/cancel":
            self._handle_job_cancel(payload)
        elif self.path == "/api/mcp":
            self._handle_mcp_save(payload)
        elif self.path == "/api/mcp/sync":
            self._send_json(mcpool.POOL.sync(force=True))
        elif self.path == "/api/triggers":
            self._handle_trigger_save(payload)
        elif self.path == "/api/triggers/fire":
            self._handle_trigger_fire(payload)
        elif self.path == "/api/package/import":
            self._handle_package_import(payload)
        elif self.path == "/api/settings/suggest-routing":
            self._handle_settings_suggest(payload)
        elif self.path == "/api/sessions/clear":
            self._handle_sessions_clear()
        elif self.path == "/api/lessons/cleanup":
            self._handle_lessons_cleanup(payload)
        elif self.path == "/api/maintenance/clear-data":
            self._handle_maintenance_clear(payload)
        elif self.path == "/api/shutdown":
            self._handle_shutdown()
        else:
            self._send_json({"error": "not found"}, 404)

    # ---------- 具体处理 ----------
    def _serve_index(self):
        _touch_page_load()  # 标记页面活动（区分刷新与关闭）
        # 每次页面加载签发一枚新票据，注入到页面里由前端随请求回传；
        # 未加载过页面的脚本/爬虫拿不到，也就调不动任何接口。
        ticket = _issue_page_ticket()
        # 优先读加密资源包（打包后 exe 内不含明文 index.html）；
        # 源码运行或 pak 缺失时回退原文件。
        html = None
        try:
            from . import assets
            html = assets.read_text("web/index.html")
        except Exception:
            html = None
        if html is None and os.path.exists(_INDEX):
            with open(_INDEX, "r", encoding="utf-8") as f:
                html = f.read()
        if html:
            html = html.replace("/*__NEXEDU_PAGE__*/",
                                "window.__NEXEDU_PAGE__='%s';" % ticket, 1)
            self._send_html(html)
        else:
            self._send_html("<h1>NexEdu</h1><p>前端页面缺失</p>")

    def _serve_favicon(self):
        """真实 .ico 图标（任务栏 / 收藏夹图标用，比 data URI 兼容性更好）。"""
        try:
            with open(_FAVICON, "rb") as f:
                data = f.read()
        except OSError:
            self._send_json({"error": "not found"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/x-icon")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    def _serve_asset(self):
        """静态品牌资源（web/assets 下的图片）。

        <img> 标签无法携带自定义请求头，故不校验令牌；但严格限制目录与扩展名，
        并拒绝任何路径穿越。
        """
        from urllib.parse import unquote
        rel = unquote(self.path[len("/assets/"):]).replace("\\", "/")
        safe = os.path.normpath(rel)
        if safe.startswith("..") or os.path.isabs(safe) or ":" in safe:
            self._send_json({"error": "not found"}, 404)
            return
        ext = os.path.splitext(safe)[1].lower()
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".svg": "image/svg+xml", ".webp": "image/webp",
                ".ico": "image/x-icon", ".gif": "image/gif"}
        if ext not in mime:
            self._send_json({"error": "not found"}, 404)
            return
        path = os.path.join(config.WEB_DIR, "assets", safe)
        if not os.path.isfile(path):
            self._send_json({"error": "not found"}, 404)
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            self._send_json({"error": "not found"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", mime[ext])
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _render_extra(skill_ids):
        """把前端勾选启用的技能 id 渲染为附加指令文本。"""
        if not skill_ids:
            return ""
        enabled = [s for s in skills.list_skills() if s.get("id") in skill_ids]
        return skills.render_enabled(enabled)

    def _build_extra(self, payload, message):
        """构造附加上下文：技能 + 深度思考 + 联网检索 + 本次上传的缓存文档。

        三件事彼此独立，可任意组合开关（教师可能只要联网、也可能三个全开）。
        每一路在不可用（未开启 / 无网络 / 无缓存）时都**如实说明**，
        不假装做过——尤其是联网，"没开就是没开"。
        """
        extra = self._render_extra(payload.get("skills") or [])
        notes = []

        if payload.get("deep"):
            extra += ("\n\n【深度思考模式】请先把问题拆开，逐层推演："
                      "先厘清关键概念与学生起点，再比较可选做法并说明取舍依据，"
                      "最后给出结论与可执行的建议。推演过程要有条理，"
                      "但不要把犹豫过程写进最终结论——结论必须明确、可用于教学。")

        if payload.get("web"):
            try:
                from . import websearch
                r = websearch.lookup(message, fetch_pages=1)
                txt = r.get("text") or ""
                if r.get("ok") and (r.get("results") or []):
                    extra += "\n\n" + txt
                    notes.append(("已联网检索到 %d 条公开资料（仅发送脱敏检索词，"
                                  "未发送任何校本资料），网络来源请自行甄别"
                                  % len(r["results"])))
                else:
                    notes.append(txt or "联网检索未取到结果")
            except Exception as e:
                notes.append(f"联网检索失败：{e}")

        try:
            from . import cache
            block = cache.render(message, topk=3)
            if block:
                extra += "\n\n" + block
        except Exception:
            pass

        return extra, notes

    def _handle_chat(self, payload):
        expert_id = payload.get("expert", "coordinator")
        message = (payload.get("message") or "").strip()
        history = payload.get("history") or []
        if not message:
            self._send_json({"error": "消息不能为空"}, 400)
            return

        # 防批量爬取：限流 + 批量抽取知识库意图拦截
        ok, why = guard.check_rate()
        if not ok:
            self._start_ndjson()
            self._emit({"type": "delta", "text": why})
            self._emit({"type": "done"})
            self._end_ndjson()
            return
        sus, why = guard.detect_scraping(message)
        if sus:
            self._start_ndjson()
            self._emit({"type": "section", "expert": "coordinator",
                        "title": "请求已被知识产权保护策略拦截"})
            self._emit({"type": "delta", "text":
                        "该请求疑似批量获取内置知识库内容，已按知识产权保护策略拦截。\n\n"
                        "**命中原因**：" + why +
                        "\n\n如果你确有教学需求，请具体说明课题与环节，我会正常为你备课。"})
            self._emit({"type": "done"})
            self._end_ndjson()
            return

        # 输入守卫 + 学生信息脱敏（设计文档 §12.1 / §12.2）
        sg = safety.preprocess(message)
        if not sg["allowed"]:
            self._start_ndjson()
            self._emit({"type": "section", "expert": "coordinator",
                        "title": "输入未通过安全校验"})
            self._emit({"type": "delta", "text":
                        "这条消息包含试图改变我既定规则的要求，我没有执行。\n\n"
                        "**命中原因**：" + "；".join(sg["blocked"]) +
                        "\n\n如果你确实需要调整输出范围或格式，"
                        "请直接说明教学需求，我会按「教-学-评」的规范来完成。"})
            self._emit({"type": "done"})
            self._end_ndjson()
            return
        message = sg["message"]
        privacy_note = ("（已自动脱敏学生信息：" + "、".join(sg["masked"]) + "）\n\n"
                        if sg["masked"] else "")

        extra, web_notes = self._build_extra(payload, message)

        # 自动路由：expert 为 auto 时按意图定位专家（教师不必手动选专家）
        target = expert_id
        if expert_id == "auto":
            target = agents.route(message)
            if target == "lesson":
                # 聊天里命中完整备课意图：直接自动转智能体编排，不要求教师再点按钮
                self._handle_agent(payload, prelude=[{
                    "type": "routed", "expert": "auto", "intent": "lesson",
                    "title": "检测到完整备课意图 · 已自动编排多步执行"}])
                return

        expert = agents.get_expert(target)
        _log.info("对话请求：expert=%s len=%d", target, len(message))
        routed = {
            "type": "routed", "expert": expert["id"], "intent": "expert",
            "name": expert["name"], "cn": expert["cn"],
            "auto": expert_id == "auto",
            "title": ("已自动匹配专家" if expert_id == "auto" else "指定专家"),
        }
        section_title = f"{expert['cn']}（{expert['name']}）"

        # 历史会话：无 session_id 或会话不存在时新建（标题取首条消息）
        sid = (payload.get("session_id") or "").strip()
        if not sid or sessions.get_session(sid) is None:
            sid = sessions.create_session(title=message[:24])["id"]

        # 绑定会话上下文：本次对话里 AI 保存的教案会记下来源会话，
        # 教师删除这条对话时，产出文件一并清掉
        lessons.set_session(sid)
        self._start_ndjson()
        # 先回传会话 id，前端据此续接对话并刷新历史列表
        self._emit({"type": "session", "id": sid, "title": message[:24]})
        self._emit(routed)
        t_chat = time.time()
        try:
            self._emit({"type": "section", "expert": expert["id"], "title": section_title,
                        "ms": 0})
            if privacy_note:
                self._emit({"type": "delta", "text": privacy_note})
            # 联网/缓存等附加上下文的状态如实告知（没开就说没开）
            for note in web_notes:
                self._emit({"type": "delta", "text": f"（{note}）\n\n"})
            parts = []
            for chunk in agents.chat(expert["id"], message, history, extra=extra):
                parts.append(chunk)
                self._emit({"type": "delta", "text": chunk})
            # 存档到历史会话
            now = time.time()
            sessions.append_messages(sid, [
                {"role": "user", "content": message, "ts": now},
                {"role": "assistant", "content": "".join(parts),
                 "expert": expert["id"], "title": section_title, "ts": now},
            ])
            # 单专家对话同样沉淀到日级记忆（设计文档 §7「沉淀」）
            try:
                memory.note(f"与{expert['cn']}对话：{_clip_text(message, 30)}")
            except Exception:
                pass
            self._emit({"type": "done", "ms": int((time.time() - t_chat) * 1000)})
        except Exception as e:
            _log.exception("对话生成失败")
            self._emit({"type": "delta", "text": f"\n\n（生成出错：{e}）"})
            self._emit({"type": "done", "ms": int((time.time() - t_chat) * 1000)})
        finally:
            lessons.clear_session()
        self._end_ndjson()

    def _handle_agent(self, payload, prelude=None):
        """智能体入口：规划 → 逐步执行（期间可调用本地工具）→ 自检修订。

        保留 /api/chat 与 /api/lesson 的原有行为，此接口面向"会思考会动手"的模式。
        """
        message = (payload.get("message") or "").strip()
        if not message:
            self._send_json({"error": "消息不能为空"}, 400)
            return

        # 防批量爬取：限流 + 批量抽取知识库意图拦截
        ok, why = guard.check_rate()
        if not ok:
            self._start_ndjson()
            self._emit({"type": "delta", "text": why})
            self._emit({"type": "done"})
            self._end_ndjson()
            return
        sus, why = guard.detect_scraping(message)
        if sus:
            self._start_ndjson()
            self._emit({"type": "delta", "text":
                        "该请求疑似批量获取内置知识库内容，已按知识产权保护策略拦截。\n\n"
                        "**命中原因**：" + why})
            self._emit({"type": "done"})
            self._end_ndjson()
            return

        sg = safety.preprocess(message)
        if not sg["allowed"]:
            self._start_ndjson()
            self._emit({"type": "delta", "text":
                        "这条消息包含试图改变我既定规则的要求，我没有执行。\n\n"
                        "**命中原因**：" + "；".join(sg["blocked"])})
            self._emit({"type": "done"})
            self._end_ndjson()
            return
        message = sg["message"]

        extra, web_notes = self._build_extra(payload, message)
        plan = planner.plan(message, payload.get("expert", "auto"),
                            (payload.get("student_info") or "").strip())
        _log.info("智能体请求：strategy=%s len=%d",
                  plan.get("strategy"), len(message))

        sid = (payload.get("session_id") or "").strip()
        if not sid or sessions.get_session(sid) is None:
            sid = sessions.create_session(title=_clip_text(message, 24))["id"]

        # 绑定会话上下文（同 /api/chat）：本次生成的教案归属这条对话
        lessons.set_session(sid)
        self._start_ndjson()
        self._emit({"type": "session", "id": sid, "title": _clip_text(message, 24)})
        for ev in (prelude or []):
            self._emit(ev)
        self._emit({"type": "plan", "plan": {
            "strategy": plan["strategy"],
            "summary": plan["summary"],
            "metacog": plan.get("metacog"),
            "steps": [{"title": s["title"], "expert": s["expert"],
                       "critique": bool(s.get("critique"))}
                      for s in plan["steps"]],
        }})
        if sg["masked"]:
            self._emit({"type": "delta", "text":
                        "（已自动脱敏学生信息：" + "、".join(sg["masked"]) + "）\n\n"})
        for note in web_notes:      # 联网/缓存等附加上下文状态如实告知
            self._emit({"type": "delta", "text": f"（{note}）\n\n"})

        history = payload.get("history") or []
        outputs, now = [], time.time()
        try:
            for idx, step in enumerate(plan["steps"], 1):
                expert = agents.get_expert(step["expert"])
                self._emit({"type": "step", "index": idx,
                            "expert": expert["id"], "title": step["title"]})
                # 与工作流引擎同构的节点事件：前端可复用同一套「思考时间线」
                self._emit({"type": "node_start", "index": idx - 1,
                            "id": "step%d" % idx, "title": step["title"],
                            "expert": expert["id"]})
                goal = step.get("goal") or message
                if outputs:     # 后续步骤承接前序成果（按段落边界截断）
                    prev = "\n\n".join(
                        f"【前序：{t}】\n{agents._clip(o, 1200)}"
                        for t, o in outputs[-2:])
                    goal = f"{goal}\n\n{prev}"
                acc = []
                t0 = time.time()
                for ev in agents.run_agent(
                        step["expert"], goal, history=history, extra=extra,
                        tools=step.get("tools") or [],
                        critique=bool(step.get("critique"))):
                    if ev.get("type") == "delta":
                        acc.append(ev["text"])
                    if ev.get("type") in workflow.THINK_TYPES:
                        # 过程事件带上步骤坐标与耗时，供前端折叠展示
                        ev = dict(ev)
                        ev.setdefault("index", idx - 1)
                        ev["label"] = workflow.THINK_LABEL.get(ev["type"], "思考")
                        ev.setdefault("ms", int((time.time() - t0) * 1000))
                    # 元认知：把「策略 → 是否判需修订」的结果记进历史（供换策略决策）
                    if ev.get("type") == "critique":
                        try:
                            from . import metacog
                            metacog.record(plan["strategy"], step["expert"],
                                           ev.get("verdict") or "pass",
                                           ev.get("round") or 1)
                        except Exception:
                            pass
                    self._emit(ev)
                    # 工具产出了文件（如保存教案）时，补一条产出提示供界面展示
                    if ev.get("type") == "observe" and ev.get("name") in (
                            "save_lesson", "export_docx"):
                        from . import tools as toolset
                        for a in toolset.LAST_ARTIFACTS[-1:]:
                            self._emit({"type": "artifact", **a})
                outputs.append((step["title"], "".join(acc)))
                self._emit({"type": "node_done", "index": idx - 1,
                            "id": "step%d" % idx, "status": "ok",
                            "ms": int((time.time() - t0) * 1000)})

            sessions.append_messages(sid, [
                {"role": "user", "content": message, "ts": now},
                {"role": "assistant", "content": "\n\n".join(o for _, o in outputs),
                 "expert": plan["expert"], "title": "智能体执行结果", "ts": now},
            ])
            # 后台自进化：与一键备课一样，教师无感，节流后在后台线程跑一轮
            try:
                from . import evolve
                evolve.note_activity()
            except Exception:
                pass
            self._emit({"type": "done"})
        except Exception as e:
            _log.exception("智能体执行失败")
            self._emit({"type": "delta", "text": f"\n\n（执行出错：{e}）"})
            self._emit({"type": "done"})
        finally:
            lessons.clear_session()
        self._end_ndjson()

    def _handle_lesson(self, payload):
        subject = (payload.get("subject") or "").strip() or "语文"
        grade = (payload.get("grade") or "").strip() or "未指定学段"
        topic = (payload.get("topic") or "").strip()
        student_info = (payload.get("student_info") or "").strip()
        if not topic:
            self._send_json({"error": "请填写课题/单元"}, 400)
            return

        # 防批量爬取：限流 + 批量抽取知识库意图拦截
        ok, why = guard.check_rate()
        if not ok:
            self._send_json({"error": why}, 429)
            return
        sus, why = guard.detect_scraping(topic)
        if sus:
            self._send_json({"error": "请求疑似批量获取内置知识库内容，"
                                      "已按知识产权保护策略拦截（" + why + "）"}, 403)
            return

        # 输入守卫 + 学情脱敏
        sg = safety.preprocess(topic, student_info)
        if not sg["allowed"]:
            self._send_json({"error": "输入未通过安全校验："
                                      + "；".join(sg["blocked"])}, 400)
            return
        topic, student_info = sg["message"], sg["student"]

        extra = self._render_extra(payload.get("skills") or [])

        # 一键备课若由某条对话发起（payload 带 session_id），产出同样归属该对话
        lessons.set_session((payload.get("session_id") or "").strip())
        self._start_ndjson()
        try:
            for ev in agents.orchestrate_lesson(subject, grade, topic, student_info, extra=extra):
                self._emit(ev)
        except Exception as e:
            _log.exception("一键备课失败")
            self._emit({"type": "delta", "text": f"\n\n（生成出错：{e}）"})
            self._emit({"type": "done"})
        finally:
            lessons.clear_session()
        self._end_ndjson()

    # ---------- 工作流引擎 ----------
    def _handle_workflow_run(self, payload):
        """创建工作流运行并以 NDJSON 流式执行（全自动模式无人值守，交互模式遇检查点暂停）。

        payload 带 ``async: true`` 时不占用当前连接，改为提交后台任务并立即返回
        job_id——这是「多任务并行」的入口：教师可以同时开多个备课任务。
        """
        if payload.get("async"):
            payload = dict(payload)
            payload["kind"] = "workflow"
            self._handle_job_run(payload)
            return
        wf_id = (payload.get("workflow") or "w1-lesson").strip()
        subject = (payload.get("subject") or "").strip() or "语文"
        grade = (payload.get("grade") or "").strip()
        topic = (payload.get("topic") or "").strip()
        student_info = (payload.get("student_info") or "").strip()
        auto = bool(payload.get("auto"))

        if not topic:
            self._send_json({"error": "请填写课题/单元"}, 400)
            return

        ok, why = guard.check_rate()
        if not ok:
            self._send_json({"error": why}, 429)
            return
        sus, why = guard.detect_scraping(topic)
        if sus:
            self._send_json({"error": "请求疑似批量获取内置知识库内容，"
                                      "已按知识产权保护策略拦截（" + why + "）"}, 403)
            return
        sg = safety.preprocess(topic, student_info)
        if not sg["allowed"]:
            self._send_json({"error": "输入未通过安全校验："
                                      + "；".join(sg["blocked"])}, 400)
            return
        topic, student_info = sg["message"], sg["student"]

        try:
            run = workflow.create_run(
                wf_id, subject, grade, topic, student_info=student_info,
                extra=self._render_extra(payload.get("skills") or []),
                skills=payload.get("skills") or [], auto=auto)
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
            return

        _log.info("工作流请求：wf=%s topic=%s", run.wf_id,
                  (run.ctx.get("topic") or "")[:40])
        self._start_ndjson()
        try:
            for ev in workflow.run_workflow(run.run_id):
                self._emit(ev)
            # 记忆沉淀（跨工作流延续教师风格）
            try:
                workflow.note_memory(run)
            except Exception:
                pass
        except Exception as e:
            _log.exception("工作流执行失败")
            self._emit({"type": "delta", "text": f"\n\n（生成出错：{e}）"})
            self._emit({"type": "done", "status": "failed"})
        self._end_ndjson()

    def _handle_workflow_resume(self, payload):
        """检查点人工决策后续跑（approve / redo / cancel）。"""
        run_id = (payload.get("run_id") or "").strip()
        decision = (payload.get("decision") or "approve").strip()
        feedback = (payload.get("feedback") or "").strip()
        if not run_id:
            self._send_json({"error": "缺少 run_id"}, 400)
            return
        self._start_ndjson()
        try:
            for ev in workflow.resume_workflow(run_id, decision, feedback=feedback):
                self._emit(ev)
        except Exception as e:
            _log.exception("工作流续跑失败")
            self._emit({"type": "delta", "text": f"\n\n（续跑出错：{e}）"})
            self._emit({"type": "done", "status": "failed"})
        self._end_ndjson()

    def _handle_workflow_run_get(self):
        """按 id 查询某次运行的持久化状态（供前端恢复进度/查看工件）。"""
        lid = self._query_id(self.path)
        d = workflow.get_run(lid)
        if d is None:
            self._send_json({"error": "运行不存在"}, 404)
        else:
            self._send_json(d)

    # ---------- 并行任务（多任务同时跑） ----------
    def _handle_job_run(self, payload):
        """提交一个后台任务（工作流 / 智能体），立即返回 job_id，前端轮询事件。"""
        kind = (payload.get("kind") or "workflow").strip()
        topic = (payload.get("topic") or "").strip()
        message = (payload.get("message") or "").strip()
        if kind == "workflow":
            wf_id = (payload.get("workflow") or "w1-lesson").strip()
            if not topic:
                self._send_json({"error": "请填写课题/单元"}, 400)
                return
            ok, why = guard.check_rate()
            if not ok:
                self._send_json({"error": why}, 429)
                return
            sg = safety.preprocess(topic, payload.get("student_info") or "")
            if not sg["allowed"]:
                self._send_json({"error": "输入未通过安全校验："
                                          + "；".join(sg["blocked"])}, 400)
                return
            subject = (payload.get("subject") or "").strip() or "语文"
            factory = jobs.make_workflow_job(
                wf_id, subject, (payload.get("grade") or "").strip(),
                sg["message"], student_info=sg["student"],
                extra=self._render_extra(payload.get("skills") or []),
                skills=payload.get("skills") or [],
                auto=bool(payload.get("auto", True)))
            title = "%s《%s》" % (subject, topic[:20])
        elif kind in ("agent", "chat"):
            if not message:
                self._send_json({"error": "消息不能为空"}, 400)
                return
            ok, why = guard.check_rate()
            if not ok:
                self._send_json({"error": why}, 429)
                return
            sg = safety.preprocess(message)
            if not sg["allowed"]:
                self._send_json({"error": "输入未通过安全校验："
                                          + "；".join(sg["blocked"])}, 400)
                return
            factory = jobs.make_agent_job(
                sg["message"], payload.get("expert", "auto"),
                extra=self._render_extra(payload.get("skills") or []),
                history=payload.get("history") or [])
            title = _clip_text(message, 24)
        else:
            self._send_json({"error": "未知任务类型：" + kind}, 400)
            return

        job = jobs.MANAGER.submit(kind, title, factory,
                                  meta={"workflow": payload.get("workflow", ""),
                                        "topic": topic or message[:30]})
        self._send_json({"ok": True, "job": job.to_dict()})

    def _handle_job_cancel(self, payload):
        jid = (payload.get("id") or "").strip()
        if not jid:
            self._send_json({"error": "缺少任务 id"}, 400)
            return
        ok = jobs.MANAGER.cancel(jid)
        self._send_json({"ok": ok} if ok else {"error": "任务不存在"},
                        200 if ok else 404)

    def _handle_job_events(self):
        """增量拉取任务事件：?id=xx&since=N。"""
        qs = parse_qs(urlparse(self.path).query)
        jid = (qs.get("id") or [""])[0].strip()
        try:
            since = int((qs.get("since") or ["0"])[0])
        except (TypeError, ValueError):
            since = 0
        job = jobs.MANAGER.get(jid)
        if job is None:
            self._send_json({"error": "任务不存在"}, 404)
            return
        self._send_json({"id": job.id, "status": job.status,
                         "seq": job.seq, "since": since,
                         "events": job.events_since(since)})

    # ---------- MCP 外接工具 ----------
    def _handle_mcp_save(self, payload):
        servers = payload.get("servers")
        if servers is None or not isinstance(servers, list):
            self._send_json({"error": "缺少 servers 列表"}, 400)
            return
        cfg = mcpool.POOL.load_config()
        cfg["servers"] = servers
        if not mcpool.POOL.save_config(cfg):
            self._send_json({"error": mcpool.POOL.last_error}, 500)
            return
        self._send_json(mcpool.POOL.sync(force=True))

    # ---------- 触发器（定时 / 回调） ----------
    def _handle_trigger_save(self, payload):
        action = (payload.get("action") or "create").strip()
        if action == "delete":
            ok = triggers.STORE.delete((payload.get("id") or "").strip())
            self._send_json({"ok": ok} if ok else {"error": "触发器不存在"},
                            200 if ok else 404)
            return
        if action == "update":
            t = triggers.STORE.update((payload.get("id") or "").strip(), payload)
            if t is None:
                self._send_json({"error": "触发器不存在"}, 404)
                return
            self._send_json({"ok": True, "trigger": t})
            return
        t = triggers.STORE.create(payload)
        self._send_json({"ok": True, "trigger": t})

    def _handle_trigger_fire(self, payload):
        name = (payload.get("name") or "").strip()
        tid = (payload.get("id") or "").strip()
        t = triggers.STORE.by_name(name) if name else triggers.STORE.get(tid)
        if t is None:
            self._send_json({"error": "触发器不存在"}, 404)
            return
        job, err = triggers.fire(t, payload=payload.get("payload"),
                                 reason="manual")
        if job is None:
            self._send_json({"error": err}, 400)
            return
        self._send_json({"ok": True, "job": job.to_dict()})

    def _handle_webhook(self):
        """外部系统回调入口：/api/hook/<触发器名>?token=xxx。"""
        name = unquote(self.path[len("/api/hook/"):].split("?")[0]).strip()
        try:
            body = self._read_body()
            payload = json.loads(body) if body else {}
        except (json.JSONDecodeError, ValueError):
            payload = {}
        qs = parse_qs(urlparse(self.path).query)
        token = (qs.get("token") or [""])[0] or payload.get("token") or ""
        auth_ok = hmac.compare_digest(self.headers.get("X-NexEdu-Token") or "",
                                      _TOKEN)
        if not auth_ok:
            t = triggers.STORE.by_name(name)
            if t is None or not hmac.compare_digest(str(token),
                                                    str(t.get("token") or "")):
                self._send_json({"error": "回调未授权"}, 403)
                return
        job, err = triggers.fire_by_name(name, payload=payload)
        if job is None:
            self._send_json({"error": err}, 404)
            return
        self._send_json({"ok": True, "job_id": job.id,
                         "status": job.status})

    # ---------- 专家 / 技能包 ----------
    def _handle_package_import(self, payload):
        data = payload.get("package") or payload
        stat = package.import_package(
            data,
            import_skills=bool(payload.get("import_skills", True)),
            import_triggers=bool(payload.get("import_triggers", False)))
        self._send_json(stat, 200 if stat.get("ok") else 400)

    def _handle_settings_save(self, payload):
        try:
            result = config.apply_settings(payload)
        except Exception as e:
            self._send_json({"error": str(e)}, 400)
            return
        self._send_json({"ok": True, "settings": result})

    def _handle_settings_test(self, payload):
        from .llm import LLMClient
        api_key = payload.get("api_key") or config.get_api_key()
        client = LLMClient(
            api_key=api_key,
            base_url=payload.get("base_url") or None,
            model=payload.get("model") or None,
        )
        ok, msg = client.test_connection()
        self._send_json({"ok": ok, "message": msg})

    def _handle_pick_folder(self):
        """弹出系统文件夹选择对话框，用于设置教案输出目录。"""
        try:
            from . import folder
        except ImportError:
            self._send_json({"ok": False, "error": "当前环境不支持"}, 400)
            return
        path, err = folder.pick_folder("选择教案输出文件夹（Word 将直接生成到这里）")
        if not path:
            self._send_json({"ok": False, "error": err or "已取消"}, 200)
            return
        saved = config.set_output_dir(path)
        self._send_json({"ok": True, "output_dir": saved})

    def _handle_feedback(self, payload):
        """教师反馈（设计文档 §14.1 可用性维度）。"""
        rating = (payload.get("rating") or "").strip().lower()
        if rating not in ("up", "down"):
            self._send_json({"error": "rating 需为 up 或 down"}, 400)
            return
        metrics.record_feedback(rating,
                                expert=(payload.get("expert") or "").strip(),
                                session_id=(payload.get("session_id") or "").strip(),
                                comment=payload.get("comment") or "")
        self._send_json({"ok": True})

    # ---------- Phase 3：评测基准与自我改进 ----------
    def _handle_evolve(self, payload):
        """自我改进闭环：只给建议 / 试跑 A/B / 采纳 / 撤销 / 恢复默认。

        默认 auto=False：任何采纳都要教师显式确认，且全部留痕可追溯。
        """
        from . import evolve
        action = (payload.get("action") or "state").strip()
        if action == "state":
            self._send_json({
                "knobs": [{"name": k, "desc": v["desc"], "value": v["_get"](),
                           "min": v["min"], "max": v["max"]}
                          for k, v in evolve.KNOBS.items()],
                "state": evolve.load_state(),
                "log": evolve.audit_log(20),
                "autopilot": evolve.autopilot_state(),
            })
        elif action == "propose":
            self._send_json(evolve.propose())
        elif action == "trial":
            knob = (payload.get("knob") or "").strip()
            try:
                delta = float(payload.get("delta") or 0)
            except (TypeError, ValueError):
                self._send_json({"error": "delta 需为数字"}, 400)
                return
            # auto_adopt 只有教师显式传 True 才生效（默认只试不采纳）
            r = evolve.trial(knob, delta, auto_adopt=bool(payload.get("adopt")))
            self._send_json(r if "error" not in r else r)
        elif action == "set":
            knob = (payload.get("knob") or "").strip()
            if knob not in evolve.KNOBS:
                self._send_json({"error": "未知旋钮"}, 400)
                return
            evolve.set_knob_logged(knob, payload.get("value"))
            self._send_json({"ok": True, "knobs": evolve.knob_values()})
        elif action == "revert":
            self._send_json(evolve.revert_last())
        elif action == "reset":
            self._send_json(evolve.reset_defaults())
        elif action == "set_auto":
            st = evolve.load_state()
            st["auto"] = bool(payload.get("auto"))
            evolve.save_state(st)
            self._send_json({"ok": True, "auto": st["auto"]})
        else:
            self._send_json({"error": f"未知动作：{action}"}, 400)

    def _handle_evals(self, payload):
        """跑评测：behavior（零模型调用、可复现，A/B 基线）/ quality（产出质量）。"""
        from . import evals
        if (payload.get("action") or "").strip() == "quality":
            self._send_json(evals.run_quality(limit=payload.get("limit")))
        else:
            self._send_json(evals.run())

    # ---------- Phase 2.3 教师画像 / 2.4 长程任务 ----------
    def _handle_profile(self, payload):
        """教师画像：查看 / 重新提炼 / 清除（教师对自己数据的控制权）。"""
        from . import memory
        action = (payload.get("action") or "get").strip()
        if action == "get":
            self._send_json({"profile": memory.get_profile(),
                             "working": memory.working_context()})
        elif action == "rebuild":
            self._send_json({"profile": memory.build_profile()})
        elif action == "clear":
            memory.clear_profile()
            memory.working_clear()
            self._send_json({"ok": True})
        elif action == "compact":
            self._send_json({"ok": True, **memory.compact_profile()})
        else:
            self._send_json({"error": f"未知动作：{action}"}, 400)

    # ---------- 班级学情 / 产出核验 / 多方案择优 ----------
    def _handle_classroom(self, payload):
        """班级档案：列表 / 查看 / 保存 / 追加观察 / 删除 / 清空。"""
        from . import classroom
        action = (payload.get("action") or "list").strip()
        if action == "list":
            self._send_json({"classes": classroom.list_classes(),
                             **classroom.summary_text()})
        elif action == "get":
            c = classroom.get_class(payload.get("name"))
            if c is None:
                self._send_json({"error": "班级不存在"}, 404)
            else:
                self._send_json({"class": c})
        elif action == "save":
            c, masked = classroom.save_class(
                payload.get("name"), payload.get("subject") or "",
                payload.get("grade") or "", payload.get("level") or "",
                payload.get("progress") or "", payload.get("weak_points"),
                replace=bool(payload.get("replace")))
            if c is None:
                self._send_json({"error": "班级名无效或数量超限"}, 400)
            else:
                self._send_json({"ok": True, "class": c, "masked": masked})
        elif action == "note":
            c, masked = classroom.add_note(payload.get("name"),
                                           payload.get("text") or "",
                                           payload.get("tag") or "")
            if c is None:
                self._send_json({"error": "班级不存在或内容为空"}, 400)
            else:
                self._send_json({"ok": True, "class": c, "masked": masked})
        elif action == "remove":
            self._send_json({"ok": classroom.remove_class(payload.get("name"))})
        elif action == "clear":
            classroom.clear_all()
            self._send_json({"ok": True})
        elif action == "context":
            self._send_json({"context": classroom.build_context()})
        else:
            self._send_json({"error": f"未知动作：{action}"}, 400)

    def _handle_verify(self, payload):
        """产出核验：对给定文本做确定性核对（也可由生成流程自动调用）。"""
        from . import verify
        text = payload.get("text") or ""
        if not text.strip():
            self._send_json({"error": "缺少待核验文本"}, 400)
            return
        result = verify.verify(text, kind=payload.get("kind"),
                               student_info=payload.get("student_info") or "")
        self._send_json({"ok": True, **result, "render": verify.render(result)})

    def _handle_options(self, payload):
        """多方案择优：生成候选教学路线并给出推荐与代价。"""
        from . import options
        result = options.generate(
            payload.get("message") or "", payload.get("subject") or "",
            payload.get("grade") or "", payload.get("topic") or "",
            payload.get("minutes"), payload.get("student_info") or "",
            payload.get("layered"))
        self._send_json({"ok": True, **result, "render": options.render(result)})

    def _handle_tasks(self, payload):
        """长程任务：新增 / 列表 / 取消 / 删除 / 立即执行。"""
        from . import tasks
        action = (payload.get("action") or "list").strip()
        if action == "list":
            self._send_json({"tasks": tasks.list_tasks(),
                             "due": [t["id"] for t in tasks.due_tasks()]})
        elif action == "add":
            t = tasks.add(
                title=(payload.get("title") or "").strip(),
                kind=(payload.get("kind") or "lesson").strip(),
                subject=(payload.get("subject") or "").strip(),
                grade=(payload.get("grade") or "").strip(),
                topic=(payload.get("topic") or "").strip(),
                student_info=(payload.get("student_info") or "").strip(),
                sched=payload.get("sched") if isinstance(payload.get("sched"), dict) else None)
            self._send_json({"ok": True, "task": t})
        elif action == "cancel":
            self._send_json({"ok": tasks.cancel(payload.get("id")) is not None})
        elif action == "remove":
            self._send_json({"ok": tasks.remove(payload.get("id"))})
        elif action == "run":
            tid = payload.get("id")
            if not tid:
                self._send_json({"error": "缺少 id"}, 400)
                return
            # 立即执行（同步）：备课任务耗时较长，界面上以「执行中」呈现
            self._send_json(tasks.run_task(tid))
        else:
            self._send_json({"error": f"未知动作：{action}"}, 400)

    def _handle_task_run(self, payload):
        """到期任务批量触发（也可由界面手动点「检查到期」）。"""
        from . import tasks
        self._send_json({"ran": tasks.tick()})

    # ---------- 自主度 / 联网 / 图片 / 任务分解 ----------
    def _handle_autonomy(self, payload):
        """自主度档位：查看 / 设置 / 紧急停止 / 撤销上一次自动动作。"""
        from . import autonomy
        action = (payload.get("action") or "get").strip()
        if action == "get":
            self._send_json({"state": autonomy.describe(),
                             "ledger": autonomy.ledger(20)})
        elif action == "set":
            lvl = payload.get("level")
            self._send_json({"ok": True, "state": autonomy.save(level=lvl)})
        elif action == "kill":
            self._send_json({"ok": True, "state": autonomy.save(kill=True)})
        elif action == "resume":
            self._send_json({"ok": True, "state": autonomy.save(kill=False)})
        elif action == "undo":
            self._send_json(autonomy.undo_last())
        else:
            self._send_json({"error": f"未知动作：{action}"}, 400)

    def _handle_web(self, payload):
        """联网检索：状态查询 / 检索（需教师已开启）。"""
        from . import websearch
        action = (payload.get("action") or "status").strip()
        if action == "status":
            self._send_json(websearch.status())
        elif action == "search":
            q = (payload.get("query") or "").strip()
            if not q:
                self._send_json({"error": "缺少检索词"}, 400)
                return
            if not websearch.available():
                self._send_json({"error": (websearch.status() or {}).get("message")
                                 or "联网检索未开启"}, 403)
                return
            self._send_json(websearch.lookup(q[:120],
                                             int(payload.get("fetch_pages") or 1)))
        else:
            self._send_json({"error": f"未知动作：{action}"}, 400)

    def _handle_vision(self, payload):
        """图片理解：状态查询 / 识别（需教师已开启）。"""
        from . import vision
        action = (payload.get("action") or "status").strip()
        if action == "status":
            self._send_json(vision.status())
        elif action == "describe":
            path = (payload.get("path") or "").strip()
            if not path:
                self._send_json({"error": "缺少图片路径"}, 400)
                return
            self._send_json(vision.describe(
                path, (payload.get("prompt") or "请描述这张图片的内容")[:500]))
        else:
            self._send_json({"error": f"未知动作：{action}"}, 400)

    def _handle_cache(self, payload):
        """会话缓存：列出 / 删除 / 清空。缓存不进知识库，用完可弃。"""
        from . import cache
        action = (payload.get("action") or "list").strip()
        if action == "list":
            self._send_json({"files": cache.list_files(),
                             "note": "缓存文档只在近期会话中作为参考，"
                                     "不计入知识库，可一键清空"})
        elif action == "remove":
            self._send_json({"ok": cache.remove((payload.get("name") or "").strip())})
        elif action == "clear":
            self._send_json({"ok": cache.clear()})
        else:
            self._send_json({"error": f"未知动作：{action}"}, 400)

    def _handle_cache_upload(self):
        """上传文档到缓存（不入库）。支持与知识库相同的日常格式。"""
        from urllib.parse import urlparse, parse_qs, unquote
        from . import cache
        qs = parse_qs(urlparse(self.path).query)
        name = os.path.basename(unquote((qs.get("name") or [""])[0]))
        if not name:
            self._send_json({"error": "缺少文件名"}, 400)
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > 30 * 1024 * 1024:
            self._send_json({"error": "文件超过 30MB 上限"}, 413)
            return
        data = self.rfile.read(length) if length > 0 else b""
        if not data:
            self._send_json({"error": "文件内容为空"}, 400)
            return
        n, why = cache.add(name, data)
        self._send_json({"ok": n > 0, "chunks": n, "name": name,
                         "warning": why or ""})

    def _handle_plan(self, payload):
        """任务分解：把复合请求拆成子任务（可一键放入长程任务队列）。"""
        from . import planner, tasks
        msg = (payload.get("message") or "").strip()
        if not msg:
            self._send_json({"error": "缺少请求内容"}, 400)
            return
        subs = planner.decompose(msg)
        if payload.get("enqueue") and subs:
            created = []
            for s in subs:
                created.append(tasks.add(
                    title=s["title"], kind="step",
                    subject=(payload.get("subject") or "").strip(),
                    grade=(payload.get("grade") or "").strip(),
                    topic=(payload.get("topic") or "").strip(),
                    depends=s.get("depends") or [], max_retry=2)["id"])
            self._send_json({"subtasks": subs, "enqueued": created})
            return
        self._send_json({"subtasks": subs, "compound": bool(subs)})

    # ---------- 知识库：上传 / 删除 ----------
    def _handle_kb_chunk(self):
        """按引用 ID 返回**单条**切片原文（引用可追溯）。

        刻意只返回该条、不提供全库遍历接口：教师能核对 AI 引用了什么，
        但无法借这个接口把知识库搬走（防爬取的要求）。
        """
        cid = (parse_qs(urlparse(self.path).query).get("id") or [""])[0].strip()
        item = knowledge.get_chunk(cid)
        if item is None:
            self._send_json({"error": "引用不存在或已失效"}, 404)
            return
        self._send_json(item)

    def _handle_kb_add(self):
        from urllib.parse import urlparse, parse_qs, unquote
        qs = parse_qs(urlparse(self.path).query)
        name = unquote((qs.get("name") or [""])[0])
        name = os.path.basename(name)  # 防路径穿越
        if not name or not name.lower().endswith(knowledge.SUPPORTED_EXT):
            self._send_json({"error": "支持 txt / md / docx / doc / pptx / pdf "
                                      "与图片（png/jpg/jpeg/bmp/tif）"}, 400)
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD_BYTES:
            self._send_json({"error": "文件超过 50MB 上限"}, 413)
            return
        data = self.rfile.read(length) if length > 0 else b""
        if not data:
            self._send_json({"error": "文件内容为空"}, 400)
            return
        user_dir = os.path.join(config.WORKSPACE_DIR, "knowledge")
        os.makedirs(user_dir, exist_ok=True)
        path = os.path.join(user_dir, name)
        with open(path, "wb") as f:
            f.write(data)
        try:
            n = knowledge.add_file(path)
        except Exception as e:
            self._send_json({"error": f"入库失败：{e}"}, 500)
            return
        if n == 0:
            # 把具体原因回传：图片缺 OCR 引擎、扫描版 PDF、旧版 .doc 各不相同，
            # 笼统说"未提取到文本"教师无从下手
            why = (knowledge._STATE.get("message") or "").strip()
            self._send_json({"ok": True, "chunks": 0, "name": name,
                             "warning": why or "未提取到文本（内容损坏或为扫描版）"})
        else:
            self._send_json({"ok": True, "chunks": n, "name": name})

    def _handle_kb_remove(self, payload):
        name = (payload.get("name") or "").strip()
        if not name:
            self._send_json({"error": "缺少文档名"}, 400)
            return
        knowledge.remove_doc(name)
        self._send_json({"ok": True})

    def _handle_kb_failed_clear(self, payload):
        """清除「导入失败」提示（教师手动确认后不再显示）。

        传 {name} 只删该条；不传 name 则清空全部。
        注：下次构建若该文件仍然失败，提示会重新出现——不做永久屏蔽，
        避免教师以为文件其实已经修好。
        """
        name = (payload.get("name") or "").strip()
        removed = knowledge.dismiss_failed(name or None)
        self._send_json({"ok": True, "removed": removed})

    # ---------- 教案沙盒：保存 / 查看 / 下载 / 导出 ----------
    @staticmethod
    def _query_id(path):
        from urllib.parse import urlparse, parse_qs, unquote
        qs = parse_qs(urlparse(path).query)
        return unquote((qs.get("id") or [""])[0]).strip()

    def _handle_lesson_save(self, payload):
        title = (payload.get("title") or "").strip()
        content = payload.get("content") or ""
        if not content.strip():
            self._send_json({"error": "教案内容为空"}, 400)
            return
        meta = payload.get("meta") or {}
        if not isinstance(meta, dict):
            meta = {}
        try:
            m = lessons.save_lesson(
                title or "未命名教案", content, meta,
                session_id=(payload.get("session_id") or "").strip() or None)
        except Exception as e:
            self._send_json({"error": f"保存失败：{e}"}, 500)
            return
        self._send_json({
            "ok": True, "id": m["id"], "title": m["title"],
            "docx": m.get("docx"), "created_at": m["created_at"],
        })

    def _handle_lesson_get(self):
        lid = self._query_id(self.path)
        m = lessons.get_lesson(lid)
        if m is None:
            self._send_json({"error": "教案不存在"}, 404)
        else:
            self._send_json(m)

    def _handle_lesson_download(self):
        lid = self._query_id(self.path)
        p = lessons.docx_path(lid)
        if p is None:
            self._send_json({"error": "Word 文件不存在"}, 404)
            return
        try:
            with open(p, "rb") as f:
                data = f.read()
        except OSError as e:
            self._send_json({"error": f"读取失败：{e}"}, 500)
            return
        self._send_binary(data, os.path.basename(p),
                          "application/vnd.openxmlformats-officedocument"
                          ".wordprocessingml.document")

    def _handle_lesson_remove(self, payload):
        lid = (payload.get("id") or "").strip()
        if not lid:
            self._send_json({"error": "缺少教案 id"}, 400)
            return
        ok = lessons.delete_lesson(lid)
        self._send_json({"ok": ok} if ok else {"error": "教案不存在"}, 200 if ok else 404)

    def _handle_export_docx(self, payload):
        """直接导出 Word（不保存沙盒）：{title, content, meta} → 文件字节。"""
        title = (payload.get("title") or "").strip() or "未命名文档"
        content = payload.get("content") or ""
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        try:
            data = lessons.export_docx_bytes(title, content, meta)
        except Exception as e:
            self._send_json({"error": f"生成 Word 失败：{e}"}, 500)
            return
        safe = title.replace("/", "_").replace("\\", "_").replace(":", "_")[:60]
        self._send_binary(data, safe + ".docx",
                          "application/vnd.openxmlformats-officedocument"
                          ".wordprocessingml.document")

    def _handle_export_pptx(self, payload):
        """直接导出 PPT 课件：{title, content} → 按标题层级自动分页。"""
        title = (payload.get("title") or "").strip() or "未命名演示"
        content = payload.get("content") or ""
        try:
            data = lessons.export_pptx_bytes(title, content)
        except Exception as e:
            self._send_json({"error": f"生成 PPT 失败：{e}"}, 500)
            return
        safe = title.replace("/", "_").replace("\\", "_").replace(":", "_")[:60]
        self._send_binary(data, safe + ".pptx",
                          "application/vnd.openxmlformats-officedocument"
                          ".presentationml.presentation")

    def _handle_export_svg(self, payload):
        """直接导出图表 SVG（不保存，返回文件字节）。

        补齐「输出多形态」：Word / PPT 之外的数据可视化矢量导出。
        payload: {type: bar|line|pie, title, labels:[], values:[], 可选 x_title/y_title/width/height}
        """
        from . import svgchart
        try:
            data = svgchart.bytes_of(payload)
        except Exception as e:
            self._send_json({"error": f"生成图表失败：{e}"}, 500)
            return
        title = (payload.get("title") or "").strip() or "图表"
        safe = title.replace("/", "_").replace("\\", "_").replace(":", "_")[:60]
        self._send_binary(data, safe + ".svg", "image/svg+xml")

    # ---------- 技能管理 ----------
    def _handle_skill_create(self, payload):
        try:
            skill = skills.create_skill(
                payload.get("name"), payload.get("desc"), payload.get("prompt"))
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
            return
        self._send_json({"ok": True, "skill": skill})

    def _handle_skill_delete(self, payload):
        skill_id = (payload.get("id") or "").strip()
        if not skill_id:
            self._send_json({"error": "缺少技能 id"}, 400)
            return
        try:
            skills.delete_skill(skill_id)
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
            return
        self._send_json({"ok": True})

    # ---------- 数据维护：路由建议 / 清理 / 提示词包 / 备份恢复 ----------
    def _handle_settings_suggest(self, payload):
        """一键建议：把 coordinator（闭环回流卡等轻任务）指向当前服务商的轻量模型。"""
        presets = {p["id"]: p for p in config.MODEL_PRESETS}
        p = presets.get(config.get_provider()) or presets.get("glm") or {}
        models = p.get("models") or []
        lite = next((m for m in models if any(
            k in m.lower() for k in ("flash", "lite", "mini", "air", "turbo",
                                     "-chat", "4-flash"))), None)
        if not lite:
            self._send_json({"ok": False,
                             "error": "当前服务商预设中没有找到明确的轻量模型，"
                                      "可在「模型路由」里手动填写（如 flash 系列）"})
            return
        config.apply_settings({"routing": {"coordinator": lite}})
        _log.info("已建议路由：coordinator -> %s", lite)
        self._send_json({"ok": True, "suggested": lite,
                         "settings": config.current()})

    def _handle_sessions_clear(self):
        """清除全部历史对话（含各对话产出的教案文件）。"""
        sids = [s["id"] for s in sessions.list_sessions()]
        files = 0
        for s in sids:
            try:
                files += lessons.delete_by_session(s)
            except Exception:
                pass
        n = sessions.clear_all()
        _log.info("清除历史对话：%d 个会话，%d 个产出文件", n, files)
        self._send_json({"ok": True, "sessions": n, "files": files})

    def _handle_lessons_cleanup(self, payload):
        """按时间批量清理教案（默认 30 天前），保留 Word/PPT 的联动删除。"""
        try:
            days = max(1, int(payload.get("days") or 30))
        except (TypeError, ValueError):
            days = 30
        removed = lessons.cleanup(days)
        self._send_json({"ok": True, "removed": removed, "days": days})

    def _handle_maintenance_backup(self):
        """导出全部本地数据为单个 zip（可选口令加密）。"""
        from . import maintenance
        qs = parse_qs(urlparse(self.path).query)
        password = (qs.get("password") or [""])[0]
        try:
            data, name = maintenance.build_backup(password)
        except Exception as e:
            self._send_json({"error": f"备份失败：{e}"}, 500)
            return
        self._send_binary(data, name, "application/octet-stream")

    def _handle_maintenance_restore(self):
        """从备份 zip（可选口令）恢复本地数据。body 为原始文件字节。"""
        from . import maintenance
        qs = parse_qs(urlparse(self.path).query)
        password = (qs.get("password") or [""])[0]
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 512 * 1024 * 1024:
            self._send_json({"error": "备份文件为空或超过 512MB"}, 400)
            return
        data = self.rfile.read(length)
        try:
            stat = maintenance.restore_backup(data, password)
        except Exception as e:
            self._send_json({"error": f"恢复失败：{e}"}, 400)
            return
        # 恢复后后台重建知识库索引与缓存
        try:
            threading.Thread(target=knowledge.build_all, daemon=True).start()
            from . import cache
            cache.load()
        except Exception:
            pass
        self._send_json({"ok": True, **stat})

    def _handle_maintenance_clear(self, payload):
        """清除全部本地个人数据（会话/教案/缓存/记忆/技能/评测数据）。
        用于转赠设备前的清痕；知识库资料保留（属教学资料而非个人数据）。"""
        if not payload.get("confirm"):
            self._send_json({"error": "需要 confirm:true 确认"}, 400)
            return
        from . import maintenance
        stat = maintenance.clear_personal_data()
        self._send_json({"ok": True, **stat})

    def _handle_prompts_pack(self):
        """导出已定制的提示词（口令加密，可跨机分发）。"""
        qs = parse_qs(urlparse(self.path).query)
        password = (qs.get("password") or [""])[0]
        if not password:
            self._send_json({"error": "请先设置导出口令"}, 400)
            return
        try:
            blob = package.export_prompt_pack(password)
        except Exception as e:
            self._send_json({"error": f"导出失败：{e}"}, 400)
            return
        self._send_binary(blob.encode("utf-8"),
                          "nexedu-prompts-%s.pack" % time.strftime("%Y%m%d"),
                          "application/octet-stream")

    def _handle_prompts_import(self):
        qs = parse_qs(urlparse(self.path).query)
        password = (qs.get("password") or [""])[0]
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 10 * 1024 * 1024:
            self._send_json({"error": "文件为空或过大"}, 400)
            return
        data = self.rfile.read(length).decode("utf-8", "ignore")
        try:
            stat = package.import_prompt_pack(data, password)
        except Exception as e:
            self._send_json({"error": f"导入失败：{e}"}, 400)
            return
        self._send_json({"ok": True, **stat})

    # ---------- 生命周期 ----------
    def _handle_shutdown(self):
        """前端窗口关闭/刷新时触发（sendBeacon）。延迟数秒退出：
        若 shutdown 之后发生过新的页面加载（刷新），则取消退出。"""
        global _shutdown_at
        self._send_json({"ok": True})
        _shutdown_at = time.time()

        def _watch():
            time.sleep(3.0)
            # 关闭请求之后没有新页面加载 → 确认是窗口关闭，退出程序
            if _last_page_load < _shutdown_at:
                os._exit(0)

        threading.Thread(target=_watch, daemon=True).start()

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return ""
        return self.rfile.read(length).decode("utf-8", "ignore")

    def log_message(self, fmt, *args):
        pass  # 静默访问日志（错误走 logging 落盘）

    def log_error(self, fmt, *args):
        _log.warning("HTTP %s", fmt % args)


def _bootstrap():
    """启动时的自恢复：旋钮写回、长程任务断点续跑、调度线程。

    刻意放在这里而不是模块导入时——导入时不该有副作用（测试也要能干净导入）。
    """
    try:
        from . import evolve
        evolve.apply_state()                 # 恢复教师已确认的旋钮值
    except Exception:
        pass
    try:
        from . import tasks
        tasks.resume_interrupted()           # 上次被打断的任务重置为待跑
        tasks.start_scheduler(interval=60)   # 进程内轮询，非常驻系统服务
    except Exception:
        pass
    try:
        from . import cache
        cache.load()                         # 恢复会话缓存（不进知识库）
    except Exception:
        pass
    try:
        triggers.start_scheduler()           # 定时触发（工作流引擎的调度入口）
    except Exception:
        pass
    try:
        mcpool.auto_start()                  # 有 mcp.json 才连，无配置则空转
    except Exception:
        pass


def make_server(host=None, port=None):
    """创建本地服务；端口被占用时向后顺延最多 20 个端口。

    返回 (server, 实际端口)。
    """
    _bootstrap()
    host = host or config.SERVER_HOST
    start = port or config.SERVER_PORT
    for p in range(start, start + 20):
        try:
            return ThreadingHTTPServer((host, p), Handler), p
        except OSError:
            continue
    raise RuntimeError(f"端口 {start}~{start + 19} 均被占用，无法启动本地服务")