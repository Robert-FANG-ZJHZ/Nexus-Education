"""本地工具集：让专家不只是"会说"，而是真的"会做"（设计文档 §3.2 的 Tools 能力）。

工具由模型在输出中以 JSON 指令块的形式调用：

    ```tool
    {"tool": "search_knowledge", "args": {"query": "背影 教学目标"}}
    ```

执行结果以「观察」的形式回灌给模型，模型据此继续生成（ReAct 循环）。

全部工具均为本地实现、零第三方依赖、不联网；文件写入限制在 workspace 沙盒内，
不提供任何删除、执行外部程序或访问沙盒之外路径的能力。
"""

import json
import os
import re
import threading
import time

from . import config, knowledge, lessons, memory, pptxgen

# 工具调用块：```tool\n{json}\n```
TOOL_OPEN = "```tool"
TOOL_BLOCK_RE = re.compile(r"```tool\s*(\{.*?\})\s*```", re.S)

MAX_TOOL_TURNS = 3          # 单个任务最多执行几轮工具调用
MAX_OBSERVE_CHARS = 3000    # 单次观察回灌给模型的最大字符数


def _clip(text, limit=MAX_OBSERVE_CHARS):
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "\n…（结果已截断）"


# ---------------- 工具实现 ----------------

def search_knowledge(query="", topk=4):
    """检索本地知识库（教材、案例、内置教学理论）。"""
    hits = knowledge.search(query, topk=topk)
    if not hits:
        return "未检索到相关内容。"
    lines = []
    for i, (text, src) in enumerate(hits, 1):
        lines.append(f"{i}. {src} {_clip(text, 320)}")
    return "检索到 %d 条内容：\n" % len(hits) + "\n".join(lines)


def read_memory():
    """读取教师档案与近期任务记忆。"""
    core = (memory.get_core() or "").strip()
    daily = (memory.get_daily() or "").strip()
    if not core and not daily:
        return "暂无记忆记录。"
    return f"【教师档案】\n{_clip(core, 800)}\n\n【今日任务记录】\n{_clip(daily, 800)}"


def list_lessons(query="", limit=8):
    """列出教案沙盒中的历史教案（可按标题关键词过滤）。"""
    items = lessons.list_lessons()
    if query:
        items = [m for m in items if query in (m.get("title") or "")]
    if not items:
        return "教案沙盒中暂无历史教案。"
    lines = []
    for m in items[:limit]:
        t = time.strftime("%m-%d %H:%M", time.localtime(m.get("created_at") or 0))
        meta = m.get("meta") or {}
        scope = " · ".join(x for x in (meta.get("subject"), meta.get("grade"),
                                       meta.get("topic")) if x)
        lines.append(f"- [{m['id']}] {m.get('title','')}（{t}"
                     + (f"，{scope}" if scope else "") + "）")
    return "历史教案 %d 份：\n" % len(items) + "\n".join(lines)


def get_lesson(lesson_id=""):
    """读取某份历史教案的正文（前 3000 字）。"""
    m = lessons.get_lesson(lesson_id)
    if m is None:
        return "教案不存在或 id 无效。"
    meta = m.get("meta") or {}
    head = " · ".join(x for x in (meta.get("subject"), meta.get("grade"),
                                  meta.get("topic")) if x)
    return f"《{m.get('title','')}》" + (f"（{head}）" if head else "") \
        + "\n\n" + _clip(m.get("content") or "", 3000)


def save_lesson(title="", content=""):
    """把产出保存到教案沙盒（Markdown + Word 双格式）。"""
    if not (content or "").strip():
        return "保存失败：内容为空。"
    m = lessons.save_lesson(title or "未命名教案", content)
    return f"已保存到教案沙盒：{m['title']}（id={m['id']}，Word：{m.get('docx')}）"


def export_docx(title="", content=""):
    """生成 Word 文件字节并返回下载路径（不落沙盒，供即时下载）。"""
    if not (content or "").strip():
        return "导出失败：内容为空。"
    data = lessons.export_docx_bytes(title or "未命名文档", content)
    return "已生成 Word：%s（%d 字节，可在界面「导出 Word」下载）" % (
        (title or "未命名文档"), len(data))


def export_pptx(title="", content=""):
    """把教案 Markdown 生成 PPT 课件字节（按标题自动分页），供即时下载。

    适合教师说「把这份教案做成课件」「导出 PPT」时使用。
    """
    if not (content or "").strip():
        return "导出失败：内容为空。"
    data = lessons.export_pptx_bytes(title or "未命名演示", content)
    return "已生成 PPT 课件：%s（%d 页，可在界面「导出 PPT」下载）" % (
        (title or "未命名演示"), len(pptxgen.build_slides(title, content)))


def note_memory(text=""):
    """把一条要点写入今日记忆（供后续会话复用）。"""
    if not (text or "").strip():
        return "写入失败：内容为空。"
    memory.note(text.strip()[:200])
    try:
        from . import autonomy, memory as _m
        autonomy.record("note_memory", text.strip()[:60],
                        {"kind": "memory", "path": _m._daily_path(),
                         "line": "- " + text.strip()[:200]})
    except Exception:
        pass
    return "已写入今日记忆。"


def web_search(query="", fetch_pages=1):
    """联网检索公开资料（需教师在设置中显式开启）。

    只发送脱敏后的检索词，不发送教案正文、校本文档或教师档案。
    未开启时如实说明，不假装检索过。
    """
    from . import websearch
    if not (query or "").strip():
        return "请提供检索词。"
    r = websearch.lookup(query.strip()[:120], fetch_pages=int(fetch_pages or 1))
    return r.get("text") or "（未取到网络资料）"


def describe_image(path=""):
    """识别一张本地图片（板书/学生作业照片），需教师在设置中开启图片理解。

    图片只发给已配置的服务商，不写入任何本地知识库。
    """
    from . import vision
    if not (path or "").strip():
        return "请提供图片路径。"
    r = vision.describe(path.strip(), "请用中文客观描述这张图片的内容，"
                                      "若含文字请一并识别。")
    return r.get("text") or (r.get("error") or "识别失败")


# ---------------- 工具注册表（可扩展） ----------------
#
# 原先是硬编码字典：每加一个工具都要改 agents.py。改为注册表后，
# 新增能力（如联网检索、PPT 生成、代码执行）只需调用 register() 声明，
# 智能体侧无需改动——这是后续能力扩展的统一入口。

TOOLS = {}          # 注册表本体：name -> spec


def register(name, func, desc="", args=None, required=None,
             side_effect=False, retry=True):
    """注册一个工具。返回工具名。

    name         工具名（模型调用时使用）
    func         实现函数
    desc         给模型看的说明
    args         {参数名: 参数说明}
    required     必填参数名列表
    side_effect  是否会产生写入等副作用（落盘/改记忆）
    retry        参数不合法时是否允许清洗后重试一次
    """
    if not desc and func.__doc__:
        desc = func.__doc__.strip().splitlines()[0]
    TOOLS[name] = {
        "func": func,
        "desc": (desc or "").strip(),
        "args": dict(args or {}),
        "required": list(required or []),
        "side_effect": bool(side_effect),
        "retry": bool(retry),
    }
    return name


def unregister(name):
    """注销工具（便于按场景裁剪可用能力）。"""
    return TOOLS.pop(name, None)


def validate_args(name, args):
    """按注册信息校验参数。返回 (是否通过, 说明)。

    模型最常见的错误是多塞未知字段或漏掉必填项；明确报出来，
    既便于重试纠正，也让上层（动态重规划）能判断该工具是否可用。
    """
    spec = TOOLS.get(name)
    if not spec:
        return False, f"不存在的工具：{name}"
    args = args if isinstance(args, dict) else {}
    for key in (spec.get("required") or ()):
        v = args.get(key)
        if v is None or (isinstance(v, str) and not v.strip()):
            return False, f"缺少必填参数：{key}"
    known = set(spec.get("args") or {})
    unknown = [k for k in args if k not in known]
    if unknown:
        return False, "未知参数：" + "、".join(unknown)
    return True, ""


register("search_knowledge", search_knowledge,
         "检索本地知识库（教材原文、优秀案例、教学理论）",
         {"query": "检索关键词，如「背影 教学目标」", "topk": "可选，返回条数（默认4）"})
register("read_memory", read_memory,
         "读取教师档案与近期任务记忆", {})
register("list_lessons", list_lessons,
         "列出教案沙盒中的历史教案",
         {"query": "可选，标题关键词", "limit": "可选，条数（默认8）"})
register("get_lesson", get_lesson,
         "读取某份历史教案正文",
         {"lesson_id": "教案 id，来自 list_lessons"})
register("save_lesson", save_lesson,
         "把当前产出保存进教案沙盒（同时生成 Word）",
         {"title": "教案标题", "content": "Markdown 正文"},
         required=["content"], side_effect=True)
register("export_docx", export_docx,
         "导出 Word 文件供即时下载",
         {"title": "文档标题", "content": "Markdown 正文"},
         required=["content"], side_effect=True)
register("export_pptx", export_pptx,
         "把教案 Markdown 生成 PPT 课件（按标题自动分页）供下载",
         {"title": "课件标题", "content": "Markdown 正文"},
         required=["content"], side_effect=True)
register("web_search", web_search,
         "联网检索公开资料（需教师显式开启），参数：query、fetch_pages（可选）",
         {"query": "检索关键词", "fetch_pages": "抓取正文的条数，默认 1"},
         required=["query"], side_effect=False, retry=True)
register("describe_image", describe_image,
         "识别本地图片内容（板书/作业照片，需开启图片理解），参数：path",
         {"path": "图片文件绝对路径"},
         required=["path"], side_effect=False, retry=True)
register("note_memory", note_memory,
         "把一条要点写入今日记忆",
         {"text": "要点内容，≤200 字"},
         required=["text"], side_effect=True)


# ---------------- 子智能体委派（Sub-agent Delegation） ----------------
# 让专家在遇到自己不擅长的子问题时，把这块工作**派给另一位专家**，
# 拿到结论后再继续自己的主线——而不是一个人硬扛全部环节。
# 深度限制为 1（子智能体不能再往下派），避免无限递归把模型额度打爆。

MAX_DELEGATE_DEPTH = 1
MAX_DELEGATE_CHARS = 1500      # 回灌给主智能体的结论长度上限

_delegate_depth = threading.local()


def delegate(expert="", subtask="", context="", max_chars=MAX_DELEGATE_CHARS):
    """把子任务委派给另一位专家（子智能体），返回其结论。

    expert   目标专家 id：nexaim / nexdes / nexsca / coordinator
    subtask  要对方完成的具体子任务（一句话说清）
    context  交给对方的背景信息（≤800 字即可，不传全文）
    """
    from . import agents            # 延迟导入：agents 依赖 tools，避免循环
    depth = getattr(_delegate_depth, "value", 0)
    if depth >= MAX_DELEGATE_DEPTH:
        return "（委派已达深度上限，请基于现有信息自行完成该子任务。）"

    expert_id = (expert or "").strip().lower()
    valid = {e["id"] for e in agents.EXPERTS}
    if expert_id not in valid:
        return "（未知专家：%s。可选：%s）" % (expert or "空", "、".join(sorted(valid)))
    if not (subtask or "").strip():
        return "（缺少子任务描述：请用一句话说明要对方做什么。）"

    target = agents.get_expert(expert_id)
    prompt = ("你是%s（%s）。以下是主任务背景下需要你独立完成的一个子任务，"
              "请直接给出结论要点，不要寒暄、不要复述任务：\n\n"
              "【子任务】%s\n\n【背景】%s"
              % (target["name"], target["cn"], subtask.strip()[:400],
                 (context or "无").strip()[:800]))

    _delegate_depth.value = depth + 1
    try:
        parts = []
        for ev in agents.run_agent(expert_id, prompt,
                                   tools=["search_knowledge"], critique=False):
            if ev.get("type") == "delta":
                parts.append(ev["text"])
    except Exception as e:
        return "（委派执行失败：%s）" % e
    finally:
        _delegate_depth.value = depth

    text = "".join(parts).strip()
    if not text:
        return "（子智能体未给出内容，请自行完成该子任务。）"
    try:
        limit = int(max_chars)
    except (TypeError, ValueError):
        limit = MAX_DELEGATE_CHARS
    limit = max(200, min(limit, 4000))
    if len(text) > limit:
        text = text[:limit] + "\n…（子智能体结论已截断）"
    return "【%s 的结论】\n%s" % (target["name"], text)


register("delegate", delegate,
         "把一个子任务委派给另一位专家（子智能体），拿到结论后继续主线",
         {"expert": "目标专家 id：nexaim/nexdes/nexsca/coordinator",
          "subtask": "要对方完成的具体子任务（一句话）",
          "context": "交给对方的背景信息（可选，≤800字）",
          "max_chars": "可选，结论长度上限（默认1500）"},
         required=["expert", "subtask"], side_effect=False, retry=True)


# ---------------- 调用解析与执行 ----------------

def parse_calls(text):
    """从模型输出中解析工具调用指令。返回 [(工具名, 参数dict, 原始块)]。"""
    calls = []
    for raw in TOOL_BLOCK_RE.findall(text or ""):
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        name = (obj.get("tool") or "").strip()
        if name in TOOLS:
            args = obj.get("args")
            calls.append((name, args if isinstance(args, dict) else {}, raw))
    return calls


def execute(name, args):
    """执行单个工具，返回 (是否成功, 观察文本)。

    先按注册信息校验参数；若不合法且该工具允许重试，则剔除未知参数后重试一次
    （模型常会多塞字段），仍失败就给出结构化错误，交由动态重规划降级处理。
    """
    spec = TOOLS.get(name)
    if not spec:
        return False, f"不存在的工具：{name}"
    args = args if isinstance(args, dict) else {}

    # 自主度闸口：当前档位不允许自动执行的动作直接拦下并说明原因。
    # 智能体不会"绕过教师自己动手"——被拦下时如实告知，由动态重规划改走别的路径。
    try:
        from . import autonomy
        allowed, why = autonomy.can_execute(name, spec.get("side_effect", False))
        if not allowed:
            return False, f"{why}，本次未执行 {name}"
    except Exception:
        pass

    ok, why = validate_args(name, args)
    if not ok and spec.get("retry"):
        cleaned = {k: v for k, v in args.items()
                   if k in (spec.get("args") or {})}
        ok2, why2 = validate_args(name, cleaned)
        if ok2:
            args, ok, why = cleaned, True, ""
    if not ok:
        return False, f"工具 {name} 参数有误：{why}"

    try:
        result = spec["func"](**args)
    except TypeError as e:
        return False, f"工具 {name} 参数有误：{e}"
    except Exception as e:      # 工具内部异常不打断主流程
        return False, f"工具 {name} 执行失败：{e}"
    return True, str(result)


def strip_calls(text):
    """去掉输出中的工具调用块，只保留面向教师的正文。"""
    return TOOL_BLOCK_RE.sub("", text or "").strip()


def prompt_description(names=None):
    """生成注入 System Prompt 的工具说明。"""
    picked = names or list(TOOLS)
    lines = ["# 可用工具（需要时调用，不需要则不要调用）",
             "调用方式：在回答中插入如下代码块，系统会自动执行并把结果回给你：",
             "",
             "```tool",
             '{"tool": "工具名", "args": {"参数名": "参数值"}}',
             "```",
             "",
             f"规则：一次只调用一个工具；拿到结果后再继续；最多调用 {MAX_TOOL_TURNS} 次；"
             "工具结果只作为依据，最终仍由教师确认。",
             ""]
    for name in picked:
        spec = TOOLS.get(name)
        if not spec:
            continue
        req = spec.get("required") or ()
        arg_desc = "、".join(
            (f"**{k}**(必填)" if k in req else k) + f"（{v}）"
            for k, v in spec["args"].items())
        lines.append(f"- **{name}**：{spec['desc']}" + (
            f" —— 参数：{arg_desc}" if arg_desc else ""))
    return "\n".join(lines)
