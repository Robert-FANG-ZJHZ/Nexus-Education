# -*- coding: utf-8 -*-
"""前端静态校验：抽取内联 <script> 做语法检查，并做 HTML 标签配平检查。

用法（Windows / Git Bash 环境 PATH 损坏时也能跑）：
  "C:/Users/FANG/.workbuddy/binaries/python/versions/3.13.12/python.exe" tests/_check_frontend.py
"""
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "web", "index.html")
NODE = r"C:\Users\FANG\.workbuddy\binaries\node\versions\22.22.2-6\node.exe"

src = open(HTML, "r", encoding="utf-8").read()

# ---- 1) 抽取内联脚本做语法检查 ----
blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", src, re.S)
print("内联 script 块数：%d" % len(blocks))

ok = True
tmpdir = tempfile.mkdtemp(prefix="nexedu_js_")
for i, code in enumerate(blocks):
    path = os.path.join(tmpdir, "block_%d.js" % i)
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    try:
        r = subprocess.run([NODE, "--check", path],
                           capture_output=True, text=True, timeout=60)
    except Exception as e:
        print("  块%d: 无法调用 node（%s）" % (i, e))
        continue
    if r.returncode == 0:
        print("  块%d: JS 语法 OK（%d 行）" % (i, code.count("\n") + 1))
    else:
        ok = False
        print("  块%d: JS 语法错误：" % i)
        print(r.stderr[:2000])

# ---- 2) HTML 标签配平（忽略自闭合与 void 元素）----
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}
from html.parser import HTMLParser


class Balance(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.errors = []

    def handle_starttag(self, tag, attrs):
        if tag in VOID:
            return
        self.stack.append((tag, self.getpos()[0]))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack:
            self.errors.append("第%d行：多余的 </%s>" % (self.getpos()[0], tag))
            return
        if self.stack[-1][0] == tag:
            self.stack.pop()
        else:
            # 尝试在栈中查找同名标签（容忍轻微嵌套不一致），否则报错
            for k in range(len(self.stack) - 1, -1, -1):
                if self.stack[k][0] == tag:
                    unclosed = [x[0] for x in self.stack[k + 1:]]
                    self.errors.append(
                        "第%d行：</%s> 闭合时，%s 未闭合" %
                        (self.getpos()[0], tag, ",".join(unclosed)))
                    del self.stack[k:]
                    return
            self.errors.append("第%d行：多余的 </%s>" % (self.getpos()[0], tag))


p = Balance()
p.feed(src)
if p.stack:
    p.errors.append("文件结束仍有未闭合标签：%s" %
                    ",".join("%s(第%d行)" % (t, ln) for t, ln in p.stack[-8:]))

if p.errors:
    ok = False
    print("\nHTML 标签配平问题：")
    for e in p.errors[:15]:
        print("  " + e)
else:
    print("HTML 标签配平：OK")

# ---- 3) 关键点位自检 ----
need = {
    # 本轮改版：并发的粒度是「会话」而不是「页面窗口」——
    # 快速对话始终只显示一个会话（单窗口），但新建对话 / 历史对话继续追问
    # 得到的多个会话可以各自在后台同时生成，最多 6 个，侧栏「进行中」管理。
    "对话区单窗口容器": 'id="messages"',
    "并发上限(会话)": "const MAX_RUNS = 6",
    "侧栏进行中列表": 'id="runList"',
    "侧栏运行计数": 'id="runCount"',
    "新建对话按钮": 'id="newChatBtn"',
    "新建会话槽位": "function newTask",
    "会话切换显示": "function showTask",
    "会话作用域切换": "function withTask",
    "单会话停止": "function stopTask",
    "单会话执行": "async function runTask",
    "会话级状态": "function setTaskStatus",
    "会话计时渲染": "function renderRuns",
    # 本轮：滑块（滚动条）+ 进行中只留运行中的 + 删对话连带删产出 + 防爬取
    "滚动条滑块样式": "::-webkit-scrollbar-thumb",
    "宽表格可横向滚动": 'class="md-table"',
    # 对话区/侧栏必须能真滚动：flex 子项缺 min-height:0 会被内容撑开、
    # 被外层 overflow:hidden 裁掉（实测 clientHeight=4939 > 视口、scrollTop 恒为 0）
    "对话区锁定视口高度": "min-height:0",
    "回到底部按钮": 'id="jumpBottom"',
    "滚动跟随策略": "function bindScrollFollow",
    # 本轮：失败可原地重试 + 对话内容可一键复制
    "复制按钮": "function makeCopyBtn",
    "复制条容器": "function addCopyBar",
    "失败重试行": "function addRetryRow",
    "重试执行": "async function retryTask",
    "进行中只留运行中的": "暂无正在生成的对话",
    "删除对话连带删文件": "生成的本地文件",
    "删除结果轻提示": "function toast",
    "页面票据占位": "/*__NEXEDU_PAGE__*/",
    "请求携带页面票据": "X-NexEdu-Page",
    "一键备课进度条": 'id="wfProgress"',
    "一键备课走工作流": "workflow: 'w1-lesson'",
    "全自动标记": "auto: true",
    "工作流事件处理器": "function wfEventHandler",
    # 本轮新增：取消专家栏 / 自动路由 / 并行任务 / 节点思考折叠
    # 聊天页不再内置工作流任务提交表单（任务中心已是 6 路并行对话），
    # 长程编排统一走一键备课的工作流引擎。
    "拉取任务事件": "/api/jobs/events",
    "取消任务": "/api/jobs/cancel",
    "任务轮询": "function startJobPolling",
    "节点卡片渲染": "function makeNodeCard",
    "思考折叠区": 'class="think-box"',
    "思考行样式": ".think-row",
    "自动路由提示": "function autoRouteChip",
    "自动路由事件": "ev.type === 'routed'",
    "专家默认自动": "let CURRENT_EXPERT = 'auto'",
    # 本轮新增：批量删除对话 / 备课结果去方框 / 内容默认折叠可展开
    "批量管理入口": 'id="batchDelBtn"',
    "批量操作条": 'id="batchBar"',
    "批量全选框": 'id="batchAll"',
    "批量删除按钮": 'id="batchDelete"',
    "批量态开关函数": "function toggleBatchMode",
    "批量计数刷新": "function syncBatchUI",
    "批量删除执行": "function deletePickedSessions",
    "批量选中集合": "const SESSION_PICK = new Set()",
    "批量态渲染勾选框": 'class="s-pick"',
    "批量态隐藏单个删除": ".session-item.pick .s-del{display:none;}",
    "批量入口已接线": "$('#batchDelBtn').onclick",
    "批量全选已接线": "$('#batchAll').onchange",
    "批量删除已接线": "$('#batchDelete').onclick",
    "批量调用删除接口": "method:'DELETE'",
    "结果区去方框": 'class="result plain" id="lessonResult"',
    "结果区无标题框": ".result.plain{background:none;border:none;padding:0;box-shadow:none;}",
    "折叠工具条": 'id="lessonTools"',
    "全部展开按钮": 'id="lessonExpandAll"',
    "全部收起按钮": 'id="lessonCollapseAll"',
    "统一开合函数": "function toggleAllNodes",
    "节点默认折叠": "card.className = 'node-card' + (fold ? ' collapsed' : '')",
    "备课走折叠模式": "ev.expert, ev.index, true)",
    "折叠时隐藏正文": ".node-card.collapsed .node-out,",
    "折叠开启支撑$-3": "const setCollapsed = v =>",
    "标题可点展开": "card.querySelector('.node-head').onclick",
    "手动干预后不自动开合": "if(fold && !touched) setCollapsed(true)",
    "字数提示": "'共 ' + len + ' 字 · 点击展开'",
}
print("\n已移除项：")
gone = {
    "左侧专家栏容器": 'id="expertList"',
    "对话专家分组标签": ">对话专家<",
    "欢迎页引导选专家": "左侧选择专家",
    # 本轮改版：取消「智能体模式 / 非智能体模式」的区分，去掉一键备课的闪电图标
    "智能体模式开关": 'id="agentToggle"',
    "智能体模式字样": "智能体模式",
    "一键备课闪电图标": "⚡ 一键备课",
    "独立任务中心视图": 'id="view-tasks"',
    "独立工作流导航项": 'data-view="workflow"',
    # 任务中心不再是抽屉/弹窗，也不再有「启用任务中心」开关
    "抽屉式任务中心": 'class="task-drawer"',
    "任务中心启用开关": 'id="tsEnableChip"',
    "任务中心开关函数": "function setTaskCenter",
    # 本轮改版：不是在一个页面里开 6 个窗口，多窗口网格已整体移除
    "多窗口网格容器": 'id="chatLanes"',
    "多窗口上限常量": "MAX_LANES",
    "多窗口作用域": "function withLane",
    # 本轮改版：界面文案精简 + 工作流引擎默认启用（无开关）
    "启用工作流引擎按钮": 'id="wfEnableChip"',
    "智能体已就绪提示": 'id="agentHint"',
    "模型路由设置块": "suggestRouting",
    "本地用量设置块": 'id="usageTip"',
    "协商与复核开关块": 'id="flagNegoParallel"',
    "教案输出目录标签": "教案输出目录",
    "技能分两类说明": "技能分两类",
    # 本轮改版：工作流引擎面板与历史运行界面整体删除，引擎内置于智能体自动调用
    "工作流引擎面板": 'class="wf-embed"',
    "工作流嵌入节点": 'data-page-node-id="wfEmbed"',
    "工作流卡片列表": 'id="wfList"',
    "历史运行区": 'id="wfRuns"',
    "运行面板": 'id="wfRunPanel"',
    "检查点续跑前端调用": "/api/workflow/resume",
    "工作流同类工具条": 'id="wfTools"',
}
for k, v in gone.items():
    hit = v in src
    print("  %-16s %s" % (k, "OK（已移除）" if not hit else "仍存在 → " + v))
    if hit:
        ok = False
print("\n关键点位：")
for k, v in need.items():
    hit = v in src
    print("  %-16s %s" % (k, "OK" if hit else "缺失 → " + v))
    if not hit:
        ok = False

print("\n结论：%s" % ("全部通过" if ok else "存在问题"))
sys.exit(0 if ok else 1)
