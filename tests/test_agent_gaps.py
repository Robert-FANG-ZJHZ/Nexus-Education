"""专项测试：补齐「可靠验证 / 班级记忆 / 多方案择优」三项能力后的回归。

用法（零第三方依赖）：
    python tests/test_agent_gaps.py

前半部分是模块级确定性测试；后半部分起真实服务，验证三个新接口在
防爬取与鉴权之下可用，且核验结果能随产出质量变化。
"""
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("NEXEDU_FORCE_DEMO", "1")
os.environ.setdefault("NEXEDU_NO_DIALOG", "1")

from nexedu import verify as verify_mod          # noqa: E402
from nexedu import classroom as class_mod       # noqa: E402
from nexedu import options as options_mod       # noqa: E402
from nexedu import config                       # noqa: E402

HOST = "127.0.0.1"
PORT = 8607
_results = []


def check(name, ok, detail=""):
    _results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def req(path, method="GET", body=None, headers=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    r = urllib.request.Request(f"http://{HOST}:{PORT}{path}", data=data,
                               headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, resp.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")


# ---------------- 一、产出核验 ----------------

GOOD_LESSON = """## 教学目标
- 学生能说出《背影》中三次流泪的作用
- 学生能写出至少两处细节描写的表达效果

## 教学过程
- 活动一：圈画三次流泪的句子，小组讨论其作用
- 活动二：仿写一段细节描写

## 评价
- 当堂检测：写出两处细节描写的作用，教师即时反馈
- 分层：基础层圈画，进阶层写出，挑战层仿写
"""

BAD_LESSON = """这是一份教案。
老师讲一讲，学生听一听，然后做练习。
依据是[来源:不存在的文件.txt]中的课标要求。
"""


def test_verify():
    r = verify_mod.verify(GOOD_LESSON, kind="lesson")
    check("完整教案通过核验（无 blocker）", r["passed"],
          f"score={r['score']} issues={[i['code'] for i in r['issues']]}")
    check("通过时给出置信度", r["confidence"] in ("high", "medium"),
          f"confidence={r['confidence']}")

    r2 = verify_mod.verify(BAD_LESSON, kind="lesson")
    codes = {i["code"] for i in r2["issues"]}
    check("缺环节/假引用被判为阻断项", not r2["passed"] and r2["blockers"] >= 2,
          f"blockers={r2['blockers']} codes={sorted(codes)}")
    check("引用无法核对时如实提示（不假装通过）",
          any(i["code"].startswith("citation") for i in r2["issues"]),
          f"codes={sorted(codes)}")

    vague = "## 教学目标\n- 学生理解并掌握说明方法，体会说明文语言。\n\n## 教学过程\n- 讲解说明方法\n\n## 评价\n- 检测：完成练习"
    r3 = verify_mod.verify(vague, kind="lesson")
    check("目标不可观测被点名",
          any(i["code"] in ("objective_observable", "objective_vague")
              for i in r3["issues"]),
          f"issues={[i['code'] for i in r3['issues']]}")

    r4 = verify_mod.verify(GOOD_LESSON, kind="lesson",
                           student_info="本班基础薄弱，两极分化")
    check("给了学情但未落地分层 → 提示",
          any(i["code"] == "adaptation" for i in r4["issues"]) or
          r4["metrics"].get("adapted") is True,
          f"metrics={r4['metrics']}")

    r5 = verify_mod.verify("")
    check("空文本不崩溃", isinstance(r5, dict) and r5["kind"] == "general")
    check("渲染提示在有问题时非空",
          verify_mod.render(verify_mod.verify(BAD_LESSON, kind="lesson")).strip() != "")


# ---------------- 二、班级学情记忆 ----------------

def test_classroom():
    class_mod.clear_all()
    c, masked = class_mod.save_class(
        "八年级2班", subject="语文", grade="八年级",
        level="整体中等，两极分化", progress="已完成第二单元",
        weak_points=["细节描写作用分析弱", "概括能力不足"])
    check("新建班级档案成功", isinstance(c, dict) and c.get("grade") == "八年级",
          f"keys={sorted(c)[:6]}")
    check("班级列表可查", any(i["name"] == "八年级2班"
                          for i in class_mod.list_classes()))
    c2, hit = class_mod.add_note("八年级2班", "联系家长：家长 13812345678",
                                 tag="家校沟通")
    check("学情记录自动脱敏手机号", bool(hit) and "13812345678" not in json.dumps(
        c2, ensure_ascii=False), f"hit={hit}")
    c3, _ = class_mod.save_class("八年级2班", level="期中后略有提升")
    check("字段级合并不清空既有内容",
          c3.get("weak_points") and c3.get("progress"), f"progress={c3.get('progress')}")
    check("注入上下文含班级信息且受限长",
          "八年级2班" in class_mod.build_context()
          and len(class_mod.build_context()) <= 1600)
    check("删除班级生效", class_mod.remove_class("八年级2班")
          and not class_mod.list_classes())
    class_mod.clear_all()
    check("清空后无残留", class_mod.list_classes() == [])


# ---------------- 三、多方案择优 ----------------

def test_options():
    r = options_mod.generate("请设计八年级语文《背影》一课，45分钟",
                             subject="语文", grade="八年级", topic="背影")
    check("生成 3 条候选路线", len(r["candidates"]) == 3,
          f"n={len(r['candidates'])}")
    scores = [c["score"] for c in r["candidates"]]
    check("候选按适配度降序", scores == sorted(scores, reverse=True), f"{scores}")
    check("给出推荐与理由", bool(r["recommended"]) and bool(r["reason"]))
    check("每条候选含代价与不适用场景",
          all(c.get("costs") and c.get("unfit") for c in r["candidates"]))

    r2 = options_mod.generate("《背影》新授，40分钟", student_info="本班基础薄弱")
    top_weak = r2["candidates"][0]["id"]
    check("薄弱班推荐更稳的路线（结构式优先）", top_weak == "direct",
          f"top={top_weak}")
    r3 = options_mod.generate("《背影》单元综合实践，90分钟",
                              student_info="基础好，能力强")
    check("长课时且基础好 → 探究/任务式优先",
          r3["candidates"][0]["id"] in ("inquiry", "task"),
          f"top={r3['candidates'][0]['id']}")
    check("渲染为 Markdown 可用",
          "候选教学路线" in options_mod.render(r))
    check("pick 可取推荐项", options_mod.pick(r)["id"] == r["recommended"])


# ---------------- 四、接口层（真实起服务） ----------------

def test_api():
    from nexedu.server import make_server
    srv, port = make_server(HOST, PORT)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    for _ in range(40):
        time.sleep(0.5)
        try:
            urllib.request.urlopen(f"http://{HOST}:{PORT}/", timeout=5)
            break
        except Exception:
            continue

    code, page = req("/")
    ticket = (re.search(r"__NEXEDU_PAGE__='([0-9a-f]+)'", page or "")
              or [None, ""])[1] or ""
    hdr = {"X-NexEdu-Page": ticket}
    code, text = req("/api/status", headers=hdr)
    token = (json.loads(text).get("token") if code == 200 else "")
    auth = {"X-NexEdu-Token": token, "X-NexEdu-Page": ticket}

    code, text = req("/api/classroom", headers=auth)
    check("GET /api/classroom 可用", code == 200 and "classes" in text, f"http={code}")

    code, text = req("/api/classroom", method="POST",
                     body={"action": "save", "name": "测试班", "grade": "七年级",
                           "level": "整体中等"}, headers=auth)
    check("POST /api/classroom 新建班级", code == 200
          and json.loads(text).get("ok"), f"http={code}")

    code, text = req("/api/verify", method="POST",
                     body={"text": BAD_LESSON, "kind": "lesson"}, headers=auth)
    ok = code == 200 and not json.loads(text).get("passed", True)
    check("POST /api/verify 能判出问题产出", ok, f"http={code}")

    code, text = req("/api/options", method="POST",
                     body={"message": "八年级语文《背影》45分钟"}, headers=auth)
    ok = code == 200 and len(json.loads(text).get("candidates") or []) == 3
    check("POST /api/options 返回 3 条候选", ok, f"http={code}")

    # 防爬取：新接口同样受票据保护
    code, _ = req("/api/classroom", headers={"X-NexEdu-Token": token})
    check("新接口受页面票据保护（无票据 403）", code == 403, f"http={code}")

    try:
        req("/api/classroom", method="POST",
            body={"action": "clear"}, headers=auth)
    except Exception:
        pass


# ---------------- 五、多智能体对等协商：跨轮多议题并行 ----------------

def _bad_outputs():
    aim = ("1. 了解文章的主要内容\n2. 掌握细节描写的方法\n"
           "3. 理解父子之情的内涵")
    des = "情境导入：播放图片。教师讲授。学生听讲。"
    sca = ("1. 背诵第六段\n"
           + "".join("%d. 练习题第%d题\n" % (i, i) for i in range(2, 20))
           + "预计用时 90 分钟")
    return {"aim": aim, "des": des, "sca": sca}


def _fake_respond(delay=0.35, fail_for=()):
    """假的模型回应：可设置耗时，用于验证并行确实省墙钟时间。"""
    def respond(expert_id, prompt, sink):
        import time
        time.sleep(delay)
        if expert_id in fail_for:
            raise RuntimeError("模拟模型调用失败")
        sink.append("接受质疑。已把目标改为可观测表述，并补充对应评价。")
        yield {"type": "delta", "text": sink[-1]}
    return respond


def test_negotiation_parallel():
    import time
    from nexedu import negotiation as neg

    outs = _bad_outputs()
    state = {"outputs": dict(outs)}

    t0 = time.time()
    events = list(neg.converge(state, "语文", "八年级", "背影",
                               _fake_respond(0.35), align=0.05,
                               parallel=True))
    t_par = time.time() - t0
    rounds = state.get("rounds") or []
    check("协商检出多个议题并全部回应", len(rounds) >= 2, f"议题={len(rounds)}")
    check("并行执行：墙钟时间 < 串行累加时间",
          t_par < 0.35 * len(rounds) * 0.8 + 0.3,
          f"并行耗时={t_par:.2f}s 议题={len(rounds)}（串行需约 "
          f"{0.35 * len(rounds):.2f}s）")

    sections = [e for e in events if e.get("type") == "section"]
    check("并行后界面事件仍按议题顺序回放（不乱序）",
          len(sections) == len(rounds)
          and all("专家协商" in s.get("title", "") for s in sections),
          f"sections={len(sections)} rounds={len(rounds)}")
    check("并行标记写进协商纪要",
          any(e.get("type") == "negotiation" and e.get("parallel")
              for e in events))

    # 串行对照组：同样的议题，耗时应当明显更长
    state2 = {"outputs": dict(outs)}
    t0 = time.time()
    list(neg.converge(state2, "语文", "八年级", "背影",
                      _fake_respond(0.35), align=0.05, parallel=False))
    t_seq = time.time() - t0
    check("串行对照组确实更慢（说明并行生效）", t_seq > t_par + 0.2,
          f"串行={t_seq:.2f}s 并行={t_par:.2f}s")

    # 单条失败不拖垮同轮其他议题
    state3 = {"outputs": dict(outs)}
    ev3 = list(neg.converge(state3, "语文", "八年级", "背影",
                            _fake_respond(0.1, fail_for=("nexaim",)),
                            align=0.05, parallel=True))
    rounds3 = state3.get("rounds") or []
    check("单个议题失败不影响同轮其他议题完成",
          len(rounds3) >= 2 and any(r["verdict"] != "accept" for r in rounds3),
          f"verdicts={[r['verdict'] for r in rounds3]}")
    check("失败事件如实呈现给教师（不静默吞掉）",
          any("出错" in str(e.get("text", "")) for e in ev3))


def test_flags():
    """可选能力开关：能持久化、能真正影响行为（不是摆设）。"""
    from nexedu import config
    from nexedu import negotiation as neg
    fl = (config.current().get("flags") or {})
    check("配置暴露可选开关位", "negotiate_parallel" in fl, f"flags={fl}")
    check("默认并行开启", neg.parallel_enabled() is True)
    config.apply_settings({"flags": {"negotiate_parallel": False}})
    check("关掉开关后协商退回串行", neg.parallel_enabled() is False)
    config.apply_settings({"flags": {"negotiate_parallel": True,
                                     "deep_review": False}})
    check("恢复默认后并行重新生效", neg.parallel_enabled() is True)
    check("深入复核默认关闭（不额外烧调用次数）",
          neg.deep_review_enabled() is False)


def main():
    print("== 产出核验 ==")
    test_verify()
    print("\n== 班级学情记忆 ==")
    test_classroom()
    print("\n== 多方案择优 ==")
    test_options()
    print("\n== 接口层 ==")
    test_api()
    print("\n== 多智能体对等协商（并行） ==")
    test_negotiation_parallel()
    print("\n== 可选能力开关 ==")
    test_flags()

    passed = sum(1 for _, ok, _ in _results if ok)
    print(f"\n合计 {passed}/{len(_results)} 通过")
    return 0 if passed == len(_results) else 1


if __name__ == "__main__":
    sys.exit(main())
