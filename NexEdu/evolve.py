"""自我改进闭环（设计文档 Phase 3.1）——本轮风险最高的一项，因此规则从严。

核心原则：**先评测后采纳 + 可回滚 + 改动留痕 + 默认人工确认**。

它改的是什么：
不是让模型"自己改写自己的提示词"（那样既不可控也难追责），而是一组**有边界的
可调旋钮**：对齐度阈值、协商议题上限、覆盖率下限、作业负担预警线。每条旋钮都有
明确的取值区间与语义，改完能在评测集上测出差别。

闭环流程：
  1. 以质量看板的 weakest（最常被判需修订的产出类型）为触发，生成候选改动；
  2. 在**固定评测集**（evals.py，零模型调用、可复现）上跑 A/B：
     应用候选 → 重跑评测 → 与基线对比；
  3. 判定：分数**严格提升**才采纳；不升或下降一律自动回滚；
  4. 每一次试验都写入审计日志，教师可查看、可撤销、可一键恢复默认。

默认 `auto=False`：只给建议，采纳需教师显式确认。这是刻意的——教育场景里
"AI 自己改自己"必须有人的确认，且必须留痕可追责。
"""

import json
import os
import threading
import time

from . import config, evals

_DIR = os.path.join(config.WORKSPACE_DIR, "evolve")
_STATE_PATH = os.path.join(_DIR, "state.json")
_LOG_PATH = os.path.join(_DIR, "log.jsonl")
_MAX_LOG = 200


def _ensure():
    os.makedirs(_DIR, exist_ok=True)


# ---------------- 可调旋钮 ----------------

def _knob_agents(name):
    def get():
        from . import agents
        return getattr(agents, name)

    def set_(v):
        from . import agents
        setattr(agents, name, v)
    return get, set_


def _knob_negotiation(name):
    def get():
        from . import negotiation
        return getattr(negotiation, name)

    def set_(v):
        from . import negotiation
        setattr(negotiation, name, v)
    return get, set_


KNOBS = {
    "align_threshold": {
        "desc": "目标-评价对齐度阈值：低于此值即触发专家协商。调高→更早发现问题，但可能误报。",
        "min": 0.05, "max": 0.60, "step": 0.05, "fmt": float,
        "_get": _knob_agents("ALIGN_THRESHOLD")[0],
        "_set": _knob_agents("ALIGN_THRESHOLD")[1],
    },
    "des_cover_min": {
        "desc": "目标片段在教学设计中的覆盖率下限：低于此值判定「设计未承接目标」。",
        "min": 0.05, "max": 0.50, "step": 0.05, "fmt": float,
        "_get": _knob_negotiation("DES_COVER_MIN")[0],
        "_set": _knob_negotiation("DES_COVER_MIN")[1],
    },
    "max_issues": {
        "desc": "单轮协商最多处理几个冲突议题。调高→更彻底，但更慢更费额度。",
        "min": 1, "max": 4, "step": 1, "fmt": int,
        "_get": _knob_negotiation("MAX_ISSUES")[0],
        "_set": _knob_negotiation("MAX_ISSUES")[1],
    },
    "max_exercises": {
        "desc": "作业题量预警线（题）。调低→更早提示减负。",
        "min": 5, "max": 30, "step": 1, "fmt": int,
        "_get": _knob_negotiation("MAX_EXERCISES")[0],
        "_set": _knob_negotiation("MAX_EXERCISES")[1],
    },
}


def knob_values():
    return {k: v["_get"]() for k, v in KNOBS.items()}


# 出厂值：在模块导入时（应用任何持久化改动之前）快照一次，供「恢复默认」使用
_FACTORY = knob_values()


def set_knob(name, value):
    spec = KNOBS.get(name)
    if not spec:
        raise KeyError(name)
    v = spec["fmt"](value)
    v = max(spec["min"], min(spec["max"], v))
    spec["_set"](v)
    return v


# ---------------- 状态与日志 ----------------

def _defaults():
    return {"auto": False, "knobs": knob_values(), "last_run": None}


def load_state():
    _ensure()
    if os.path.exists(_STATE_PATH):
        try:
            with open(_STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return _defaults()


def save_state(state):
    _ensure()
    state["knobs"] = knob_values()
    with open(_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def audit_log(limit=30):
    if not os.path.exists(_LOG_PATH):
        return []
    try:
        with open(_LOG_PATH, "r", encoding="utf-8") as f:
            lines = [json.loads(l) for l in f if l.strip()]
    except Exception:
        return []
    return lines[-limit:]


def _log(entry):
    _ensure()
    with open(_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    # 日志不无限增长
    try:
        with open(_LOG_PATH, "r", encoding="utf-8") as f:
            lines = [l for l in f if l.strip()]
        if len(lines) > _MAX_LOG:
            with open(_LOG_PATH, "w", encoding="utf-8") as f:
                f.writelines(lines[-_MAX_LOG:])
    except Exception:
        pass


def apply_state():
    """启动时把持久化的旋钮值写回模块（否则重启后改动丢失）。"""
    st = load_state()
    for k, v in (st.get("knobs") or {}).items():
        if k in KNOBS:
            try:
                set_knob(k, v)
            except Exception:
                pass
    return st


# ---------------- 候选生成 ----------------

def propose():
    """以质量看板的 weakest 为触发，生成候选改动（只给建议，不改任何东西）。"""
    try:
        from . import metrics
        summary = metrics.summary()
    except Exception:
        summary = {}
    weakest = (summary.get("quality") or {}).get("weakest") or {}
    expert = weakest.get("expert") or ""
    rate = weakest.get("revise_rate")

    cands = []
    if expert in ("nexaim", "nexsca"):
        # 目标/评价类产出最常被判需修订：收紧对齐判定，让问题更早被协商抓住
        cands.append({"knob": "align_threshold", "delta": +0.05,
                      "reason": f"「{expert}」产出需修订率 {rate}，"
                                f"提高对齐阈值以更早触发协商"})
    if expert == "nexdes":
        cands.append({"knob": "des_cover_min", "delta": +0.05,
                      "reason": f"「{expert}」产出需修订率 {rate}，"
                                f"提高覆盖率下限以更早发现设计与目标脱节"})
    if (summary.get("feedback") or {}).get("down", 0) >= 3:
        cands.append({"knob": "max_issues", "delta": +1,
                      "reason": "教师负面反馈偏多，允许单轮多处理一个冲突议题"})
    if not cands:
        cands.append({"knob": "max_issues", "delta": +1,
                      "reason": "暂无明确薄弱环节，默认尝试增强协商覆盖"})
    return {"weakest": weakest, "candidates": cands}


# ---------------- A/B 试验 ----------------

def trial(knob, delta, auto_adopt=False):
    """在评测集上做一次 A/B：应用 → 重跑 → 判定 → 采纳或回滚。

    判定规则（从严）：
      - 新分数 > 基线 → 采纳；
      - 新分数 ≤ 基线 → 立即回滚（不允许"没坏就留着"，避免无证据的漂移）；
      - 评测跑不起来（异常）→ 回滚。

    auto_adopt=False 时**默认只试不采纳**：返回建议结果，由教师确认后再 apply。
    """
    if knob not in KNOBS:
        return {"error": f"未知旋钮：{knob}"}
    spec = KNOBS[knob]
    old = spec["_get"]()
    new = spec["fmt"](old + delta)
    new = max(spec["min"], min(spec["max"], new))
    if new == old:
        return {"error": f"旋钮 {knob} 已到边界（{old}），无可调空间"}

    baseline = evals.run()                       # 基线
    set_knob(knob, new)
    try:
        after = evals.run()                      # 候选
    except Exception as e:
        set_knob(knob, old)
        return {"error": f"评测失败已回滚：{e}"}

    improved = after["score"] > baseline["score"]
    adopted = False
    if improved and auto_adopt:
        adopted = True
    else:
        set_knob(knob, old)                      # 回滚（含"无提升"的情形）

    entry = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "knob": knob, "from": old, "to": new,
        "baseline": baseline["score"], "after": after["score"],
        "improved": improved, "adopted": adopted,
        "auto": bool(auto_adopt),
        "cases_fixed": [c["id"] for c in after["cases"] if c["ok"]]
                       and [c["id"] for c in baseline["cases"] if not c["ok"]
                            and any(x["id"] == c["id"] and x["ok"]
                                    for x in after["cases"])] or [],
        "cases_broken": [c["id"] for c in baseline["cases"] if c["ok"]
                         and not any(x["id"] == c["id"] and x["ok"]
                                     for x in after["cases"])],
    }
    _log(entry)
    st = load_state()
    st["last_run"] = entry
    save_state(st)

    return {"entry": entry, "baseline": baseline, "after": after,
            "adopted": adopted, "current": knob_values()}


def set_knob_logged(name, value, note="教师手动调整"):
    """教师手动调整旋钮：同样留痕，保证任何改动都可追溯。"""
    before = KNOBS[name]["_get"]() if name in KNOBS else None
    v = set_knob(name, value)
    _log({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "knob": name,
          "from": before, "to": v, "baseline": None, "after": None,
          "improved": None, "adopted": True, "auto": False, "note": note})
    st = load_state()
    st["last_run"] = {"knob": name, "from": before, "to": v,
                      "adopted": True, "note": note}
    save_state(st)
    return v


def revert_last():
    """撤销最近一次被采纳的改动（按日志回退）。"""
    logs = audit_log(200)
    for e in reversed(logs):
        if e.get("adopted") and e.get("knob") in KNOBS:
            old, knob = e.get("from"), e["knob"]
            cur = KNOBS[knob]["_get"]()
            set_knob(knob, old)
            _log({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "knob": knob,
                  "from": cur, "to": old, "baseline": None, "after": None,
                  "improved": None, "adopted": True, "auto": False,
                  "note": "教师撤销"})
            return {"reverted": knob, "to": old}
    return {"reverted": None}


def reset_defaults():
    """恢复所有旋钮到出厂值（当前进程默认值）。"""
    before = knob_values()
    for k, v in _FACTORY.items():
        if k in KNOBS:
            set_knob(k, v)
    _log({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "knob": "*", "from": before,
          "to": knob_values(), "baseline": None, "after": None,
          "improved": None, "adopted": True, "auto": False, "note": "恢复默认"})
    return {"before": before, "after": knob_values()}


# ---------------- 后台自进化（教师无感，但全程留痕）----------------
#
# 设计要点：
# - **节流**：每次对话都跑评测会拖慢响应，因此累积 MIN_CALLS 次活动、
#   或距上次至少 MIN_INTERVAL 秒，才在后台跑一次；
# - **后台线程**：不阻塞教师当前这次对话的输出；
# - **范围受限**：只按 propose() 给出的**数值旋钮**候选去 trial，
#   且 trial() 内部坚持"分数严格提升才采纳"，所以自动也不会劣化；
# - **不隐藏结果**：虽然教师不必操作，每一次自动试验都进审计日志，
#   在「自进化」页可查、可撤销。这符合"可控自主"——不打扰 ≠ 不可见。

MIN_CALLS = 5            # 累积多少次对话/备课才考虑跑一次
MIN_INTERVAL = 600       # 或距上次运行至少多少秒
_MAX_CANDIDATES = 1      # 每次后台最多试几个候选（控制开销）

_AUTOPILOT = {"thread": None, "calls": 0, "last": 0.0, "runs": 0,
              "last_result": None}


def autopilot_state():
    return {"calls": _AUTOPILOT["calls"], "runs": _AUTOPILOT["runs"],
            "last": _AUTOPILOT["last"], "min_calls": MIN_CALLS,
            "min_interval": MIN_INTERVAL,
            "running": bool(_AUTOPILOT["thread"]
                            and _AUTOPILOT["thread"].is_alive()),
            "last_result": _AUTOPILOT["last_result"]}


def _autopilot_run():
    """后台执行一次自进化：取候选 → A/B → 达标才采纳。"""
    try:
        prop = propose()
        cands = (prop.get("candidates") or [])[:_MAX_CANDIDATES]
        results = []
        for c in cands:
            knob, delta = c.get("knob"), c.get("delta")
            if knob not in KNOBS:
                continue
            r = trial(knob, delta, auto_adopt=True)      # 无提升会自动回滚
            if "entry" in r:
                e = dict(r["entry"])
                e["reason"] = c.get("reason", "")
                results.append(e)
        _AUTOPILOT["runs"] += 1
        _AUTOPILOT["last_result"] = results[-1] if results else None
    except Exception:
        pass                                  # 后台失败绝不影响前台


def reset_autopilot():
    """重置节流计数（供测试与界面「立即重新计数」使用）。

    注意：它只重置计数与时间戳，**不动旋钮**，也不清审计日志——
    已发生的改动必须留痕，不能借"重置"抹掉。
    """
    _AUTOPILOT["calls"] = 0
    _AUTOPILOT["last"] = 0.0
    return autopilot_state()


def note_activity():
    """前台每次对话/备课结束时调用一次。满足条件就在后台跑一轮自进化。

    返回是否真正启动了一轮（供测试与日志观察）。
    """
    _AUTOPILOT["calls"] += 1
    now = time.time()
    th = _AUTOPILOT["thread"]
    if th and th.is_alive():
        return False                          # 上一轮还没跑完
    # 触发条件取「或」：累积够次数，或距上次够久。
    # 注意 last=0（从未跑过）时不能走间隔分支——否则 now-0 恒大于间隔，
    # 节流会失效、第一次对话就跑。首次必须累积够 MIN_CALLS 次。
    by_interval = bool(_AUTOPILOT["last"]) and (now - _AUTOPILOT["last"] >= MIN_INTERVAL)
    if not (by_interval or _AUTOPILOT["calls"] >= MIN_CALLS):
        return False
    _AUTOPILOT["calls"] = 0
    _AUTOPILOT["last"] = now
    t = threading.Thread(target=_autopilot_run, daemon=True,
                         name="nexedu-autopilot")
    _AUTOPILOT["thread"] = t
    t.start()
    return True
