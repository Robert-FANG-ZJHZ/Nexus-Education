"""多智能体对等协商（设计文档 §2.3 专家矩阵的「协商」环节）。

为什么需要它：
单向流水线（NexAim → NexDes → NexSca）的通病是**下游只能承接上游，不能质疑上游**。
一旦目标写得虚，评价环节无从下手，下游只能硬着头皮编——错误会一路传递到最后。

本模块把「协商」从**单向返工**升级为**对等协商**：
  1. 检测冲突：确定性规则（覆盖率统计 + 正则），零模型调用；
  2. 提出质疑：质疑方点名被质疑方，并给出**系统自动检测的证据**（不是主观挑刺）；
  3. 对等回应：被质疑方可以「接受质疑」并最小修订，也可以「坚持原方案」并给出
     教学上的理由——**重点在于它有权反驳**，而不像返工那样只能照做；
  4. 编导仲裁：仅当双方未达成一致（出现「坚持」）时，由 coordinator 做一次裁定，
     并给出可直接替换的最终表述。

成本约束（重要）：一键备课每多一次模型调用都是真金白银且拖慢出稿，因此
  - 冲突检测零调用；
  - 最多处理 MAX_ISSUES 个冲突（按严重度排序）；
  - 每个冲突 1 次回应调用；
  - 仲裁最多 1 次调用，且只在有人「坚持」时才发生。

判定回应的态度（accept / reject）同样是确定性的关键词解析，不额外调用模型。
"""

import re
import threading

from . import agents as _agents

# ---------------- 可调参数 ----------------

MAX_ISSUES = 2              # 一轮协商最多处理几个冲突
MAX_PARALLEL = 3            # 并行回应的并发上限（议题再多也不超过它）
CALL_TIMEOUT = 120          # 单条回应最多等这么久（秒），超时按"未达成一致"处理
VAGUE_RATIO = 0.5           # 模糊动词占比超过此值 → 目标不可评价
DES_COVER_MIN = 0.25        # 目标片段在教学设计中的覆盖率下限
MAX_EXERCISES = 15          # 作业题量预警线
MAX_MINUTES = 60            # 作业时长预警线（分钟）

# 模糊动词（难以直接观测/评价）与可观测行为动词（用于判定目标是否可评价）
_VAGUE_VERBS = ("了解", "掌握", "理解", "领会", "认识", "感悟", "培养", "提高")
_OBSERVABLE_VERBS = ("说出", "写出", "列举", "设计", "完成", "解释", "背诵",
                     "圈画", "计算", "描述", "归纳", "比较", "判断", "仿写",
                     "批注", "朗读", "填写", "绘制", "分类", "举例", "改写")

_OBJECTIVE_HINT = re.compile(r"(目标|能|会|学会|掌握|理解|说出|写出|完成)")

_ACCEPT_MARKS = ("接受质疑", "接受", "同意质疑", "成立", "已修订", "确实如此")
# 否定式必须排在肯定式之前参与位置比较：「不接受质疑」里「不接受」比「接受」更靠前，
# 取首次出现位置即可正确判为 reject。
_REJECT_MARKS = ("不接受", "不同意", "坚持原方案", "坚持", "维持原方案", "维持",
                 "不宜改", "不建议修改", "不予修改", "质疑不成立")


def _clip(text, limit):
    t = (text or "").strip()
    return t if len(t) <= limit else t[:limit] + " …（略）"


def _objective_lines(text):
    return [l.strip() for l in (text or "").splitlines()
            if l.strip() and _OBJECTIVE_HINT.search(l)]


# ---------------- ① 冲突检测（确定性，零模型调用）----------------

def _vague_ratio(aim):
    """目标中「模糊动词」的占比：只看目标句，避免把说明文字算进来。"""
    objs = _objective_lines(aim)
    if len(objs) < 2:
        return 0.0, 0, []
    vague = [o for o in objs
             if any(v in o for v in _VAGUE_VERBS)
             and not any(v in o for v in _OBSERVABLE_VERBS)]
    return round(len(vague) / len(objs), 3), len(vague), vague


def _exercise_load(sca):
    """粗估作业负担：题号条目数与出现的最长时长（分钟）。"""
    text = sca or ""
    items = len(re.findall(r"^\s*(?:\d+[.、]|[-*])\s*\S", text, re.M))
    minutes = [int(x) for x in re.findall(r"(\d+)\s*分钟", text)]
    return items, (max(minutes) if minutes else 0)


def detect_issues(aim, des, sca, align=None):
    """检测专家产出之间的冲突。返回 [{id, frm, to, kind, claim, evidence, severity}]。

    frm=质疑方，to=被质疑方。严重度越高越优先处理。
    """
    issues = []

    # ① 目标-评价对齐度过低：评价环节无法承接目标
    if align is not None and align < _agents.ALIGN_THRESHOLD:
        issues.append({
            "id": "align", "frm": "nexsca", "to": "nexaim",
            "kind": "目标-评价脱节",
            "claim": "这些目标我在作业与评价中无法承接，表述太虚，没有可观测的证据点。",
            "evidence": "目标-评价对齐度 %.2f，低于阈值 %.2f（目标片段在作业评价中的覆盖率）"
                        % (align, _agents.ALIGN_THRESHOLD),
            "severity": 0.9,
        })

    # ② 目标不可评价：大量模糊动词且缺少可观测行为动词
    ratio, n_vague, samples = _vague_ratio(aim)
    if n_vague >= 2 and ratio >= VAGUE_RATIO:
        issues.append({
            "id": "vague", "frm": "nexsca", "to": "nexaim",
            "kind": "目标不可评价",
            "claim": "目标多用「了解/掌握/理解」这类词，我无法据此设计评价标准。",
            "evidence": "检测到 %d 条目标缺少可观测行为动词（占比 %.0f%%），例如：%s"
                        % (n_vague, ratio * 100, _clip(samples[0], 40)),
            "severity": 0.8,
        })

    # ③ 教学设计未承接目标：目标片段在设计中覆盖率过低
    cov = _agents.alignment_score(aim, des)
    if cov is not None and cov < DES_COVER_MIN:
        issues.append({
            "id": "uncovered", "frm": "nexaim", "to": "nexdes",
            "kind": "设计未承接目标",
            "claim": "我给出的目标在这份教学设计里看不到对应的活动，教学会跑偏。",
            "evidence": "目标片段在教学设计中的覆盖率仅 %.2f，低于 %.2f"
                        % (cov, DES_COVER_MIN),
            "severity": 0.7,
        })

    # ④ 作业负担预警
    items, minutes = _exercise_load(sca)
    if items > MAX_EXERCISES or minutes > MAX_MINUTES:
        issues.append({
            "id": "workload", "frm": "nexdes", "to": "nexsca",
            "kind": "作业负担偏重",
            "claim": "这份作业的题量/时长超出本课时的承载，课堂环节会被挤压。",
            "evidence": "检测到约 %d 个作业条目，最长时长 %d 分钟（预警线 %d 题 / %d 分钟）"
                        % (items, minutes, MAX_EXERCISES, MAX_MINUTES),
            "severity": 0.6,
        })

    # ⑤ 评价方式缺失：作业产出里看不到任何评价标准/量规 → 无法判断达成度
    if sca and not any(k in sca for k in ("量规", "评价标准", "评价任务",
                                          "评分", "自评", "互评")):
        issues.append({
            "id": "no_rubric", "frm": "nexaim", "to": "nexsca",
            "kind": "缺少评价标准",
            "claim": "这份作业没有评价依据，我不知道学生做到什么程度算达成。",
            "evidence": "作业产出中未检测到「量规 / 评价标准 / 自评互评」等字样",
            "severity": 0.55,
        })

    # ⑥ 目标-题目对应缺失：目标有若干条，作业里却没有对应关系说明
    n_obj = len(_objective_lines(aim))
    if n_obj >= 3 and sca and "对应" not in sca and "目标" not in sca:
        issues.append({
            "id": "no_mapping", "frm": "nexaim", "to": "nexsca",
            "kind": "目标-题目未对应",
            "claim": "作业没有标明每题对应哪条目标，无法判断覆盖面。",
            "evidence": f"检测到 {n_obj} 条目标，但作业产出中未见目标对应说明",
            "severity": 0.5,
        })

    issues.sort(key=lambda x: -x["severity"])
    return issues


def in_gray_zone(align):
    """处于"刚好过线"的灰区：一致性勉强达标，可能是巧合而非真的对齐。

    返回 True 时可选择触发一次深入复核（默认关闭，需教师开启 deep_review）——
    因为多一次模型调用就多一份成本，只有教师愿意为质量买单时才做。
    """
    if align is None:
        return False
    lo = _agents.ALIGN_THRESHOLD
    return lo <= align < lo + 0.15


def _flag(name, default=False):
    """读取可选能力开关（config._flags，可由设置持久化）。"""
    try:
        from . import config
        cur = config.current() or {}
        fl = cur.get("flags")
        if isinstance(fl, dict) and name in fl:
            return bool(fl[name])
        return bool(cur.get(name, default))
    except Exception:
        return default


def deep_review_enabled():
    return _flag("deep_review", False)


def parallel_enabled():
    """是否并行处理同一轮的多个议题。

    并行**不增加模型调用次数**，只把串行等待的墙钟时间压下来——一轮 2 个议题
    原来是 2×RTT，现在约等于 1×RTT。教师感知最明显的就是"一键备课变快了"。
    默认开启；若服务商限流严重，可在设置里关掉退回串行。
    """
    return _flag("negotiate_parallel", True)


# ---------------- ② 质疑 → ③ 回应 ----------------

def challenge_prompt(issue, subject, grade, topic, ctx):
    """给「被质疑方」的提示词：要求先表态，再给理由与最小修订。"""
    frm = _agents.get_expert(issue["frm"])
    return (
        f"你是「{_agents.get_expert(issue['to'])['cn']}」。"
        f"在「{grade}{subject}《{topic}》」的协作中，"
        f"「{frm['cn']}」对你的产出提出了质疑。\n\n"
        f"【质疑】{issue['claim']}\n"
        f"【依据】{issue['evidence']}（系统自动检测，非主观判断）\n\n"
        f"【相关产出（节选）】\n{ctx}\n\n"
        f"请你以**对等的合作者**身份回应，而不是被动返工：\n"
        f"- 若质疑成立：第一行写「接受质疑」，然后给出**最小修订**"
        f"（只改必要的部分，不要重写整份成果）；\n"
        f"- 若质疑不成立：第一行写「坚持原方案」，并说明**教学上的理由**"
        f"（例如某目标本就属于情感维度，强行行为化反而失真）。\n\n"
        f"要求：不超过 300 字；先表态，再理由与修订。"
    )


def verdict(text):
    """判定回应态度：'accept' / 'reject' / 'unclear'。

    取两类标记在文本中**首次出现的位置**，靠前者胜——因为提示词要求先表态，
    正文里复述对方观点时提到的词不应影响判定。
    """
    t = (text or "")[:600]
    pos = []
    for marks, label in ((_ACCEPT_MARKS, "accept"), (_REJECT_MARKS, "reject")):
        for m in marks:
            i = t.find(m)
            if i >= 0:
                pos.append((i, label))
                break
    if not pos:
        return "unclear"
    pos.sort()
    return pos[0][1]


# ---------------- ④ 编导仲裁 ----------------

def arbitration_prompt(issues, subject, grade, topic, ctx_map, responses):
    """双方未达成一致时的编导裁定提示词。"""
    lines = []
    for it, resp in zip(issues, responses):
        if resp.get("verdict") != "reject":
            continue
        lines.append(
            f"议题：{it['kind']}（{_agents.get_expert(it['frm'])['cn']} 质疑 "
            f"{_agents.get_expert(it['to'])['cn']}）\n"
            f"质疑依据：{it['evidence']}\n"
            f"被质疑方回应：{_clip(resp.get('text', ''), 400)}")
    return (
        f"你是「山海智绘」编导，负责裁定专家之间的分歧。\n"
        f"课题：{grade}{subject}《{topic}》\n\n"
        f"以下分歧未能通过协商达成一致：\n\n" + "\n\n".join(lines) + "\n\n"
        f"【相关产出（节选）】\n{_clip(ctx_map.get('aim', '') + ctx_map.get('sca', ''), 1200)}\n\n"
        f"请输出：\n"
        f"【裁定】采用质疑 / 维持原方案\n"
        f"【理由】一句话，站在教学可行性上说明。\n"
        f"【最终表述】若采用质疑，给出可直接替换的目标或设计要点（条目式）；"
        f"若维持原方案，说明教师使用时需要注意什么。\n"
        f"要求：不超过 250 字。裁定后仍由教师最终确认。"
    )


def arbitration_verdict(text):
    """解析编导裁定：'uphold'（采用质疑）/ 'keep'（维持原方案）/ 'unclear'。"""
    t = (text or "")[:400]
    i_up = min([i for i in (t.find("采用质疑"), t.find("质疑成立")) if i >= 0] or [10 ** 6])
    i_keep = min([i for i in (t.find("维持原方案"), t.find("维持")) if i >= 0] or [10 ** 6])
    if i_up < i_keep:
        return "uphold"
    if i_keep < 10 ** 6:
        return "keep"
    return "unclear"


# ---------------- 共享黑板 + 多轮收敛（v2）----------------


class Blackboard:
    """共享黑板：四环产出的结构化摘要 + 已达成的共识。

    单向传参的问题：每个专家只看得到"传给它的那一截"，看不到全局。黑板让所有
    协商都基于同一份摘要，也记录哪些议题已经谈拢（避免重复追问同一个问题）。
    """

    def __init__(self, outputs):
        self.outputs = dict(outputs or {})
        self.resolved = set()      # 已达成一致的议题类型
        self.failed = set()        # 调用失败的议题类型（不再重试，避免空转烧钱）
        self.decisions = []        # 协商纪要

    def digest(self, key, max_points=5):
        """提炼某份产出的要点（确定性：取标题/列表行）。"""
        text = self.outputs.get(key) or ""
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        pts = [l.lstrip("-#* ").strip() for l in lines
               if re.match(r"^(#{1,6}\s|[-*]\s|\d+[.、]\s)", l)]
        return (pts or lines)[:max_points]

    def view(self):
        """黑板视图（注入提示词用）：各环要点 + 已达成的共识。"""
        parts = []
        for key, label in (("aim", "目标"), ("des", "设计"), ("sca", "评价")):
            pts = self.digest(key)
            if pts:
                parts.append("【%s】%s" % (label, "；".join(p[:40] for p in pts)))
        if self.resolved:
            parts.append("【已达成共识】" + "、".join(sorted(self.resolved)))
        return "\n".join(parts)

    def post(self, issue, verdict, text):
        self.decisions.append({"kind": issue["kind"], "frm": issue["frm"],
                               "to": issue["to"], "verdict": verdict})
        if verdict == "accept":
            self.resolved.add(issue["kind"])
        return self


def _slot_prompt(issue, outputs, key_of, subject, grade, topic, bb):
    ctx = _clip(outputs.get(key_of[issue["to"]], ""), 1200)
    return (challenge_prompt(issue, subject, grade, topic, ctx)
            + "\n\n【共享黑板·全局摘要】\n" + bb.view())


def _run_parallel(slots, respond):
    """并发跑同一轮的多个议题回应。

    只并发、不乱序：每个议题的界面事件先按线程缓冲，等全部结束后再**按议题顺序**
    回放，教师看到的顺序与串行时一致；产出写入也在主线程按序完成，避免两个线程
    同时改同一份 outputs。
    """

    def _work(slot):
        try:
            for ev in respond(slot["issue"]["to"], slot["prompt"], slot["sink"]):
                slot["events"].append(ev)
        except Exception as e:                       # 单条失败不影响同轮其他议题
            slot["error"] = str(e)

    threads = [threading.Thread(target=_work, args=(s,), daemon=True)
               for s in slots]
    for t in threads:
        t.start()
    for t in threads:
        t.join(CALL_TIMEOUT)
    for s in slots:
        if s["error"]:
            s["events"].append({"type": "delta",
                                "text": f"（协商回应出错：{s['error']}）"})
    return slots


def converge(state, subject, grade, topic, respond, align=None, max_rounds=3,
             parallel=None):
    """多轮协商直到收敛（生成器，产出界面事件）。

    收敛条件（满足任一即停）：
      - 无新增冲突；
      - 本轮所有议题都被接受（达成一致）；
      - 达到 max_rounds（硬性上限，防止轮次失控）。

    仍未达成一致的议题，交给编导仲裁（最多一次）。

    v3 变化：**同一轮的多个议题并行回应**。此前两个议题是串行的，一轮要等两次
    模型往返；现在并发发起，墙钟时间约等于一次，调用次数与 token 完全不变。

    state: {"outputs": {"aim","des","sca"}} —— 就地更新
    respond(expert_id, prompt, sink): 生成器，产出界面事件并把文本写进 sink
    parallel: None=按设置决定；True/False 可强制
    """
    outputs = state["outputs"]
    bb = Blackboard(outputs)
    rounds, arbitrated = [], False
    used_parallel = False
    key_of = {"nexaim": "aim", "nexdes": "des", "nexsca": "sca"}
    if parallel is None:
        parallel = parallel_enabled()

    for _round in range(max_rounds):
        issues = detect_issues(outputs["aim"], outputs["des"], outputs["sca"],
                               align=align)
        issues = [i for i in issues
                  if i["kind"] not in bb.resolved
                  and i["kind"] not in bb.failed][:MAX_ISSUES]
        if not issues:
            break                                   # 无新增冲突 → 收敛

        round_verdicts = []
        batch = parallel and len(issues) > 1
        slots = [{"issue": i, "sink": [], "events": [], "error": "",
                  "prompt": _slot_prompt(i, outputs, key_of, subject, grade,
                                         topic, bb)}
                 for i in issues]
        if batch:
            used_parallel = True
            _run_parallel(slots[:MAX_PARALLEL], respond)
            # 并发上限之外的议题退回串行，保证不丢议题
            for s in slots[MAX_PARALLEL:]:
                try:
                    for ev in respond(s["issue"]["to"], s["prompt"], s["sink"]):
                        s["events"].append(ev)
                except Exception as e:
                    s["error"] = str(e)
                    s["events"].append({"type": "delta",
                                        "text": f"（协商回应出错：{e}）"})

        for slot in slots:
            issue = slot["issue"]
            to_e = _agents.get_expert(issue["to"])
            yield {"type": "section", "expert": issue["to"],
                   "title": f"专家协商（第{_round + 1}轮"
                            f"{'·并行' if batch else ''}） · "
                            f"{_agents.get_expert(issue['frm'])['cn']} → "
                            f"{to_e['cn']} · {issue['kind']}"}
            if batch:
                yield from slot["events"]            # 并行：事件先缓冲，此处按序回放
            else:
                yield from respond(issue["to"], slot["prompt"], slot["sink"])

            text = "".join(slot["sink"])
            v = verdict(text)
            if slot["error"]:
                # 调用失败不是"坚持原方案"：既不假装达成，也不反复重试烧调用次数
                bb.failed.add(issue["kind"])
            bb.post(issue, v, text)
            if v == "accept":
                k = key_of[issue["to"]]
                outputs[k] = (outputs[k] + "\n\n【经与"
                              + _agents.get_expert(issue["frm"])["cn"]
                              + "协商后的修订】\n" + text)
            rounds.append({"frm": issue["frm"], "to": issue["to"],
                           "kind": issue["kind"], "verdict": v, "text": text,
                           "round": _round + 1, "parallel": batch})
            round_verdicts.append(v)

        if round_verdicts and all(v == "accept" for v in round_verdicts):
            break                                   # 本轮全部达成一致 → 收敛

    # 仍未达成一致的议题 → 编导仲裁（最多一次）
    if any(r["verdict"] != "accept" for r in rounds):
        yield {"type": "section", "expert": "coordinator",
               "title": "编导仲裁 · 协商未达成一致"}
        sink = []
        yield from respond("coordinator",
                           arbitration_prompt(
                               [{"frm": r["frm"], "to": r["to"],
                                 "kind": r["kind"],
                                 "evidence": bb.view()} for r in rounds
                                if r["verdict"] != "accept"],
                               subject, grade, topic, outputs, rounds),
                           sink)
        arb = "".join(sink)
        arbitrated = True
        picked = arbitration_verdict(arb)
        target = [r for r in rounds if r["verdict"] != "accept"][-1]
        k = key_of[target["to"]]
        outputs[k] = outputs[k] + "\n\n" + (
            "【编导裁定后的最终表述】\n" if picked == "uphold"
            else "【编导裁定】\n") + arb

    state["rounds"], state["arbitrated"], state["blackboard"] = \
        rounds, arbitrated, bb
    if rounds:
        yield summary(rounds, arbitrated, used_parallel)


def summary(rounds, arbitrated, parallel=False):
    """协商纪要（给教师看的事件负载）。"""
    accepted = sum(1 for r in rounds if r["verdict"] == "accept")
    rejected = sum(1 for r in rounds if r["verdict"] == "reject")
    return {
        "type": "negotiation",
        "rounds": [{"frm": r["frm"], "to": r["to"], "kind": r["kind"],
                    "verdict": r["verdict"], "round": r.get("round", 1)}
                   for r in rounds],
        "accepted": accepted,
        "rejected": rejected,
        "arbitrated": arbitrated,
        "parallel": parallel,
        "text": ("专家协商：%d 个议题，%d 项已修订，%d 项坚持原方案%s%s"
                 % (len(rounds), accepted, rejected,
                    "，已由编导裁定" if arbitrated else "",
                    "（同轮多议题并行回应，不增加调用次数）" if parallel else "")),
    }
