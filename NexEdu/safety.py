"""内容安全与数据合规（设计文档 §12.1 / §12.2 的落地实现）。

三层防护，全部为本地规则式处理，不依赖第三方服务、不额外联网：

1. **输入守卫**：识别提示词注入 / 越狱意图（要求忽略既有指令、索取系统提示词、
   扮演无限制角色等），命中即拦截并给出说明，同时写审计日志。
2. **学生信息脱敏**：在调用模型**之前**替换姓名、学号、身份证号、手机号、住址等
   可识别信息，避免敏感数据出域；替换结果随会话保存，原文不落盘。
3. **输出复核**：检测「承诺提分/升学」「公开个体排名」「对学生绝对化断言」
   「涉医涉心理诊断」等高风险表述，命中时在结果尾部追加教师复核提示。

边界说明：规则只能覆盖可枚举的高频风险，政治性、科学性与教育性的最终判断
仍由教师负责；本模块不宣称替代人工审核，也不对通过检查的内容作正确性背书。
"""

import logging
import re
import time

_log = logging.getLogger("nexedu.safety")

# ---------------- 输入守卫：提示词注入 / 越狱 ----------------

_INJECTION_PATTERNS = [
    (r"(忽略|无视|忘记|跳过)(上面|以上|之前|前面|所有|全部的)?[^。；;\n]{0,6}"
     r"(指令|提示|规则|约束|设定|要求)", "要求忽略既有指令"),
    (r"(输出|显示|打印|重复|复述|泄露|导出|告诉我)[^。；;\n]{0,8}"
     r"(系统提示词|系统提示|提示词|system\s*prompt|初始设定|预设指令)", "索取系统提示词"),
    (r"(你现在是|从现在起你是|你现在扮演|你要扮演)[^。；;\n]{0,20}"
     r"(不受限制|无限制|没有限制|不必遵守|无视规则)", "要求扮演无约束角色"),
    (r"(越狱|jailbreak|\bDAN\b|开发者模式)", "越狱相关表述"),
    (r"(绕过|突破|解除)[^。；;\n]{0,8}(限制|约束|安全|审核|过滤)", "试图绕过安全限制"),
    (r"(reveal|show|print|repeat)\s+(me\s+)?(your\s+)?(system\s+)?prompt", "索取系统提示词"),
    (r"(不要遵守|不必遵守|无需遵守)[^。；;\n]{0,8}(规则|约束|限制|要求)", "要求不遵守规则"),
    # —— 索取 / 复述内部指令（防专家指令泄露）——
    (r"(你的|您的|你收到的)[^。；;\n]{0,6}"
     r"(系统提示|系统指令|提示词|指令|设定|配置|人设|角色设定|prefill)", "索取系统指令"),
    (r"(复述|重复|原样|一字不差|逐字|照抄|搬出|贴出)[^。；;\n]{0,10}"
     r"(上面|以上|之前|前面|你收到|你的)[^。；;\n]{0,6}(指令|提示|设定|内容|文字|话)", "要求复述内部指令"),
    (r"(translate|翻译)[^。；;\n]{0,16}(prompt|instruction|system\s*message|指令)", "诱导翻译内部指令"),
    (r"(以|用)\s*(json|yaml|xml|base64|markdown)?[^。；;\n]{0,6}"
     r"(输出|返回|给出)[^。；;\n]{0,8}(你的|完整的)?[^。；;\n]{0,6}"
     r"(系统|初始|预设)?(提示词|指令|设定|配置)", "诱导结构化导出内部指令"),
    (r"(how\s+(are|were)\s+you\s+(configured|prompted)|your\s+instructions)", "索取系统指令"),
]

# ---------------- 学生信息脱敏 ----------------

# 注意：Python 的 \b 把中文也当作词字符，"手机138…" 之间不存在词边界，
# 因此数字类模式一律用 (?<!\d)/(?!\d) 而不是 \b。
_PII_PATTERNS = [
    (r"(?<!\d)\d{17}[\dXx](?!\d)", "身份证", "【身份证-已脱敏】"),
    (r"(?<!\d)1[3-9]\d{9}(?!\d)", "手机号", "【手机号-已脱敏】"),
    (r"(学号|学籍号)\s*[:：]?\s*\d{4,}", "学号", "学号：【已脱敏】"),
    (r"(姓名|学生姓名|学生)\s*[:：]\s*[\u4e00-\u9fff]{2,4}", "姓名", "姓名：【已脱敏】"),
    (r"(家长|监护人|父亲|母亲)\s*[:：]\s*[\u4e00-\u9fff]{2,4}", "家长姓名",
     "家长：【已脱敏】"),
    (r"(家庭住址|住址|家庭地址|现住址)\s*[:：]?\s*[^\s，。；;\n]{4,}", "住址",
     "住址：【已脱敏】"),
]

# 说明：自由文本中的姓名（如「张三同学基础较弱」）无法与「请同学们朗读」这类
# 普通表述可靠区分——误脱敏会破坏教师输入的语义，因此只处理带标签的字段
# （姓名：/学生：/家长：），其余依赖教师自行避免在输入中写明真实姓名。

# ---------------- 输出复核 ----------------

_OUTPUT_RULES = [
    (r"(保证|承诺|确保|一定能|必定|必然)[^。；;\n]{0,8}"
     r"(提分|提\d+分|涨\d+分|考上|录取|升学|及格|满分)",
     "出现「承诺提分/升学」类表述，按规范不得承诺结果"),
    (r"(公开|公布|张贴|发到群里|发群)[^。；;\n]{0,6}(排名|名次|成绩单)",
     "涉及公开学生个体排名，需改为个别反馈"),
    (r"(倒数第[一二三四五六七八九十]|成绩最差|垫底|拖后腿|差生|蠢|笨|无可救药)",
     "含对学生个体的贬抑性标签，请改为描述具体行为"),
    (r"(永远|根本|一辈子|注定)[^。；;\n]{0,6}(学不会|不可能|没希望|考不上|改不了)",
     "含对学生发展的绝对化断言，请改为阶段性描述"),
    (r"(确诊|诊断为|抑郁症|多动症|自闭症|焦虑症)",
     "涉及心理或医学判断，应由专业人员评估，AI 仅可提示观察现象"),
    (r"(建议|应当|必须|需要)[^。；;\n]{0,12}(服药|用药|就医|看心理医生|去医院)",
     "涉及医疗建议，需转介校医或专业机构"),
]

REVIEW_BANNER = ("\n\n---\n> ⚠️ **内容复核提示**（AI 生成，仅供教学参考，"
                 "教师确认后方可采用）\n")

_audit = []          # 最近若干条安全事件（进程内），供 /api/metrics 查看
_MAX_AUDIT = 100


def _audit_event(kind, detail):
    item = {"ts": time.time(), "kind": kind, "detail": detail}
    _audit.append(item)
    if len(_audit) > _MAX_AUDIT:
        del _audit[0]
    _log.warning("safety[%s] %s", kind, detail)
    return item


def log_event(kind, detail):
    """公共审计入口（供 guard 等模块写入统一审计流）。"""
    return _audit_event(kind, detail)


def audit_log():
    """返回最近的安全事件（倒序）。"""
    return list(reversed(_audit))


def guard_input(text):
    """输入守卫。返回 (是否放行, 命中说明列表)。命中即建议拦截。"""
    if not text:
        return True, []
    hits = []
    for pattern, desc in _INJECTION_PATTERNS:
        if re.search(pattern, text, re.I):
            hits.append(desc)
    if hits:
        _audit_event("injection", "；".join(hits))
        return False, hits
    return True, []


def mask_pii(text):
    """学生信息脱敏。返回 (脱敏后文本, 命中的信息类型列表)。"""
    if not text:
        return text, []
    found = []
    out = text
    for pattern, kind, repl in _PII_PATTERNS:
        new, n = re.subn(pattern, repl, out)
        if n:
            out = new
            found.append(f"{kind}×{n}")
    if found:
        _audit_event("mask", "、".join(found))
    return out, found


def review_output(text):
    """输出复核。返回需要提示教师的风险说明列表。"""
    if not text:
        return []
    warnings = []
    for pattern, desc in _OUTPUT_RULES:
        if re.search(pattern, text, re.I):
            warnings.append(desc)
    if warnings:
        _audit_event("output", "；".join(warnings))
    return warnings


def citation_warnings(text):
    """引用校验：产出里 [来源:xxx] 的 ID 是否真实存在于知识库。

    假引用比不引用更危险——它让编造的课标出处看起来有据可查。因此凡无法在
    本地知识库中核对到的 ID，一律提示教师人工复核。
    """
    if not text:
        return []
    try:
        from . import knowledge
        valid, invalid = knowledge.validate_citations(text)
    except Exception:
        return []
    if not invalid:
        return []
    return ["检测到 %d 处无法核对的引用来源，已剔除其标记，请人工核对相关论断"
            % len(set(invalid))]


def banner(warnings):
    """把风险说明渲染为追加在输出末尾的提示块。"""
    if not warnings:
        return ""
    lines = [REVIEW_BANNER.rstrip("\n")]
    for w in warnings:
        lines.append(f"> - {w}")
    return "\n".join(lines) + "\n"


def preprocess(message, student_info=""):
    """调用模型前的统一处理。

    返回 dict：
      allowed   是否放行
      blocked   拦截原因列表（allowed=False 时有效）
      message   脱敏后的用户输入
      student   脱敏后的学情补充
      masked    本次脱敏命中的类型
    """
    allowed, reasons = guard_input(message)
    masked_msg, m1 = mask_pii(message)
    masked_stu, m2 = mask_pii(student_info)
    return {
        "allowed": allowed,
        "blocked": reasons,
        "message": masked_msg,
        "student": masked_stu,
        "masked": m1 + m2,
    }
