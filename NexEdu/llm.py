"""模型层：统一接入第三方大模型（OpenAI 兼容端点），零第三方依赖。

- 有 API Key 时走真实调用（GLM / DeepSeek / Qwen / Kimi / 自定义等），支持流式 SSE。
- 无 Key 时进入「演示模式」，返回各专家的示例输出，保证闭环可跑通演示。
- 端点指向本机/局域网（Ollama / LM Studio / vLLM）时进入「本地模型模式」：
  无需 API Key、超时预算放宽、跳过备用模型链——数据完全不出本机。
"""

import json
import logging
import os
import time
import urllib.error
import urllib.request

from . import config

RETRY_TIMES = 2          # 总尝试次数（含首次）
RETRY_BACKOFF = 1.5      # 退避基数（秒）

# ---- 体感预算（真实教训）----
# 曾出现「发问后 9 分钟一字未出」：socket 超时给到 120 秒，且重试 3 次、
# 备用模型再整条链重来，最坏情况 ≈ 6 分钟仍无任何输出，也没有任何失败提示。
# 云端保持三个硬约束，任一触发都会立刻失败并把原因如实抛给上层：
#   READ_TIMEOUT  单次 socket 读超时：同时约束「首字节」与「流式停顿」上限。
#   RETRY_TIMES   重试次数；配合上面的超时，最坏 30+1.5+30 ≈ 62 秒即放弃该模型。
#   TOTAL_BUDGET  单次 chat() 的总预算（含重试与备用模型切换），到点即放弃。
# 本地模型（CPU 推理慢、无额度概念）改用宽松预算，见 config.is_local_base_url。
READ_TIMEOUT = 30
TOTAL_BUDGET = 150
LOCAL_READ_TIMEOUT = 120
LOCAL_TOTAL_BUDGET = 900

_log = logging.getLogger("nexedu.llm")


class LLMError(RuntimeError):
    """模型调用失败。retryable 表示是否值得重试（网络/5xx 可重试）。"""

    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


class LLMClient:
    def __init__(self, api_key=None, base_url=None, model=None):
        self.api_key = api_key if api_key is not None else config.get_api_key()
        self.base_url = (base_url or config.get_base_url()).rstrip("/")
        self.model = model or config.get_model()
        # 演示模式强制开关：不消耗额度、不依赖网络，用于演示与自动化测试。
        # 设为 1 时即便已配置 API Key 也走示例输出，行为完全确定可复现。
        self.force_demo = (os.environ.get("NEXEDU_FORCE_DEMO")
                           or "").strip().lower() in ("1", "true", "yes")

    @property
    def is_local(self):
        return config.is_local_base_url(self.base_url)

    @property
    def available(self):
        if self.force_demo:
            return False
        # 本地端点（Ollama / LM Studio / vLLM）无需 Key 即可调用
        return bool(self.api_key) or self.is_local

    def _timeouts(self):
        """本地端点放宽超时预算（CPU 推理慢且无额度概念）；云端保持体感预算。"""
        if self.is_local:
            return LOCAL_READ_TIMEOUT, LOCAL_TOTAL_BUDGET
        return READ_TIMEOUT, TOTAL_BUDGET

    def chat(self, messages, temperature=0.3, stream=False, max_tokens=None,
             demo_role=None, task=None):
        """调用模型，返回生成器，逐块 yield 文本片段。

        demo_role: 演示模式下用于选择示例输出的专家 id。
        task: 任务类型（aim/des/sca/coordinator），用于按任务选型与故障转移。
        """
        if self.available:
            yield from self._chat_failover(messages, temperature, stream,
                                           max_tokens, task)
        else:
            yield from self._chat_demo(messages, demo_role)

    def _model_chain(self, task):
        """主模型 + 备用模型链（去重，保持顺序）。"""
        chain = [config.get_model(task) or self.model]
        for m in config.get_fallback_models():
            if m and m not in chain:
                chain.append(m)
        return chain

    def _chat_failover(self, messages, temperature, stream, max_tokens, task):
        """按任务选型调用；主模型彻底失败时自动切换到备用模型。

        只有在「尚未吐出任何内容」时才允许切换，避免把半截内容再拼一遍。
        整条链共享 TOTAL_BUDGET 时间预算：到点即放弃，绝不让用户干等。
        本地端点跳过备用链：切来切去还是同一台机器，没有意义。
        """
        chain = self._model_chain(task)
        if self.is_local:
            chain = chain[:1]
        read_timeout, budget = self._timeouts()
        deadline = time.monotonic() + budget
        started_at = time.time()
        for idx, model in enumerate(chain):
            self.model = model
            started = False
            try:
                for chunk in self._chat_remote(messages, temperature, stream,
                                               max_tokens, deadline,
                                               read_timeout):
                    started = True
                    yield chunk
                _log.info("模型调用完成：model=%s elapsed=%.1fs local=%s",
                          model, time.time() - started_at, self.is_local)
                return
            except LLMError as e:
                last = idx == len(chain) - 1
                if started or last:
                    _log.warning("模型调用失败：model=%s elapsed=%.1fs err=%s",
                                 model, time.time() - started_at, e)
                    raise
                if time.monotonic() >= deadline:
                    raise LLMError(
                        f"模型响应超时：已超出本次调用总预算 {budget} 秒"
                        f"（最后错误：{e}）") from e
                _log.warning("模型 %s 调用失败，切换备用模型 %s：%s",
                             model, chain[idx + 1], e)

    # ---------------- 真实调用（OpenAI 兼容） ----------------
    def _chat_remote(self, messages, temperature, stream, max_tokens,
                     deadline=None, read_timeout=None):
        """带重试的流式调用；仅在尚未吐出任何内容时才重试，避免内容重复。

        deadline: 绝对截止时间（time.monotonic）。每次重试前先查预算，
        避免「重试 + 备用模型」把等待时间层层叠加。
        """
        read_timeout = read_timeout or self._timeouts()[0]
        started = False
        for attempt in range(RETRY_TIMES):
            if deadline and attempt and time.monotonic() >= deadline:
                raise LLMError(
                    f"模型响应超时：已超出本次调用总预算 "
                    f"{self._timeouts()[1]} 秒")
            try:
                for chunk in self._request(messages, temperature, stream,
                                           max_tokens, read_timeout):
                    started = True
                    yield chunk
                return
            except LLMError as e:
                if started or not e.retryable or attempt == RETRY_TIMES - 1:
                    raise
                if deadline and time.monotonic() >= deadline:
                    raise
                time.sleep(RETRY_BACKOFF * (attempt + 1))

    def _request(self, messages, temperature, stream, max_tokens,
                 read_timeout=None):
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        # 本地端点（Ollama/LM Studio）忽略 Authorization，无 Key 时传占位值
        auth = "Bearer " + (self.api_key or "none")
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": auth,
            },
            method="POST",
        )
        try:
            # timeout 同时约束「首字节」与「流式停顿」：任一段超过 READ_TIMEOUT
            # 即判定卡死并立刻失败，由上层提示教师，而不是静默干等。
            resp = urllib.request.urlopen(req, timeout=read_timeout or READ_TIMEOUT)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "ignore")[:300]
            except Exception:
                pass
            # 5xx / 429 视为可重试；4xx 属于配置或请求问题，不重试
            raise LLMError(f"请求失败（HTTP {e.code}）：{detail}",
                           retryable=(e.code >= 500 or e.code == 429)) from e
        except urllib.error.URLError as e:
            raise LLMError(f"无法连接模型服务：{e.reason}", retryable=True) from e
        except TimeoutError as e:
            raise LLMError("模型服务响应超时", retryable=True) from e

        if stream:
            try:
                yield from self._parse_sse(resp)
            except TimeoutError as e:
                # 卡在半路：如实告知，而不是让界面一直转圈
                raise LLMError(
                    f"模型输出中断：超过 {read_timeout} 秒没有新内容",
                    retryable=True) from e
        else:
            try:
                data = resp.read().decode("utf-8", "ignore")
            except TimeoutError as e:
                raise LLMError(
                    f"模型响应超时：超过 {read_timeout} 秒未返回完整结果",
                    retryable=True) from e
            try:
                obj = json.loads(data)
                yield obj["choices"][0]["message"]["content"]
            except (json.JSONDecodeError, KeyError, IndexError) as e:
                raise LLMError(f"模型返回解析失败：{data[:200]}") from e

    @staticmethod
    def _parse_sse(resp):
        for raw in resp:
            line = raw.decode("utf-8", "ignore").strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
                delta = obj["choices"][0].get("delta", {})
                content = delta.get("content")
                if content:
                    yield content
            except (json.JSONDecodeError, KeyError, IndexError):
                continue

    def test_connection(self):
        """发一条最小请求，返回 (ok, message)。"""
        if not self.available:
            return False, "未配置 API Key"
        if self.is_local and not self.api_key:
            mode_note = "（本地模型模式：数据不出本机，不消耗云端额度）"
        else:
            mode_note = ""
        got = None
        try:
            got = next(self.chat(
                [{"role": "user", "content": "只回复两个字：好的"}],
                temperature=0, stream=False, max_tokens=8,
            ))
        except LLMError as e:
            return False, str(e)
        except Exception as e:
            return False, f"连接异常：{e}"
        return True, f"连接成功（返回：{got.strip()[:40]}）{mode_note}"

    # ---------------- 演示模式 ----------------
    def _chat_demo(self, messages, role=None):
        system = ""
        user = ""
        observed = False        # 是否已经拿到过工具执行结果
        for m in messages:
            if m["role"] == "system":
                system += m["content"]
            elif m["role"] == "user":
                user = m["content"]
                if "【工具" in m["content"]:
                    observed = True

        if role == "critic":
            yield from self._demo_emit(self._demo_critic(user))
            return
        # 专家协商：被质疑方的对等回应（短、先表态），演示模式下同样完整可见
        if role == "negotiate":
            yield from self._demo_emit(self._demo_negotiate(user))
            return
        if role == "arbitrate":
            yield from self._demo_emit(self._demo_arbitrate(user))
            return

        if role == "nexaim" or (role is None and "NexAim" in system):
            body = self._demo_nexaim(user)
        elif role == "nexdes" or (role is None and "NexDes" in system):
            body = self._demo_nexdes(user)
        if role == "nexsca" or (role is None and "NexSca" in system):
            body = self._demo_nexsca(user)
        elif role and role not in ("nexaim", "nexdes", "nexsca", "coordinator"):
            body = self._demo_matrix_expert(role, user)
        else:
            body = self._demo_coordinator(user)

        # 演示模式下也演示一次工具调用：让「检索 → 依据 → 产出」的闭环真实可见
        if not observed and role in ("nexaim", "nexdes", "nexsca"):
            call = ('```tool\n{"tool": "search_knowledge", '
                    '"args": {"query": "%s"}}\n```\n\n' % self._topic_of(user))
            yield call
        # 分块吐出：演示模式同样呈现"边生成边显示"的过程，而不是整段突然出现
        yield from self._demo_emit(body)

    @staticmethod
    def _demo_emit(text, size=8, delay=0.012):
        """演示模式的分块输出：模拟真实的流式观感。"""
        for i in range(0, len(text), size):
            yield text[i:i + size]
            if delay:
                time.sleep(delay)

    @staticmethod
    def _topic_of(user):
        return user.strip()[:30] or "本课时"

    def _demo_nexaim(self, user):
        t = self._topic_of(user)
        return (
            f"### 课时目标叙写（演示示例）\n课题：{t}\n\n"
            f"1. **知识目标**：在通读文本基础上，能准确概括主要内容，正确率达 80%。\n"
            f"2. **素养目标**：通过对比关键语段，能用 2~3 句说明作者表达意图（语言建构与运用）。\n"
            f"3. **评价对齐**：以上目标可通过「预习题 + 课堂提问 + 小练笔」观察与评价。\n\n"
            f"（演示模式：请在「设置」中配置 API Key 获取真实生成）"
        )

    def _demo_nexdes(self, user):
        t = self._topic_of(user)
        return (
            f"### 课时教学设计（评价前置，演示示例）\n课题：{t}\n\n"
            f"1. **学习目标**：承接 NexAim 输出；\n"
            f"2. **评价任务（前置）**：复述并归纳主题，形成表现性任务；\n"
            f"3. **活动设计**：情境导入 → 问题链 → 小组任务 → 成果展示；\n"
            f"4. **课堂观察点**：参与度、关键问答正确率、小组贡献度。\n\n"
            f"（演示模式：请在「设置」中配置 API Key 获取真实生成）"
        )

    def _demo_nexsca(self, user):
        t = self._topic_of(user)
        return (
            f"### 分层作业单（演示示例）\n课题：{t}\n\n"
            f"**基础层**：识记与复述类题目；\n"
            f"**应用层**：迁移运用与简析类题目；\n"
            f"**创新层**：比较、评价与创作类任务，附评价量规。\n\n"
            f"**错因预判**：概念混淆 / 审题不清 / 表达不规范，可配套补偿练习。\n\n"
            f"（演示模式：请在「设置」中配置 API Key 获取真实生成）"
        )

    def _demo_negotiate(self, user):
        return (
            "接受质疑。\n\n"
            "【理由】对方指出的依据成立：目标表述偏虚，评价环节确实无从下手。\n\n"
            "【最小修订】\n"
            "1. 将「了解…」改写为「能准确说出 …,正确率达 80%」；\n"
            "2. 为每条目标补一条可观察的评价证据（课堂提问 / 练笔 / 量规）。\n\n"
            "（演示模式：真实协商由模型生成，被质疑方亦可「坚持原方案」并说明理由）"
        )

    def _demo_arbitrate(self, user):
        return (
            "【裁定】采用质疑\n"
            "【理由】目标若不可观测，整条教-学-评链条都会落空，优先修正上游。\n"
            "【最终表述】\n"
            "1. 能准确说出 …,正确率达 80%；\n"
            "2. 能用 2~3 句说明 …,并对应到量规第 2 项。\n\n"
            "（演示模式：裁定后仍由教师最终确认）"
        )

    def _demo_critic(self, user):
        return (
            "结论：需修订\n"
            "问题：1. 目标 2 的行为动词仍偏模糊；"
            "2. 缺少与目标对应的评价证据；3. 未标注素养维度。\n"
            "建议：1. 用「能准确概括…」替换「了解…」；"
            "2. 为每个目标补一条可观察的评价任务；"
            "3. 标注对应的核心素养维度。\n\n"
            "（演示模式示例，配置 API Key 后将由模型真实复核）"
        )

    def _demo_matrix_expert(self, role, user):
        """41 位专家矩阵中细分专家的演示输出（未配置 Key 时的兜底）。"""
        try:
            from . import experts_data
            meta = next((e for e in experts_data.EXPERT_MATRIX
                         if e["id"] == role), None)
        except Exception:
            meta = None
        if not meta:
            return self._demo_coordinator(user)
        outs = "、".join(meta.get("outputs") or []) or "本节成果"
        flow = "；".join((meta.get("flow") or [])[:3])
        return (
            f"【{meta['name']} · {meta['cn']}】已接管本环节（{meta.get('stage','')}）。\n\n"
            f"职责：{meta.get('desc','')}\n"
            f"本环节可产出：{outs}\n"
            f"我将按此路径推进：{flow}\n\n"
            f"（演示模式：请在「设置」中配置 API Key，即可由模型按上述路径真实产出）"
        )

    def _demo_coordinator(self, user):
        return (
            f"我是山海智绘·NexEdu 的系统路由。请选择要协作的专家，或使用「一键备课」：\n\n"
            f"- **NexAim 目标设计**：课标 → 可观测目标\n"
            f"- **NexDes 教学设计**：评价前置的教案\n"
            f"- **NexSca 作业与评价**：分层作业、量规、错因诊断\n"
            f"- **一键备课**：打通「目标→教学→评价」闭环\n\n"
            f"（演示模式：请在「设置」中配置 API Key 获取真实生成）"
        )
