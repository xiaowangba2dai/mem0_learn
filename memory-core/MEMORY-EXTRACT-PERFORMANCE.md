# Memory Extract 性能分析与优化建议

分析时间：2026-06-30  
对象：当前正在运行的 `memory-core`、RocketMQ、One-API、PostgreSQL、Redis、OpenSearch

## 结论

当前 extract 性能瓶颈不在本机 CPU、内存、RocketMQ broker 或 OpenSearch，而在 extract 链路对外部模型服务的调用数量和并发控制上。单个成功任务通常需要 2 次 LLM 调用、多次 embedding 调用、1 次向量检索和 1 次 bulk 写入；当 MQ consumer 并发过高时，大量任务同时等待 LLM/embedding，尾延迟明显放大。

优先级最高的优化是：降低并发到模型服务可承受范围、减少每个任务的 embedding 次数、在 extraction 无结果时跳过 consolidation、关闭 DEBUG 级别请求/响应体日志。

## 当前运行态

容器状态：

- `memory-core`: 运行约 12 小时，healthy
- `rmqnamesrv` / `rmqbroker` / `rmqproxy`: 运行约 18 小时
- `one-api`, `postgres`, `redis`, `opensearch`, `memory-access` 正常运行

资源占用快照：

- `memory-core`: CPU 约 4%，内存约 321 MiB，PIDs 约 330
- `rmqbroker`: CPU 约 9%，内存约 3.1 GiB
- `opensearch`: CPU 约 1%，内存约 1.8 GiB
- `one-api`: CPU 约 3%，内存约 202 MiB

`memory-core` 当前关键配置：

- uvicorn workers: 2
- `ENABLE_MQ_CONSUMER=true`
- `ROCKETMQ_CONSUMPTION_THREAD_COUNT=64`
- `ROCKETMQ_MAX_CACHE_MESSAGE_COUNT=64`
- `MAAS_LLM_MODEL=deepseek-v3.2`
- LLM/Embedding 经 One-API，Rerank 直连 MaaS
- `LOG_LEVEL=DEBUG`
- PG pool max 20，CSS max connections 20

运行态和项目记忆中的预期值不一致：项目记忆里提到 MQ thread 已优化为 4，但当前容器实际是 64。这个差异会直接影响 extract 尾延迟。

## 日志观察

从最近约 5000 行 `memory-core` 日志统计：

- `task.completed`: 370 条
- extract 耗时：
  - min: 0 ms
  - p50: 约 5.3 s
  - p90: 约 38.0 s
  - p95: 约 42.7 s
  - max: 约 65.0 s
  - avg: 约 14.1 s
- `records_created` 样本：194 条
  - zero: 32 条
  - avg: 约 5.77 条
  - max: 14 条
- `written_count` 样本：162 条
  - avg: 约 6.91 条
  - max: 14 条
- `llm_generated`: 405 次
- `embedding_generated`: 1447 次
- `operation_executor_no_actionable_ops`: 32 次
- 明显失败/重试信号：0

解释：

- 成功率看起来不错，主要问题是尾延迟。
- embedding 调用次数远高于 LLM 调用次数。原因是每个任务除了 consolidation 查询前需要 1 次 embedding，还会对 LLM 输出的每条 ADD/UPDATE 逐条重新 embedding。
- 0 写入任务仍然执行了 extraction、consolidation 和 consolidation 前 embedding，存在可优化空间。
- DEBUG 日志会记录 HTTP 请求体和响应体，对 benchmark/search 场景会引入额外 I/O 和序列化成本。

## Extract 链路

当前 MQ extract 主链路：

1. RocketMQ PushConsumer 线程收到消息。
2. `MemoryExtractConsumer.consume()` 解析 payload。
3. 通过 `asyncio.run_coroutine_threadsafe()` 把任务提交到 uvicorn worker 的 event loop。
4. `TaskHandler.handle()` 执行幂等检查。
5. 查询 PG/Redis：
   - strategy + steps
   - session
   - messages by seq range
6. `MemoryExtractionPipeline.execute()` 按 step 顺序执行。
7. `ExtractionStep`:
   - 序列化 messages
   - 调 LLM 提取结构化记忆
   - parser 解析 extraction 结果
8. `ConsolidationStep`:
   - 将 extracted data 序列化为 query text
   - 调 embedding
   - OpenSearch KNN 查历史记忆，top_k 固定 20
   - 调 LLM 输出 ADD/UPDATE/SKIP
   - parser 解析操作
9. `execute_memory_operations()`:
   - 过滤 SKIP
   - 对每条 ADD/UPDATE 逐条调用 embedding
   - OpenSearch bulk 写入
10. 返回 MQ SUCCESS；失败且可重试时返回 FAILURE。

## Extract 实际例子

下面用一个具体任务说明系统到底在做什么。

### 例子 1：普通语义记忆提取

假设一次会话里有这些消息：

```text
user: I went hiking with Sam this morning.
assistant: Sounds great. How was the trail?
user: It was muddy, but Sam loved the waterfall.
assistant: Nice, I will remember Sam likes waterfall hikes.
```

上游 `memory-access` 会把一段消息范围投递到 RocketMQ，payload 大致是：

```json
{
  "task_type": "memory_extract",
  "task_id": "task-001",
  "space_id": "a0000000-0000-0000-0000-000000000001",
  "db_cell_id": 1,
  "session_id": "session-001",
  "from_seq": 1,
  "to_seq": 4,
  "strategy_id": "00000000-0000-0000-0000-000000000001",
  "strategy_type": "semantic"
}
```

`memory-core` 收到后不会直接用 MQ 消息里的完整文本，因为 MQ 里只带了 session 和 seq 范围。它会先查 PostgreSQL：

- 根据 `strategy_id` 查当前策略和 step 配置。
- 根据 `session_id` 查会话的 `actor_id` / `assistant_id`。
- 根据 `from_seq=1`、`to_seq=4` 查这 4 条消息。

然后进入 extraction step。系统会把消息拼成类似这样的文本：

```text
user: I went hiking with Sam this morning.
assistant: Sounds great. How was the trail?
user: It was muddy, but Sam loved the waterfall.
assistant: Nice, I will remember Sam likes waterfall hikes.
```

第一次 LLM 调用负责“从对话里抽取候选记忆”。LLM 可能返回：

```json
[
  {"fact": "The user went hiking with Sam this morning."},
  {"fact": "Sam likes waterfall hikes."}
]
```

这一步只是候选结果，还没有写库。接下来进入 consolidation step。

consolidation step 会先把候选记忆序列化成 query text，并调一次 embedding：

```text
[{"fact":"The user went hiking with Sam this morning."},{"fact":"Sam likes waterfall hikes."}]
```

得到向量后，去 OpenSearch 查同一 `space_id`、同一 strategy、同一 actor/session 范围内的历史记忆。假设查到：

```text
[ID]=mem-101
[MEMORY] {"fact": "Sam enjoys hiking."}

[ID]=mem-102
[MEMORY] {"fact": "The user often hikes on weekends."}
```

然后第二次 LLM 调用负责“合并新旧记忆”。它看到新候选和历史记忆后，可能输出：

```json
[
  {
    "op": "UPDATE",
    "id": "mem-101",
    "content": "{\"fact\":\"Sam enjoys hiking, especially waterfall hikes.\"}"
  },
  {
    "op": "ADD",
    "content": "{\"fact\":\"The user went hiking with Sam this morning.\"}"
  }
]
```

最后 operation executor 会做两件事：

- 对 UPDATE 后的新内容重新生成 embedding。
- 对 ADD 的新内容生成 embedding。
- 把两条记录通过 OpenSearch bulk 写入。

所以这个小例子里，一次 extract 至少产生：

- 2 次 LLM 调用：extraction + consolidation
- 3 次 embedding 调用：consolidation 查询 1 次，UPDATE 1 次，ADD 1 次
- 1 次 OpenSearch search
- 1 次 OpenSearch bulk

如果 LLM 输出 8 条 ADD/UPDATE，就会变成：

- 2 次 LLM
- 1 次查询用 embedding
- 8 次写入用 embedding
- 1 次 search
- 1 次 bulk

这就是为什么日志里 `embedding_generated` 明显多于 `llm_generated`。

### 例子 2：为什么 batch embedding 有收益

当前写入阶段是逐条 embedding。假设 consolidation 输出 6 条 ADD：

```text
ADD memory A
ADD memory B
ADD memory C
ADD memory D
ADD memory E
ADD memory F
```

现在的做法等价于：

```text
embed(A)
embed(B)
embed(C)
embed(D)
embed(E)
embed(F)
bulk_write(A, B, C, D, E, F)
```

也就是 6 次 embedding HTTP 请求。每次请求都有网络开销、One-API 调度开销、MaaS 推理排队开销。

优化后可以变成：

```text
embed_batch([A, B, C, D, E, F])
bulk_write(A, B, C, D, E, F)
```

HTTP 请求数从 6 次降到 1 次。对当前日志里常见的 `written_count=6`、`written_count=8`、`written_count=13` 的任务，这个优化会直接减少模型服务压力。

用日志中的样本粗略估算：

- 162 个写入样本，平均 `written_count` 约 6.91。
- 当前写入阶段大约是每个任务 6.91 次 embedding 请求。
- batch 后可以接近每个任务 1 次 embedding 请求。

这不会减少需要生成的向量数量，但会减少请求次数、排队次数和重试面。

### 例子 3：为什么空 extraction 要短路

假设用户只是寒暄：

```text
user: hi
assistant: hello
user: thanks
assistant: you're welcome
```

extraction LLM 很可能返回：

```json
[]
```

这说明没有值得写入的记忆。

当前流程仍可能继续进入 consolidation：

```text
extraction LLM -> []
embedding("[]")
OpenSearch search
consolidation LLM -> []
no_actionable_ops
task completed, records_created=0
```

也就是说，即使最后一条记忆都没写，仍可能多花一次 embedding、一次 search 和一次 LLM。

优化后应该是：

```text
extraction LLM -> []
skip consolidation
task completed, records_created=0
```

最近日志里 `records_created=0` 的样本约占 16.5%。这类任务短路后，既省钱，也能减少高并发时的排队。

### 例子 4：为什么 64 个 MQ 线程会放大尾延迟

假设每个 extract 任务平均需要：

- 2 次 LLM
- 7 次 embedding
- 若干 DB/OpenSearch 操作

如果 MQ consumer 同时拉起 64 个任务，瞬间会形成大约：

```text
64 * 2 = 128 次 LLM 调用需求
64 * 7 = 448 次 embedding 调用需求
```

这些请求不会真的同时被下游无限处理。One-API 和 MaaS 都有连接池、渠道和限速。结果通常是：

- 前面一部分请求很快完成。
- 后面大量请求在 One-API/MaaS 排队。
- 单任务内部的第二次 LLM、后续 embedding 又继续排队。
- p50 可能还可以，但 p95/p99 被拉长。

所以“线程越多越快”在这里不成立。更合理的做法是让内存核心服务形成背压：

```text
MQ 拉取任务并发：8-16
LLM 实际并发：按模型服务可承受 QPS 控制
embedding 实际并发：按 embedding 服务可承受 QPS 控制
```

这样总吞吐可能差不多，甚至更高，但尾延迟会更稳定。

### 例子 5：一次任务的耗时可能如何分布

一个 `records_created=6` 的任务，总耗时可能是 30 秒，内部大致像这样：

```text
DB/cache 查询：50 ms
extraction LLM：3,000 ms
consolidation query embedding：300 ms
OpenSearch search：20 ms
consolidation LLM：4,000 ms
6 条记录逐条 embedding：6 * 300 ms = 1,800 ms
OpenSearch bulk wait_for refresh：300 ms
排队等待模型服务：20,000 ms
```

这里真正的本地计算很少，主要是模型服务和排队。当前日志只有总耗时，没有阶段耗时，所以文档建议补阶段级指标。只有知道每个阶段真实耗时，才能判断下一步该调并发、换模型、批量 embedding，还是优化 OpenSearch refresh。

## 主要瓶颈

### 1. MQ consumer 并发过高

当前 `ROCKETMQ_CONSUMPTION_THREAD_COUNT=64`，但 uvicorn workers 只有 2。RocketMQ 的 64 个消费线程会把大量 coroutine 提交到少量 event loop，形成模型调用风暴。

这类任务是 I/O bound，但下游 MaaS/One-API 有限速和连接池约束。过高并发不会线性提升吞吐，反而会增加排队、超时风险和 p95/p99。

建议：

- 先把 MQ 消费线程降到 8 或 16。
- 在应用内增加按资源分组的 `asyncio.Semaphore`：
  - LLM 并发：按 One-API/MaaS 实际限速设置，例如 8-16。
  - embedding 并发：按 One-API channel 限速设置。
  - OpenSearch bulk 并发：通常可低一些，例如 8。
- 不只依赖 RocketMQ thread count，因为 HTTP debug dispatch 和未来其他入口也会绕过 MQ 线程限制。

### 2. 单任务 embedding 次数过多

当前 `execute_memory_operations()` 对每条 ADD/UPDATE 串行调用 `embedding_service.embed(content)`。一个任务写入 6-14 条记录时，就会产生 6-14 次 embedding 请求。

已有 `EmbeddingService.embed_batch()` 和 MaaS adapter 的 batch 能力，但 operation executor 没有使用。

建议：

- 在 operation executor 中先收集所有待写内容。
- 一次调用 `embed_batch(contents)`。
- 再把 embedding 结果回填到对应 record。

预期收益：

- 对写入 6 条记录的任务，operation 阶段 embedding 请求从 6 次降到 1 次。
- 对写入 14 条记录的任务，从 14 次降到 1 次。
- p95 会明显下降，One-API/MaaS 压力也会下降。

### 3. extraction 空结果仍进入 consolidation

日志里有不少 `records_created=0` 和 `operation_executor_no_actionable_ops`。有些任务 LLM 输出 `[]` 或 consolidation 输出全 SKIP，但仍然完成了 consolidation 前的 embedding、历史检索和第二次 LLM 调用。

建议：

- 在 `ExtractionStep` 后，如果 `context.extracted_data` 是空列表/空对象，直接结束 pipeline。
- 对明显空 extraction 的任务跳过 consolidation。
- 对 parser 输出全 SKIP 的任务不再做 embedding/write，这部分已经做了，但更早跳过收益更大。

预期收益：

- 0 写入任务可以从 2 次 LLM + 1 次 embedding + 1 次 search，降到 1 次 LLM。
- 当前 194 个 records 样本里 32 个 zero，比例约 16.5%，值得优化。

### 4. DEBUG 日志成本高

当前 `LOG_LEVEL=DEBUG`，`RequestLoggingMiddleware` 会读取并记录请求体和响应体。日志中能看到 search 响应体被截断前仍有数 KB。

建议：

- 性能测试和生产运行使用 `LOG_LEVEL=INFO`。
- 保留按需打开 domain debug 的能力，但默认不要记录 body。
- 对 MQ extract 添加结构化耗时指标，替代大量 debug body 日志。

### 5. 缺少阶段级耗时指标

现在日志只在 `task.completed` 记录总耗时。要定位 p95，需要知道每个阶段耗时：

- idempotency
- DB/cache query
- extraction LLM
- extraction parse
- consolidation query embedding
- OpenSearch search
- consolidation LLM
- operation embedding
- OpenSearch bulk

建议：

- 在 `TaskHandler`、`ExtractionStep`、`ConsolidationStep`、`execute_memory_operations()` 增加阶段耗时日志。
- 输出 Prometheus histogram：
  - `memory_extract_task_duration_ms`
  - `memory_extract_llm_duration_ms`
  - `memory_extract_embedding_duration_ms`
  - `memory_extract_embedding_batch_size`
  - `memory_extract_records_written`
  - `memory_extract_inflight_tasks`

## 优化方案

### P0：配置层快速优化

这些改动不需要大规模改代码：

1. `LOG_LEVEL=INFO`
2. `ROCKETMQ_CONSUMPTION_THREAD_COUNT=8` 起步压测
3. `ROCKETMQ_MAX_CACHE_MESSAGE_COUNT=16` 或 32
4. 保持 uvicorn workers 2，先不要盲目增加 worker
5. 对 One-API/MaaS 做实际限速测量，再决定是否提升 MQ 并发

为什么不直接加 worker：

- 当前 CPU 和内存都没打满。
- 增加 worker 会复制 MQ consumer，每个 worker 都可能启动 PushConsumer，消费组行为和并发会更复杂。
- 如果瓶颈是模型服务，增加 worker 只会放大下游压力。

### P1：批量 embedding

改 `execute_memory_operations()`：

- 构造 record skeleton 时先不填 embedding。
- 收集所有 `content`。
- 调 `embedding_service.embed_batch(contents)`。
- 将结果逐个写回 record。
- 失败时整体回退，保持现有 bulk 语义。

注意：

- 需要保证 batch 响应顺序和输入顺序一致。
- batch 太大时可切分，例如每批 16 或 32。
- 对空内容继续走现有错误逻辑。

### P1：空 extraction 短路

在 pipeline 或 extraction step 后加判断：

- `[]`
- `{}`
- `None`
- parser 定义的空结果

若为空，记录 `extraction_empty_skip_consolidation`，直接返回成功且 `records_created=0`。

### P1：应用内并发控制

增加共享 limiter：

- 在 `app.state` 或 service 层持有 semaphore。
- `LLMService.generate()` 包一层 `async with llm_limiter`。
- `EmbeddingService.embed/embed_batch()` 包一层 `async with embedding_limiter`。
- limiter 配置化：
  - `MAAS_LLM_MAX_CONCURRENCY`
  - `MAAS_EMBEDDING_MAX_CONCURRENCY`
  - `CSS_BULK_MAX_CONCURRENCY`

这个比只调 MQ thread 更稳，因为它覆盖所有入口。

### P2：减少 LLM 调用次数

当前每个任务固定 extraction LLM + consolidation LLM。可以考虑：

- 对低风险场景，把 extraction 和 consolidation 合并成一次 LLM：输入历史记忆和新消息，直接输出 ADD/UPDATE/SKIP。
- 对历史为空的场景跳过 consolidation LLM，直接把 extraction 输出转 ADD。
- 对 `historical_count=0` 或极低相似度结果，采用规则化 ADD，不再让 LLM 合并。

这个改动会影响记忆质量，需要用 amembench 回归验证。

### P2：历史检索 top_k 自适应

consolidation 固定 `top_k=20`。当 actor/session 下历史很少或 extraction 很短时，可以降低到 5-10；当历史很多且 query 长时再提升。

建议：

- 默认 top_k 从 20 降到 10 做 A/B。
- 对 summary/episodic 等策略单独配置。
- 保留策略 step config 覆盖能力。

### P2：OpenSearch 写入刷新策略

CSS bulk 当前 `refresh="wait_for"`，写入会等待刷新可见。这提升读后写一致性，但会拖慢写入吞吐。

如果业务允许短暂不可见：

- extract 异步后台写可以改为 `refresh=false`。
- 用户主动 CRUD 可保留 `wait_for`。
- 或按场景配置刷新策略。

这项需要确认 amembench 是否立即 search/list 验证刚写入记忆。如果 benchmark 强依赖强一致，不能直接改。

## 建议落地顺序

1. 先把运行配置调回保守值：MQ threads 8-16、cache 16-32、LOG_LEVEL INFO。
2. 加阶段耗时日志，跑一轮 benchmark 获取真实分解。
3. 实现 operation executor 的 batch embedding。
4. 实现空 extraction 短路。
5. 加 LLM/embedding semaphore。
6. 再考虑减少 LLM 调用、调整 top_k 和 refresh 策略。

## 已完成优化归档

### 2026-06-30：batch embedding

已完成：`execute_memory_operations()` 不再对每条 ADD/UPDATE 逐条调用 `embed()`，而是先收集所有待写内容，再调用一次 `embedding_service.embed_batch()`，随后构造 `VectorOperation` 并 bulk 写入 OpenSearch。

效果：

- 写入阶段 embedding HTTP 请求数从 `written_count` 次降为 1 次。
- 保留原有 SKIP、no-op UPDATE、UPDATE created_at 继承逻辑。
- 日志新增 `embedding_batch_size`。

验证：

- `py_compile` 通过。
- 最小行为测试通过：ADD + UPDATE + no-op UPDATE + SKIP 只触发 1 次 `embed_batch()`，不触发单条 `embed()`。

### 2026-06-30：空 extraction 短路

已完成：`MemoryExtractionPipeline` 在 extraction step 后检查 `context.extracted_data`。当结果为 `None`、空列表、空字典、空集合或空字符串时，直接跳过后续 consolidation/reflection。

效果：

- 空结果任务不再额外执行 consolidation LLM、query embedding 和 OpenSearch search。
- 日志新增 `extraction_empty_skip_remaining_steps`。

验证：

- `py_compile` 通过。
- 最小行为测试通过：空 extraction 时 consolidation 调用次数为 0；非空 extraction 时 consolidation 正常执行。

### 2026-06-30：阶段耗时日志

已完成：extract 主链路新增结构化阶段耗时日志。

覆盖阶段：

- `TaskHandler`: `idempotency`、`query_strategy`、`query_session`、`query_messages`、`pipeline`
- `ExtractionStep`: `serialize_messages`、`llm_generate`、`parse_response`、step 总耗时
- `ConsolidationStep`: `prepare_query`、`query_embedding`、`vector_search`、`build_prompt`、`llm_generate`、`parse_operations`、`execute_operations`、step 总耗时
- `OperationExecutor`: `prepare_writes`、`embedding_batch`、`build_vector_operations`、`vector_bulk_write`、executor 总耗时

效果：

- 每条阶段日志都有 `duration_ms`。
- 关键上下文包括 `message_count`、`historical_count`、`operation_count`、`embedding_batch_size`、`record_count`。

验证：

- `py_compile` 通过。
- batch embedding 和空 extraction 的最小行为测试均通过。

### 2026-06-30：运行配置收敛

已完成：将运行配置调整为保守并发。

配置：

- `LOG_LEVEL=INFO`
- `ROCKETMQ_CONSUMPTION_THREAD_COUNT=8`
- `ROCKETMQ_MAX_CACHE_MESSAGE_COUNT=16`
- `uvicorn workers=2`

同步文件：

- `/root/memory/memory-core/.env`
- `/root/memory/memory-core/BUILD-AND-DEPLOY.md`
- `/root/memory/start-all.sh`

效果：

- 降低 MQ consumer 对 One-API/MaaS 的瞬时请求风暴。
- 避免 DEBUG 请求/响应体日志干扰 benchmark。
- 避免全栈重启脚本把 worker 数改回 4。

### 2026-06-30：真实压测阶段观察

测试窗口：从 `2026-06-30T07:54:37Z` 开始抓取 `memory-core` 容器日志。

样本概况：

- 完成任务数：85
- `task.completed`: p50 34228ms，p90 54184ms，p95 58176ms，p99 67719ms，max 83265ms
- `pipeline`: p50 34224ms，p90 54181ms，p95 58172ms，p99 67715ms，max 83261ms
- `records_created`: p50 7，p90 12，p95 13，max 24
- `embedding_batch_size`: p50 7，p90 12，p95 13，max 24

阶段耗时：

- `extraction_llm`: p50 10174ms，p90 15012ms，p95 16913ms，max 21629ms
- `consolidation_llm`: p50 18932ms，p90 36540ms，p95 38589ms，max 59593ms
- `consolidation_query_embedding`: p50 689ms，p90 4432ms，p95 9272ms，max 12279ms
- `operation_embedding_batch`: p50 975ms，p90 4481ms，p95 5160ms，max 7874ms
- `consolidation_vector_search`: p50 6ms，p95 8ms，max 36ms
- `operation_bulk_write`: p50 532ms，p95 1013ms，max 1036ms
- `execute_operations`: p50 1656ms，p95 5921ms，max 8831ms

资源观察：

- `memory-core` CPU 未打满，内存约 254MiB。
- OpenSearch、Postgres、Redis CPU 都很低。
- RocketMQ broker CPU 约 8%-9%，不是当前主瓶颈。
- 当前瓶颈不是本机 CPU/内存/DB/OpenSearch，而是 LLM/embedding 服务调用耗时和排队尾延迟。

结论：

- `pipeline` 几乎等于 `task.completed`，说明主要耗时在 extract pipeline 内部。
- 第一瓶颈是 `consolidation_llm`，p95 接近 39s，max 接近 60s。
- 第二瓶颈是 `extraction_llm`，p95 约 17s。
- embedding 已经批量化生效：`embedding_batch_size` 与 `records_created` 分布一致，说明写入阶段每个任务走一次 batch embedding。
- OpenSearch vector search p95 只有 8ms，不需要优先优化。
- bulk write p95 约 1s，有优化空间，但收益远小于减少 LLM consolidation 耗时。

本轮发现的正确性问题：

- 出现 1 次 `task.failed` 和 1 次 `mq.task_retriable`。
- 失败点在 semantic consolidation parser。
- LLM 返回的 `UpdateMemory` item 使用了 `updated_memory` 字段：

```json
{
  "operation": "UpdateMemory",
  "update_id": "...",
  "updated_memory": {
    "fact": "..."
  }
}
```

- 当前 parser 报错：`ParseError: Malformed semantic operation item: 'memory'`。
- 判断：parser 可能在分支判断前强制读取 `item["memory"]`，导致 UPDATE 结构没有 `memory` 字段时失败。
- 这个问题会触发 MQ retry，放大队列压力，应优先修复。

下一步优先级：

1. 修复 semantic parser 或 prompt schema，兼容 `UpdateMemory.updated_memory`，避免可恢复格式问题进入 MQ retry。
2. 优化 `consolidation_llm`：减少 historical memory 数量、压缩 prompt、降低输出长度，或在低风险场景跳过 consolidation LLM。
3. 给 LLM 和 embedding 加应用级 semaphore，防止 `2 workers * 8 MQ threads` 同时把上游模型服务打出排队尾延迟。
4. 评估 consolidation 使用更快模型；extraction 和 consolidation 可以分模型配置。
5. 在 LLM 主瓶颈缓解后，再考虑 bulk write refresh 策略和 OpenSearch 写入优化。

### 2026-06-30：关于“等待 LLM 时释放 MQ 槽位”的分析

用户提出的想法：大部分耗时都在等待 LLM 返回，那么是否可以把等待中的任务先放到某个地方，释放当前 MQ consumer 槽位；等大模型返回后，再把任务拿回来继续执行。这样 consumer 可以继续拉新任务、发起更多 LLM 请求，从而减少 MQ 堆积。

这个方向是合理的，但要拆成两个问题：

1. **MQ consumer 是否被等待中的任务占住。**
2. **LLM/embedding 上游是否真的还能接受更多并发。**

如果只看本机资源，`memory-core` CPU 很低，说明大量任务确实在等待外部 I/O。但这不等价于“可以无限拉更多任务”。当前链路里，一个 MQ message 被消费后，会在同一个任务生命周期里依次执行 extraction LLM、consolidation embedding、vector search、consolidation LLM、write embedding、bulk write。只要这个 pipeline 没结束，这个任务就占着一个处理槽位。

当前配置大致是：

```text
uvicorn workers = 2
ROCKETMQ_CONSUMPTION_THREAD_COUNT = 8
理论同时处理中的 MQ task 约 16 个
```

如果这 16 个任务都在等 `consolidation_llm` 返回，本机 CPU 会很闲，但 MQ consumer 不能无限继续处理新消息，因为处理槽位已经被这 16 个未完成任务占住。

#### 例子 1：同步占槽模式

假设有 100 个 MQ task，每个 task 的耗时是：

```text
本地查询：1ms
extraction_llm：10s
query_embedding + search：1s
consolidation_llm：20s
write：2s
总耗时：约 33s
```

如果 consumer 并发是 16：

```text
第 0 秒：拉取 16 个任务
第 0-10 秒：16 个任务都在等待 extraction_llm
第 11-31 秒：大部分任务等待 consolidation_llm
第 33 秒：第一批 16 个任务完成，consumer 才释放槽位继续拉下一批
```

这种模式下，单机 CPU 可能只有个位数到十几个百分点，但 MQ backlog 仍然下降很慢。瓶颈不是本地计算，而是“任务等待期间仍占用 MQ 处理槽位”。

#### 例子 2：释放槽位的异步状态机模式

可以把 pipeline 拆成多个可恢复阶段：

```text
stage=received
  -> 查询 strategy/session/messages
  -> 发起 extraction_llm
  -> 持久化 task_state=waiting_extraction_llm
  -> 释放 MQ consumer 槽位

LLM 返回
  -> 恢复 task_state
  -> parse extraction
  -> 发起 query_embedding / vector_search / consolidation_llm
  -> 持久化 task_state=waiting_consolidation_llm
  -> 再次释放执行槽位

consolidation LLM 返回
  -> 恢复 task_state
  -> parse operations
  -> batch embedding
  -> bulk write
  -> task completed
```

这样做以后，MQ consumer 不需要一直被等待中的任务占住。等待任务可以放在持久化状态表、Redis stream、内部 delayed queue，或者专门的 workflow/task engine 里。LLM 返回后，通过 callback、polling、或者内部完成队列恢复执行。

如果上游 LLM 还有余量，这种模式会明显提高吞吐：

```text
同步占槽：最多 16 个任务同时等待 LLM
异步状态机：consumer 可以很快接收更多任务，但 LLM inflight 由专门 limiter 控制，比如 32 或 64
```

此时 MQ backlog 会下降更快，因为“MQ 消费速度”和“LLM 返回速度”被解耦了。

#### 例子 3：为什么不能无限提高并发

如果 LLM 服务实际只能稳定处理 20 个并发请求，而我们把 inflight 拉到 100，结果通常不是吞吐提升 5 倍，而是：

```text
前 20 个请求：正常生成
后 80 个请求：在 One-API/MaaS/模型服务侧排队
单次 p95 从 20s 变成 60s+
timeout/retry 增加
MQ retry 增加
同一批任务占用系统资源更久
```

这时只是把队列从 RocketMQ 搬到了模型服务内部。外观看起来 MQ backlog 下降了，但端到端完成时间和失败率可能更差。

所以正确设计不是“consumer 能拉多少就拉多少”，而是加一个 LLM 调度层：

```text
MQ consumer concurrency：负责快速接收和拆分任务
LLM inflight semaphore：负责控制真正打到模型服务的并发
embedding inflight semaphore：负责控制 embedding 并发
task state store：负责保存等待中的上下文，避免进程重启丢任务
completion queue：负责在模型返回后恢复 pipeline
```

#### 推荐的演进路径

短期不建议直接重构成完整 workflow。更稳的落地顺序：

1. **先加 LLM/embedding semaphore。**
   - 例如 `MAAS_LLM_MAX_CONCURRENCY=16` 起步。
   - 逐步测 16、24、32。
   - 观察 LLM p50/p95、timeout、429、retry、task throughput。

2. **让 MQ 并发略高于 LLM 并发，但不要无限高。**
   - 例如 LLM 并发 16，MQ task 并发可以 24-32。
   - 这样可以覆盖 DB 查询、parse、write 的短暂空档。
   - 但不能让几百个任务同时堆进 LLM。

3. **如果确认 MQ 槽位被 LLM wait 严重占住，再拆异步状态机。**
   - 第一阶段可以只拆 `consolidation_llm`，因为它是最大瓶颈。
   - extraction 仍保持同步，consolidation LLM 改成 pending/resume。

4. **最后再做完整持久化 workflow。**
   - 每个外部模型调用前后都落 task state。
   - 支持重启恢复、超时扫描、幂等续跑、失败重试。

#### 判断方案是否有效的指标

需要同时看这些指标，而不是只看 MQ backlog：

- `task.completed` throughput 是否提高。
- `task.completed` p95/p99 是否恶化。
- `consolidation_llm` p95/p99 是否恶化。
- LLM timeout、429、retry 是否增加。
- MQ backlog 是否下降。
- waiting 状态任务数量是否可控。
- 从 MQ 入队到最终 completed 的端到端耗时是否下降。

如果 MQ backlog 降了，但 `task.completed` p95 从 58s 涨到 120s，或者 retry 明显增加，就说明只是把压力转移到了 LLM 服务侧，不是有效优化。

### 2026-06-30：MaaS deepseek-v3.2 容量测试

测试目标：确认 MaaS/One-API 在不同请求量级下的真实持续能力，而不是只看控制台显示的 `deepseek-v3.2 RPM=700`。

已知配额：

```text
deepseek-v3.2 RPM = 700
deepseek-v3.2 TPM = 500000
当前通过 One-API 配了 2 个 API key
```

如果 MaaS 按 key 分别计量，理论聚合上限大致是：

```text
RPM ≈ 1400
TPM ≈ 1000000
```

但这只是理论值，实际还会受 One-API 调度、连接池、渠道状态、请求输出长度、动态限流影响。

测试脚本：

- `/root/memory/memory-core/scripts/maas_llm_capacity.py`
- 容器内执行路径：`/tmp/maas_llm_capacity.py`
- 使用容器内真实环境变量：`MAAS_LLM_ENDPOINT=http://127.0.0.1:3000`、`MAAS_LLM_MODEL=deepseek-v3.2`
- 不打印 API key。

测试方式：

- 使用 open-loop / waves 模式。
- 每隔固定时间发一批新请求，不等待上一批完成。
- 这样可以模拟“MQ 继续拉新任务并持续把请求打到 LLM”的场景。
- 关键指标包括 `ok`、`failed`、`429`、`500`、`ConnectError`、`p95_ms`、`max_inflight`、`drain_s`。

三种请求量级：

```text
small:
  短 prompt，短输出，类似健康检查或极短 JSON 生成。

memory:
  中等 prompt 和中等输出，模拟较轻的 memory 类结构化生成。

heavy:
  长 prompt + 长输出，接近真实 consolidation。
  输出长度约 2.5k-3k 字符，和真实日志中的 consolidation 响应长度相近。
```

#### RPM 和 TPM 的关系

RPM 只约束“每分钟请求数”，TPM 约束“每分钟输入 + 输出 token 数”。对 LLM 生成任务来说，TPM 往往比 RPM 更关键。

简单估算：

```text
可持续请求数 ≈ TPM / 单请求平均 token 数
```

例如：

```text
small 请求:
  每次只输出很短 JSON。
  单请求 token 很少。
  因此可以接近 RPM 上限。

heavy consolidation:
  prompt 包含历史记忆 + 候选记忆。
  输出包含多条 ADD/UPDATE/SKIP 操作。
  单请求 token 远高于 small。
  因此即使 RPM 还有余量，也可能先碰到 TPM 或生成吞吐瓶颈。
```

这能解释测试结果：

```text
small 12/s * 3min:
  actual_rpm=709.6
  0 失败

heavy 4/s * 3min:
  actual_rpm=177.6
  0 失败
  p95 接近 59s

heavy 5/s * 3min:
  actual_rpm=218.8
  出现 8 个 429
```

也就是说，`small` 在看 RPM，`heavy` 更接近在看 TPM / token 生成吞吐。

#### 测试结论总表

| 场景 | 稳定档位 | 上探档位 | 结论 |
|---|---:|---:|---|
| `small` 短输出 | `12/s`，0 失败 | `15/s`，1 个 500/2700 | 短请求可以接近或超过 700 RPM，12/s 是保守值，15/s 是激进值 |
| `memory` 中等输出 | `10/s`，0 失败 | `15/s`，571 个 429/2700 | 中等输出稳定上限约 10/s |
| `heavy` 长输出 | `4/s`，0 失败 | `5/s`，8 个 429/900；10/s 大量 429 | 接近真实 consolidation 的稳定上限约 4/s |

#### small：短输出能力

`12/s * 3min`：

```text
total=2160
ok=2160
failed=0
actual_rpm=709.6
p50=2011ms
p90=3659ms
p95=4137ms
max=8874ms
max_inflight=44
drain=3.6s
```

`15/s * 3min`：

```text
total=2700
ok=2699
failed=1
errors={'500': 1}
actual_rpm=878.9
p50=2191ms
p90=3617ms
p95=3969ms
max=8583ms
max_inflight=55
drain=5.3s
```

结论：

- 短请求可以接近甚至超过 700 RPM。
- 控制台 RPM 对短输出请求有参考意义。
- 但这个结果不能外推到 memory consolidation，因为 consolidation 的输出长度和生成时间完全不同。

#### memory：中等输出能力

`5/s * 3min`：

```text
total=900
ok=900
failed=0
actual_rpm=284.1
p50=8509ms
p90=10801ms
p95=12055ms
max=17084ms
max_inflight=54
drain=11.1s
```

`10/s * 3min`：

```text
total=1800
ok=1800
failed=0
actual_rpm=557.0
p50=8629ms
p90=11264ms
p95=12375ms
max=18605ms
max_inflight=104
drain=14.9s
```

`15/s * 3min`：

```text
total=2700
ok=2129
failed=571
errors={'429': 571}
actual_rpm=681.1
p50=8472ms
p90=10631ms
p95=11712ms
max=17779ms
max_inflight=145
drain=8.5s
```

结论：

- 中等输出在 `10/s` 时稳定。
- `15/s` 会触发大量 429。
- 中等输出推荐稳定发起速率不超过 `10/s`。

#### heavy：接近真实 consolidation 的能力

`4/s * 3min`：

```text
total=720
ok=720
failed=0
actual_rpm=177.6
p50=39758ms
p90=55430ms
p95=59219ms
max=77799ms
max_inflight=171
drain=64.2s
```

`5/s * 3min`：

```text
total=900
ok=892
failed=8
errors={'429': 8}
actual_rpm=218.8
p50=41281ms
p90=56823ms
p95=62111ms
max=80724ms
max_inflight=213
drain=65.6s
```

`10/s * 3min`：

```text
total=1800
ok=1183
failed=617
errors={'429': 617}
actual_rpm=283.9
p50=40818ms
p90=55801ms
p95=61939ms
max=85653ms
max_inflight=418
drain=71.0s
```

`20/s * 3min`：

```text
total=3600
ok=1605
failed=1995
errors={'429': 1992, '500': 3}
actual_rpm=380.6
p50=42160ms
p90=58283ms
p95=65057ms
max=90166ms
max_inflight=808
drain=74.0s
```

`30/s * 60s`：

```text
total=1800
ok=1200
failed=600
errors={'429': 484, 'ConnectError': 116}
actual_rpm=608.7
p50=42123ms
p90=57346ms
p95=62867ms
max=83654ms
max_inflight=1041
drain=59.3s
```

`50/s * 30s`：

```text
total=1500
ok=1085
failed=415
errors={'429': 75, 'ConnectError': 340}
actual_rpm=637.5
p50=41092ms
p90=55060ms
p95=60161ms
max=99101ms
max_inflight=1065
drain=73.1s
```

结论：

- heavy 场景下，短时 burst 可以接住很多请求，但持续运行会出现 429。
- `20/s * 30s` 曾经 0 失败，但 `20/s * 3min` 失败率达到 55.4%，说明不能用短时 burst 判断稳定能力。
- `5/s * 3min` 只有 8 个 429，已经接近上限。
- `4/s * 3min` 0 失败，是当前最可靠的 heavy 稳定档位。
- heavy 的真实限制不是请求 RPM，而是 TPM 和长输出 token 生成吞吐。

#### 对 memory-core 的配置建议

真实 memory extraction 的瓶颈主要是 consolidation LLM，应按 `heavy` 场景配置，而不是按 `small` 场景或控制台 `700 RPM` 配置。原因是 consolidation 消耗的是大量 input/output token，真正受 TPM 和生成吞吐约束。

推荐值：

```text
MAAS_LLM_GLOBAL_RATE_PER_SECOND=4
MAAS_LLM_MAX_INFLIGHT=160-180
```

解释：

- `4/s` 是 heavy 场景 3 分钟 0 失败档位。
- heavy p95 约 59s，按 Little's Law 粗算：`4 req/s * 59s ≈ 236 inflight`。但实际测试中 `max_inflight=171`，说明请求完成分布不是全在 p95。
- 初始 `MAX_INFLIGHT` 可设 `160-180`，再根据真实 benchmark 调整。
- 如果允许极少量 429 并做退避重试，可以把速率上调到 `5/s`，但不建议作为默认值。

实现建议：

- 优先做全局 Redis token bucket，而不是只做进程内 semaphore。
- 当前 `uvicorn workers=2`，进程内 limiter 会被 worker 数放大。
- Redis key 可以按模型维度隔离，例如 `llm:rate:deepseek-v3.2`。
- 请求失败如果是 429，必须指数退避，避免 retry storm。
- MQ consumer 并发可以略高于 LLM rate，但不要让任务无限堆进 LLM pending。

运行配置建议：

```text
ROCKETMQ_CONSUMPTION_THREAD_COUNT=8
ROCKETMQ_MAX_CACHE_MESSAGE_COUNT=16
uvicorn workers=2

LLM 全局发起速率：4/s
LLM 全局 inflight：160-180
```

如果只加 semaphore、不加 rate limit：

- 仍可能在短时间内集中发起太多请求。
- 对 heavy 场景，单纯控制 inflight 不足以避免 429。
- rate limit 负责控制持续注入速度，inflight limit 负责控制极端排队深度，两者最好同时存在。

### 2026-06-30：core 统一大模型调用调度方案

目标：core 中所有大模型调用，包括 LLM、embedding、rerank，都走同一个调度层。调度层负责充分利用 MaaS 能力，同时避免把 MQ backlog 直接转移成 MaaS/One-API 排队和 429。

#### 核心判断

经过容量测试，MaaS 的能力不是一个简单的全局 RPM 数字：

- `small` 短输出可以稳定接近 700 RPM，甚至短时超过 800 RPM。
- `memory` 中等输出稳定约 `10/s`。
- `heavy` 长输出，也就是接近 consolidation 的真实场景，稳定约 `4/s`。

所以最合适的方案不是给所有请求一个统一并发值，而是：

```text
统一入口 + 分类型限流 + 全局共享状态 + 自适应退避
```

#### 设计总览

新增一个应用层组件：`AIRequestScheduler`。

所有 MaaS 调用必须经过它：

```text
LLMService.generate()
  -> AIRequestScheduler.execute(kind=llm, profile=small|memory|heavy, model=deepseek-v3.2, ...)
  -> MaaSLLMAdapter.generate()

EmbeddingService.embed_batch()
  -> AIRequestScheduler.execute(kind=embedding, profile=batch, model=bge-m3, ...)
  -> MaaSEmbeddingAdapter.embed_batch()

RerankService.rerank()
  -> AIRequestScheduler.execute(kind=rerank, profile=default, model=bge-reranker-v2-m3, ...)
  -> MaaSRerankAdapter.rerank()
```

调度层负责：

- 全局 rate limit：控制持续发起速率，防止 429。
- 全局 token budget：按 profile 估算 input/output token，避免打满 TPM。
- 全局 inflight limit：控制排队深度，防止请求在 One-API/MaaS 侧堆太深。
- 请求 profile 分类：不同 prompt/output 量级用不同预算。
- 优先级：用户在线查询优先于后台 memory extract。
- 失败退避：429/5xx/ConnectError 触发模型级降速。
- 指标：记录每类请求的 QPS、latency、429、timeout、inflight、wait time。

#### 为什么不能只用 semaphore

semaphore 只能限制同时进行中的请求数量，但不能控制持续注入速率。

例子：

```text
heavy 请求 p95 约 60s
如果只设 max_inflight=200
短时间内仍可能一下子发出 200 个请求
```

这会造成：

- 前 200 个请求进入 MaaS/One-API 排队。
- 后续任务在 core 内等待 semaphore。
- 如果 MaaS 有 RPM 或动态限速，仍然可能 429。

所以需要两个阀门：

```text
rate limit:
  控制每秒可以新发起多少请求。

inflight limit:
  控制已经发出去但未返回的请求总数。
```

#### 为什么必须是全局 Redis 限流

当前部署是：

```text
uvicorn workers=2
每个 worker 内都有 MQ consumer / service 实例
```

如果只在进程内做 limiter：

```text
每进程 LLM rate=4/s
workers=2
实际全局可能变成 8/s
```

这会直接超过 heavy 稳定档位。

因此 rate 和 inflight 必须放在 Redis：

```text
ai:limit:{kind}:{model}:{profile}:tokens
ai:tpm:{kind}:{model}:{profile}:tokens
ai:inflight:{kind}:{model}:{profile}
ai:cooldown:{kind}:{model}:{profile}
```

Redis 是现有依赖，core 已经有 Redis adapter 和 idempotency 使用经验，适合做跨 worker 协调。

#### 请求 profile 分类

LLM 请求需要按量级分类。不要只按 API 路径或模型限流。

建议初始 profile：

| profile | 典型场景 | 初始 rate | 初始 inflight | 说明 |
|---|---|---:|---:|---|
| `llm.small` | 短 JSON、短回答、健康类测试 | `12/s` | `50` | 接近 700 RPM，p95 约 4s |
| `llm.memory` | 中等 memory 结构化生成 | `10/s` | `100` | p95 约 12s，0 失败 |
| `llm.heavy` | consolidation 长 prompt/长输出 | `4/s` | `160-180` | p95 约 59s，0 失败 |
| `embedding.single` | 单条 query embedding | 待测，先 `20/s` | `80` | 延迟较低，但线上有尾延迟 |
| `embedding.batch` | 写入阶段 batch embedding | 待测，先 `10/s` | `60` | batch size 不同，成本不同 |
| `rerank.default` | search rerank | 待测，先 `10/s` | `40` | 用户在线请求，优先级高 |

后续应给每个 LLM profile 增加 token 预算：

| profile | token 预算策略 |
|---|---|
| `llm.small` | 低估算成本，主要受 RPM 控制 |
| `llm.memory` | 中等估算成本，同时看 RPM 和 TPM |
| `llm.heavy` | 高估算成本，主要受 TPM / 生成吞吐控制 |

第一版可以先按 profile 配固定估算 token：

```text
llm.small estimated_tokens=300
llm.memory estimated_tokens=1500
llm.heavy estimated_tokens=4000
```

更准确的版本再接 tokenizer 或使用响应 usage 字段回填真实 token 消耗。

profile 判断规则：

```text
consolidation_llm:
  默认 llm.heavy

extraction_llm:
  message_count <= 10 且 serialized_length 较短 -> llm.memory
  否则 -> llm.heavy

短 schema / 小工具类请求:
  llm.small

embedding:
  按 input 数量和字符长度分 single / batch

rerank:
  按 candidates 数量分 default / large，初期可统一 default
```

为了避免实现过度复杂，第一版可以只做：

```text
LLM:
  extraction -> llm.memory
  consolidation -> llm.heavy

Embedding:
  query embedding -> embedding.single
  operation embedding -> embedding.batch

Rerank:
  rerank -> rerank.default
```

#### 调度流程

一次模型调用的流程：

```text
1. 业务层构造请求上下文
   kind=llm
   model=deepseek-v3.2
   profile=llm.heavy
   priority=background
   timeout=180s

2. scheduler 检查 cooldown
   如果该 profile 正在 cooldown，则等待或快速失败。

3. scheduler 获取 rate token
   使用 Redis token bucket。
   没有 token 时，短等待，不忙等。

4. scheduler 获取 token budget
   根据 profile 的 estimated_tokens 从 Redis TPM bucket 扣除。
   如果 token 预算不足，则等待。

5. scheduler 获取 inflight slot
   使用 Redis 计数器 + TTL。
   超过 inflight 上限时等待。

6. 执行真实 MaaS 请求
   调用 adapter。

7. 记录结果
   success / 429 / 5xx / timeout / connect_error
   duration_ms
   queue_wait_ms
   inflight_before/after
   estimated_tokens / actual_tokens

8. 释放 inflight slot
   finally 中执行，避免泄漏。

9. 如果失败是 429/连接错误
   更新短期错误率，必要时触发 profile 降速。
```

#### 自适应退避

静态限流只能解决大部分问题，但 MaaS 实际能力会随时间、渠道和其他服务共享负载变化。

建议增加轻量自适应机制：

```text
每个 profile 维护最近 60s 指标：
  request_count
  success_count
  429_count
  timeout_count
  connect_error_count
  p95_duration_ms
```

降速规则：

```text
如果 429_rate > 1%:
  rate = max(rate * 0.8, min_rate)
  cooldown 30s

如果 timeout/connect_error 连续出现:
  rate = max(rate * 0.7, min_rate)
  cooldown 60s

如果连续 5 分钟 0 失败，且 p95 稳定:
  rate = min(rate * 1.05, configured_max_rate)
```

第一版不需要自动升速，可以只实现自动降速和人工配置上限。

#### 优先级和公平性

core 里模型调用至少有两类：

```text
online:
  用户 search/rerank 或直接 API 请求。

background:
  MQ memory extract / consolidation。
```

如果不区分优先级，后台 extract 很容易把 MaaS 吃满，影响在线查询。

建议：

```text
online priority:
  使用独立 token bucket 或预留 20%-30% 预算。

background priority:
  使用剩余预算。
```

例子：

```text
llm.heavy global rate = 4/s
online reserve = 1/s
background max = 3/s
```

如果当前没有在线 LLM 请求，background 可以临时借用 online 预算；但一旦在线请求出现，应优先放行 online。

#### 和 MQ consumer 的关系

MQ consumer 并发不要再被当成模型并发来调。

建议职责拆分：

```text
MQ consumer:
  负责从 MQ 取任务、做本地查询、进入 pipeline。

AIRequestScheduler:
  负责控制真正打到 MaaS 的速度和深度。
```

当前配置可以保持：

```text
ROCKETMQ_CONSUMPTION_THREAD_COUNT=8
ROCKETMQ_MAX_CACHE_MESSAGE_COUNT=16
uvicorn workers=2
```

但模型调用实际由 scheduler 控制：

```text
llm.heavy rate=4/s
llm.heavy inflight=160-180
```

这样做的效果：

- MQ 不会因为本地 CPU 空闲就无限把任务推进 MaaS。
- MaaS 被稳定吃满，但不进入大规模 429。
- 如果 MQ backlog 高，任务会在 core 的 scheduler 前排队，而不是在 MaaS/One-API 内部排队。
- core 可以观察 pending/wait time，从而知道瓶颈在哪里。

#### 是否需要“释放 MQ 槽位”的状态机

长期看，需要；短期可以先不上。

第一阶段：

```text
同步 pipeline + AIRequestScheduler
```

优点：

- 改动小。
- 可以快速阻止 429 和模型侧深队列。
- 保留现有 TaskHandler / Pipeline 结构。

缺点：

- MQ task 等待 LLM token 时仍占用处理槽。
- MQ 消费吞吐受 consumer 并发影响。

第二阶段：

```text
consolidation_llm pending/resume
```

只拆最大瓶颈：

```text
extraction 完成
  -> query_embedding/vector_search 完成
  -> 进入 waiting_consolidation_llm
  -> 释放 MQ 处理槽
  -> scheduler 获得预算后发起 LLM
  -> LLM 返回后恢复 parse/write
```

第三阶段：

```text
完整 workflow 化
```

每个外部模型调用前后都落状态，支持进程重启恢复、超时扫描、幂等续跑。

#### 第一版落地范围

建议第一版只做这些：

1. 新增 `AIRequestScheduler`。
2. 新增 Redis token bucket + Redis inflight counter。
3. LLMService 接入 scheduler。
4. EmbeddingService 接入 scheduler。
5. RerankService 接入 scheduler。
6. 配置化 profile：

```text
AI_LLM_SMALL_RATE_PER_SECOND=12
AI_LLM_SMALL_MAX_INFLIGHT=50

AI_LLM_MEMORY_RATE_PER_SECOND=10
AI_LLM_MEMORY_MAX_INFLIGHT=100

AI_LLM_HEAVY_RATE_PER_SECOND=4
AI_LLM_HEAVY_MAX_INFLIGHT=180
AI_LLM_HEAVY_ESTIMATED_TOKENS=4000
AI_LLM_HEAVY_TPM=450000

AI_EMBEDDING_SINGLE_RATE_PER_SECOND=20
AI_EMBEDDING_SINGLE_MAX_INFLIGHT=80

AI_EMBEDDING_BATCH_RATE_PER_SECOND=10
AI_EMBEDDING_BATCH_MAX_INFLIGHT=60

AI_RERANK_RATE_PER_SECOND=10
AI_RERANK_MAX_INFLIGHT=40
```

7. 日志新增：

```text
ai_scheduler_wait_completed
ai_scheduler_request_completed
ai_scheduler_request_failed
ai_scheduler_rate_limited
ai_scheduler_cooldown_started
```

字段：

```text
kind
model
profile
priority
wait_ms
duration_ms
inflight
rate_per_second
status
error_type
```

#### 对当前 memory extract 的直接建议

当前真实瓶颈是 `consolidation_llm`，所以优先做：

```text
consolidation_llm -> llm.heavy -> 4/s global
extraction_llm -> llm.memory -> 10/s global
query_embedding -> embedding.single
operation_embedding_batch -> embedding.batch
```

这样可以最大限度利用 MaaS：

- extraction 不被 heavy consolidation 完全拖住。
- consolidation 稳定吃满 heavy 可持续能力。
- embedding 和 rerank 不和 LLM 共用同一个粗暴 limiter。
- 429 出现时只降低对应 profile，不影响其他 profile。

最重要的是：调度层要让排队发生在 core 内部，而不是 MaaS/One-API 内部。core 内部排队可观测、可取消、可恢复、可做优先级；MaaS 内部排队不可控，只会表现为 p95/p99 拉长和 429。

#### 例子 1：为什么不能按 700 RPM 配 consolidation

控制台显示 `deepseek-v3.2 RPM=700`，直觉上可能会想：

```text
700 RPM ≈ 11.6 req/s
那 memory extract 的 LLM 就配置 10/s 或 12/s
```

但测试结果说明这只适合短输出：

```text
small 12/s * 3min:
  2160/2160 成功
  p95 约 4s

heavy 10/s * 3min:
  1183/1800 成功
  617 个 429
  p95 约 62s
```

同样是 deepseek-v3.2，短请求 12/s 很稳，heavy 请求 10/s 会大量 429。原因是 heavy consolidation 不只是“一个请求”，而是一个长 prompt + 长输出的生成任务，真正消耗的是模型的 token 生成能力。

所以 consolidation 不能按 `700 RPM` 配，应该按 heavy 测出来的稳定档位配：

```text
consolidation_llm -> llm.heavy -> 4/s
```

#### 例子 2：一个 memory extract 任务如何进入调度层

假设一个 MQ task 进入 extract pipeline：

```text
message_count=24
serialized_length=6200
historical_count=20
预计会产生较长 consolidation 输出
```

调度层分类：

```text
extraction_llm:
  profile=llm.memory
  rate=10/s
  max_inflight=100

consolidation_llm:
  profile=llm.heavy
  rate=4/s
  max_inflight=180

query_embedding:
  profile=embedding.single

operation_embedding_batch:
  profile=embedding.batch
```

执行时间线示例：

```text
T+0ms:
  TaskHandler 收到 MQ message，查询 strategy/session/messages。

T+5ms:
  extraction_llm 准备发起。
  scheduler 申请 llm.memory token。

T+10ms:
  llm.memory 当前未满，放行。
  请求发到 MaaS。

T+9000ms:
  extraction LLM 返回。

T+9010ms:
  query_embedding 申请 embedding.single token。
  embedding 返回后进行 vector_search。

T+9500ms:
  consolidation_llm 准备发起。
  scheduler 申请 llm.heavy token。

T+9500ms - T+12000ms:
  如果当前 heavy 已达到 4/s 或 inflight 达上限，任务在 core 内等待。
  这段等待会记录为 ai_scheduler_wait_completed。

T+12000ms:
  llm.heavy 放行，请求发到 MaaS。

T+52000ms:
  consolidation LLM 返回。

T+53000ms:
  operation_embedding_batch 申请 embedding.batch token。
  batch embedding + bulk write 完成。
```

这个例子的重点：等待发生在 core scheduler 前，而不是把请求直接打进 MaaS 内部排队。core 能知道“这个任务等 heavy token 等了多久”，也能给在线请求插队。

#### 例子 3：为什么 rate limit 和 inflight limit 都需要

只做 rate limit 的问题：

```text
llm.heavy rate=4/s
但是 MaaS 某一段时间变慢，单请求从 40s 变成 120s
```

如果一直按 4/s 发：

```text
120s * 4/s = 480 个 inflight
```

这会导致请求越积越多，内存、连接、超时风险都上升。

只做 inflight limit 的问题：

```text
llm.heavy max_inflight=180
没有 rate limit
```

当 MQ backlog 很高时，core 可能瞬间放出 180 个 heavy 请求。MaaS 会接住一部分，其余排队，仍可能触发 429。

所以要两个阀门同时存在：

```text
rate=4/s:
  控制持续注入速度。

max_inflight=180:
  控制极端排队深度。
```

实际行为：

```text
如果当前 inflight=80:
  按 4/s 持续发。

如果 MaaS 变慢导致 inflight=180:
  暂停新 heavy 请求，等待已有请求返回。

如果出现 429:
  profile=llm.heavy 进入 cooldown，临时降到 3.2/s 或更低。
```

#### 例子 4：为什么要全局 Redis，而不是每个 worker 一个 limiter

当前部署：

```text
uvicorn workers=2
每个 worker 都可能处理 MQ task
```

如果每个 worker 自己配置：

```text
llm.heavy rate=4/s
```

实际全局就是：

```text
worker1: 4/s
worker2: 4/s
total: 8/s
```

而 heavy 8/s 持续 3 分钟没有通过测试，10/s 已经出现大量 429。

用 Redis token bucket 后：

```text
worker1 和 worker2 都抢同一个 key:
  ai:limit:llm:deepseek-v3.2:heavy

全局总发起速率仍然是 4/s。
```

这样 worker 数增加不会偷偷放大 MaaS 压力。

#### 例子 5：online 请求如何避免被后台 extract 饿死

假设后台 MQ 正在大量跑 memory extract：

```text
background consolidation_llm 持续占用 llm.heavy 4/s
```

这时用户发起一个在线 search，需要 rerank 或 LLM：

```text
online rerank:
  profile=rerank.default
  priority=online
```

调度策略：

```text
rerank.default 有独立预算，不和 llm.heavy 抢。
online priority 有预留 token。
```

如果未来 online 也需要 LLM：

```text
llm.heavy global rate=4/s
online reserve=1/s
background max=3/s
```

效果：

- 后台 extract 不能把所有 token 吃光。
- 用户请求不用排在几百个后台 consolidation 后面。
- 如果没有在线请求，background 可以临时借用 online 预算，提高利用率。

#### 例子 6：429 出现时怎么处理

假设 `llm.heavy` 当前配置：

```text
rate=4/s
max_inflight=180
```

某个时间段 MaaS 负载升高，最近 60 秒出现：

```text
request_count=240
429_count=6
429_rate=2.5%
```

调度层动作：

```text
1. 记录 ai_scheduler_cooldown_started
2. llm.heavy rate 临时降速:
   4/s * 0.8 = 3.2/s
3. cooldown 30s
4. 30s 后如果 429 消失，保持 3.2/s 一段时间
5. 连续 5 分钟稳定后，再人工或自动慢慢升回 4/s
```

这样 retry 不会在高压期继续放大流量。

没有这个机制时，常见情况是：

```text
MaaS 返回 429
业务层 retry
retry 又立刻打到 MaaS
更多 429
MQ task retry
队列压力继续放大
```

这就是 retry storm。

#### 例子 7：对当前配置的实际效果

当前保守运行配置：

```text
uvicorn workers=2
ROCKETMQ_CONSUMPTION_THREAD_COUNT=8
理论 MQ task 并发约 16
```

没有 scheduler 时：

```text
最多约 16 个任务同时走到 LLM。
如果后续为了清 MQ backlog 把 MQ threads 调到 32 或 workers 调到 4，
LLM 压力会被直接放大。
```

有 scheduler 后：

```text
MQ 并发可以服务于本地查询、parse、write。
LLM 发起速率由 profile 控制：
  extraction -> 10/s
  consolidation -> 4/s
```

如果 MQ backlog 高：

```text
任务会在 core 内等待 llm.heavy token。
日志可以看到 wait_ms 增加。
MaaS 侧不会被直接打出大量 429。
```

这时扩 MQ consumer 的意义变成：

```text
提高本地预处理和非模型阶段吞吐。
```

而不是：

```text
无限增加 LLM 并发。
```

#### 落地版说明：第一版到底怎么做

这版不用先做完整状态机，也不用把 pipeline 拆得很复杂。先在 core 里加一个统一的模型调用入口，让所有 MaaS 请求都从这里过。

现在的调用关系可以理解成：

```text
ExtractionStep -> LLMService -> MaaS
ConsolidationStep -> LLMService -> MaaS
EmbeddingService -> MaaS
RerankService -> MaaS
```

第一版改成：

```text
ExtractionStep -> LLMService -> ModelGate -> MaaS
ConsolidationStep -> LLMService -> ModelGate -> MaaS
EmbeddingService -> ModelGate -> MaaS
RerankService -> ModelGate -> MaaS
```

`ModelGate` 只做三件事：

1. 判断这次请求属于哪种量级。
2. 判断现在能不能发。
3. 发完后记录成功、失败和耗时。

量级先不要设计太复杂，第一版只分这几类：

```text
llm.memory:
  extraction LLM 用这个。
  每秒最多 10 个。

llm.heavy:
  consolidation LLM 用这个。
  每秒最多 4 个。

embedding.single:
  query embedding 用这个。

embedding.batch:
  写入前的 batch embedding 用这个。

rerank.default:
  rerank 用这个。
```

请求进来后的流程：

```text
1. consolidation 要调用 LLM。
2. LLMService 标记这次请求是 llm.heavy。
3. ModelGate 去 Redis 看 llm.heavy 这一秒还有没有名额。
4. 再看当前 llm.heavy 还没返回的请求数有没有超过上限。
5. 如果都没超过，就发给 MaaS。
6. 如果超过，就在 core 内等一小会儿再检查。
7. MaaS 返回后，ModelGate 释放 inflight 计数，并记录日志。
```

这里最重要的是第 6 步：等待发生在 core 里，不是先把请求发给 MaaS 再让 MaaS 排队。

第一版需要加的配置可以先是固定值：

```text
AI_LLM_MEMORY_RATE_PER_SECOND=10
AI_LLM_MEMORY_MAX_INFLIGHT=100

AI_LLM_HEAVY_RATE_PER_SECOND=4
AI_LLM_HEAVY_MAX_INFLIGHT=180

AI_EMBEDDING_SINGLE_RATE_PER_SECOND=20
AI_EMBEDDING_SINGLE_MAX_INFLIGHT=80

AI_EMBEDDING_BATCH_RATE_PER_SECOND=10
AI_EMBEDDING_BATCH_MAX_INFLIGHT=60

AI_RERANK_RATE_PER_SECOND=10
AI_RERANK_MAX_INFLIGHT=40
```

TPM 也要留接口，但第一版可以先用估算值：

```text
llm.memory 每次按 1500 tokens 估算
llm.heavy 每次按 4000 tokens 估算
```

后面如果 MaaS 返回 `usage` 字段，再用真实 token 消耗修正估算。

第一版日志必须打清楚，否则后续不好调：

```text
model_gate_request_waited
  profile=llm.heavy
  wait_ms=2300
  inflight=168

model_gate_request_completed
  profile=llm.heavy
  duration_ms=42160
  status=success

model_gate_request_failed
  profile=llm.heavy
  status=429
  duration_ms=800
```

上线后主要看这几个数：

```text
llm.heavy 是否还有 429
llm.heavy wait_ms 是否越来越大
consolidation_llm p95 是否下降或稳定
MQ backlog 是否可控
task.completed p95 是否恶化
```

如果 `llm.heavy` 还有 429：

```text
4/s -> 3/s
```

如果长时间没有 429，且 `wait_ms` 很高：

```text
4/s -> 5/s 小步试
```

如果 `wait_ms` 高但 MaaS 已经接近 429：

```text
不要继续加速。
说明瓶颈就是 MaaS heavy 能力。
应该优化 consolidation prompt 或减少 consolidation 次数。
```

这就是第一版最实用的落地方式：先把所有模型请求统一管起来，分量级限速，日志可观测。等这层稳定后，再考虑 pending/resume 释放 MQ 槽位。

#### 换模型时如何快速兼容

这个方案必须按 `model + profile` 管配置，不能只按 profile 写死。

错误做法：

```text
llm.heavy = 4/s
```

这样一旦换模型，`4/s` 可能完全不适合。新模型可能更快，也可能更慢，TPM/RPM 也可能不同。

正确做法：

```text
deepseek-v3.2 + llm.heavy = 4/s
deepseek-v3.2 + llm.memory = 10/s

new-model + llm.heavy = 新模型自己的测试值
new-model + llm.memory = 新模型自己的测试值
```

配置结构建议：

```yaml
ai_limits:
  default:
    llm:
      memory:
        rate_per_second: 2
        max_inflight: 30
        estimated_tokens: 1500
      heavy:
        rate_per_second: 1
        max_inflight: 30
        estimated_tokens: 4000

  deepseek-v3.2:
    llm:
      memory:
        rate_per_second: 10
        max_inflight: 100
        estimated_tokens: 1500
      heavy:
        rate_per_second: 4
        max_inflight: 180
        estimated_tokens: 4000
```

查配置的规则：

```text
1. 先按真实 model name 找配置。
2. 找不到，就走 default。
3. default 必须保守，防止新模型一上线就被打爆。
```

换模型流程：

```text
1. 新模型先走 default 保守配置。

2. 用 scripts/maas_llm_capacity.py 跑三类 profile：
   small
   memory
   heavy

3. 找到每类 profile 的 3 分钟稳定档位。

4. 把结果写入 ai_limits.{model_name}。

5. 灰度切一部分流量。

6. 观察：
   429
   p95/p99
   wait_ms
   max_inflight
   task.completed p95

7. 稳定后再扩大流量。
```

如果新模型没有测试数据，第一版默认值建议：

```text
llm.memory:
  rate_per_second=2
  max_inflight=30

llm.heavy:
  rate_per_second=1
  max_inflight=30
```

这样新模型可以先安全跑起来，不会因为沿用 deepseek-v3.2 的配置导致 429 或超时。

`ModelGate` 的 Redis key 也必须包含 model：

```text
ai:limit:llm:{model}:memory
ai:limit:llm:{model}:heavy
ai:inflight:llm:{model}:memory
ai:inflight:llm:{model}:heavy
ai:tpm:llm:{model}:memory
ai:tpm:llm:{model}:heavy
```

这样同一时间支持多个模型也不会互相影响。

例如灰度时：

```text
90% 流量 -> deepseek-v3.2
10% 流量 -> new-model
```

两个模型分别使用自己的限流桶：

```text
deepseek-v3.2: llm.heavy 4/s
new-model: llm.heavy 1/s
```

如果 `new-model` 429 增加，只会降低 `new-model` 的速率，不会影响 deepseek-v3.2。

## 验证指标

每轮改动至少记录：

- MQ backlog / lag
- extract task throughput
- `task.completed` p50/p90/p95/p99
- records_created 分布
- LLM QPS、embedding QPS、错误率、429/timeout 数量
- One-API channel 命中和降级情况
- OpenSearch search/bulk latency
- amembench 质量指标，确认优化没有降低记忆正确性

## 预期收益

保守估计：

- 关闭 DEBUG：降低日志 I/O 和序列化开销，改善 benchmark 干扰。
- 降 MQ 并发 + 应用 limiter：降低 p95/p99 和超时风险，吞吐更稳定。
- batch embedding：对写入多条记忆的任务，模型请求数显著下降，是最确定的性能收益。
- 空 extraction 短路：对约 10%-20% 的零写入任务，节省一次 LLM、一次 embedding、一次 OpenSearch search。

最终目标不是把并发拉满，而是让模型服务、MQ、OpenSearch 之间形成稳定背压：平均吞吐接近下游可持续 QPS，尾延迟不被排队放大。
