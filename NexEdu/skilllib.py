# -*- coding: utf-8 -*-
"""内置技能库（Skill Library）：127 个教学领域技能 + 50 个通用能力技能。

数据同源：`docs/教学评一体化备课蓝图.md` 的 Skill 库（127 个，按阶段分组）；
本模块另补 50 个**通用类**技能，覆盖思维性 / 操作性 / 理解性 / 处理性 / 输入输出性
五类，使智能体在非教学环节也具备稳定的通用能力。

每条技能含：
  id     技能 ID（勾选启用时的唯一标识）
  name   名称
  expert 归属专家（general 表示跨专家通用）
  group  分组（阶段名或通用类名）
  desc   一句话作用
  how    执行要点（3–4 步，注入提示词后模型按此执行）
  check  自检要点（产出前的硬性检查）

技能是「附加指令」：内置技能不出现在技能工坊，由对应专家在需要时自动调用
（agents.build_system → skilllib.pick_for）；教师自建技能才走勾选注入。
内置技能只读——不可删除；教师自建技能存于 workspace/skills.json。
"""

# ============================================================================
# 一、教学领域技能（127 个，源自蓝图 Skill 库）
# ============================================================================
DOMAIN_SKILLS = [
    # —— 需求与定位（4） ——
    {"id": "skill-route", "name": "意图路由", "expert": "coordinator", "group": "需求与定位",
     "desc": "识别教师意图并路由到合适专家",
     "how": "抽取学科/学段/课型与动作动词（设计/诊断/命题/分析/改写）；按职责匹配专家；给出首选与备选并说明理由；信息不足时先提一个澄清问题。",
     "check": "路由结论可解释；不越权代写内容。"},
    {"id": "skill-plan", "name": "任务分解规划", "expert": "coordinator", "group": "需求与定位",
     "desc": "把复杂需求拆成可解释的执行计划",
     "how": "按「阶段—节点—负责人—产出」四列拆解；每步标注输入来自哪一步；标出可并行与必须串行的步骤；给出预计步骤数。",
     "check": "步骤之间有清晰依赖；无孤立步骤。"},
    {"id": "skill-arbitrate", "name": "专家仲裁", "expert": "coordinator", "group": "需求与定位",
     "desc": "专家意见不一致时给出裁定",
     "how": "列出分歧点与各方理由；以「目标一致性、学情适配、可操作性」为判据；给出裁定与让步条件；保留少数意见供教师参考。",
     "check": "裁定有判据；不简单和稀泥。"},
    {"id": "skill-backflow", "name": "闭环回流汇总", "expert": "coordinator", "group": "需求与定位",
     "desc": "汇总缺口提示与二次备课建议",
     "how": "汇总各环节遗留缺口；按影响排序；给出二次备课的优先动作；明确哪些必须教师确认。",
     "check": "缺口逐条可追踪；建议可执行。"},

    # —— 课标与课程（12） ——
    {"id": "skill-std-parse", "name": "课标条目解析", "expert": "nexstd", "group": "课标与课程",
     "desc": "把课标条目拆为内容要求/学业质量/素养",
     "how": "定位学段学科条目；拆出内容要求（学什么）；摘出学业质量（学到什么程度）；标注素养维度；列出与教材的对应与缺口。",
     "check": "引用准确到学段；不编造原文。"},
    {"id": "skill-achievement", "name": "学业质量提炼", "expert": "nexstd", "group": "课标与课程",
     "desc": "提炼学业质量水平描述",
     "how": "找出条目中的水平动词与条件；转化为「学生在什么条件下能做到什么」；给出可观察的表现样例。",
     "check": "水平描述可观察、可判分。"},
    {"id": "skill-literacy-std", "name": "课标素养提取", "expert": "nexstd", "group": "课标与课程",
     "desc": "从课标提取核心素养维度",
     "how": "列出本学科素养框架；逐条标注条目对应的素养；说明该素养在本节的具体表现。",
     "check": "素养对应有行为表现支撑。"},
    {"id": "skill-textbook-map", "name": "教材结构图", "expert": "nextxt", "group": "课标与课程",
     "desc": "教材章节→知识结构图",
     "how": "列出本章知识点；按层级与关系连线；标出核心概念与支撑概念；用文本结构图呈现。",
     "check": "层级不倒置；关系标注有方向。"},
    {"id": "skill-keypoint", "name": "重难点预判", "expert": "nextxt", "group": "课标与课程",
     "desc": "预判课时重难点",
     "how": "结合课标与学情判断重点；从抽象性/跨度/前概念干扰判断难点；给出突破难点的具体做法。",
     "check": "重难点的判断有依据；附突破策略。"},
    {"id": "skill-sequence", "name": "知识前后衔接", "expert": "nextxt", "group": "课标与课程",
     "desc": "梳理知识的前后联系与迁移路径",
     "how": "列出前置知识及学生是否已具备；列出后续延伸；标出本节在链条中的位置。",
     "check": "前置缺失时有补启建议。"},
    {"id": "skill-unit-design", "name": "大单元整体设计", "expert": "nexunit", "group": "课标与课程",
     "desc": "以单元为单位的整体方案",
     "how": "确定单元大概念；设计单元目标链；安排课时序列与递进关系；配置单元评价与长周期任务。",
     "check": "课时之间递进而非并列。"},
    {"id": "skill-big-idea", "name": "大概念提取", "expert": "nexunit", "group": "课标与课程",
     "desc": "提炼学科大概念/核心概念",
     "how": "从具体内容中抽象可迁移的概括；用一句话陈述；说明它可迁移到哪些情境。",
     "check": "是概括句而非知识点罗列。"},
    {"id": "skill-unit-kb", "name": "单元知识结构", "expert": "nexunit", "group": "课标与课程",
     "desc": "构建单元知识网络",
     "how": "列出单元知识点与关系；标出节点度（枢纽概念）；指出学生的断点风险。",
     "check": "枢纽概念明确；断点可干预。"},
    {"id": "skill-cross-subject", "name": "跨学科主题整合", "expert": "nexxcu", "group": "课标与课程",
     "desc": "设计跨学科融合主题",
     "how": "确定主学科目标；找真实问题作为载体；分配各学科承担的任务；说明对主目标的增益。",
     "check": "主学科目标不被稀释。"},
    {"id": "skill-course-ideology", "name": "课程思政融合", "expert": "nexxcu", "group": "课标与课程",
     "desc": "把思政元素自然融入学科教学",
     "how": "从学科内容中找内在价值点；用真实人物/事件/数据承载；避免口号式附加；给出融合时机。",
     "check": "融合自然、不贴标签。"},
    {"id": "skill-project-x", "name": "跨学科项目设计", "expert": "nexxcu", "group": "课标与课程",
     "desc": "设计跨学科项目式任务",
     "how": "确定驱动问题；列出各学科贡献点；设计阶段成果与最终产品；给出评价维度。",
     "check": "产品可评价；周期可行。"},

    # —— 学情诊断（12） ——
    {"id": "skill-profiling", "name": "学情画像", "expert": "nexprof", "group": "学情诊断",
     "desc": "生成学情画像",
     "how": "汇总起点证据；描述群体共性与个体差异；标注证据来源；给出教学含义。",
     "check": "结论有证据；学生信息脱敏。"},
    {"id": "skill-startpoint", "name": "学习起点分析", "expert": "nexprof", "group": "学情诊断",
     "desc": "定位学生学习起点",
     "how": "列出本节所需的前置知识；判断已具备/部分具备/未具备；给出补启方案。",
     "check": "判断依据明确；补启不挤占正课。"},
    {"id": "skill-style", "name": "学习风格分析", "expert": "nexprof", "group": "学情诊断",
     "desc": "分析学习风格与偏好",
     "how": "从作业与课堂表现归纳偏好（视觉/听觉/动手/合作）；给出对应的呈现方式建议；避免给学生贴固定标签。",
     "check": "建议多样呈现；不固化标签。"},
    {"id": "skill-misconception", "name": "前概念预判", "expert": "nexmis", "group": "学情诊断",
     "desc": "预判学生前概念/相异构想",
     "how": "列出核心概念；写出学生可能的直觉理解；设计暴露前概念的问题；给出纠正路径。",
     "check": "有暴露与纠正两步，不只列清单。"},
    {"id": "skill-error-prone", "name": "高频易错点预判", "expert": "nexmis", "group": "学情诊断",
     "desc": "预判高频易错点",
     "how": "按概念/审题/表达/计算四类预判；说明每类成因；给出预防性设计。",
     "check": "成因具体到可干预。"},
    {"id": "skill-zpd", "name": "最近发展区定位", "expert": "nexmis", "group": "学情诊断",
     "desc": "定位最近发展区(ZPD)",
     "how": "确定学生独立能达成的水平；确定在支架下能达成的水平；设计支架及其撤除时机。",
     "check": "支架可撤除，非常驻。"},
    {"id": "skill-tiering", "name": "分层分组策略", "expert": "nexdif", "group": "学情诊断",
     "desc": "设计分层分组方案",
     "how": "确定分层维度与组数；说明分组是固定的还是任务驱动的；给出流动机制。",
     "check": "分组可流动；不贴标签。"},
    {"id": "skill-diff-task", "name": "差异化任务设计", "expert": "nexdif", "group": "学情诊断",
     "desc": "为不同水平设计差异化任务",
     "how": "同一目标下设计不同复杂度/不同支架/不同成果要求的任务；保证目标一致。",
     "check": "目标一致而路径不同。"},
    {"id": "skill-scalaffold-level", "name": "支架分层", "expert": "nexdif", "group": "学情诊断",
     "desc": "设计分层学习支架",
     "how": "列出支架类型（范例/提示/问题/图示/模板）；按难度递进；给出撤除条件。",
     "check": "撤除条件明确。"},
    {"id": "skill-inclusion", "name": "融合教育适配", "expert": "nexincl", "group": "学情诊断",
     "desc": "为特殊需求学生做适配",
     "how": "识别需要的便利（时间/材料/表达/环境）；给出课堂可执行的调整；说明不影响他人学习。",
     "check": "建议具体可执行；不越界诊断。"},
    {"id": "skill-special-need", "name": "特殊需求支持", "expert": "nexincl", "group": "学情诊断",
     "desc": "提供个别化支持建议",
     "how": "制定个别化目标（同目标不同路径或降低幅度）；安排同伴支持；提示专业转介边界。",
     "check": "个别化目标仍指向学科核心。"},
    {"id": "skill-sel", "name": "社会情感学习", "expert": "nexincl", "group": "学情诊断",
     "desc": "融入社会情感学习(SEL)要素",
     "how": "从自我认知/自我管理/社会认知/人际技能/负责任决策中选取 1–2 项；嵌入现有活动而非另起炉灶。",
     "check": "嵌入自然；不额外占时。"},

    # —— 目标设计（12） ——
    {"id": "skill-objective-write", "name": "目标叙写(ABCD)", "expert": "nexaim", "group": "目标设计",
     "desc": "主体+行为动词+行为条件+表现标准",
     "how": "按 ABCD 四要素叙写；替换了解/掌握/体会等模糊动词；补上条件与达标标准。",
     "check": "四要素齐全；动词可观测。"},
    {"id": "skill-objective-decompose", "name": "目标分解", "expert": "nexaim", "group": "目标设计",
     "desc": "课标→单元→课时逐级分解",
     "how": "先写课标级目标；再拆单元目标；最后落到课时目标；标注上下级对应关系。",
     "check": "上下级可追溯；不越级。"},
    {"id": "skill-behavior-verb", "name": "行为动词规范", "expert": "nexaim", "group": "目标设计",
     "desc": "替换模糊动词为可观测动词",
     "how": "列出模糊动词；按认知层级给出替代表（说出/举例/比较/解释/设计/论证等）；逐条替换。",
     "check": "替换后目标可观察可评价。"},
    {"id": "skill-literacy-map", "name": "素养映射", "expert": "nexlit", "group": "目标设计",
     "desc": "目标标注素养维度(避贴标签)",
     "how": "逐条目标标注素养；说明该目标中素养的具体表现；区分主导与伴生。",
     "check": "有行为表现支撑；非生硬对应。"},
    {"id": "skill-literacy-balance", "name": "素养均衡检视", "expert": "nexlit", "group": "目标设计",
     "desc": "检视素养维度覆盖是否均衡",
     "how": "统计各素养命中次数；指出过度集中或缺失；给出调整建议（改叙写或补活动）。",
     "check": "统计口径一致；建议可执行。"},
    {"id": "skill-solo-level", "name": "SOLO层级标注", "expert": "nexsol", "group": "目标设计",
     "desc": "用 SOLO 标注认知层级",
     "how": "按前结构/单点/多点/关联/抽象扩展五级标注；说明判定依据；标出层级分布。",
     "check": "判定有依据；分布不断层。"},
    {"id": "skill-bloom-level", "name": "布鲁姆层级", "expert": "nexsol", "group": "目标设计",
     "desc": "用布鲁姆分类标注认知层级",
     "how": "按识记/理解/应用/分析/评价/创造六级标注；给出动词对照；提示层级抬升点。",
     "check": "动词与层级匹配。"},
    {"id": "skill-cognitive-ladder", "name": "认知阶梯设计", "expert": "nexsol", "group": "目标设计",
     "desc": "设计认知递进阶梯",
     "how": "把课时活动按认知层级排序；设计每一级台阶的任务；确保逐级抬升且台阶高度合理。",
     "check": "台阶高度适中；不跳跃。"},
    {"id": "skill-alignment", "name": "目标-评价对齐校验", "expert": "nexalign", "group": "目标设计",
     "desc": "校验目标与评价的对齐度",
     "how": "建立目标—评价对应表；逐条计算覆盖；输出对齐度与缺口；低于阈值提示返工。",
     "check": "每条目标有证据；缺口逐条列出。"},
    {"id": "skill-observable", "name": "可观测性校验", "expert": "nexalign", "group": "目标设计",
     "desc": "校验目标是否可观测",
     "how": "检查是否有行为动词与可见产物；把不可观测表述改写为可观测表述。",
     "check": "改写后可直接观察或收取产物。"},
    {"id": "skill-measurable", "name": "可评价性校验", "expert": "nexalign", "group": "目标设计",
     "desc": "校验目标是否可评价",
     "how": "检查是否给出表现标准（正确率/完成度/质量等级）；补上缺失的标准。",
     "check": "标准可操作、可判分。"},
    {"id": "skill-objective-gap", "name": "目标缺口提示", "expert": "nexalign", "group": "目标设计",
     "desc": "提示目标与评价的缺口",
     "how": "列出「有目标无评价」与「有评价无目标」两类缺口；给出补齐建议。",
     "check": "两类缺口分别列出。"},

    # —— 评价设计（15） ——
    {"id": "skill-eval-task", "name": "评价任务设计", "expert": "nexeval", "group": "评价设计",
     "desc": "设计评价任务",
     "how": "针对每条目标设计评价任务；明确任务形式、材料与要求；说明收集方式。",
     "check": "任务与目标对应；材料可准备。"},
    {"id": "skill-eval-first", "name": "评价前置(逆向)", "expert": "nexeval", "group": "评价设计",
     "desc": "先定评价再设计教学",
     "how": "在写教学活动之前先定评价；说明评价如何驱动活动设计；避免事后补评价。",
     "check": "活动服务于评价证据的收集。"},
    {"id": "skill-evidence", "name": "评价证据设计", "expert": "nexeval", "group": "评价设计",
     "desc": "为每个目标设计评价证据",
     "how": "列出证据类型（作品/表现/答题/观察记录）；说明判读方式；给出证据样例。",
     "check": "证据可收集、可判读。"},
    {"id": "skill-rubric", "name": "量规设计", "expert": "nexrub", "group": "评价设计",
     "desc": "设计评分量规",
     "how": "确定 3–5 个维度；撰写等级描述；配平权重；给出示例锚定。",
     "check": "维度互斥；描述可观察。"},
    {"id": "skill-rubric-level", "name": "等级描述", "expert": "nexrub", "group": "评价设计",
     "desc": "撰写各等级表现描述",
     "how": "按优秀/良好/达标/待改进写出差异化表现；避免「较好/一般」这类无信息描述。",
     "check": "相邻等级差异可辨别。"},
    {"id": "skill-self-peer", "name": "自评互评表", "expert": "nexrub", "group": "评价设计",
     "desc": "设计自评/互评表",
     "how": "给出学生能读懂的条目；配 3–4 级评分；写明互评的礼貌与证据要求。",
     "check": "学生可独立填写。"},
    {"id": "skill-performance-task", "name": "表现性任务", "expert": "nexperf", "group": "评价设计",
     "desc": "设计表现性任务",
     "how": "设定角色、对象、情境与产品；明确质量标准；说明过程记录方式。",
     "check": "产品可评价；情境有意义。"},
    {"id": "skill-real-scenario", "name": "真实情境评价", "expert": "nexperf", "group": "评价设计",
     "desc": "用真实情境做评价",
     "how": "选取真实场景与真实受众；把知识置于需解决的任务中；说明评价点。",
     "check": "情境真实；评价点清晰。"},
    {"id": "skill-portfolio", "name": "成长档案袋", "expert": "nexperf", "group": "评价设计",
     "desc": "设计成长档案袋评价",
     "how": "确定收录内容（作品/反思/评价表）；设计收录节奏；设计回顾与自评方式。",
     "check": "收录可坚持；回顾有结构。"},
    {"id": "skill-formative", "name": "过程性评价", "expert": "nexproc", "group": "评价设计",
     "desc": "设计形成性评价",
     "how": "确定课堂关键时点；选择轻量工具；规定收到证据后的调整动作。",
     "check": "轻量可嵌入；有后续动作。"},
    {"id": "skill-classroom-qa", "name": "课堂提问评价", "expert": "nexproc", "group": "评价设计",
     "desc": "设计课堂提问评价",
     "how": "设计检测性提问与追问；安排提问对象覆盖；规定候答时间与反馈方式。",
     "check": "提问覆盖不同水平学生。"},
    {"id": "skill-observation-scale", "name": "课堂观察量表", "expert": "nexproc", "group": "评价设计",
     "desc": "设计课堂观察量表",
     "how": "确定观察维度；给出行为指标；设计记录方式（频次/等级/事件）。",
     "check": "指标可观察；记录不占满课堂。"},
    {"id": "skill-item-write", "name": "客观题命题", "expert": "nexsum", "group": "评价设计",
     "desc": "命制选择题/填空题",
     "how": "编题干与干扰项；干扰项来自典型错误；避免暗示与歧义；给出答案与解析。",
     "check": "答案唯一；干扰项合理。"},
    {"id": "skill-item-subjective", "name": "主观题命题", "expert": "nexsum", "group": "评价设计",
     "desc": "命制简答/论述题",
     "how": "明确设问动词与限定条件；列出评分要点与给分档；给出示例作答。",
     "check": "评分要点完整；作答量适中。"},
    {"id": "skill-spec-table", "name": "双向细目表", "expert": "nexsum", "group": "评价设计",
     "desc": "知识点×认知层级×题型细目表",
     "how": "列出知识点与认知层级；分配题型与分值；检查覆盖率与难度梯度。",
     "check": "覆盖单元目标；梯度合理。"},

    # —— 教学与活动（18） ——
    {"id": "skill-backward-design", "name": "逆向教学设计", "expert": "nexdes", "group": "教学与活动",
     "desc": "目标→评价→活动的逆向设计",
     "how": "先明确期望结果；再定评价证据；最后设计活动；检查三者一致。",
     "check": "活动可追溯到目标。"},
    {"id": "skill-activity-design", "name": "学习活动设计", "expert": "nexdes", "group": "教学与活动",
     "desc": "设计学习活动",
     "how": "写清活动的任务、材料、步骤、时间、分组与产出；说明它与哪条目标对应。",
     "check": "步骤可执行；时间可控。"},
    {"id": "skill-lesson-flow", "name": "课堂流程编排", "expert": "nexdes", "group": "教学与活动",
     "desc": "编排课堂流程",
     "how": "按导入—探究—建构—应用—小结编排；标注每段时长与教师/学生行为；留出弹性时间。",
     "check": "时长合计等于课时；有缓冲。"},
    {"id": "skill-question-chain", "name": "问题链设计", "expert": "nexq", "group": "教学与活动",
     "desc": "核心问题→驱动问题→子问题",
     "how": "提炼核心问题；拆为 2–3 个驱动问题；再拆为课堂子问题；标注提问时机。",
     "check": "子问题支撑核心问题。"},
    {"id": "skill-core-question", "name": "核心/驱动问题", "expert": "nexq", "group": "教学与活动",
     "desc": "提炼核心/驱动问题",
     "how": "从大概念反提问；确保问题开放、有争议空间、可持续追问；避免是非问。",
     "check": "问题开放且指向本质。"},
    {"id": "skill-wait-time", "name": "追问与候答", "expert": "nexq", "group": "教学与活动",
     "desc": "设计追问与候答策略",
     "how": "规定候答时长（3–5 秒）；预设追问链（为什么/怎么知道/还有别的吗）；规定叫答顺序。",
     "check": "候答时间明确；追问有层级。"},
    {"id": "skill-situation", "name": "真实情境创设", "expert": "nexsit", "group": "教学与活动",
     "desc": "创设真实情境",
     "how": "选取真实素材；说明它与知识的连接点；设计进入与退出方式；控制时长。",
     "check": "情境服务于知识；不喧宾夺主。"},
    {"id": "skill-conflict", "name": "认知冲突设计", "expert": "nexsit", "group": "教学与活动",
     "desc": "设计认知冲突",
     "how": "利用前概念与现象的反差制造冲突；给出冲突的呈现方式；设计化解路径。",
     "check": "冲突指向核心概念；可化解。"},
    {"id": "skill-life-link", "name": "生活联系", "expert": "nexsit", "group": "教学与活动",
     "desc": "建立知识与生活的联系",
     "how": "找出知识在生活中的应用场景；设计「用所学解释生活现象」的任务。",
     "check": "联系真实；不牵强。"},
    {"id": "skill-grouping", "name": "小组分组", "expert": "nexcoop", "group": "教学与活动",
     "desc": "设计分组方案",
     "how": "确定组规模与异质/同质策略；说明分工依据；给出调整机制。",
     "check": "分组理由明确；可调整。"},
    {"id": "skill-jigsaw", "name": "拼图法/互学", "expert": "nexcoop", "group": "教学与活动",
     "desc": "设计拼图式互学",
     "how": "切分子任务；专家组学习后回组互教；设计互教质量检查；安排个人测验。",
     "check": "互教有质量检查环节。"},
    {"id": "skill-role-assign", "name": "角色分工", "expert": "nexcoop", "group": "教学与活动",
     "desc": "设计小组角色分工",
     "how": "设置主持人/记录员/计时员/发言人/质疑者等；写明职责；安排轮换。",
     "check": "角色有实质职责；可轮换。"},
    {"id": "skill-pbl", "name": "项目式学习", "expert": "nexpbl", "group": "教学与活动",
     "desc": "设计 PBL 项目",
     "how": "确定驱动问题与产品；拆解里程碑；配置资源与支架；设计公开成果展示。",
     "check": "产品明确；里程碑可检。"},
    {"id": "skill-inquiry", "name": "探究式学习", "expert": "nexpbl", "group": "教学与活动",
     "desc": "设计探究式学习",
     "how": "提出问题—猜想—设计—收集证据—论证—交流；强调证据与变量控制。",
     "check": "变量控制明确；证据充分。"},
    {"id": "skill-driven-question", "name": "驱动问题链", "expert": "nexpbl", "group": "教学与活动",
     "desc": "设计项目驱动问题链",
     "how": "由一个总驱动问题拆出阶段性问题；每个阶段问题对应一个产出。",
     "check": "问题与产出一一对应。"},
    {"id": "skill-gamification", "name": "游戏化机制", "expert": "nexgame", "group": "教学与活动",
     "desc": "设计游戏化机制",
     "how": "选取机制（任务/积分/关卡/徽章/排行榜）；绑定学习目标；控制外在奖励强度。",
     "check": "机制服务目标；不过度奖励。"},
    {"id": "skill-motivation", "name": "动机激发", "expert": "nexgame", "group": "教学与活动",
     "desc": "设计动机激发策略",
     "how": "从自主性/胜任感/归属感入手；给出开场钩子与意义说明；设计可达成的挑战。",
     "check": "策略与内容相关；不靠空洞口号。"},
    {"id": "skill-feedback-loop", "name": "即时反馈", "expert": "nexgame", "group": "教学与活动",
     "desc": "设计即时反馈循环",
     "how": "设计反馈触发点；反馈要具体、及时、可行动；安排二次尝试机会。",
     "check": "反馈可行动；有重试机会。"},

    # —— 作业设计（9） ——
    {"id": "skill-homework-tier", "name": "分层作业", "expert": "nexhw", "group": "作业设计",
     "desc": "基础/应用/创新三层作业",
     "how": "按三层设计题单；标注层间梯度；给出选做规则；控制各层题量。",
     "check": "梯度清晰；总量可控。"},
    {"id": "skill-hw-objective", "name": "目标-题目对应", "expert": "nexhw", "group": "作业设计",
     "desc": "每题标注对应目标",
     "how": "制表列出题目—目标—认知层级；检查每条目标是否被覆盖；标注重复覆盖。",
     "check": "覆盖完整；重复有说明。"},
    {"id": "skill-burden", "name": "作业负担监测", "expert": "nexhw", "group": "作业设计",
     "desc": "估算时长与题量并预警",
     "how": "逐题估时；汇总总时长；对照学段建议时长；超限则给出删减建议。",
     "check": "估算有依据；超限给出方案。"},
    {"id": "skill-error-analysis", "name": "错因归类", "expert": "nexerr", "group": "作业设计",
     "desc": "归类概念/审题/表达/计算错因",
     "how": "按四类归因；标注每类的占比；指出共性错因；给出课堂干预动作。",
     "check": "归因有证据；干预具体。"},
    {"id": "skill-remedial", "name": "补偿练习/变式", "expert": "nexerr", "group": "作业设计",
     "desc": "设计补偿练习与变式题",
     "how": "针对薄弱点设计变式（改条件/改数据/改问法/逆推）；控制题量（3–5 题）。",
     "check": "变式有梯度；不重复原题。"},
    {"id": "skill-wrongbook", "name": "错题本策略", "expert": "nexerr", "group": "作业设计",
     "desc": "设计错题本使用策略",
     "how": "规定记录字段（原题/错答/错因/正解/同类题）；设计复练节奏（1 天/3 天/1 周）。",
     "check": "字段精简可坚持。"},
    {"id": "skill-practice-hw", "name": "实践性作业", "expert": "nexprac", "group": "作业设计",
     "desc": "设计实践性作业",
     "how": "明确实践任务与成果；说明材料与安全；设计过程记录；给出展示方式。",
     "check": "安全可行；成果可展示。"},
    {"id": "skill-cross-hw", "name": "跨学科作业", "expert": "nexprac", "group": "作业设计",
     "desc": "设计跨学科作业",
     "how": "确定学科结合点；设计需要两科知识才能完成的任务；说明评价维度。",
     "check": "确需两科；评价维度明确。"},
    {"id": "skill-holiday-hw", "name": "假期/长周期作业", "expert": "nexprac", "group": "作业设计",
     "desc": "设计假期/长周期作业",
     "how": "拆成周任务；设置中途提交点；控制总量；设计开学展示与评价。",
     "check": "有中途节点；总量可控。"},

    # —— 资源课件（10） ——
    {"id": "skill-ppt-script", "name": "课件分页脚本", "expert": "nexppt", "group": "资源课件",
     "desc": "按标题层级分页写脚本",
     "how": "一页一意；每页写标题、要点（≤5 条）、图示建议、教师备注；标注页码与切换。",
     "check": "一页一要点；备注可讲。"},
    {"id": "skill-visual-layout", "name": "课件视觉排版", "expert": "nexppt", "group": "资源课件",
     "desc": "设计课件视觉排版",
     "how": "确定标题层级与字号；控制每页信息量；给出配色与留白建议；检查对比度。",
     "check": "字号可读；信息不过载。"},
    {"id": "skill-animation-hint", "name": "动效/呈现节奏", "expert": "nexppt", "group": "资源课件",
     "desc": "设计动效与呈现节奏",
     "how": "标注分步呈现顺序；动效只用于表示关系与顺序；给出每页预计停留时间。",
     "check": "动效有信息功能；节奏合理。"},
    {"id": "skill-handout", "name": "学案排版", "expert": "nexhand", "group": "资源课件",
     "desc": "预习→探究→反馈的学案排版",
     "how": "三段结构组织；留白便于书写；标注时间；控制总页数。",
     "check": "结构完整；书写量适中。"},
    {"id": "skill-guide-sheet", "name": "导学单", "expert": "nexhand", "group": "资源课件",
     "desc": "设计导学单",
     "how": "列出学习目标、导学问题、资源、自检；问题学生可独立作答。",
     "check": "问题可自学作答。"},
    {"id": "skill-worksheet", "name": "课堂练习单", "expert": "nexhand", "group": "资源课件",
     "desc": "设计课堂练习单",
     "how": "3–5 题当堂练习；标注用时；配自检答案；留出分层选做。",
     "check": "当堂可完成；有自检。"},
    {"id": "skill-micro-script", "name": "微课脚本", "expert": "nexmicro", "group": "资源课件",
     "desc": "编写微课脚本",
     "how": "开场钩子 15 秒；单一知识点；口语化逐句稿；总时长 5–10 分钟。",
     "check": "一课一点；时长可控。"},
    {"id": "skill-micro-storyboard", "name": "微课分镜", "expert": "nexmicro", "group": "资源课件",
     "desc": "设计微课分镜",
     "how": "按时间轴分镜；每格写画面、口播、字幕、时长；标注转场。",
     "check": "画面与口播同步。"},
    {"id": "skill-visualization", "name": "知识可视化", "expert": "nexmedia", "group": "资源课件",
     "desc": "图示/思维导图等可视化",
     "how": "选择图式类型；给出绘制要点与数据；说明课堂上如何使用。",
     "check": "图式匹配内容；不过载。"},
    {"id": "skill-digital-resource", "name": "数字资源/仿真", "expert": "nexmedia", "group": "资源课件",
     "desc": "整合数字资源与仿真",
     "how": "给出资源类型与使用环节；说明所需设备与备用方案；提示使用风险。",
     "check": "有备用方案；风险已提示。"},

    # —— 学科专长（16） ——
    {"id": "skill-ch-reading", "name": "阅读教学(单篇/群文)", "expert": "nexch", "group": "学科专长",
     "desc": "单篇/群文阅读教学设计",
     "how": "确定文本特质与阅读目标；设计初读/精读/研读活动；群文时给出议题与比读支架。",
     "check": "活动指向语言运用。"},
    {"id": "skill-ch-writing", "name": "写作支架", "expert": "nexch", "group": "学科专长",
     "desc": "写作支架与范文",
     "how": "提供构思支架（提纲/思维导图/句式）；给范文并标注可仿之处；设计升格与互评。",
     "check": "支架可用；范文可仿可评。"},
    {"id": "skill-ch-classical", "name": "文言文/古诗教学", "expert": "nexch", "group": "学科专长",
     "desc": "文言文/古诗教学策略",
     "how": "设计诵读—疏通—赏读—积累路径；落实字词与文化常识；避免逐字翻译到底。",
     "check": "诵读与积累到位。"},
    {"id": "skill-math-concept", "name": "数学概念建构", "expert": "nexmath", "group": "学科专长",
     "desc": "数学概念建构教学",
     "how": "从具体情境引入；经历具象—表象—抽象；用反例澄清概念边界；给出概念图。",
     "check": "建构过程完整；有反例。"},
    {"id": "skill-math-modeling", "name": "数学建模", "expert": "nexmath", "group": "学科专长",
     "desc": "数学建模任务设计",
     "how": "给出真实问题；明确假设与变量；建模求解；回检现实合理性；反思模型局限。",
     "check": "假设明确；有回检环节。"},
    {"id": "skill-math-problem", "name": "解题策略", "expert": "nexmath", "group": "学科专长",
     "desc": "解题策略指导",
     "how": "按审题—表征—策略—求解—回顾五步示范；暴露思维过程；配变式巩固。",
     "check": "过程外显；有变式。"},
    {"id": "skill-eng-listening", "name": "听力教学", "expert": "nexeng", "group": "学科专长",
     "desc": "英语听力教学策略",
     "how": "听前激活—听中任务分层—听后输出；控制语速与遍数；给听力策略提示。",
     "check": "任务分层；策略可教。"},
    {"id": "skill-eng-speaking", "name": "口语/对话", "expert": "nexeng", "group": "学科专长",
     "desc": "口语与对话教学",
     "how": "设定交际目的与情境；提供功能句支架；设计 pair work 与展示；给纠错方式。",
     "check": "有交际目的；支架充分。"},
    {"id": "skill-eng-reading", "name": "英语阅读", "expert": "nexeng", "group": "学科专长",
     "desc": "英语阅读教学",
     "how": "读前预测—读中获取信息—读后迁移；处理词汇策略；设计读后输出任务。",
     "check": "输入输出衔接。"},
    {"id": "skill-eng-writing", "name": "英语写作", "expert": "nexeng", "group": "学科专长",
     "desc": "英语写作教学",
     "how": "提供语篇结构与句型支架；过程写作（草稿—反馈—修改）；用量规评价。",
     "check": "支架与量规齐备。"},
    {"id": "skill-sci-experiment", "name": "实验探究设计", "expert": "nexsci", "group": "学科专长",
     "desc": "设计实验探究",
     "how": "明确变量与控制；写清步骤与数据表；预设异常与处理；给出结论表达方式。",
     "check": "变量控制明确；有异常处理。"},
    {"id": "skill-sci-inquiry", "name": "科学探究(证据)", "expert": "nexsci", "group": "学科专长",
     "desc": "基于证据的科学探究",
     "how": "问题—猜想—证据—论证—交流；强调证据的充分性与反驳；区分事实与解释。",
     "check": "事实与解释分开。"},
    {"id": "skill-sci-safety", "name": "实验安全", "expert": "nexsci", "group": "学科专长",
     "desc": "实验安全教育",
     "how": "列出危险源与防护；写明操作规程与应急处理；提示废弃物处理。",
     "check": "危险源与应急齐备。"},
    {"id": "skill-soc-source", "name": "史料/地图解读", "expert": "nexsoc", "group": "学科专长",
     "desc": "史料/地图解读教学",
     "how": "教解读方法（出处/立场/时空定位/互证）；设计问题链；引导多源互证。",
     "check": "方法可迁移；多源互证。"},
    {"id": "skill-soc-current", "name": "时政链接", "expert": "nexsoc", "group": "学科专长",
     "desc": "链接时政热点",
     "how": "选取与知识点相关的时事；核对事实与来源；设计分析任务；避免立场先行。",
     "check": "来源可靠；事实准确。"},
    {"id": "skill-soc-value", "name": "价值引领", "expert": "nexsoc", "group": "学科专长",
     "desc": "家国情怀等价值引领",
     "how": "从学科内容中自然生成价值议题；用真实事迹与数据承载；设计讨论与反思。",
     "check": "引领自然；不空洞说教。"},

    # —— 反思与数据（13） ——
    {"id": "skill-lesson-diagnosis", "name": "试讲/磨课诊断", "expert": "nexref", "group": "反思与数据",
     "desc": "定位试讲问题并给修改方向",
     "how": "对照目标逐段检视；用时间点与行为作证据；按影响排序；给出可执行的修改。",
     "check": "问题有证据；建议可执行。"},
    {"id": "skill-refine", "name": "定向修改", "expert": "nexref", "group": "反思与数据",
     "desc": "按诊断意见定向修改",
     "how": "逐条对应诊断意见修改；标注修改位置与前后差异；说明修改后预期效果。",
     "check": "修改可追溯；有前后对照。"},
    {"id": "skill-observation", "name": "课堂观察(LICC)", "expert": "nexobs", "group": "反思与数据",
     "desc": "LICC 范式课堂观察",
     "how": "按学生学习/教师教学/课程性质/课堂文化四维度记录；先记事实再作推论。",
     "check": "事实与推论分开。"},
    {"id": "skill-observe-scale", "name": "观察量表", "expert": "nexobs", "group": "反思与数据",
     "desc": "设计课堂观察量表",
     "how": "确定维度与指标；选择记录方式（频次/等级/事件抽样）；控制指标数量。",
     "check": "指标可观察；数量适中。"},
    {"id": "skill-error-category", "name": "错因归类", "expert": "nexdata", "group": "反思与数据",
     "desc": "归类高频错因",
     "how": "制定分类口径；逐题归类；统计占比；指出共性问题与个别问题。",
     "check": "口径一致；统计可复核。"},
    {"id": "skill-weakpoint-map", "name": "薄弱知识点图谱", "expert": "nexdata", "group": "反思与数据",
     "desc": "构建薄弱知识点图谱",
     "how": "把薄弱点映射到知识结构；标出关联薄弱点；指出关键断点。",
     "check": "映射准确；断点明确。"},
    {"id": "skill-data-viz", "name": "学情数据可视化", "expert": "nexdata", "group": "反思与数据",
     "desc": "学情数据可视化呈现",
     "how": "选图（柱状/折线/饼图）；标注样本量与口径；给出一句话结论；避免误导性刻度。",
     "check": "图表可读；刻度不误导。"},
    {"id": "skill-tracking", "name": "学情追踪", "expert": "nextrack", "group": "反思与数据",
     "desc": "长期学情追踪",
     "how": "确定稳定指标；按时间记录；标注关键事件（换教材/考试改革）；给出趋势判断。",
     "check": "指标可比；解释谨慎。"},
    {"id": "skill-trend", "name": "成绩趋势分析", "expert": "nextrack", "group": "反思与数据",
     "desc": "分析成绩变化趋势",
     "how": "看趋势而非单点；区分波动与转折；结合教学事件解释；说明不确定性。",
     "check": "区分波动与趋势。"},
    {"id": "skill-review-cycle", "name": "复习周期规划", "expert": "nextrack", "group": "反思与数据",
     "desc": "规划复习周期",
     "how": "按间隔重现排期（当天/次日/一周/一月）；按薄弱点加权；控制每次复习量。",
     "check": "间隔合理；量可控。"},
    {"id": "skill-teach-reflect", "name": "教学反思/叙事", "expert": "nexresearch", "group": "反思与数据",
     "desc": "撰写教学反思与叙事",
     "how": "描述事实—分析原因—提炼经验—给出改进；避免空泛感想；引用具体课堂细节。",
     "check": "有具体细节；有改进动作。"},
    {"id": "skill-research-topic", "name": "论文/课题选题", "expert": "nexresearch", "group": "反思与数据",
     "desc": "论文/课题选题",
     "how": "从真实问题出发；说明研究价值与可行性；给出研究问题与大致方法；提示文献方向。",
     "check": "问题来自实践；可行。"},
    {"id": "skill-case-study", "name": "教学案例研究", "expert": "nexresearch", "group": "反思与数据",
     "desc": "撰写教学案例研究",
     "how": "背景—问题—过程—结果—反思五段；保留真实细节与数据；给出可迁移的结论。",
     "check": "细节真实；结论可迁移。"},

    # —— 跨环节治理（6，所有节点默认可用） ——
    {"id": "skill-consistency", "name": "一致性校验", "expert": "coordinator", "group": "跨环节治理",
     "desc": "确定性指标校验目标-评价一致性",
     "how": "建立目标—评价对应表；计算覆盖率；低于阈值触发返工提示；不额外调用模型猜测。",
     "check": "计算口径明确；阈值明确。"},
    {"id": "skill-traceability", "name": "引用可追溯", "expert": "coordinator", "group": "跨环节治理",
     "desc": "引用逐条可核对",
     "how": "关键结论后附 [来源:xxx]；不确定的标注待核；不杜撰文献与文件名。",
     "check": "每条引用可核对。"},
    {"id": "skill-anonymize", "name": "学情脱敏", "expert": "coordinator", "group": "跨环节治理",
     "desc": "学生信息去标识化",
     "how": "不出现真实姓名/学号/联系方式/家庭信息；用「学生A」等代号；不公开排名。",
     "check": "无直接标识符；不排名。"},
    {"id": "skill-safety", "name": "安全复核", "expert": "coordinator", "group": "跨环节治理",
     "desc": "高风险表述拦截",
     "how": "检查高危实验、体罚或羞辱性表述、医疗与心理判断、敏感内容；命中则改写或提示。",
     "check": "高风险项已改写或提示。"},
    {"id": "skill-citation", "name": "引用格式规范", "expert": "coordinator", "group": "跨环节治理",
     "desc": "规范引用格式",
     "how": "统一使用 [来源:名称] 或文末来源列表；课标引用写明学段与学科；网络来源写明出处。",
     "check": "格式统一；要素完整。"},
    {"id": "skill-quality-gate", "name": "质量闸门(六维自检)", "expert": "coordinator", "group": "跨环节治理",
     "desc": "六维量规自检",
     "how": "按一致性、层次性、可观测性、可评价性、负担可控性、伦理安全性六维逐项自检；"
           "输出简表并说明不达标项的处理。",
     "check": "六维逐项有结论。"},
]

# ============================================================================
# 二、通用能力技能（50 个）：思维性 / 操作性 / 理解性 / 处理性 / 输入输出性
# ============================================================================
GENERAL_SKILLS = [
    # —— 思维性（10） ——
    {"id": "skill-think-critical", "name": "批判性审视", "expert": "general", "group": "通用·思维性",
     "desc": "对结论与论据做质疑式检验",
     "how": "区分事实/推断/观点；检查论据是否支撑结论；列出反例与替代解释；说明置信度。",
     "check": "质疑指向论证而非人；不确定处明确标注。"},
    {"id": "skill-think-system", "name": "系统思维", "expert": "general", "group": "通用·思维性",
     "desc": "从要素、关系与反馈看问题",
     "how": "列出要素与关系；标出反馈回路（增强/调节）；指出延迟与副作用；给出杠杆点。",
     "check": "有反馈回路；不只看线性因果。"},
    {"id": "skill-think-firstprinciple", "name": "第一性原理拆解", "expert": "general", "group": "通用·思维性",
     "desc": "回到基本事实重新推导",
     "how": "剥离类比与惯例；列出不可再拆的基本事实；从事实重新推导；对比既有做法的差异。",
     "check": "基本事实明确；推导可复现。"},
    {"id": "skill-think-reverse", "name": "逆向思考", "expert": "general", "group": "通用·思维性",
     "desc": "从目标或失败倒推路径",
     "how": "先定义成功/失败的终局；倒推必要条件；找出最难满足的条件优先解决。",
     "check": "倒推链条完整；关键障碍已识别。"},
    {"id": "skill-think-decompose", "name": "结构化拆解", "expert": "general", "group": "通用·思维性",
     "desc": "用 MECE 方式拆解问题",
     "how": "确定拆解维度；保证各部分互斥且穷尽；层层下钻 2–3 层；标注每部分的处理方式。",
     "check": "互斥且穷尽；层级清晰。"},
    {"id": "skill-think-analogy", "name": "类比迁移", "expert": "general", "group": "通用·思维性",
     "desc": "从相似域迁移解法",
     "how": "找结构相似的已知问题；比对要素映射关系；迁移解法并验证差异；说明类比失效边界。",
     "check": "映射关系明确；边界已说明。"},
    {"id": "skill-think-hypothesis", "name": "假设检验", "expert": "general", "group": "通用·思维性",
     "desc": "提出假设并设计检验",
     "how": "提出可证伪的假设；设计最小检验；明确判据；给出检验后的结论与下一步。",
     "check": "假设可证伪；判据明确。"},
    {"id": "skill-think-dialectic", "name": "辩证权衡", "expert": "general", "group": "通用·思维性",
     "desc": "同时呈现正反两面并权衡",
     "how": "列出支持与反对理由；给出权重依据；形成有条件结论；说明何种条件下结论会反转。",
     "check": "两面均有论据；结论有条件。"},
    {"id": "skill-think-metacog", "name": "元认知复盘", "expert": "general", "group": "通用·思维性",
     "desc": "对思考过程本身做复盘",
     "how": "回顾用了什么策略；哪里卡住、为什么；下次换什么策略；形成可复用的检查点。",
     "check": "复盘指向策略而非结果好坏。"},
    {"id": "skill-think-decision", "name": "决策矩阵", "expert": "general", "group": "通用·思维性",
     "desc": "多方案量化比较后决策",
     "how": "列出候选方案；确定评价维度与权重；逐项打分；给出推荐与敏感性说明。",
     "check": "维度与权重公开；敏感性已说明。"},

    # —— 操作性（10） ——
    {"id": "skill-op-checklist", "name": "清单执行", "expert": "general", "group": "通用·操作性",
     "desc": "把流程固化为可勾选清单",
     "how": "按顺序列出动作项；每项给完成判据；执行后逐项打勾；未通过项说明原因。",
     "check": "每项有完成判据。"},
    {"id": "skill-op-sop", "name": "SOP 编排", "expert": "general", "group": "通用·操作性",
     "desc": "把重复任务编成标准作业流程",
     "how": "写清触发条件、步骤、责任人、异常分支与完成标准；控制步骤数在 7±2。",
     "check": "有异常分支；有完成标准。"},
    {"id": "skill-op-template", "name": "模板套用", "expert": "general", "group": "通用·操作性",
     "desc": "按既有模板产出内容",
     "how": "识别模板字段；逐字段填充；缺值处明确留空并说明；不擅自改模板结构。",
     "check": "字段齐全；缺值已标注。"},
    {"id": "skill-op-diff", "name": "版本对比", "expert": "general", "group": "通用·操作性",
     "desc": "对比两版本的差异",
     "how": "按条列出增/删/改；标注差异影响；给出是否采纳的建议；保留原文对照。",
     "check": "差异逐条可追溯。"},
    {"id": "skill-op-batch", "name": "批量处理", "expert": "general", "group": "通用·操作性",
     "desc": "对同类对象批量执行同一操作",
     "how": "确定对象集合与统一规则；逐项执行并记录；失败的单独列出；给汇总结果。",
     "check": "规则一致；失败项已列出。"},
    {"id": "skill-op-parameterize", "name": "参数化生成", "expert": "general", "group": "通用·操作性",
     "desc": "把可变部分抽成参数批量产出",
     "how": "区分固定骨架与可变参数；列出参数取值表；按参数逐一生成；校验生成结果。",
     "check": "骨架与参数分离；结果已校验。"},
    {"id": "skill-op-timebox", "name": "时间盒管理", "expert": "general", "group": "通用·操作性",
     "desc": "给任务设定时间上限与优先级",
     "how": "按重要性×紧急度排序；给每项设时间盒；超时则降级或另开一轮；记录实际耗时。",
     "check": "排序有依据；超时有处理。"},
    {"id": "skill-op-rollback", "name": "回滚与修订", "expert": "general", "group": "通用·操作性",
     "desc": "保留可回退的修订路径",
     "how": "修改前保留原稿要点；标注每处修订及理由；给出回退办法；说明修订后风险。",
     "check": "原稿可恢复；修订理由明确。"},
    {"id": "skill-op-verify", "name": "结果校验", "expert": "general", "group": "通用·操作性",
     "desc": "对产出做独立校验",
     "how": "列出验收标准；逐项核对；不通过项给出定位与修复建议；通过则给出结论。",
     "check": "验收标准提前声明。"},
    {"id": "skill-op-automation-hint", "name": "可自动化提示", "expert": "general", "group": "通用·操作性",
     "desc": "指出哪些环节值得自动化",
     "how": "识别高频重复环节；评估自动化的收益与风险；给出最小可行自动化方案。",
     "check": "收益与风险均说明。"},

    # —— 理解性（10） ——
    {"id": "skill-comp-clarify", "name": "概念澄清", "expert": "general", "group": "通用·理解性",
     "desc": "把模糊概念讲清楚",
     "how": "给出定义；给正例与反例；说明边界与常见误解；用一句话概括。",
     "check": "有反例；边界明确。"},
    {"id": "skill-comp-term-align", "name": "术语对齐", "expert": "general", "group": "通用·理解性",
     "desc": "统一术语与口径",
     "how": "列出易混术语；给出本场景下的统一用法；标注已出现的历史别名。",
     "check": "全篇用法一致。"},
    {"id": "skill-comp-extract", "name": "要点提炼", "expert": "general", "group": "通用·理解性",
     "desc": "从长文中提炼关键要点",
     "how": "按结构分块；每块一句话概括；去重合并；保留关键数据与原意。",
     "check": "要点不遗漏关键数据；不改变原意。"},
    {"id": "skill-comp-closeread", "name": "精细阅读", "expert": "general", "group": "通用·理解性",
     "desc": "逐句精读并标注含义",
     "how": "标出关键句与逻辑连接词；解释难句；区分作者观点与事实；给出段落功能。",
     "check": "观点与事实已区分。"},
    {"id": "skill-comp-outline", "name": "层次概括", "expert": "general", "group": "通用·理解性",
     "desc": "构建层次化提纲",
     "how": "按总—分结构组织；层级不超过三层；每层一句概括；标注层级间关系。",
     "check": "层级关系清晰。"},
    {"id": "skill-comp-relation", "name": "关系梳理", "expert": "general", "group": "通用·理解性",
     "desc": "梳理概念之间的关系",
     "how": "列出概念对；标注关系类型（包含/因果/并列/对立/条件）；用文本关系图呈现。",
     "check": "关系类型标注准确。"},
    {"id": "skill-comp-visual-text", "name": "图文互释", "expert": "general", "group": "通用·理解性",
     "desc": "在文字与图示之间互相转换",
     "how": "把文字转成结构化图示描述；或把图示转成分点文字；两种表达内容一致。",
     "check": "两种表达信息等价。"},
    {"id": "skill-comp-prereq", "name": "前置知识补全", "expert": "general", "group": "通用·理解性",
     "desc": "补齐理解所需的前置知识",
     "how": "识别理解缺口；列出最少必要的前置概念；用简短解释补齐；不展开无关内容。",
     "check": "只补必要部分。"},
    {"id": "skill-comp-locate-doubt", "name": "疑问定位", "expert": "general", "group": "通用·理解性",
     "desc": "定位真正不懂的地方",
     "how": "把「看不懂」拆成具体疑问（哪个词/哪一步/哪个跳跃）；逐项标出；给出求解路径。",
     "check": "疑问具体到可查证。"},
    {"id": "skill-comp-rephrase", "name": "释义重写", "expert": "general", "group": "通用·理解性",
     "desc": "换一种说法重述以保持原意",
     "how": "保持原意与限定条件；换句式与用词；可按受众调整深浅；标注与原意的差异。",
     "check": "限定条件未丢失。"},

    # —— 处理性（10） ——
    {"id": "skill-proc-clean", "name": "信息清洗", "expert": "general", "group": "通用·处理性",
     "desc": "清洗噪声与无效信息",
     "how": "识别空值/乱码/重复/越界值；逐类处理；记录处理规则；保留原始可追溯。",
     "check": "处理规则已记录。"},
    {"id": "skill-proc-dedup", "name": "去重归并", "expert": "general", "group": "通用·处理性",
     "desc": "合并重复项并保留信息",
     "how": "定义相同判据；合并时保留并集信息；标注合并来源；说明取舍规则。",
     "check": "合并判据明确。"},
    {"id": "skill-proc-classify", "name": "分类编码", "expert": "general", "group": "通用·处理性",
     "desc": "按统一口径分类编码",
     "how": "制定分类体系（互斥穷尽）；给出判定规则；逐项归类；难以归类的单列。",
     "check": "分类体系互斥穷尽。"},
    {"id": "skill-proc-anomaly", "name": "异常识别", "expert": "general", "group": "通用·处理性",
     "desc": "发现异常值与异常模式",
     "how": "确定正常范围或模式；标出偏离项；判断是错误还是真实异常；给出处理建议。",
     "check": "区分错误与真实异常。"},
    {"id": "skill-proc-conflict", "name": "冲突消解", "expert": "general", "group": "通用·处理性",
     "desc": "处理信息之间的冲突",
     "how": "列出冲突点与各方来源；比较可信度与时效；给出采纳结论与理由；保留异议记录。",
     "check": "结论有来源与理由。"},
    {"id": "skill-proc-impute", "name": "缺失补全", "expert": "general", "group": "通用·处理性",
     "desc": "处理缺失信息",
     "how": "区分「不可得」与「暂缺」；能推则标注推算依据；不能推则显式留空并说明影响。",
     "check": "不编造；缺失已标注。"},
    {"id": "skill-proc-normalize", "name": "格式规整", "expert": "general", "group": "通用·处理性",
     "desc": "统一格式与单位",
     "how": "统一单位、日期、命名与层级；给出映射表；逐项转换；标注无法自动转换的项。",
     "check": "映射表完整；例外已说明。"},
    {"id": "skill-proc-prioritize", "name": "优先级排序", "expert": "general", "group": "通用·处理性",
     "desc": "按影响与成本排序",
     "how": "确定评价维度（影响/紧急/成本/风险）；给出排序与依据；标注可并行项。",
     "check": "排序依据可解释。"},
    {"id": "skill-proc-summarize", "name": "摘要压缩", "expert": "general", "group": "通用·处理性",
     "desc": "按比例压缩篇幅保留要点",
     "how": "给出压缩目标比例；保留结论与关键数据；删除重复与修饰；标注被省略的内容类型。",
     "check": "关键数据保留；省略有说明。"},
    {"id": "skill-proc-evidence", "name": "证据核查", "expert": "general", "group": "通用·处理性",
     "desc": "核查结论是否有证据支撑",
     "how": "逐条结论找证据；标注证据强度；指出无证据的断言；建议补证方式。",
     "check": "断言与证据一一对应。"},

    # —— 输入输出性（10） ——
    {"id": "skill-io-structured", "name": "结构化输出", "expert": "general", "group": "通用·输入输出性",
     "desc": "按指定结构输出（表格/列表/JSON）",
     "how": "确认目标结构与字段名；逐字段填充；保证类型与取值合法；不额外添加未定义字段。",
     "check": "结构严格匹配；字段类型正确。"},
    {"id": "skill-io-convert", "name": "格式转换", "expert": "general", "group": "通用·输入输出性",
     "desc": "在不同表达格式之间转换",
     "how": "明确源格式与目标格式；逐项映射；标注无法映射的信息；转换后自检完整性。",
     "check": "信息无损或已标注损失。"},
    {"id": "skill-io-batchio", "name": "批量导入导出", "expert": "general", "group": "通用·输入输出性",
     "desc": "批量处理输入输出",
     "how": "确定批次与字段映射；逐批执行；记录成功/失败清单；给出汇总统计。",
     "check": "失败清单完整。"},
    {"id": "skill-io-chunk", "name": "长文分块", "expert": "general", "group": "通用·输入输出性",
     "desc": "把长内容切成可处理的块",
     "how": "按语义边界（章节/段落）切分；控制块大小；保留块间顺序与上下文标识。",
     "check": "不切断语义；顺序可还原。"},
    {"id": "skill-io-field-extract", "name": "字段抽取", "expert": "general", "group": "通用·输入输出性",
     "desc": "从文本中抽取指定字段",
     "how": "定义字段与取值规则；逐字段抽取；未命中的留空并说明；不做推断填充。",
     "check": "未命中已标注；不臆造。"},
    {"id": "skill-io-naming", "name": "命名规范", "expert": "general", "group": "通用·输入输出性",
     "desc": "统一命名与编号规则",
     "how": "确定命名模式（前缀/日期/序号）；给出示例；全量套用；标注历史命名映射。",
     "check": "全量一致；映射可查。"},
    {"id": "skill-io-cite", "name": "引用标注", "expert": "general", "group": "通用·输入输出性",
     "desc": "给输出加上可核对的引用",
     "how": "关键结论后附来源标识；课标/教材写明出处与位置；网络来源写明名称；不杜撰。",
     "check": "引用可核对；要素完整。"},
    {"id": "skill-io-chart", "name": "成图输出", "expert": "general", "group": "通用·输入输出性",
     "desc": "把数据转成图表描述",
     "how": "选图型（柱状/折线/饼图）；给出标题、轴名、标签与数据；标注样本量与口径。",
     "check": "图型匹配数据；口径已标注。"},
    {"id": "skill-io-length", "name": "篇幅控制", "expert": "general", "group": "通用·输入输出性",
     "desc": "按字数或条目数控制篇幅",
     "how": "明确字数/条目上限；先搭骨架再填充；超限时说明删减了什么；保证结构完整。",
     "check": "不超限；结构仍完整。"},
    {"id": "skill-io-bilingual", "name": "术语与语言统一", "expert": "general", "group": "通用·输入输出性",
     "desc": "统一语言风格与专业术语",
     "how": "确定目标读者与语域；统一术语表；统一人称与时态；检查前后一致性。",
     "check": "术语前后一致；语域统一。"},
]

SKILL_LIBRARY = DOMAIN_SKILLS + GENERAL_SKILLS

# 分组展示顺序（领域在前、通用在后）
GROUP_ORDER = ["需求与定位", "课标与课程", "学情诊断", "目标设计", "评价设计",
               "教学与活动", "作业设计", "资源课件", "学科专长", "反思与数据",
               "跨环节治理", "通用·思维性", "通用·操作性", "通用·理解性",
               "通用·处理性", "通用·输入输出性"]

_BY_ID = {s["id"]: s for s in SKILL_LIBRARY}
BUILTIN_IDS = frozenset(_BY_ID)


def render_prompt(s):
    """把技能定义渲染为注入 System Prompt 的附加指令（简短可执行）。"""
    head = "【%s】（%s｜%s）" % (s["name"], s["id"], s.get("group") or "")
    lines = [head, "作用：" + (s.get("desc") or ""),
             "执行要点：" + (s.get("how") or ""),
             "自检：" + (s.get("check") or "")]
    return "\n".join(lines)


def get(skill_id):
    return _BY_ID.get(skill_id)


# ---------------- 自动调用（内置技能不进入技能工坊，由专家按需要自动生效） ----------------
# 通用技能不属于任何单一专家，按「当前问题」的关键词命中挑选，避免全量注入浪费上下文。

# 通用技能的语义触发词。
# 早期实现把 desc 逐字切成 2 字滑窗（"论与"、"ME"、"EC" 之类）当作匹配词，两头不讨好：
# 「设计一节课的教学目标」会误命中「假设检验」（其描述含"设计"）与「逆向思考」（含"目标"），
# 而「分析成绩不好的原因」这类真需要数据处理技能的提问反而零命中。
# 改为按语义配词，并刻意避开"设计/目标/内容/教学"这类高频词，防止噪声注入。
_GENERAL_KW = {
    # —— 通用·思维性 ——
    "skill-think-critical": ["质疑", "批判", "站得住", "漏洞", "反驳", "检验结论", "经得起推敲"],
    "skill-think-system": ["全局", "整体来看", "系统性", "要素", "相互关联", "反馈回路", "联动"],
    "skill-think-firstprinciple": ["第一性", "基本原理", "本质", "从头推导", "根本原理", "回到事实"],
    "skill-think-reverse": ["倒推", "反过来想", "逆向", "从结果出发", "反推", "要达到"],
    "skill-think-decompose": ["拆解", "分解", "MECE", "拆开", "分层拆", "结构化拆"],
    "skill-think-analogy": ["类比", "迁移", "举一反三", "类似的", "借鉴", "照搬思路"],
    "skill-think-hypothesis": ["假设", "猜想", "待验证", "验证假设", "猜测"],
    "skill-think-dialectic": ["权衡", "利弊", "两面", "辩证", "正反", "取舍", "两难"],
    "skill-think-metacog": ["复盘", "反思过程", "回顾", "元认知", "哪里走偏"],
    "skill-think-decision": ["决策", "多方案比较", "打分", "选哪个", "决策矩阵", "如何选"],
    # —— 通用·操作性 ——
    "skill-op-checklist": ["清单", "逐项", "勾选", "检查表", "一项项"],
    "skill-op-sop": ["流程", "标准作业", "SOP", "固化步骤", "规范化流程"],
    "skill-op-template": ["模板", "套用", "照着模板", "按模板", "既有格式"],
    "skill-op-diff": ["对比两版", "差异", "改了什么", "前后版本", "版本对比", "变更点"],
    "skill-op-batch": ["批量", "一次性处理", "全部都", "批量处理", "多个一起"],
    "skill-op-parameterize": ["参数化", "变量", "套变量", "批量生成", "替换参数"],
    "skill-op-timebox": ["限时", "时间盒", "排期", "截止时间", "控制时长", "给多长"],
    "skill-op-rollback": ["回退", "撤销", "改回去", "修订历史", "回滚", "恢复上一版"],
    "skill-op-verify": ["校验", "核查", "检查一遍", "验证结果", "复核"],
    "skill-op-automation-hint": ["自动化", "能不能自动", "省力", "哪些可以自动"],
    # —— 通用·理解性 ——
    "skill-comp-clarify": ["是什么意思", "讲清楚", "概念", "澄清", "不明白", "没懂"],
    "skill-comp-term-align": ["术语", "口径", "统一叫法", "命名一致", "说法一致"],
    "skill-comp-extract": ["要点", "提炼", "划重点", "关键信息", "提取要点"],
    "skill-comp-closeread": ["精读", "逐句", "细读", "字斟句酌", "逐字"],
    "skill-comp-outline": ["提纲", "层次", "大纲", "结构概括", "梳理结构"],
    "skill-comp-relation": ["之间关系", "联系", "脉络", "相互", "关系梳理"],
    "skill-comp-visual-text": ["图示", "画出来", "图文", "配图", "示意图"],
    "skill-comp-prereq": ["前置", "先修", "需要什么基础", "基础知识", "补基础"],
    "skill-comp-locate-doubt": ["哪里不懂", "卡在哪", "疑问点", "到底哪里"],
    "skill-comp-rephrase": ["换个说法", "改写", "重述", "换句话说", "换个讲法"],
    # —— 通用·处理性 ——
    "skill-proc-clean": ["清洗", "去噪", "乱码", "无效信息", "脏数据"],
    "skill-proc-dedup": ["去重", "重复", "合并同类", "归并", "重复项"],
    "skill-proc-classify": ["分类", "归类", "编码", "打标签", "分门别类"],
    "skill-proc-anomaly": ["异常", "离群", "反常", "可疑", "不对劲"],
    "skill-proc-conflict": ["冲突", "矛盾", "不一致", "打架", "说法冲突"],
    "skill-proc-impute": ["缺失", "空缺", "补全", "缺什么", "补齐"],
    "skill-proc-normalize": ["格式统一", "规整", "单位统一", "规范化格式", "统一格式"],
    "skill-proc-prioritize": ["优先级", "排序", "哪个先", "先后顺序", "先做哪个"],
    "skill-proc-summarize": ["摘要", "压缩篇幅", "精简", "缩短", "概括一下"],
    "skill-proc-evidence": ["证据", "依据", "出处", "有没有依据", "凭什么"],
    # —— 通用·输入输出性 ——
    "skill-io-structured": ["结构化", "表格输出", "JSON", "按结构", "用表格"],
    "skill-io-convert": ["转换格式", "转成", "导出为", "格式互转", "换个格式"],
    "skill-io-batchio": ["批量导入", "批量导出", "导入导出", "批量上传"],
    "skill-io-chunk": ["分块", "太长", "切分", "分段处理", "内容过长"],
    "skill-io-field-extract": ["抽取字段", "提取字段", "字段", "提取信息"],
    "skill-io-naming": ["命名", "编号规则", "命名规范", "统一编号"],
    "skill-io-cite": ["引用", "标注来源", "出处标注", "参考文献", "加引用"],
    "skill-io-chart": ["图表", "画个图", "可视化", "柱状图", "趋势图"],
    "skill-io-length": ["篇幅", "字数限制", "控制字数", "多少字", "限定字数"],
    "skill-io-bilingual": ["语言风格", "术语统一", "文风一致", "统一语言"],
}


def _kw_tokens(s):
    """为通用技能生成匹配词：技能名 + 语义触发词。"""
    toks = [s.get("name") or ""]
    toks += list(_GENERAL_KW.get(s["id"]) or [])
    return [t for t in toks if len(t) >= 2]


_GENERAL_TOKENS = {s["id"]: _kw_tokens(s) for s in GENERAL_SKILLS}


def match_general(query, limit=3):
    """按问题文本挑出最相关的通用技能（命中 0 条则返回空）。"""
    q = (query or "").strip()
    if not q:
        return []
    scored = []
    for s in GENERAL_SKILLS:
        score = sum(q.count(t) for t in _GENERAL_TOKENS[s["id"]])
        if score > 0:
            scored.append((-score, s["id"], s))
    scored.sort()
    return [x[2] for x in scored[:limit]]


def skills_of(expert_id, stage=""):
    """某专家名下的技能；无专属绑定时按其所属阶段整组取用。"""
    own = owner_map().get(expert_id) or []
    if not own and stage:
        own = [s["id"] for s in DOMAIN_SKILLS if s.get("group") == stage]
    return [_BY_ID[i] for i in own if i in _BY_ID]


# 治理类技能（安全复核 / 学情脱敏 / 引用规范 / 质量闸门 …）是产出的底线要求，
# 必须无条件生效，不参与 domain_limit 配额——否则专家专属技能一多就会被截断，
# 结果「安全复核」「学情脱敏」这类最不该丢的反而是最先被丢的。
GOVERNANCE_GROUP = "跨环节治理"


def pick_for(expert_id, query="", stage="", domain_limit=6, general_limit=3):
    """挑出本次对话应自动生效的内置技能：治理类 + 专家专属 + 命中的通用技能。"""
    own = skills_of(expert_id, stage)
    gov = [s for s in own if s.get("group") == GOVERNANCE_GROUP]
    rest = [s for s in own if s.get("group") != GOVERNANCE_GROUP]
    picks = gov + rest[:domain_limit]
    if general_limit > 0:
        own_ids = {s["id"] for s in picks}
        for s in match_general(query, general_limit):
            if s["id"] not in own_ids:
                picks.append(s)
    return picks


def render_inline(s):
    """单行渲染（用于自动注入，压缩占行数）。"""
    return "【%s】（%s）作用：%s；执行要点：%s；自检：%s" % (
        s.get("name") or "", s.get("group") or "", s.get("desc") or "",
        s.get("how") or "", s.get("check") or "")


def render_block(skills_list):
    """把自动生效的技能渲染为 System Prompt 附加块。"""
    if not skills_list:
        return ""
    lines = ["# 系统内置技能（本次自动生效，无需教师勾选）",
             "下列技能是本环节的可复用做法，按需选用，不必逐条执行；"
             "与专家职责冲突时以专家职责为准。"]
    for s in skills_list:
        lines.append("- " + render_inline(s))
    return "\n".join(lines)


def list_builtin():
    """返回带渲染 prompt 的内置技能列表（供 /api/skills 与前端使用）。"""
    out = []
    for s in SKILL_LIBRARY:
        item = dict(s)
        item["prompt"] = render_prompt(s)
        item["builtin"] = True
        out.append(item)
    return out


def groups():
    """分组 → 技能 id 列表，按 GROUP_ORDER 排序。"""
    g = {}
    for s in SKILL_LIBRARY:
        g.setdefault(s.get("group") or "其他", []).append(s["id"])
    ordered = [(k, g[k]) for k in GROUP_ORDER if k in g]
    for k in g:
        if k not in GROUP_ORDER:
            ordered.append((k, g[k]))
    return ordered


def owner_map():
    """专家 id → 其名下技能 id 列表（含 general 的归属信息单独返回）。"""
    m = {}
    for s in SKILL_LIBRARY:
        m.setdefault(s.get("expert") or "", []).append(s["id"])
    return m


def validate():
    """自检：数量、唯一性、归属专家合法性。返回问题列表。"""
    problems = []
    ids = [s["id"] for s in SKILL_LIBRARY]
    if len(ids) != len(set(ids)):
        dup = [i for i in set(ids) if ids.count(i) > 1]
        problems.append("重复技能 id：%s" % dup)
    for s in SKILL_LIBRARY:
        for k in ("id", "name", "group", "desc", "how", "check"):
            if not s.get(k):
                problems.append("%s 缺字段 %s" % (s.get("id"), k))
    if len(DOMAIN_SKILLS) < 100:
        problems.append("领域技能数 %d < 100" % len(DOMAIN_SKILLS))
    return problems
