"""评测数据采集（设计文档 §14.1 的落地实现）。

只采集**运行指标**，不采集学生个人信息：
- 每次模型调用的：任务类型、专家、模型、耗时、是否成功、输出字数；
- 教师反馈：对某条回复「有用 / 无用」。

落盘为 JSONL，便于后续统计与导出：
  workspace/logs/metrics.jsonl
  workspace/logs/feedback.jsonl

对应设计文档的四个评测维度：效率（耗时）、可用性（反馈）、
安全合规（安全事件见 safety.audit_log）、准确率需人工标注，暂不自动采集。
"""

import json
import os
import threading
import time

from . import config

LOG_DIR = os.path.join(config.WORKSPACE_DIR, "logs")
METRICS_PATH = os.path.join(LOG_DIR, "metrics.jsonl")
FEEDBACK_PATH = os.path.join(LOG_DIR, "feedback.jsonl")
CRITIQUE_PATH = os.path.join(LOG_DIR, "critique.jsonl")

_LOCK = threading.Lock()
_MAX_READ = 2000        # 汇总时最多读取的最近记录数


def _append(path, item):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with _LOCK:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
    except OSError:
        pass


def record_call(expert, model, elapsed, ok, chars=0, task="chat",
                isolated=False, masked=(), warnings=0):
    """记录一次模型调用。"""
    _append(METRICS_PATH, {
        "ts": round(time.time(), 3),
        "task": task,                 # chat / lesson
        "expert": expert,
        "model": model,
        "elapsed": round(float(elapsed), 2),
        "ok": bool(ok),
        "chars": int(chars),
        "est_tokens": max(1, int(int(chars) / 1.5)),
        "isolated": bool(isolated),
        "masked": list(masked or []),
        "warnings": int(warnings),
    })


def record_critique(expert="", task="chat", verdict="pass", rounds=1):
    """记录一次自检结论。

    这是「反馈闭环」的数据源：把 Critic 的判定（pass / revise）与迭代轮次
    落盘，才能回答「哪一类产出最常被判定需修订」，进而定向优化对应专家的
    提示词与检索偏好——而不是让采集到的数据沉睡在日志里。
    """
    _append(CRITIQUE_PATH, {
        "ts": round(time.time(), 3),
        "expert": expert,
        "task": task,
        "verdict": verdict,
        "rounds": int(rounds),
    })


def record_feedback(rating, expert="", session_id="", comment=""):
    """记录教师对某条回复的反馈：rating 为 'up' / 'down'。"""
    _append(FEEDBACK_PATH, {
        "ts": round(time.time(), 3),
        "rating": rating,
        "expert": expert,
        "session_id": session_id,
        "comment": (comment or "")[:500],
    })


def _read(path, limit=_MAX_READ):
    if not os.path.exists(path):
        return []
    items = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return items[-limit:]


def summary():
    """汇总评测指标，供 /api/metrics 使用。"""
    from . import safety

    calls = _read(METRICS_PATH)
    feedback = _read(FEEDBACK_PATH)

    by_expert, by_task = {}, {}
    total_elapsed = 0.0
    total_chars = 0
    ok_count = 0
    for c in calls:
        e = c.get("expert") or "未知"
        by_expert.setdefault(e, {"calls": 0, "ok": 0, "chars": 0})
        by_expert[e]["calls"] += 1
        by_expert[e]["ok"] += 1 if c.get("ok") else 0
        by_expert[e]["chars"] += int(c.get("chars") or 0)
        t = c.get("task") or "chat"
        by_task[t] = by_task.get(t, 0) + 1
        total_elapsed += float(c.get("elapsed") or 0)
        total_chars += int(c.get("chars") or 0)
        ok_count += 1 if c.get("ok") else 0

    up = sum(1 for f in feedback if f.get("rating") == "up")
    down = sum(1 for f in feedback if f.get("rating") == "down")

    # ---- 质量看板：自检结论 + 教师反馈 ----
    critiques = _read(CRITIQUE_PATH)
    by_expert_quality = {}
    for c in critiques:
        e = c.get("expert") or "未知"
        d = by_expert_quality.setdefault(
            e, {"critiques": 0, "revise": 0, "rounds": 0})
        d["critiques"] += 1
        d["revise"] += 1 if c.get("verdict") == "revise" else 0
        d["rounds"] += int(c.get("rounds") or 1)
    fb_by_expert = {}
    for f in feedback:
        e = f.get("expert") or "未知"
        d = fb_by_expert.setdefault(e, {"up": 0, "down": 0})
        if f.get("rating") == "up":
            d["up"] += 1
        elif f.get("rating") == "down":
            d["down"] += 1
    weakest = None
    if by_expert_quality:
        e, d = max(by_expert_quality.items(),
                   key=lambda kv: kv[1]["revise"] / kv[1]["critiques"])
        weakest = {"expert": e,
                   "revise_rate": round(d["revise"] / d["critiques"], 3),
                   "samples": d["critiques"]}

    est_total = sum(int(c.get("est_tokens") or max(1, int(int(c.get("chars") or 0) / 1.5)))
                    for c in calls)
    return {
        "usage": {
            "calls": len(calls),
            "chars": total_chars,
            "est_tokens": est_total,
        },
        "calls": len(calls),
        "ok_rate": round(ok_count / len(calls), 3) if calls else None,
        "avg_elapsed": round(total_elapsed / len(calls), 2) if calls else None,
        "by_expert": by_expert,
        "by_task": by_task,
        "feedback": {"up": up, "down": down, "total": up + down},
        "feedback_rate": round(up / (up + down), 3) if (up + down) else None,
        "quality": {
            "critiques": len(critiques),
            "revise": sum(1 for c in critiques if c.get("verdict") == "revise"),
            "revise_rate": (round(sum(1 for c in critiques
                                      if c.get("verdict") == "revise")
                                  / len(critiques), 3) if critiques else None),
            "avg_rounds": (round(sum(int(c.get("rounds") or 1) for c in critiques)
                                 / len(critiques), 2) if critiques else None),
            "by_expert": by_expert_quality,
            "feedback_by_expert": fb_by_expert,
            "weakest": weakest,      # 最常被判定需修订的产出类型
        },
        "safety_events": safety.audit_log()[:20],
        "safety_event_count": len(safety.audit_log()),
    }


def clear():
    """清空评测数据（教师可在界面触发）。"""
    for p in (METRICS_PATH, FEEDBACK_PATH, CRITIQUE_PATH):
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass
