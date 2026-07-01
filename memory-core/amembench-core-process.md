# amembench 核心测试流程（实例驱动）

> 用真实问题 `conv0_q0` 贯穿全流程，讲清楚一次 benchmark 从命令行到准确率报告的每一步。
> 可对照磁盘实物：`results/jiuwenclaw/py-1782745182727/conv0_q0/`

## 0. 命令与角色

启动命令（本次跑）：

```bash
amembench run \
  -d locomo \                                    # 数据集
  -b huawei_agentarts \                          # 记忆后端
  --backend-config examples/agentarts-local.json \
  --benchmark-config examples/jiuwenclaw.json
```

三个 glm-5.1 各司其职（详见 `docs/` 下 glm-5.1 分析）：

| 角色 | 谁 | 干什么 |
|---|---|---|
| **数据** | locomo10.json | 10 段对话 + 每段若干 QA（question + gold answer + category） |
| **记忆后端** | huawei_agentarts（`192.168.0.51:8086`） | 存对话、异步抽取记忆、按 query 检索 |
| **被测 Agent** | jiuwenswarm（Docker `jiuwenswarm:claw`）+ glm-5.1 | 读记忆、多步推理、产出答案 |
| **评分 Judge** | binary_llm + glm-5.1 | 判 generated answer 对/错 |
| **编排器** | amembench（Rust，经 PyO3 暴露给 Python CLI） | 调度、ingest、收集、汇总 |

## 1. 启动：CLI → Python → Rust

```
python/amembench/cli.py  run()                              # click 命令
  → python/amembench/wrapper.py  Benchmark.bench()         # 组装配置、progress 回调
      → _run_benchmark(...)                                 # PyO3 调进 Rust
          → src/lib.rs  run_benchmark_async()              # 真正的编排
```

`run_benchmark_async`（`src/lib.rs:249`）做四件事：生成 `run_id`、下载+加载数据集、构建 evaluator 工厂、跑并发 runner。

本次 `run_id = py-1782745182727`（时间戳）—— 它是所有产物路径和 AgentArts actor_id 的命名空间前缀。

## 2. 准备：数据集与工厂

**下载数据集** → `/tmp/amembench-datasets/locomo10.json`。

**加载**（`src/dataset/locomo.rs:268` `LocomoReader::load_stream`）：把 JSON 数组流式转成 `Question` 流。每条 Question 由 `convert_official`（`locomo.rs:244`）生成，以 `conv0_q0` 为例：

```
question_id  = "conv0_q0"                       # format!("conv{conversation_index}_q{qa_index}")
question     = "When did Caroline go to the LGBTQ support group?"
answer       = FreeForm { text: "7 May 2023" }  # gold answer，来自 qa.answer
question_type= "temporal_reasoning"             # category 2 → category_name
sessions     = [整段 conv0 对话的全部 session]   # official_sessions：未按 evidence 裁剪，全量
```

> ⚠ 这里的 `sessions` 是**整段对话**，是后文 mem_timeout 的结构性根因（每题都重灌一遍）。

**构建工厂**（`build_question_evaluator_factory`）：读 `jiuwenclaw.json` 的三块配置，造出可按 question 产出 (memory store, agent, judge) 三件套的 `ModelQuestionEvaluatorFactory`：

- `agent` = ACP provider，app=jiuwenswarm，runtime=docker，env 含 `MODEL_NAME=glm-5.1` / `API_BASE` / `API_KEY`。
- `judge` = binary_llm，model=glm-5.1。
- `backend` = huawei_agentarts（来自 `agentarts-local.json`：`poll_timeout_secs=600`）。

## 3. 调度：并发 runner

`ParallelBenchmarkRunner`（`src/pipeline/runner.rs`），`max_concurrency=8`、`fail_fast=false`，调 `run_collecting`（`runner.rs:244`）。

对每个出队的 Question：

1. `scope_for_question`（`runner.rs:121`）构造隔离作用域：
   ```
   actor_id = user_id = "amembench:py-1782745182727:conv0_q0"
   ```
   这是 AgentArts 里这一题记忆的命名空间（每题独立）。
2. `factory.build(question, scope)`（`model_factory.rs`）装配出 `QuestionEvaluator { memory, agent, judge }`。
3. `join_set.spawn( evaluate_one(question) )` —— 最多 8 个同时在飞。

progress 事件（`QuestionScheduled/Completed/Failed`）经 PyO3 回调写回 `output.log`，即你看到的 `progress completed=N failed=M in_flight=K`。

## 4. 单题流水线：`evaluate_one`（核心）

`src/pipeline/mod.rs:55`，三步：**ingest → answer → judge**。跟着 `conv0_q0` 走。

### 4.1 ingest —— 把对话灌进 AgentArts

```
memory.ingest_sessions(&question.sessions)
  → HuaweiAgentArtsBackend::ingest_sessions (src/backend/huawei_agentarts/mod.rs:71)
      for session in sessions:
          client.create_session(actor_id, assistant_id)      # 建会话
          client.add_messages(session_id, messages, ...)     # 灌整段对话的消息
      wait_for_extraction(actor_id)                          # 轮询直到记忆抽取完成
```

`wait_for_extraction`（`mod.rs:42`）：每 3s 调一次 `list_memories`，看到 `total>0` 就返回；超过 `poll_timeout_secs=600` 仍为 0 → `bail!("Timeout waiting for AgentArts memory extraction")`。这就是 39 个 mem_timeout 的来源。

> conv0_q0 这题成功通过：AgentArts 把灌进去的对话异步抽成了记忆条目，list_memories 返回 total>0。

### 4.2 answer —— agent 检索 + 推理 + 答题

```
agent.answer(question)
  → AcpAgent::answer (src/agent/acp_agent.rs:300)
      prompt = build_agent_answer_prompt(question)   # src/agent/prompts.rs:82
      for attempt in 0..=retry_count(2):
          timeout(600s, run_once(prompt))
```

**关键点**：`build_agent_answer_prompt` 传的是 `&[]`（空记忆），prompt 里的 `{memories}` 段渲染成 `- <none>`。也就是说 **amembench 不喂检索结果给 agent**——记忆检索由 jiuwenswarm agent 自己做：它经 ExternalMemoryRail（agentarts provider，见 `conv0_q0/agent/.logs/agent_server.log` 里 `[ExternalMemoryRail] Provider 'agentarts' initialized`）用同一个 actor_id 调 AgentArts 检索，拿到记忆后按 prompt 的 Step 1-7（扫描→实体验证→交叉引用→选最优→时间锚定→包含性检查→提交）推理，最后输出含 `ANSWER: ...` 的消息。

`run_once`（`acp_agent.rs:187`）通过 ACP stdio 协议（`AcpStdioTransport`）拉起 `jiuwenswarm-acp` 子进程，后者连到 Docker 容器里的 gateway（`ws://127.0.0.1:<port>/acp`）。ACP 会话历史落地到 `conv0_q0/agent/sessions/acp_<id>/history.json`。

> conv0_q0 实际产出的最终 assistant 消息（从 `history.json` 取最后一条 assistant）：
> ```
> 根据 retrieved memories……  ANSWER: Caroline went to the LGBTQ support group on 2023-10-21 (yesterday, relative to the conversation date).
> ```

**解析答案**（`src/model/mod.rs:267` `parse_generated_answer`）：`rsplit_once("ANSWER:")` 取最后一个 `ANSWER:` 之后的文本，trim。即：

```
generated.text = "Caroline went to the LGBTQ support group on 2023-10-21 (yesterday, relative to the conversation date)."
```

若解析后为空 → `malformed answer response: answer must not be empty`（即 empty_ans 失败类）。

### 4.3 judge —— LLM 二分类判分

```
judge.evaluate(question_id, question, generated, ground_truth)
  → BinaryLlmJudge::evaluate (src/judge/binary_llm.rs:74)
      prompt = render_judge_prompt(question, gold, generated)   # 套 BINARY_LLM_JUDGE_PROMPT_TEMPLATE
      model_client.complete(prompt)                             # 调 glm-5.1（经 RetryingAgentModelClient，retry=5, timeout=30s）
      parse_judge_label(response.content)                       # extract_json_object 取 {..}，serde 解析 label
```

judge prompt（`binary_llm.rs:18`）给 glm-5.1 的 7 条宽松规则：部分给分、释义算对、多细节算对、**日期 ±14 天容差**、同指代算对……要求模型回 `{"reasoning":..., "label":"CORRECT"|"WRONG"}`。

> 对照 conv0_q0：gold = `7 May 2023`，generated = `... 2023-10-21 ...`。按规则 4（日期 ±14 天），5 月 vs 10 月差了 5 个月 → 判 **WRONG**（score=0.0）。（仅作判分逻辑示例，非实际跑出的 verdict。）

若 judge 超时 → `judge model request timed out after 30 seconds`；若模型把 JSON 包进 ` ```json ` 围栏导致 `serde_json::from_str` 失败 → `malformed binary judge response`（且因解析发生在 `complete()` 成功之后，**不触发重试**）。

### 4.4 汇成 QuestionResult

`evaluate_one` 把上面三步的结果封成 `QuestionResult`（`src/pipeline/types.rs:53`）：含 `ground_truth_answer`、`generated_answer`、`metric_score.score`（1.0/0.0）、`question_type` 等，塞回 runner。

## 5. 汇总：从单题到准确率

`run_collecting` 收齐所有 `QuestionResult` + errors 后返回（`runner.rs:244`），`run_benchmark_async` 收尾（`src/lib.rs:305`）：

1. **补延迟**：`populate_elapsed_ms` 从各题 `history.json` 读检索耗时回填 `elapsed_ms`。
2. **算聚合**：`AggregateReport::from_results`（`src/pipeline/types.rs:86`）—— `mean_score` 就是**准确率**（所有题 score 的均值），外加按 `question_type` / `source` 拆分、retrieve_latency p50/p95。
3. **落盘报告**：`BenchmarkRunReport::new(...)` + `write_run_report`（`src/results.rs:56`）原子写到 `results/jiuwenclaw/<run-id>.report.json`（路径由 `output.dir` 决定）。
4. **回报 CLI**：`PyBenchmarkResult { overall: aggregate.mean_score }`，`cli.py` 打印 `overall=0.xxxx`。

## 6. 一图流：conv0_q0 的完整旅程

```
locomo10.json
   │ LocomoReader.load_stream → Question{conv0_q0, "When did Caroline…", gold="7 May 2023", sessions=[整段对话]}
   ▼
ParallelBenchmarkRunner (max_concurrency=8)
   │ scope_for_question → actor_id="amembench:py-…:conv0_q0"
   │ factory.build → {memory: AgentArts, agent: AcpAgent, judge: BinaryLlmJudge}
   ▼
evaluate_one (mod.rs:55)
   ├─ ingest   AgentArts.create_session+add_messages(整段对话) → wait_for_extraction(轮询≤600s)   ✅
   ├─ answer   AcpAgent→jiuwenswarm-acp(Docker)→glm-5.1 自检索 AgentArts + Step1-7 推理
   │              → "…ANSWER: Caroline went … on 2023-10-21 …"
   │           parse_generated_answer: rsplit_once("ANSWER:") → generated.text
   └─ judge    BinaryLlmJudge 渲染 prompt(question/gold/generated) → glm-5.1 → {"label":"WRONG"} (±14天不符? 否) → score=0.0
   ▼
QuestionResult{metric_score.score=0.0, …}
   ▼ (与其它 ~1985 题汇合)
AggregateReport.from_results → mean_score = 准确率
   ▼
write_run_report → results/jiuwenclaw/py-1782745182727.report.json
   ▼
cli: overall=0.xxxx
```

## 7. 失败点对照（衔接零 error 方案）

| 流水线位置 | 失败类 | 原因 |
|---|---|---|
| 4.1 ingest `wait_for_extraction` | mem_timeout | 整段对话每题重灌 + 抽取无重试 + 600s 上限 |
| 4.2 answer ACP 调用 | ratelimit_429 / acp_600s | 退避 50ms 级（对 TPM 无效）/ agent 推理卡住 |
| 4.2 parse | empty_ans | agent 没给 `ANSWER:`，解析失败不重试 |
| 4.3 judge complete | judge_30s | 30s 超时 |
| 4.3 judge parse | malformed_judge | ` ```json ` 围栏，解析在 complete 之后不重试 |

详见 `docs/zero-error-plan.md` 的分层治理策略。
