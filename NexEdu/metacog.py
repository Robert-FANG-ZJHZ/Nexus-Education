"""元认知：让智能体能判断"我该不该换策略"（设计文档 Phase 3.2）。

做法很朴素，刻意不用模型来"反思"：
把每次执行的**策略 + 结果**（自检是否判需修订）记进本地流水，
当某个策略的历史返工率明显更高，而备选策略样本足够且明显更好时，才建议切换。

从严的三条边界（防止它自作主张）：
1. **样本门槛**：备选策略至少 MIN_SAMPLES 次记录才参与比较，避免两次偶然就换；
2. **增益门槛**：备选要比当前好 MIN_GAIN 以上才算"明显更优"；
3. **可切换范围有限**：只在 single ↔ lookup 之间切（两者成本接近）；
   绝不自动升到 pipeline（成本翻倍），也绝不自动把整课时备课降级为单步——
   教学闭环的完整性优先于省事。

切换会在计划里标注 `metacog` 字段，界面可见：**换策略这件事必须让教师看见**。
"""

import json
import os
import threading

from . import config

_PATH = os.path.join(config.WORKSPACE_DIR, "metrics", "strategy.jsonl")
_LOCK = threading.Lock()

MIN_SAMPLES = 5         # 参与比较的最小样本数
MIN_GAIN = 0.20         # 明显的增益门槛（返工率差 20 个百分点以上）
SWITCHABLE = {"single", "lookup"}     # 只允许在这两个策略间切换


def _ensure():
    os.makedirs(os.path.dirname(_PATH), exist_ok=True)


def record(strategy, expert="", verdict="pass", rounds=1, task=""):
    """记录一次执行结果。verdict: pass / revise。"""
    _ensure()
    item = {"ts": _now(), "strategy": strategy or "single",
            "expert": expert or "", "verdict": verdict or "pass",
            "rounds": int(rounds or 1), "task": task or ""}
    with _LOCK:
        try:
            with open(_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        except OSError:
            pass
    return item


def _now():
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S")


def read(limit=500):
    if not os.path.exists(_PATH):
        return []
    try:
        with open(_PATH, "r", encoding="utf-8") as f:
            lines = [json.loads(l) for l in f if l.strip()]
    except (OSError, json.JSONDecodeError):
        return []
    return lines[-limit:]


def stats():
    """各策略的历史表现：样本数、返工率、平均自检轮次。"""
    out = {}
    for r in read():
        s = r.get("strategy") or "single"
        d = out.setdefault(s, {"n": 0, "revise": 0, "rounds": 0})
        d["n"] += 1
        d["revise"] += 1 if r.get("verdict") == "revise" else 0
        d["rounds"] += int(r.get("rounds") or 1)
    for s, d in out.items():
        d["revise_rate"] = round(d["revise"] / d["n"], 3) if d["n"] else None
        d["avg_rounds"] = round(d["rounds"] / d["n"], 2) if d["n"] else None
    return out


def suggest(current, expert=""):
    """是否建议换策略。返回 {"from","to","reason"} 或 None。"""
    cur = (current or "single")
    if cur not in SWITCHABLE:
        return None                       # pipeline 不参与自动切换
    st = stats()
    mine = st.get(cur)
    if not mine or mine["n"] < MIN_SAMPLES:
        return None                       # 自身样本不足，谈不上"反思"
    my_rate = mine["revise_rate"] or 0.0

    best, best_rate = None, my_rate
    for s, d in st.items():
        if s == cur or s not in SWITCHABLE:
            continue
        if d["n"] < MIN_SAMPLES:
            continue
        rate = d["revise_rate"] or 0.0
        if rate < best_rate:
            best, best_rate = s, rate
    if not best or (my_rate - best_rate) < MIN_GAIN:
        return None
    return {
        "from": cur, "to": best,
        "reason": (f"历史 {mine['n']} 次「{cur}」策略返工率 {my_rate:.0%}，"
                   f"而「{best}」为 {best_rate:.0%}（{st[best]['n']} 次），"
                   f"故改用后者"),
    }


def clear():
    """清空策略历史。

    用**截断**而非删除文件：删除在某些环境（如受控目录、只读快照）会失败，
    而"清空记录"的语义用截断即可达成，且不依赖删除权限。
    """
    try:
        with open(_PATH, "w", encoding="utf-8"):
            pass
    except OSError:
        pass
    return True
