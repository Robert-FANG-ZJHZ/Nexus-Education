"""联网检索（把"不联网"这条刻意取舍改为**教师显式授权后可用**）。

为什么现在可以做：离线优先的底线是"校本资料与学生数据不出本地"，
这与"教师主动发起一次公开资料的检索"并不冲突——关键是三件事必须成立：
  1. **默认关闭**：不配置就不会有任何出网请求；
  2. **教师知情**：开启需要显式操作，界面上每次联网检索都有标记；
  3. **发出去的东西可控**：检索词先脱敏（去学生姓名/手机号/学校名），
     只发查询词，**不发送**教案正文、校本文档、教师档案。

实现上不绑定某一家厂商：支持 SearXNG（可自建、最合规）/ Brave / Bing 三种
JSON 接口，配置写在 settings.json。抓取正文只用标准库，strip 掉脚本与样式。

任何一步失败都静默降级为"未取到网络资料"，绝不阻塞本地备课流程。
"""

import json
import os
import re
import urllib.parse
import urllib.request

from . import config

_TIMEOUT = 12
_MAX_RESULTS = 5
_MAX_PAGE_CHARS = 4000

_TAG = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_HTML_TAG = re.compile(r"<[^>]+>")


def settings():
    try:
        cur = config.current() or {}
    except Exception:
        cur = {}
    return {
        "enabled": bool(cur.get("web_enabled")),
        "provider": (cur.get("web_provider") or "searxng").strip(),
        "endpoint": (cur.get("web_endpoint") or "").strip(),
        "key": (cur.get("web_key") or "").strip(),
    }


def available():
    s = settings()
    return s["enabled"] and bool(s["endpoint"])


def status():
    s = settings()
    if not s["enabled"]:
        return {"ok": False,
                "message": "联网检索默认关闭。教师可在设置中显式开启；"
                           "开启后只发送脱敏后的检索词，不发送任何校本资料。"}
    if not s["endpoint"]:
        return {"ok": False, "message": "已开启但未配置检索端点（ SearXNG / Brave / Bing ）"}
    return {"ok": True, "message": f"已开启：{s['provider']}", "provider": s["provider"]}


def _desensitize(query):
    """发送前脱敏：去掉手机号、邮箱、可能的姓名与学校名。"""
    out = query or ""
    try:
        from . import guard
        if hasattr(guard, "redact_text"):
            out = guard.redact_text(out) or out
    except Exception:
        pass
    out = re.sub(r"1[3-9]\d{9}", "[手机号]", out)
    out = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[邮箱]", out)
    return out.strip()


def _get(url, headers=None, timeout=_TIMEOUT):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return raw.decode("utf-8", "ignore")


def search(query, topk=_MAX_RESULTS):
    """检索。返回 [{"title","url","snippet"}]，失败或未开启时返回 []。"""
    if not query or not available():
        return []
    s = settings()
    q = _desensitize(query)
    if not q:
        return []
    try:
        if s["provider"] == "searxng":
            url = (s["endpoint"].rstrip("/") + "/search?"
                   + urllib.parse.urlencode({"q": q, "format": "json",
                                             "language": "zh-CN"}))
            data = json.loads(_get(url))
            items = data.get("results") or []
            return [{"title": (i.get("title") or "")[:120],
                     "url": i.get("url") or "",
                     "snippet": (i.get("content") or "")[:300]}
                    for i in items[:topk]]
        if s["provider"] == "brave":
            url = ("https://api.search.brave.com/res/v1/web/search?"
                   + urllib.parse.urlencode({"q": q, "count": topk}))
            data = json.loads(_get(url, {"X-Subscription-Token": s["key"],
                                         "Accept": "application/json"}))
            items = ((data.get("web") or {}).get("results") or [])
            return [{"title": (i.get("title") or "")[:120],
                     "url": i.get("url") or "",
                     "snippet": (i.get("description") or "")[:300]}
                    for i in items[:topk]]
        if s["provider"] == "bing":
            url = ("https://api.bing.microsoft.com/v7.0/search?"
                   + urllib.parse.urlencode({"q": q, "count": topk}))
            data = json.loads(_get(url, {"Ocp-Apim-Subscription-Key": s["key"]}))
            items = ((data.get("webPages") or {}).get("value") or [])
            return [{"title": (i.get("name") or "")[:120],
                     "url": i.get("url") or "",
                     "snippet": (i.get("snippet") or "")[:300]}
                    for i in items[:topk]]
    except Exception:
        return []
    return []


def fetch(url, limit=_MAX_PAGE_CHARS):
    """抓取页面正文（纯文本）。失败返回空串。"""
    if not url or not available():
        return ""
    try:
        html = _get(url, {"User-Agent": "Mozilla/5.0 NexEdu"})
    except Exception:
        return ""
    html = _TAG.sub(" ", html)
    text = _HTML_TAG.sub(" ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def lookup(query, fetch_pages=1):
    """检索 + 可选抓正文。返回可注入上下文的资料块（自带来源与免责说明）。

    未开启时返回明确说明，而不是假装检索过——**不隐瞒"我没查"**。
    """
    if not available():
        return {"ok": False, "results": [],
                "text": "（联网检索未开启，本次仅依据本地知识库；"
                        "如需公开资料请在设置中开启并配置端点）"}
    results = search(query)
    if not results:
        return {"ok": True, "results": [],
                "text": "（已联网但未检到有效结果，本次依据本地知识库）"}
    if fetch_pages:
        for r in results[:fetch_pages]:
            body = fetch(r["url"])
            if body:
                r["body"] = body
    lines = ["# 联网检索结果（教师已授权；仅发送脱敏检索词，未发送任何校本资料）"]
    for i, r in enumerate(results, 1):
        line = f"{i}. 《{r['title']}》 {r['url']}"
        if r.get("snippet"):
            line += f"\n   摘要：{r['snippet']}"
        if r.get("body"):
            line += f"\n   正文节选：{r['body'][:800]}"
        lines.append(line)
    lines.append("注意：网络来源需教师自行甄别，不宜直接作为课标依据。")
    return {"ok": True, "results": results, "text": "\n".join(lines)}
