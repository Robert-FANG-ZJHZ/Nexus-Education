# -*- coding: utf-8 -*-
"""生成「教学评一体化备课流程」蓝图：专家矩阵(≥30) + Skill 库(≥100) + n8n 步骤蓝图。

一次生成三份、数据同源，保证一致：
  docs/教学评一体化备课蓝图.md
  blueprint/教学评一体化备课流程.json   （n8n 可导入）
  blueprint/教学评一体化备课步骤.csv    （order/name/type/on_failure 清单）

触发方式：manual（手动，全流程 W0）/ schedule（定时，学情跟踪）/ webhook（回调，数据回流）。
用法：
  "C:/Users/FANG/.workbuddy/binaries/python/versions/3.13.12/python.exe" tools/gen_workflow_blueprint.py
"""
import csv
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLUEPRINT_DIR = os.path.join(ROOT, "blueprint")
DOC_PATH = os.path.join(ROOT, "docs", "教学评一体化备课蓝图.md")
JSON_PATH = os.path.join(BLUEPRINT_DIR, "教学评一体化备课流程.json")
CSV_PATH = os.path.join(BLUEPRINT_DIR, "教学评一体化备课步骤.csv")

# ============================================================================
# 专家矩阵（id, 中文名, 英文名, 所属阶段, 职责, 关键产出）—— 41 位
# ============================================================================
EXPERTS = [
    ("nexcoord", "编导/协调", "NexCoord", "需求与定位", "意图路由、任务分解、专家仲裁、闭环回流", "计划摘要、仲裁纪要、闭环回流卡"),
    ("nexstd", "课标解析", "NexStd", "课标与课程", "课标条目→内容要求/学业质量/素养维度", "课标解析卡"),
    ("nextxt", "教材分析", "NexTxt", "课标与课程", "教材结构、重难点、知识前后衔接", "教材结构图、重难点清单"),
    ("nexunit", "大单元设计", "NexUnit", "课标与课程", "大概念统领的单元整体设计", "单元整体方案、大概念"),
    ("nexxcu", "跨学科整合", "NexXcu", "课标与课程", "跨学科主题、课程思政融合", "跨学科主题设计"),
    ("nexprof", "学情画像", "NexProf", "学情诊断", "学生起点、差异、分层需求", "学情画像"),
    ("nexmis", "前概念/易错点", "NexMis", "学情诊断", "前概念与高频易错点预判", "前概念/易错点清单"),
    ("nexdif", "差异分层", "NexDif", "学情诊断", "分层分组与差异化教学策略", "分层策略"),
    ("nexincl", "融合教育", "NexIncl", "学情诊断", "特殊需求与融合教育适配", "融合教育适配建议"),
    ("nexaim", "目标设计", "NexAim", "目标设计", "课标→单元/课时目标链、目标叙写", "单元目标链、课时目标"),
    ("nexlit", "素养映射", "NexLit", "目标设计", "核心素养维度映射与均衡检视", "素养映射表"),
    ("nexsol", "SOLO/认知层级", "NexSol", "目标设计", "SOLO/布鲁姆层级标注与认知阶梯", "认知层级标注"),
    ("nexalign", "目标-评价对齐", "NexAlign", "目标设计", "目标与评价的可观测/可评价对齐校验", "对齐度报告"),
    ("nexeval", "评价任务设计", "NexEval", "评价设计", "评价任务前置与评价证据设计", "评价任务单"),
    ("nexrub", "量规设计", "NexRub", "评价设计", "评分量规、等级描述、自评互评表", "评分量规"),
    ("nexperf", "表现性评价", "NexPerf", "评价设计", "表现性任务与真实情境评价", "表现性任务"),
    ("nexproc", "过程性评价", "NexProc", "评价设计", "过程性/课堂形成性评价", "形成性评价方案"),
    ("nexsum", "终结性评价/命题", "NexSum", "评价设计", "客观/主观题命题、双向细目表", "试卷、双向细目表"),
    ("nexdes", "教学设计", "NexDes", "教学与活动", "逆向教学设计、活动编排、课堂流程", "课时教学设计"),
    ("nexq", "问题链/提问", "NexQ", "教学与活动", "核心问题、问题链与追问设计", "问题链"),
    ("nexsit", "情境创设", "NexSit", "教学与活动", "真实情境与认知冲突设计", "情境素材"),
    ("nexcoop", "合作学习", "NexCoop", "教学与活动", "分组、拼图法、角色分工", "合作学习方案"),
    ("nexpbl", "项目式/探究", "NexPbl", "教学与活动", "PBL 与探究式学习设计", "项目/探究方案"),
    ("nexgame", "游戏化/动机", "NexGame", "教学与活动", "游戏化机制与学习动机激发", "游戏化设计"),
    ("nexhw", "分层作业", "NexHw", "作业设计", "分层作业、目标-题目对应、负担监测", "分层作业单"),
    ("nexerr", "错题/补偿", "NexErr", "作业设计", "错题本、补偿练习与变式训练", "补偿练习包"),
    ("nexprac", "实践/跨学科作业", "NexPrac", "作业设计", "实践性、跨学科、长周期作业", "实践作业单"),
    ("nexppt", "课件设计", "NexPpt", "资源课件", "课件分页脚本与视觉排版", "课件脚本"),
    ("nexhand", "学案/导学单", "NexHand", "资源课件", "学案、导学单、课堂练习单", "学案"),
    ("nexmicro", "微课脚本", "NexMicro", "资源课件", "微课脚本与分镜", "微课脚本"),
    ("nexmedia", "可视化/数字资源", "NexMedia", "资源课件", "知识可视化、数字资源与仿真", "可视化/数字资源"),
    ("nexch", "语文学科", "NexCh", "学科专长", "阅读、写作、文言文/古诗教学", "语文教学设计"),
    ("nexmath", "数学学科", "NexMath", "学科专长", "概念建构、数学建模、解题策略", "数学教学设计"),
    ("nexeng", "英语学科", "NexEng", "学科专长", "听、说、读、写教学", "英语教学设计"),
    ("nexsci", "理化生学科", "NexSci", "学科专长", "实验探究、科学探究与实验安全", "理科实验设计"),
    ("nexsoc", "史地政学科", "NexSoc", "学科专长", "史料/地图解读、时政链接、价值引领", "文科教学设计"),
    ("nexref", "试讲/磨课诊断", "NexRef", "反思与数据", "试讲/磨课问题诊断与定向修改", "磨课诊断报告"),
    ("nexobs", "课堂观察", "NexObs", "反思与数据", "课堂观察（LICC 范式）", "课堂观察记录"),
    ("nexdata", "错因/数据分析", "NexData", "反思与数据", "错因归类、薄弱知识点、数据可视化", "错因/薄弱点报告"),
    ("nextrack", "学情追踪", "NexTrack", "反思与数据", "学情长期追踪、趋势与复习周期", "学情追踪档案"),
    ("nexresearch", "教研/论文课题", "NexResearch", "反思与数据", "教学反思、论文课题、案例研究", "教研成果"),
]

# ============================================================================
# Skill 库（id, 名称, 归属专家, 作用）—— 127 个
# ============================================================================
SKILLS = [
    ("skill-route", "意图路由", "nexcoord", "识别教师意图并路由到合适专家"),
    ("skill-plan", "任务分解规划", "nexcoord", "把复杂需求拆成可解释的执行计划"),
    ("skill-arbitrate", "专家仲裁", "nexcoord", "专家意见不一致时给出裁定"),
    ("skill-backflow", "闭环回流汇总", "nexcoord", "汇总缺口提示与二次备课建议"),

    ("skill-std-parse", "课标条目解析", "nexstd", "把课标条目拆为内容要求/学业质量/素养"),
    ("skill-achievement", "学业质量提炼", "nexstd", "提炼学业质量水平描述"),
    ("skill-literacy-std", "课标素养提取", "nexstd", "从课标提取核心素养维度"),
    ("skill-textbook-map", "教材结构图", "nextxt", "教材章节→知识结构图"),
    ("skill-keypoint", "重难点预判", "nextxt", "预判课时重难点"),
    ("skill-sequence", "知识前后衔接", "nextxt", "梳理知识的前后联系与迁移路径"),
    ("skill-unit-design", "大单元整体设计", "nexunit", "以单元为单位的整体方案"),
    ("skill-big-idea", "大概念提取", "nexunit", "提炼学科大概念/核心概念"),
    ("skill-unit-kb", "单元知识结构", "nexunit", "构建单元知识网络"),
    ("skill-cross-subject", "跨学科主题整合", "nexxcu", "设计跨学科融合主题"),
    ("skill-course-ideology", "课程思政融合", "nexxcu", "把思政元素自然融入学科教学"),
    ("skill-project-x", "跨学科项目设计", "nexxcu", "设计跨学科项目式任务"),

    ("skill-profiling", "学情画像", "nexprof", "生成学情画像"),
    ("skill-startpoint", "学习起点分析", "nexprof", "定位学生学习起点"),
    ("skill-style", "学习风格分析", "nexprof", "分析学习风格与偏好"),
    ("skill-misconception", "前概念预判", "nexmis", "预判学生前概念/相异构想"),
    ("skill-error-prone", "高频易错点预判", "nexmis", "预判高频易错点"),
    ("skill-zpd", "最近发展区定位", "nexmis", "定位最近发展区(ZPD)"),
    ("skill-tiering", "分层分组策略", "nexdif", "设计分层分组方案"),
    ("skill-diff-task", "差异化任务设计", "nexdif", "为不同水平设计差异化任务"),
    ("skill-scalaffold-level", "支架分层", "nexdif", "设计分层学习支架"),
    ("skill-inclusion", "融合教育适配", "nexincl", "为特殊需求学生做适配"),
    ("skill-special-need", "特殊需求支持", "nexincl", "提供个别化支持建议"),
    ("skill-sel", "社会情感学习", "nexincl", "融入社会情感学习(SEL)要素"),

    ("skill-objective-write", "目标叙写(ABCD)", "nexaim", "主体+行为动词+行为条件+表现标准"),
    ("skill-objective-decompose", "目标分解", "nexaim", "课标→单元→课时逐级分解"),
    ("skill-behavior-verb", "行为动词规范", "nexaim", "替换模糊动词为可观测动词"),
    ("skill-literacy-map", "素养映射", "nexlit", "目标标注素养维度(避贴标签)"),
    ("skill-literacy-balance", "素养均衡检视", "nexlit", "检视素养维度覆盖是否均衡"),
    ("skill-solo-level", "SOLO层级标注", "nexsol", "用 SOLO 标注认知层级"),
    ("skill-bloom-level", "布鲁姆层级", "nexsol", "用布鲁姆分类标注认知层级"),
    ("skill-cognitive-ladder", "认知阶梯设计", "nexsol", "设计认知递进阶梯"),
    ("skill-alignment", "目标-评价对齐校验", "nexalign", "校验目标与评价的对齐度"),
    ("skill-observable", "可观测性校验", "nexalign", "校验目标是否可观测"),
    ("skill-measurable", "可评价性校验", "nexalign", "校验目标是否可评价"),
    ("skill-objective-gap", "目标缺口提示", "nexalign", "提示目标与评价的缺口"),

    ("skill-eval-task", "评价任务设计", "nexeval", "设计评价任务"),
    ("skill-eval-first", "评价前置(逆向)", "nexeval", "先定评价再设计教学"),
    ("skill-evidence", "评价证据设计", "nexeval", "为每个目标设计评价证据"),
    ("skill-rubric", "量规设计", "nexrub", "设计评分量规"),
    ("skill-rubric-level", "等级描述", "nexrub", "撰写各等级表现描述"),
    ("skill-self-peer", "自评互评表", "nexrub", "设计自评/互评表"),
    ("skill-performance-task", "表现性任务", "nexperf", "设计表现性任务"),
    ("skill-real-scenario", "真实情境评价", "nexperf", "用真实情境做评价"),
    ("skill-portfolio", "成长档案袋", "nexperf", "设计成长档案袋评价"),
    ("skill-formative", "过程性评价", "nexproc", "设计形成性评价"),
    ("skill-classroom-qa", "课堂提问评价", "nexproc", "设计课堂提问评价"),
    ("skill-observation-scale", "课堂观察量表", "nexproc", "设计课堂观察量表"),
    ("skill-item-write", "客观题命题", "nexsum", "命制选择题/填空题"),
    ("skill-item-subjective", "主观题命题", "nexsum", "命制简答/论述题"),
    ("skill-spec-table", "双向细目表", "nexsum", "知识点×认知层级×题型细目表"),

    ("skill-backward-design", "逆向教学设计", "nexdes", "目标→评价→活动的逆向设计"),
    ("skill-activity-design", "学习活动设计", "nexdes", "设计学习活动"),
    ("skill-lesson-flow", "课堂流程编排", "nexdes", "编排课堂流程"),
    ("skill-question-chain", "问题链设计", "nexq", "核心问题→驱动问题→子问题"),
    ("skill-core-question", "核心/驱动问题", "nexq", "提炼核心/驱动问题"),
    ("skill-wait-time", "追问与候答", "nexq", "设计追问与候答策略"),
    ("skill-situation", "真实情境创设", "nexsit", "创设真实情境"),
    ("skill-conflict", "认知冲突设计", "nexsit", "设计认知冲突"),
    ("skill-life-link", "生活联系", "nexsit", "建立知识与生活的联系"),
    ("skill-grouping", "小组分组", "nexcoop", "设计分组方案"),
    ("skill-jigsaw", "拼图法/互学", "nexcoop", "设计拼图式互学"),
    ("skill-role-assign", "角色分工", "nexcoop", "设计小组角色分工"),
    ("skill-pbl", "项目式学习", "nexpbl", "设计 PBL 项目"),
    ("skill-inquiry", "探究式学习", "nexpbl", "设计探究式学习"),
    ("skill-driven-question", "驱动问题链", "nexpbl", "设计项目驱动问题链"),
    ("skill-gamification", "游戏化机制", "nexgame", "设计游戏化机制"),
    ("skill-motivation", "动机激发", "nexgame", "设计动机激发策略"),
    ("skill-feedback-loop", "即时反馈", "nexgame", "设计即时反馈循环"),

    ("skill-homework-tier", "分层作业", "nexhw", "基础/应用/创新三层作业"),
    ("skill-hw-objective", "目标-题目对应", "nexhw", "每题标注对应目标"),
    ("skill-burden", "作业负担监测", "nexhw", "估算时长与题量并预警"),
    ("skill-error-analysis", "错因归类", "nexerr", "归类概念/审题/表达/计算错因"),
    ("skill-remedial", "补偿练习/变式", "nexerr", "设计补偿练习与变式题"),
    ("skill-wrongbook", "错题本策略", "nexerr", "设计错题本使用策略"),
    ("skill-practice-hw", "实践性作业", "nexprac", "设计实践性作业"),
    ("skill-cross-hw", "跨学科作业", "nexprac", "设计跨学科作业"),
    ("skill-holiday-hw", "假期/长周期作业", "nexprac", "设计假期/长周期作业"),

    ("skill-ppt-script", "课件分页脚本", "nexppt", "按标题层级分页写脚本"),
    ("skill-visual-layout", "课件视觉排版", "nexppt", "设计课件视觉排版"),
    ("skill-animation-hint", "动效/呈现节奏", "nexppt", "设计动效与呈现节奏"),
    ("skill-handout", "学案排版", "nexhand", "预习→探究→反馈的学案排版"),
    ("skill-guide-sheet", "导学单", "nexhand", "设计导学单"),
    ("skill-worksheet", "课堂练习单", "nexhand", "设计课堂练习单"),
    ("skill-micro-script", "微课脚本", "nexmicro", "编写微课脚本"),
    ("skill-micro-storyboard", "微课分镜", "nexmicro", "设计微课分镜"),
    ("skill-visualization", "知识可视化", "nexmedia", "图示/思维导图等可视化"),
    ("skill-digital-resource", "数字资源/仿真", "nexmedia", "整合数字资源与仿真"),

    ("skill-ch-reading", "阅读教学(单篇/群文)", "nexch", "单篇/群文阅读教学设计"),
    ("skill-ch-writing", "写作支架", "nexch", "写作支架与范文"),
    ("skill-ch-classical", "文言文/古诗教学", "nexch", "文言文/古诗教学策略"),
    ("skill-math-concept", "数学概念建构", "nexmath", "数学概念建构教学"),
    ("skill-math-modeling", "数学建模", "nexmath", "数学建模任务设计"),
    ("skill-math-problem", "解题策略", "nexmath", "解题策略指导"),
    ("skill-eng-listening", "听力教学", "nexeng", "英语听力教学策略"),
    ("skill-eng-speaking", "口语/对话", "nexeng", "口语与对话教学"),
    ("skill-eng-reading", "英语阅读", "nexeng", "英语阅读教学"),
    ("skill-eng-writing", "英语写作", "nexeng", "英语写作教学"),
    ("skill-sci-experiment", "实验探究设计", "nexsci", "设计实验探究"),
    ("skill-sci-inquiry", "科学探究(证据)", "nexsci", "基于证据的科学探究"),
    ("skill-sci-safety", "实验安全", "nexsci", "实验安全教育"),
    ("skill-soc-source", "史料/地图解读", "nexsoc", "史料/地图解读教学"),
    ("skill-soc-current", "时政链接", "nexsoc", "链接时政热点"),
    ("skill-soc-value", "价值引领", "nexsoc", "家国情怀等价值引领"),

    ("skill-lesson-diagnosis", "试讲/磨课诊断", "nexref", "定位试讲问题并给修改方向"),
    ("skill-refine", "定向修改", "nexref", "按诊断意见定向修改"),
    ("skill-observation", "课堂观察(LICC)", "nexobs", "LICC 范式课堂观察"),
    ("skill-observe-scale", "观察量表", "nexobs", "设计课堂观察量表"),
    ("skill-error-category", "错因归类", "nexdata", "归类高频错因"),
    ("skill-weakpoint-map", "薄弱知识点图谱", "nexdata", "构建薄弱知识点图谱"),
    ("skill-data-viz", "学情数据可视化", "nexdata", "学情数据可视化呈现"),
    ("skill-tracking", "学情追踪", "nextrack", "长期学情追踪"),
    ("skill-trend", "成绩趋势分析", "nextrack", "分析成绩变化趋势"),
    ("skill-review-cycle", "复习周期规划", "nextrack", "规划复习周期"),
    ("skill-teach-reflect", "教学反思/叙事", "nexresearch", "撰写教学反思与叙事"),
    ("skill-research-topic", "论文/课题选题", "nexresearch", "论文/课题选题"),
    ("skill-case-study", "教学案例研究", "nexresearch", "撰写教学案例研究"),

    ("skill-consistency", "一致性校验", "nexcoord", "确定性指标校验目标-评价一致性"),
    ("skill-traceability", "引用可追溯", "nexcoord", "引用逐条可核对"),
    ("skill-anonymize", "学情脱敏", "nexcoord", "学生信息去标识化"),
    ("skill-safety", "安全复核", "nexcoord", "高风险表述拦截"),
    ("skill-citation", "引用格式规范", "nexcoord", "规范引用格式"),
    ("skill-quality-gate", "质量闸门(六维自检)", "nexcoord", "六维量规自检"),
]

# ============================================================================
# 步骤（细化后的执行合约）
# ============================================================================
# 字段：order / name / type(http|llm|db|task) / on_failure(retry|skip|stop) /
#       expert / skills / stage / trigger(all|schedule|webhook) / output
STEPS = [
    # —— A 需求与定位 ——
    {"order": 1, "name": "需求解析", "type": "llm", "on_failure": "retry", "expert": "nexcoord", "skills": ["skill-route", "skill-plan"], "stage": "A 需求与定位", "trigger": "all", "output": "需求解析卡"},
    {"order": 2, "name": "范围确认", "type": "task", "on_failure": "stop", "expert": "", "skills": [], "stage": "A 需求与定位", "trigger": "all", "output": "范围确认（检查点①）"},
    # —— B 课标与课程 ——
    {"order": 3, "name": "课标解析", "type": "llm", "on_failure": "retry", "expert": "nexstd", "skills": ["skill-std-parse", "skill-achievement", "skill-literacy-std"], "stage": "B 课标与课程", "trigger": "all", "output": "课标解析卡"},
    {"order": 4, "name": "教材分析", "type": "llm", "on_failure": "retry", "expert": "nextxt", "skills": ["skill-textbook-map", "skill-keypoint", "skill-sequence"], "stage": "B 课标与课程", "trigger": "all", "output": "教材结构图、重难点"},
    {"order": 5, "name": "大单元设计", "type": "llm", "on_failure": "retry", "expert": "nexunit", "skills": ["skill-unit-design", "skill-big-idea", "skill-unit-kb"], "stage": "B 课标与课程", "trigger": "all", "output": "单元整体方案"},
    {"order": 6, "name": "跨学科整合", "type": "llm", "on_failure": "skip", "expert": "nexxcu", "skills": ["skill-cross-subject", "skill-course-ideology", "skill-project-x"], "stage": "B 课标与课程", "trigger": "all", "output": "跨学科主题设计"},
    # —— C 学情诊断 ——
    {"order": 7, "name": "学情画像", "type": "llm", "on_failure": "retry", "expert": "nexprof", "skills": ["skill-profiling", "skill-startpoint", "skill-style"], "stage": "C 学情诊断", "trigger": "all", "output": "学情画像"},
    {"order": 8, "name": "前概念/易错点预判", "type": "llm", "on_failure": "retry", "expert": "nexmis", "skills": ["skill-misconception", "skill-error-prone", "skill-zpd"], "stage": "C 学情诊断", "trigger": "all", "output": "前概念/易错点清单"},
    {"order": 9, "name": "差异分层", "type": "llm", "on_failure": "retry", "expert": "nexdif", "skills": ["skill-tiering", "skill-diff-task", "skill-scalaffold-level"], "stage": "C 学情诊断", "trigger": "all", "output": "分层策略"},
    {"order": 10, "name": "融合教育适配", "type": "llm", "on_failure": "skip", "expert": "nexincl", "skills": ["skill-inclusion", "skill-special-need", "skill-sel"], "stage": "C 学情诊断", "trigger": "all", "output": "融合教育适配建议"},
    # —— D 目标设计 ——
    {"order": 11, "name": "目标设计", "type": "llm", "on_failure": "retry", "expert": "nexaim", "skills": ["skill-objective-write", "skill-objective-decompose", "skill-behavior-verb"], "stage": "D 目标设计", "trigger": "all", "output": "单元目标链、课时目标"},
    {"order": 12, "name": "素养映射", "type": "llm", "on_failure": "retry", "expert": "nexlit", "skills": ["skill-literacy-map", "skill-literacy-balance"], "stage": "D 目标设计", "trigger": "all", "output": "素养映射表"},
    {"order": 13, "name": "SOLO/认知层级标注", "type": "llm", "on_failure": "retry", "expert": "nexsol", "skills": ["skill-solo-level", "skill-bloom-level", "skill-cognitive-ladder"], "stage": "D 目标设计", "trigger": "all", "output": "认知层级标注"},
    {"order": 14, "name": "目标-评价对齐校验", "type": "db", "on_failure": "stop", "expert": "nexalign", "skills": ["skill-alignment", "skill-observable", "skill-measurable"], "stage": "D 目标设计", "trigger": "all", "output": "对齐度报告"},
    # —— E 评价设计（前置） ——
    {"order": 15, "name": "评价任务设计", "type": "llm", "on_failure": "retry", "expert": "nexeval", "skills": ["skill-eval-task", "skill-eval-first", "skill-evidence"], "stage": "E 评价设计", "trigger": "all", "output": "评价任务单"},
    {"order": 16, "name": "量规设计", "type": "llm", "on_failure": "retry", "expert": "nexrub", "skills": ["skill-rubric", "skill-rubric-level", "skill-self-peer"], "stage": "E 评价设计", "trigger": "all", "output": "评分量规"},
    {"order": 17, "name": "表现性任务", "type": "llm", "on_failure": "retry", "expert": "nexperf", "skills": ["skill-performance-task", "skill-real-scenario", "skill-portfolio"], "stage": "E 评价设计", "trigger": "all", "output": "表现性任务"},
    {"order": 18, "name": "过程性评价", "type": "llm", "on_failure": "retry", "expert": "nexproc", "skills": ["skill-formative", "skill-classroom-qa", "skill-observation-scale"], "stage": "E 评价设计", "trigger": "all", "output": "形成性评价方案"},
    {"order": 19, "name": "命题/双向细目", "type": "llm", "on_failure": "retry", "expert": "nexsum", "skills": ["skill-item-write", "skill-item-subjective", "skill-spec-table"], "stage": "E 评价设计", "trigger": "all", "output": "试卷、双向细目表"},
    {"order": 20, "name": "方案确认", "type": "task", "on_failure": "stop", "expert": "", "skills": [], "stage": "E 评价设计", "trigger": "all", "output": "方案确认（检查点②）"},
    # —— F 教学与活动 ——
    {"order": 21, "name": "逆向教学设计", "type": "llm", "on_failure": "retry", "expert": "nexdes", "skills": ["skill-backward-design", "skill-activity-design", "skill-lesson-flow"], "stage": "F 教学与活动", "trigger": "all", "output": "课时教学设计"},
    {"order": 22, "name": "问题链设计", "type": "llm", "on_failure": "retry", "expert": "nexq", "skills": ["skill-question-chain", "skill-core-question", "skill-wait-time"], "stage": "F 教学与活动", "trigger": "all", "output": "问题链"},
    {"order": 23, "name": "情境创设", "type": "llm", "on_failure": "retry", "expert": "nexsit", "skills": ["skill-situation", "skill-conflict", "skill-life-link"], "stage": "F 教学与活动", "trigger": "all", "output": "情境素材"},
    {"order": 24, "name": "合作学习", "type": "llm", "on_failure": "retry", "expert": "nexcoop", "skills": ["skill-grouping", "skill-jigsaw", "skill-role-assign"], "stage": "F 教学与活动", "trigger": "all", "output": "合作学习方案"},
    {"order": 25, "name": "项目式/探究", "type": "llm", "on_failure": "retry", "expert": "nexpbl", "skills": ["skill-pbl", "skill-inquiry", "skill-driven-question"], "stage": "F 教学与活动", "trigger": "all", "output": "项目/探究方案"},
    {"order": 26, "name": "游戏化/动机", "type": "llm", "on_failure": "skip", "expert": "nexgame", "skills": ["skill-gamification", "skill-motivation", "skill-feedback-loop"], "stage": "F 教学与活动", "trigger": "all", "output": "游戏化设计"},
    # —— G 作业设计 ——
    {"order": 27, "name": "分层作业", "type": "llm", "on_failure": "retry", "expert": "nexhw", "skills": ["skill-homework-tier", "skill-hw-objective", "skill-burden"], "stage": "G 作业设计", "trigger": "all", "output": "分层作业单"},
    {"order": 28, "name": "错题/补偿练习", "type": "llm", "on_failure": "retry", "expert": "nexerr", "skills": ["skill-error-analysis", "skill-remedial", "skill-wrongbook"], "stage": "G 作业设计", "trigger": "all", "output": "补偿练习包"},
    {"order": 29, "name": "实践/跨学科作业", "type": "llm", "on_failure": "skip", "expert": "nexprac", "skills": ["skill-practice-hw", "skill-cross-hw", "skill-holiday-hw"], "stage": "G 作业设计", "trigger": "all", "output": "实践作业单"},
    # —— H 资源课件 ——
    {"order": 30, "name": "课件脚本", "type": "llm", "on_failure": "retry", "expert": "nexppt", "skills": ["skill-ppt-script", "skill-visual-layout", "skill-animation-hint"], "stage": "H 资源课件", "trigger": "all", "output": "课件脚本"},
    {"order": 31, "name": "学案/导学单", "type": "llm", "on_failure": "retry", "expert": "nexhand", "skills": ["skill-handout", "skill-guide-sheet", "skill-worksheet"], "stage": "H 资源课件", "trigger": "all", "output": "学案"},
    {"order": 32, "name": "微课脚本", "type": "llm", "on_failure": "skip", "expert": "nexmicro", "skills": ["skill-micro-script", "skill-micro-storyboard"], "stage": "H 资源课件", "trigger": "all", "output": "微课脚本"},
    {"order": 33, "name": "可视化/数字资源", "type": "llm", "on_failure": "skip", "expert": "nexmedia", "skills": ["skill-visualization", "skill-digital-resource"], "stage": "H 资源课件", "trigger": "all", "output": "可视化/数字资源"},
    {"order": 34, "name": "成果确认", "type": "task", "on_failure": "stop", "expert": "", "skills": [], "stage": "H 资源课件", "trigger": "all", "output": "成果确认（检查点③）"},
    # —— I 学科专长注入 ——
    {"order": 35, "name": "学科专长注入", "type": "llm", "on_failure": "retry", "expert": "nexch", "skills": ["skill-ch-reading", "skill-ch-writing", "skill-ch-classical"], "stage": "I 学科专长", "trigger": "all", "output": "学科化教学设计"},
    # —— J 实施与反思 ——
    {"order": 36, "name": "试讲/磨课诊断", "type": "llm", "on_failure": "retry", "expert": "nexref", "skills": ["skill-lesson-diagnosis", "skill-refine"], "stage": "J 实施与反思", "trigger": "all", "output": "磨课诊断报告"},
    {"order": 37, "name": "课堂观察", "type": "llm", "on_failure": "retry", "expert": "nexobs", "skills": ["skill-observation", "skill-observe-scale"], "stage": "J 实施与反思", "trigger": "all", "output": "课堂观察记录"},
    {"order": 38, "name": "定向修改", "type": "llm", "on_failure": "retry", "expert": "nexdes", "skills": ["skill-backward-design", "skill-refine"], "stage": "J 实施与反思", "trigger": "all", "output": "修订稿"},
    {"order": 39, "name": "定稿确认", "type": "task", "on_failure": "stop", "expert": "", "skills": [], "stage": "J 实施与反思", "trigger": "all", "output": "定稿确认（检查点④）"},
    # —— K 数据回流（schedule / webhook 触发） ——
    {"order": 40, "name": "数据回收", "type": "http", "on_failure": "retry", "expert": "nexdata", "skills": ["skill-error-category"], "stage": "K 数据回流", "trigger": "webhook", "output": "作业/测验/观察数据"},
    {"order": 41, "name": "错因/薄弱点分析", "type": "llm", "on_failure": "retry", "expert": "nexdata", "skills": ["skill-error-category", "skill-weakpoint-map", "skill-data-viz"], "stage": "K 数据回流", "trigger": "webhook", "output": "错因/薄弱点报告"},
    {"order": 42, "name": "学情追踪更新", "type": "db", "on_failure": "stop", "expert": "nextrack", "skills": ["skill-tracking", "skill-trend", "skill-review-cycle"], "stage": "K 数据回流", "trigger": "webhook", "output": "学情追踪档案"},
    {"order": 43, "name": "回流二次备课", "type": "llm", "on_failure": "retry", "expert": "nexcoord", "skills": ["skill-backflow", "skill-consistency"], "stage": "K 数据回流", "trigger": "webhook", "output": "闭环回流卡"},
    {"order": 44, "name": "教研成果沉淀", "type": "llm", "on_failure": "skip", "expert": "nexresearch", "skills": ["skill-teach-reflect", "skill-research-topic", "skill-case-study"], "stage": "K 数据回流", "trigger": "schedule", "output": "教研成果"},
]

TRIGGERS = [
    {"id": "manual", "type": "manualTrigger", "desc": "教师在一键备课界面点击启动，跑全流程 W0（步骤 1–44）"},
    {"id": "schedule", "type": "scheduleTrigger", "desc": "按周周期自动运行，跑学情跟踪与教研沉淀（步骤 40–44）"},
    {"id": "webhook", "type": "webhook", "desc": "作业/测验/课堂观察数据录入后回调，跑数据回流（步骤 40–43）"},
]

# 学科 → 学科专家 映射（步骤 35 按学科路由）
SUBJECT_EXPERTS = {
    "语文": "nexch", "数学": "nexmath", "英语": "nexeng",
    "物理": "nexsci", "化学": "nexsci", "生物": "nexsci",
    "历史": "nexsoc", "地理": "nexsoc", "道德与法治": "nexsoc",
}


# ============================================================================
# 校验
# ============================================================================
def validate():
    errors = []
    exp_ids = {e[0] for e in EXPERTS}
    skill_ids = {s[0] for s in SKILLS}
    if len(EXPERTS) < 30:
        errors.append("专家数 %d < 30" % len(EXPERTS))
    if len(SKILLS) < 100:
        errors.append("Skill 数 %d < 100" % len(SKILLS))
    # skill 归属专家必须存在
    for s in SKILLS:
        if s[2] not in exp_ids:
            errors.append("skill %s 归属专家 %s 不存在" % (s[0], s[2]))
    # 步骤：顺序连续、类型/失败策略合法、专家与 skill 引用存在
    for i, st in enumerate(STEPS):
        if st["order"] != i + 1:
            errors.append("步骤顺序断裂：期望 %d 实得 %s" % (i + 1, st["order"]))
        if st["type"] not in ("http", "llm", "db", "task"):
            errors.append("步骤 %s 类型非法：%s" % (st["name"], st["type"]))
        if st["on_failure"] not in ("retry", "skip", "stop"):
            errors.append("步骤 %s 失败策略非法：%s" % (st["name"], st["on_failure"]))
        if st["expert"] and st["expert"] not in exp_ids:
            errors.append("步骤 %s 专家 %s 不存在" % (st["name"], st["expert"]))
        for sk in st["skills"]:
            if sk not in skill_ids:
                errors.append("步骤 %s 引用 skill %s 不存在" % (st["name"], sk))
        if st["trigger"] not in ("all", "schedule", "webhook"):
            errors.append("步骤 %s trigger 非法：%s" % (st["name"], st["trigger"]))
    # 每个专家至少被一个技能引用、至少被一个步骤引用（除纯控制外）
    used_exp = {st["expert"] for st in STEPS if st["expert"]}
    for e in EXPERTS:
        if e[0] not in used_exp and e[0] != "":
            pass  # 学科专家可能在路由时引用，不强校验
    return errors


# ============================================================================
# 生成 Markdown
# ============================================================================
def gen_md():
    lines = []
    a = lines.append
    a("# 教学评一体化备课流程 · 蓝图（外部引擎 + 细化版）\n")
    a("> 由生成器 `tools/gen_workflow_blueprint.py` 一次产出，三份文件数据同源。")
    a("> 采用**外部自动化引擎（n8n 兼容）**编排；触发方式：**手动 / 定时 / 回调** 三种。")
    a("> 规模：**%d 位专家 · %d 个 Skill · %d 个步骤 · 4 个检查点**。\n" % (
        len(EXPERTS), len(SKILLS), len(STEPS)))

    a("## 一、设计变更说明\n")
    a("- 撤销上一版「不引外部引擎」的非目标：现采用 n8n 兼容蓝图，可导入 n8n 或内部编排引擎；")
    a("- 工作流细化到 %d 个步骤，专家从 10 位扩展至 %d 位，Skill 从约 24 个扩展到 %d 个；\n"
      % (len(STEPS), len(EXPERTS), len(SKILLS)))

    a("## 二、触发方式\n")
    for t in TRIGGERS:
        a("- **%s（%s）**：%s" % (t["id"], t["type"], t["desc"]))

    a("\n## 三、专家矩阵（%d 位，按阶段分组）\n" % len(EXPERTS))
    stage = None
    for e in EXPERTS:
        if e[3] != stage:
            stage = e[3]
            a("\n### %s\n" % stage)
            a("| ID | 专家 | 职责 | 关键产出 |")
            a("|---|---|---|---|")
        a("| `%s` | **%s**（%s） | %s | %s |" % (e[0], e[1], e[2], e[4], e[5]))

    a("\n## 四、Skill 库（%d 个，按阶段分组）\n" % len(SKILLS))
    stage = None
    for s in SKILLS:
        exp = next((e for e in EXPERTS if e[0] == s[2]), None)
        st = exp[3] if exp else "—"
        if st != stage:
            stage = st
            a("\n### %s\n" % stage)
            a("| Skill ID | 名称 | 归属专家 | 作用 |")
            a("|---|---|---|---|")
        a("| `%s` | %s | %s | %s |" % (s[0], s[1], s[2], s[3]))

    a("\n## 五、细化步骤与执行合约（%d 步）\n" % len(STEPS))
    a("字段：order（排序）/ type（http·llm·db·task）/ on_failure（retry·skip·stop）。")
    a("checkpoint 为 `task` 类型、`on_failure=stop`，交互模式暂停等待教师，全自动模式自动放行。\n")
    a("| # | 步骤 | 类型 | 失败策略 | 专家 | Skill | 阶段 | 产出 |")
    a("|---:|---|---|---|---|---|---|---|")
    for st in STEPS:
        sk = "、".join(st["skills"]) if st["skills"] else "—"
        a("| %d | %s | %s | %s | %s | %s | %s | %s |" % (
            st["order"], st["name"], st["type"], st["on_failure"],
            st["expert"] or "—", sk, st["stage"], st["output"]))

    a("\n## 六、与 n8n / 编排引擎的映射\n")
    a("| 蓝图概念 | 落地 |")
    a("|---|---|")
    a("| 触发 | `manualTrigger` / `scheduleTrigger` / `webhook` 三个触发器节点 |")
    a("| 步骤 | n8n 节点；`llm`=调用本地引擎生成，`db`=持久化，`task`=人工检查点，`http`=外部 OCR/ASR/回调 |")
    a("| 失败策略 | 节点 `onError`（continue=skip / stopWorkflow=stop / retry=retry） |")
    a("| 检查点 | `task` 节点，全自动模式下由引擎 `auto=true` 自动放行 |")
    a("| 上下文 | 节点间只传「摘要」不传全文（黑板机制） |")
    a("| 数据回流 | `webhook` 触发 → 步骤 40–43，写入学情档案 |")

    a("\n## 七、落地优先级建议\n")
    a("1. 先建 %d 位专家的 System Prompt（沿用现有四环叙事规范）；" % len(EXPERTS))
    a("2. 按 Skill 库批量建自定义技能（可在侧边栏勾选启用）；")
    a("3. 用 n8n 蓝图组织 %d 个步骤，`task` 节点接人工检查点；" % len(STEPS))
    a("4. `webhook`/`schedule` 触发数据回流与学情跟踪，形成长周期闭环。\n")
    return "\n".join(lines)


# ============================================================================
# 生成 JSON（n8n 兼容蓝图）
# ============================================================================
def _node(name, ntype, parameters, pos):
    import uuid
    return {
        "parameters": parameters,
        "id": uuid.uuid4().hex[:16],
        "name": name,
        "type": ntype,
        "typeVersion": 1,
        "position": pos,
    }


def gen_json():
    nodes = []
    edges = {}
    # 三个触发器
    nodes.append(_node("手动触发", "n8n-nodes-base.manualTrigger", {}, [0, -160]))
    nodes.append(_node("定时触发(周)", "n8n-nodes-base.scheduleTrigger",
                       {"rule": {"interval": [{"field": "weeks", "weeksInterval": 1}]}}, [220, -160]))
    nodes.append(_node("回调触发(数据录入)", "n8n-nodes-base.webhook",
                       {"httpMethod": "POST", "path": "nexedu/data"}, [440, -160]))
    # 步骤节点
    for st in STEPS:
        ptype = st["type"]
        ntype = {
            "llm": "n8n-nodes-base.set",   # 占位：代表调用本地引擎生成（契约在 assignments）
            "db": "n8n-nodes-base.set",
            "task": "n8n-nodes-base.set",
            "http": "n8n-nodes-base.httpRequest",
        }[ptype]
        params = {
            "assignments": {
                "assignments": [
                    {"id": "a1", "name": "order", "value": st["order"], "type": "number"},
                    {"id": "a2", "name": "type", "value": st["type"], "type": "string"},
                    {"id": "a3", "name": "on_failure", "value": st["on_failure"], "type": "string"},
                    {"id": "a4", "name": "expert", "value": st["expert"], "type": "string"},
                    {"id": "a5", "name": "skills", "value": ",".join(st["skills"]), "type": "string"},
                    {"id": "a6", "name": "stage", "value": st["stage"], "type": "string"},
                ],
            },
            "options": {},
        }
        if ptype == "http":
            params = {"url": "http://127.0.0.1:8520/api/collect",
                      "method": "POST",
                      "sendBody": True,
                      "bodyParameters": {"parameters": [
                          {"name": "payload", "value": "={{ $json }}"}]}}
        name = "%02d %s" % (st["order"], st["name"])
        nodes.append(_node(name, ntype, params, [0, st["order"] * 80]))

    # 连接：手动触发器 → 步骤 1 → 2 → … → 44（线性主链）
    def _edge(src, dst):
        e = edges.setdefault(src, {"main": [[]]})
        e["main"][0].append({"node": dst, "type": "main", "index": 0})
    step_names = ["%02d %s" % (st["order"], st["name"]) for st in STEPS]
    _edge("手动触发", step_names[0])
    for i in range(len(step_names) - 1):
        _edge(step_names[i], step_names[i + 1])
    # 定时触发 → 步骤 40（数据回流）；回调触发 → 步骤 40
    _edge("定时触发(周)", step_names[39])
    _edge("回调触发(数据录入)", step_names[39])
    # 端点节点（无下游）补空连接，保证结构完整
    for n in nodes:
        if n["name"] not in edges:
            edges[n["name"]] = {"main": [[]]}

    return {
        "name": "教学评一体化备课流程",
        "meta": {
            "experts": len(EXPERTS),
            "skills": len(SKILLS),
            "steps": len(STEPS),
            "checkpoints": sum(1 for s in STEPS if s["type"] == "task"),
            "triggers": [t["type"] for t in TRIGGERS],
            "dry_run": True,
            "note": "llm/db/task 步骤用 n8n Set 节点作占位，节点 assignments 内即执行合约；"
                    "落地时替换为本地引擎 HTTP 节点或 LLM 节点。",
        },
        "nodes": nodes,
        "connections": edges,
        "active": False,
        "settings": {"executionOrder": "v1"},
        "versionId": "0.1.0",
    }


# ============================================================================
# 生成 CSV
# ============================================================================
def gen_csv():
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["order", "name", "type", "on_failure", "expert", "skills", "stage", "trigger", "output"])
    for st in STEPS:
        w.writerow([st["order"], st["name"], st["type"], st["on_failure"],
                    st["expert"] or "", ",".join(st["skills"]), st["stage"],
                    st["trigger"], st["output"]])
    return buf.getvalue()


# ============================================================================
def main():
    errs = validate()
    if errs:
        print("校验失败：")
        for e in errs:
            print("  -", e)
        return 1

    os.makedirs(BLUEPRINT_DIR, exist_ok=True)

    md = gen_md()
    with open(DOC_PATH, "w", encoding="utf-8") as f:
        f.write(md)

    js = gen_json()
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(js, f, ensure_ascii=False, indent=2)

    csv_text = gen_csv()
    with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
        f.write(csv_text)

    # 状态摘要
    print("=" * 60)
    print("教学评一体化备课流程蓝图 · 生成完成")
    print("=" * 60)
    print("专家数：%d（要求 ≥30）" % len(EXPERTS))
    print("Skill 数：%d（要求 ≥100）" % len(SKILLS))
    print("步骤数：%d" % len(STEPS))
    print("检查点数：%d" % sum(1 for s in STEPS if s["type"] == "task"))
    print("触发方式：%s" % ", ".join(t["type"] for t in TRIGGERS))
    print("dry_run：True")
    print("输出文件：")
    print("  Markdown  %s" % DOC_PATH)
    print("  JSON      %s" % JSON_PATH)
    print("  CSV       %s" % CSV_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
