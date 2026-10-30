# -*- coding: utf-8 -*-
"""生成《NexEdu 内置专家与技能清单》Markdown（完整版：含每位专家的内部提示词原文）。

数据直接取自运行时（nexedu.agents.list_experts / nexedu.skilllib）与原始矩阵
（nexedu.experts_data.EXPERT_MATRIX），不手写维护；专家矩阵或技能库一改，
重跑本脚本即可同步。

用法：
    py -3.12 tools/gen_capability_md.py              # 完整版（默认）
    py -3.12 tools/gen_capability_md.py --brief      # 精简版（不含提示词原文）
"""
import collections
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from nexedu import agents, experts_data, skilllib  # noqa: E402

OUT_FULL = os.path.join(ROOT, "docs", "NexEdu内置专家与技能清单.md")
OUT_BRIEF = os.path.join(ROOT, "docs", "NexEdu内置专家与技能清单-精简版.md")

# 专家环节展示顺序（与备课流程一致）
EXPERT_STAGE_ORDER = [
    "需求与定位", "课标与课程", "学情诊断", "目标设计",
    "评价设计", "教学与活动", "作业设计", "资源课件",
    "学科专长", "反思与数据",
]

STAGE_FLOW = ("需求定位 → 课标与课程 → 学情诊断 → 目标设计 → 教学与活动 "
              "→ 评价设计 → 作业设计 → 资源课件 → 反思与数据")


def esc(s):
    """表格单元格内转义：竖线破坏表格，换行压成空格。"""
    s = "" if s is None else str(s)
    return s.replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def fence(text):
    """安全包裹代码块：正文若含 ``` 则改用四个反引号。"""
    t = "" if text is None else str(text)
    f = "````" if "```" in t else "```"
    return "%s\n%s\n%s" % (f, t.rstrip(), f)


def _indent_fence(text, spaces):
    """缩进的代码块（用于列表项内部，保持层级可读）。"""
    t = "" if text is None else str(text)
    pad = " " * spaces
    body = "\n".join(pad + ln for ln in t.rstrip().split("\n"))
    return "%s```\n%s\n%s```" % (pad, body, pad)


def build(full=True):
    experts = agents.list_experts()
    skills = skilllib.SKILL_LIBRARY
    matrix = {m["id"]: m for m in experts_data.EXPERT_MATRIX}

    def owner_name(oid):
        if not oid or oid == "general":
            return "跨专家通用"
        oe = next((x for x in experts if x.get("id") == oid), None)
        return (oe.get("cn") or oe.get("name") or oid) if oe else oid

    by_stage = collections.defaultdict(list)
    for e in experts:
        by_stage[e.get("stage") or "未分组"].append(e)
    order = [s for s in EXPERT_STAGE_ORDER if s in by_stage]
    order += sorted(k for k in by_stage if k not in EXPERT_STAGE_ORDER)

    grp = collections.defaultdict(list)
    for s in skills:
        grp[s.get("group") or "未分组"].append(s)
    gorder = [g for g in skilllib.GROUP_ORDER if g in grp]
    gorder += sorted(k for k in grp if k not in skilllib.GROUP_ORDER)

    owned = collections.defaultdict(list)
    for s in skills:
        if s.get("expert") and s["expert"] != "general":
            owned[s["expert"]].append(s)
    n_general = sum(1 for s in skills if s.get("expert") == "general")
    gen_groups = {g for g in gorder if g.startswith("通用")}

    o = io.StringIO()
    w = o.write

    w("# NexEdu 内置专家与技能清单\n\n")
    w("> 本清单由 `tools/gen_capability_md.py` 从运行时数据自动生成，"
      "与代码中的专家矩阵、技能库逐字一致，勿手工编辑。\n")
    if full:
        w("> 专家部分含每位专家的**内部提示词原文**（`system` 字段）"
          "与其绑定的全部技能；技能部分含每条技能的全部字段（作用 / 用法 / 验收）。\n\n")
    else:
        w("> 精简版：不含专家提示词原文与技能逐条展开；"
          "需要完整内容请跑 `py -3.12 tools/gen_capability_md.py`。\n\n")

    # ---------- 目录 ----------
    w("## 目录\n\n")
    w("- [一、总览](#一总览)\n")
    w("- [二、内置专家（%d 位）](#二内置专家%d-位)\n" % (len(experts), len(experts)))
    for st in order:
        w("  - [%s（%d 位）](#%s%d-位)\n"
          % (esc(st), len(by_stage[st]), esc(st).replace("·", ""), len(by_stage[st])))
    w("- [三、内置技能（%d 条）](#三内置技能%d-条)\n" % (len(skills), len(skills)))
    for g in gorder:
        w("  - [%s（%d 条）](#%s%d-条)\n"
          % (esc(g), len(grp[g]), esc(g).replace("·", ""), len(grp[g])))
    w("- [四、数据来源与口径](#四数据来源与口径)\n\n---\n\n")

    # ---------- 一、总览 ----------
    w("## 一、总览\n\n")
    w("| 项 | 数量 | 说明 |\n|---|---:|---|\n")
    w("| 内置专家 | %d 位 | 覆盖 %s 共 9 个环节 |\n" % (len(experts), esc(STAGE_FLOW)))
    w("| 内置技能 | %d 条 | 领域技能 %d 条 + 通用技能 %d 条 |\n"
      % (len(skills), len(skilllib.DOMAIN_SKILLS), len(skilllib.GENERAL_SKILLS)))
    w("| 技能分组 | %d 组 | 环节组 %d 个 + 通用组 5 个（思维 / 操作 / 理解 / 处理 / 输入输出） |\n"
      % (len(grp), len(grp) - len(gen_groups)))
    w("| 专家专属技能 | %d 条 | 其余 %d 条通用技能（`expert=general`）跨专家共享 |\n"
      % (sum(len(v) for v in owned.values()), n_general))
    w("| 教师自建技能 | 不计入 | 内置技能不在技能工坊列出，也不可删除 |\n\n")

    w("### 专家环节分布\n\n| 环节 | 专家数 | 该环节专家绑定技能数 |\n|---|---:|---:|\n")
    for st in order:
        n = sum(len(owned.get(e.get("id"), [])) for e in by_stage[st])
        w("| %s | %d | %d |\n" % (esc(st), len(by_stage[st]), n))
    w("\n")

    w("### 技能分组分布\n\n| 分组 | 技能数 | 类型 |\n|---|---:|---|\n")
    for g in gorder:
        w("| %s | %d | %s |\n"
          % (esc(g), len(grp[g]), "通用" if g in gen_groups else "领域/环节"))
    w("\n")

    # ---------- 生效机制（容易误解为"技能丢失"的关键说明） ----------
    n_gov = sum(1 for s in skills if s.get("group") == skilllib.GOVERNANCE_GROUP)
    w("### 技能如何生效（重要）\n\n")
    w("内置技能**不在技能工坊列出、也不由教师勾选**，由系统在构建提示词时自动注入"
      "（`agents.build_system` → `skilllib.pick_for`）。分三类：\n\n")
    w("| 类别 | 数量 | 生效规则 |\n|---|---:|---|\n")
    w("| 治理类（%s 组） | %d 条 | **无条件生效**，不占配额——安全复核 / 学情脱敏 / "
      "引用规范 / 质量闸门等属产出底线，不能被配额挤掉 |\n"
      % (esc(skilllib.GOVERNANCE_GROUP), n_gov))
    w("| 专家专属技能 | %d 条 | 归属专家被调用时自动生效，最多 %d 条（`domain_limit`） |\n"
      % (sum(len(v) for v in owned.values()) - n_gov, 6))
    w("| 通用技能 | %d 条 | 按提问文本命中**语义触发词**后才生效，每次最多 %d 条"
      "（`general_limit`），未命中则不注入 |\n" % (n_general, 3))
    w("\n> 因此「某技能没出现在某次对话里」**不等于丢失**：\n")
    w("> 专属技能要看是否由对应专家处理；通用技能要看提问是否命中触发词。\n")
    w("> 本清单第三节逐条列出了每条技能的生效方式与触发词。\n\n---\n\n")

    # ---------- 二、专家 ----------
    w("## 二、内置专家（%d 位）\n\n" % len(experts))
    w("每位专家按「元数据 → 绑定技能 → 工作流 → 自检项 → 内部提示词原文」的顺序给出全部内容。\n\n")
    for st in order:
        w("### %s（%d 位）\n\n" % (esc(st), len(by_stage[st])))
        for e in by_stage[st]:
            eid = e.get("id")
            m = matrix.get(eid) or {}
            w("#### %s · %s\n\n" % (esc(e.get("cn") or e.get("name")), esc(e.get("name"))))
            w("**① 元数据**\n\n")
            w("| 字段 | 内容 |\n|---|---|\n")
            w("| ID | `%s` |\n" % esc(eid))
            w("| 中文名 | %s |\n" % esc(e.get("cn")))
            w("| 英文名 | %s |\n" % esc(e.get("en")))
            w("| 所属环节 | %s |\n" % esc(e.get("stage")))
            w("| 定位（desc） | %s |\n" % esc(e.get("desc")))
            if m.get("duty"):
                w("| 核心职责（duty） | %s |\n" % esc(m["duty"]))
            outs = e.get("outputs") or []
            if outs:
                w("| 典型产出（outputs） | %s |\n" % "、".join(esc(x) for x in outs))
            tags = e.get("tags") or []
            if tags:
                w("| 标签（tags） | %s |\n" % "、".join(esc(x) for x in tags))
            kw = e.get("kw") or []
            if kw:
                w("| 触发词（kw） | %s |\n" % "、".join("「%s」" % esc(x) for x in kw))
            w("| 来源 | %s |\n"
              % ("专家矩阵 `EXPERT_MATRIX`" if m else "手写四环专家（`agents.py`）"))
            w("\n")

            mine = owned.get(eid, [])
            w("**② 本专家绑定的技能（%d 条）**\n\n" % len(mine))
            if mine:
                w("| 技能 | ID | 作用（desc） |\n|---|:--|---|\n")
                for s in mine:
                    w("| %s | `%s` | %s |\n"
                      % (esc(s.get("name")), esc(s.get("id")), esc(s.get("desc"))))
            else:
                w("（无专属技能；可调用 %d 条通用技能）\n" % n_general)
            w("\n")

            if m.get("flow"):
                w("**③ 工作流（flow）**\n\n")
                for i, f in enumerate(m["flow"], 1):
                    w("%d. %s\n" % (i, esc(f)))
                w("\n")
            if m.get("check"):
                w("**④ 自检项（check）**\n\n")
                for c in m["check"]:
                    w("- %s\n" % esc(c))
                w("\n")
            if not m:
                w("**③④ 工作流与自检项**：该专家为 `agents.py` 手写条目，"
                  "矩阵中无对应结构化字段，其工作流与自检内容见下方⑤提示词原文。\n\n")

            if full:
                w("**⑤ 内部提示词原文（`system` 字段，运行时实际注入）**\n\n")
                # 手写四环专家的提示词优先于矩阵渲染版：两者都在时明确标注，避免混淆
                if m and e.get("system") != experts_data.render_system(m):
                    w("> 注：该 id 存在手写提示词（`agents.py`），优先级高于矩阵渲染版；"
                      "以下为运行时真正注入的内容，与上方③④措辞可能不同。\n\n")
                w(fence(e.get("system") or "（无）") + "\n\n")
            w("---\n\n")

    # ---------- 三、技能 ----------
    w("## 三、内置技能（%d 条）\n\n" % len(skills))
    w("每条技能含三个内容字段：`desc` 作用、`how` 用法、`check` 验收标准。\n\n")
    for g in gorder:
        items = grp[g]
        w("### %s（%d 条）\n\n" % (esc(g), len(items)))
        w("**汇总表**\n\n")
        w("| # | 技能 | ID | 归属专家 | 作用（desc） |\n|---:|---|:--|:--|---|\n")
        for i, s in enumerate(items, 1):
            w("| %d | **%s** | `%s` | %s | %s |\n"
              % (i, esc(s.get("name")), esc(s.get("id")),
                 esc(owner_name(s.get("expert"))), esc(s.get("desc"))))
        w("\n")

        if full:
            w("**逐条完整内容**\n\n")
            for s in items:
                oid = s.get("expert")
                is_general = (not oid or oid == "general")
                olink = ("`general`（跨专家通用）" if is_general
                         else "`%s`（%s）" % (oid, esc(owner_name(oid))))
                w("- **%s**　ID：`%s`　分组：%s　归属：%s\n"
                  % (esc(s.get("name")), esc(s.get("id")), esc(s.get("group")), olink))
                # 生效方式：这是"技能是不是丢了"的直接答案
                if is_general:
                    kw = skilllib._GENERAL_KW.get(s["id"]) or []
                    w("  - 生效方式：**通用技能**，提问命中下列触发词才注入（每次最多 3 条）\n")
                    w("  - 触发词：%s\n" % ("、".join("「%s」" % esc(t) for t in kw) or "（无）"))
                elif s.get("group") == skilllib.GOVERNANCE_GROUP:
                    w("  - 生效方式：**治理类**，归属专家被调用时**无条件生效**（不占配额）\n")
                else:
                    w("  - 生效方式：专家专属，%s 被调用时自动生效（同专家最多 6 条）\n"
                      % esc(owner_name(oid)))
                w("  - 作用（desc）：%s\n" % esc(s.get("desc")))
                w("  - 用法（how）：%s\n" % esc(s.get("how")))
                w("  - 验收（check）：%s\n" % esc(s.get("check")))
                # 真正注入提示词的文本（与专家的 system 原文对等）
                w("  - 注入原文（`render_prompt` 渲染结果）：\n\n")
                w(_indent_fence(skilllib.render_prompt(s), 4) + "\n")
            w("\n")
        else:
            w("<details><summary>展开：各技能的用法与验收标准</summary>\n\n")
            for s in items:
                w("- **%s**（`%s`）\n" % (esc(s.get("name")), esc(s.get("id"))))
                w("  - 用法：%s\n" % esc(s.get("how")))
                w("  - 验收：%s\n" % esc(s.get("check")))
            w("\n</details>\n\n")
        w("---\n\n")

    # ---------- 四、来源 ----------
    w("## 四、数据来源与口径\n\n")
    w("| 内容 | 来源 |\n|---|---|\n")
    w("| 专家列表 | `nexedu/agents.py` 的 `list_experts()`（运行时真实结果） |\n")
    w("| 专家提示词 | 各专家的 `system` 字段（由 `experts_data.render_system()` 渲染） |\n")
    w("| 专家原始字段 | `nexedu/experts_data.py` 的 `EXPERT_MATRIX`（duty / flow / check / tags / kw） |\n")
    w("| 技能库 | `nexedu/skilllib.py` 的 `SKILL_LIBRARY` = `DOMAIN_SKILLS` %d 条 + `GENERAL_SKILLS` %d 条 |\n"
      % (len(skilllib.DOMAIN_SKILLS), len(skilllib.GENERAL_SKILLS)))
    w("| 归属关系 | `skilllib.owner_map()`，启动时经 `bind_skills()` 绑定并写入专家提示词 |\n\n")

    w("### 数量口径（容易数错的地方）\n\n")
    w("- 专家 **%d 位** = `EXPERT_MATRIX` %d 位 + `agents.py` 手写四环专家中的 `nexsca`"
      "（矩阵中无此条，去重并入后成为第 42 位）。\n"
      % (len(experts), len(experts_data.EXPERT_MATRIX)))
    w("- 手写四环专家 `nexaim` / `nexdes` / `nexsca` / `coordinator` 的提示词更细，"
      "**优先级高于**矩阵中同 id 条目（同 id 不覆盖，只补新 id）。\n")
    w("- 通用技能 `expert=general`，不属于任何单一专家，界面显示为「跨专家通用」。\n")

    return o.getvalue()


def main():
    full = "--brief" not in sys.argv
    text = build(full=full)
    out = OUT_FULL if full else OUT_BRIEF
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)

    n_exp = len(agents.list_experts())
    n_skill = len(skilllib.SKILL_LIBRARY)
    n_grp = len(set(s.get("group") for s in skilllib.SKILL_LIBRARY))
    print("已生成：%s" % out)
    print("  专家 %d 位 / 技能 %d 条 / 分组 %d 个 / 正文 %d 字符"
          % (n_exp, n_skill, n_grp, len(text)))


if __name__ == "__main__":
    main()
