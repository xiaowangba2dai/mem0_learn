# Memory-Core / Memory-Access 与 Mem0 记忆策略对比

更新时间：2026-07-01

## 一句话结论

我们当前的 memory-core 更像一个“可配置策略流水线”：按 strategy 把对话切成 semantic、user_preference、summary、episodic 等不同形态，写入前用第二次 LLM 做 consolidation，尽量把新事实和历史记忆合并成更干净的最终记忆。

Mem0 最新开源实现和 README 描述的 2026-04 新算法更像一个“追加式记忆索引”：写入时单次 LLM 抽取事实，默认只 ADD，不再让 LLM 决定 UPDATE/DELETE；靠 hash 去重、实体链接、BM25 关键词、向量相似度和实体 boost 在检索时把正确记忆找出来。

本质区别不是“谁用了 LLM、谁用了向量库”，而是：

- 我们把复杂度主要放在写入阶段，尤其是 consolidation。
- Mem0 把复杂度更多放在检索阶段，写入更轻、更追加、更保留事实痕迹。

## 资料边界

本仓库里有 memory-core 源码，但没有 memory-access Java 源码目录。本文对 memory-access 的判断来自当前启动脚本和已有链路：

```text
memory-access(:8086) -> memory-core(:8000) -> One-API(:3000) -> MaaS
```

也就是说，本文主要比较的是：

- memory-access 作为 API 网关 / 任务入口；
- memory-core 作为记忆抽取、合并、检索核心；
- Mem0 作为外部先进记忆系统的公开实现和公开设计。

参考资料：

- Mem0 GitHub README，2026-04 新算法说明：`https://github.com/mem0ai/mem0`
- Mem0 官方 Quickstart：`https://docs.mem0.ai/platform/quickstart`
- Mem0 论文摘要：`https://arxiv.org/abs/2504.19413`
- Mem0 当前 `mem0/memory/main.py`：`https://raw.githubusercontent.com/mem0ai/mem0/main/mem0/memory/main.py`

## 我们当前的记忆策略

### 总体链路

当前系统是分层的：

```text
客户端 / benchmark
  -> memory-access
  -> memory-core
  -> RocketMQ / REST dispatch
  -> TaskHandler
  -> MemoryExtractionPipeline
  -> OpenSearch / Redis / PostgreSQL / MaaS
```

memory-access 主要承担网关入口角色。memory-core 负责真正的记忆策略：

1. 从 MQ 或 debug dispatch 收到 `memory_extract` 任务。
2. 查 PostgreSQL，拿到 space、session、messages、strategy、steps。
3. 按 strategy 配置执行 pipeline。
4. extraction step 调一次 LLM，从对话里抽候选记忆。
5. 如果 extraction 是空结果，直接短路，不再进入 consolidation。
6. consolidation step 把候选记忆转成 query，做 embedding。
7. 用 query embedding 去 OpenSearch 找历史记忆，默认 `top_k=20`。
8. 把“新候选 + 旧记忆”一起交给第二次 LLM，让它输出 `AddMemory`、`UpdateMemory`、`SkipMemory`。
9. 执行 ADD/UPDATE/SKIP，批量 embedding 后写入 OpenSearch。
10. search 时对 query 做 embedding，向量检索，再可选 rerank。

对应源码：

- `src/core/application/pipelines/memory_extraction.py`
- `src/core/application/pipelines/steps/extraction.py`
- `src/core/application/pipelines/steps/consolidation.py`
- `src/core/application/pipelines/steps/_operation_executor.py`
- `src/core/application/memory_search.py`

### 策略类型

我们是显式 strategy 驱动：

- `semantic`：事实记忆，例如“用户喜欢喝美式咖啡”。
- `user_preference`：偏好记忆，例如“用户希望回答简洁一些”。
- `summary`：摘要记忆，例如一段长期会话的 global summary 和 delta summary。
- `episodic`：事件 / 回合记忆，例如某次对话的 situation、intent、action、assessment。

不同 strategy 有不同 parser：

- semantic / user_preference：JSON。
- summary / episodic：XML。

这说明我们当前设计更偏“业务可配置记忆系统”。记忆不是只有一种通用 fact，而是可以按业务策略拆成多个结构。

### 写入策略

我们的典型写入是两段式：

```text
新消息
  -> extraction LLM
  -> 候选记忆
  -> embedding 候选记忆
  -> vector search 历史记忆
  -> consolidation LLM
  -> ADD / UPDATE / SKIP
  -> batch embedding
  -> OpenSearch bulk write
```

这套设计的目标是：写入时就尽量保持记忆干净，避免同一个事实越存越多。

例子：

```text
第 1 天：
用户：我喜欢喝拿铁。

系统写入：
{"fact": "用户喜欢喝拿铁"}

第 7 天：
用户：我最近不喝拿铁了，改喝美式。
```

我们的 consolidation 会先查到旧记忆：

```text
[ID]=m1
[MEMORY]={"fact":"用户喜欢喝拿铁"}
```

然后让 LLM 判断：

```json
[
  {
    "operation": "UpdateMemory",
    "update_id": "m1",
    "memory": {"fact": "用户喜欢喝拿铁"},
    "updated_memory": {"fact": "用户现在更喜欢喝美式，而不是拿铁"}
  }
]
```

最后 OpenSearch 里倾向于保留一条被更新后的记忆。

这就是“写入时合并”的思路。

### 检索策略

我们的 search 当前比较直接：

```text
query
  -> embedding
  -> OpenSearch KNN
  -> min_score 过滤
  -> 可选 rerank
  -> top_k
```

过滤维度主要是：

- `space_id`
- `strategy_id`
- `strategy_type`
- `actor_id`
- `assistant_id`
- `session_id`
- `isolation_level`
- `memory_type`
- `created_at` 时间范围

例子：

```text
用户问：我喜欢喝什么咖啡？
```

系统会：

1. 对“我喜欢喝什么咖啡？”做 embedding。
2. 在指定 space / actor / session / strategy 范围内做向量检索。
3. 找到类似：

```json
{"fact":"用户现在更喜欢喝美式，而不是拿铁"}
```

4. 如果 rerank 打开，再让 reranker 重排。

当前检索侧主要依赖语义相似度和可选 rerank；没有内置 BM25、实体索引、时间推理排序。

## Mem0 当前公开策略

### 旧论文里的核心

Mem0 论文把它描述为一个可扩展的长期记忆架构，核心能力是从持续对话中动态抽取、合并、检索重要信息。论文还提出 graph memory 变体，用图结构捕获对话元素之间的复杂关系。

这和我们当前 core 的设计方向有相似处：都不是简单 RAG chunk，都强调从对话里抽“可复用记忆”。

但 Mem0 最新 README 和源码已经显示，它在 2026-04 新算法后有一次明显转向。

### 最新 README 声明的变化

Mem0 README 的 “New Memory Algorithm (April 2026)” 写了几个关键变化：

- 单次 ADD-only 抽取：一次 LLM 调用，不做 UPDATE/DELETE。
- agent 生成的事实也作为一等公民保存。
- 实体链接：抽取实体、embedding、跨记忆建立链接。
- 多信号检索：semantic、BM25 keyword、entity matching 并行打分融合。
- 时间推理：面向当前状态、过去事件、未来计划做时间感知排序。

这和我们当前的最大差异是：

```text
我们：写入时让 LLM 负责合并、更新、跳过。
Mem0：写入时尽量追加，检索时用多路信号把最相关的事实找出来。
```

### Mem0 当前开源写入流程

从 Mem0 当前 `main.py` 可以看到，它的 `_add_to_vector_store` 是一个 phased batch pipeline：

```text
Phase 0: 取最近消息上下文
Phase 1: 检索已有 memory，作为抽取参考
Phase 2: 单次 LLM extraction
Phase 3: 批量 embedding 抽取出的 memory texts
Phase 4/5: CPU 处理 + hash 去重
Phase 6: 批量写入 vector store
Phase 7: 批量实体链接
Phase 8: 保存消息历史
```

注意两个点：

1. 它仍会检索 existing memories 给 LLM 看，但不是为了让 LLM 输出 UPDATE/DELETE，而是辅助抽取和减少重复。
2. 它对 memory 文本做 hash 去重。完全相同的记忆不再写，但语义相近、时间不同、主体不同的记忆可以继续追加。

例子：

```text
用户：我下周五要去东京出差。
助手：好的，我会记住。
```

Mem0 可能写入：

```json
{
  "memory": "User has a business trip to Tokyo next Friday.",
  "event": "ADD",
  "metadata": {
    "user_id": "u1",
    "hash": "...",
    "created_at": "..."
  }
}
```

并额外抽实体：

```text
Tokyo -> linked_memory_ids=[m1]
next Friday -> linked_memory_ids=[m1]
business trip -> linked_memory_ids=[m1]
```

### Mem0 当前检索流程

Mem0 search 不是单纯向量检索。源码里 `_search_vector_store` 大致是：

```text
query
  -> lemmatize for BM25
  -> extract query entities
  -> query embedding
  -> semantic vector search，over-fetch
  -> keyword_search / BM25
  -> entity boost
  -> score_and_rank 融合
  -> 可选 rerank
```

也就是说，Mem0 把检索拆成多路证据：

- 语义相似：意思接近。
- 关键词匹配：字面词命中。
- 实体命中：人名、地点、项目、时间等关键实体命中。
- 时间/过期字段：隐藏过期记忆，平台版本还有更强时间能力。
- rerank：可选最后重排。

例子：

```text
记忆 1：User is going to Tokyo for business next Friday.
记忆 2：User likes Japanese food.

用户问：我东京行程是什么时候？
```

纯向量可能觉得两条都和 Japan/Tokyo 有关。

Mem0 多信号会加权：

- `Tokyo` 实体命中记忆 1；
- `行程 / trip` 关键词或语义更靠近记忆 1；
- 时间表达 `next Friday` 也在记忆 1；
- 所以记忆 1 排在更前。

## 本质区别 1：写入时合并 vs 读取时融合

### 我们的路线

我们在写入阶段就尝试整理记忆：

```text
新事实 + 旧事实 -> LLM 判断 -> 更新成一条更准确的记忆
```

优点：

- 记忆库更干净。
- 同类事实不容易膨胀。
- search 返回结果更短、更像“当前结论”。
- 对业务侧更友好，因为每条 memory 更像最终档案。

缺点：

- 写入成本高，每个任务通常至少 2 次 LLM。
- consolidation LLM 是当前真实性能瓶颈。
- LLM 一旦合并错，旧事实可能被覆盖，追溯难度变高。
- 对时间问题不天然友好，因为“旧状态”容易被 UPDATE 掉。

### Mem0 的路线

Mem0 最新策略倾向：

```text
新事实 -> 单次 LLM 抽取 -> ADD -> 检索时多信号排序
```

优点：

- 写入轻，吞吐更容易做高。
- 历史事实不容易被覆盖。
- 适合时间类、多跳类、追溯类问题。
- agent 行为、用户事实、历史事件都能保留。

缺点：

- 记忆数量会增长。
- 检索算法必须更强，否则容易返回重复或过期事实。
- 如果没有时间排序 / 实体链接 / BM25，ADD-only 会变成“记忆堆积”。

### 例子：用户偏好变化

对话：

```text
1 月：用户说“我喜欢吃辣”。
3 月：用户说“最近胃不好，不吃辣了”。
6 月：用户问“点外卖帮我避开什么？”
```

我们当前可能最终只有一条：

```json
{"fact":"用户最近胃不好，不吃辣"}
```

回答 6 月问题很方便。

但如果用户问：

```text
我以前是不是说过喜欢吃辣？
```

如果旧记忆被 UPDATE 覆盖，就可能找不到“以前喜欢吃辣”这个历史状态，除非我们保留 history 或 extraction_meta。

Mem0 ADD-only 可能有两条：

```json
{"memory":"User likes spicy food.","created_at":"2026-01-10"}
{"memory":"User currently avoids spicy food due to stomach issues.","created_at":"2026-03-20"}
```

回答“现在点外卖避开什么”时，需要时间感知排序选择第二条。

回答“以前是不是喜欢吃辣”时，可以找回第一条。

## 本质区别 2：策略类型显式化 vs 通用 fact + metadata

我们有明确 strategy：

```text
semantic / user_preference / summary / episodic
```

每种 strategy 可以有自己的 prompt、parser、isolation_level、model_name。

Mem0 更偏通用 memory item：

```text
memory text + metadata + user_id/agent_id/run_id + entity links
```

### 我们的优势

业务可控性强。

例如同一段对话：

```text
用户：我今天在深圳出差，晚上想吃清淡点。以后回答我尽量短一点。
```

我们可以拆成：

semantic：

```json
{"fact":"用户今天在深圳出差"}
```

user_preference：

```json
{
  "context": "回答风格",
  "preference": "用户希望回答尽量短一点",
  "categories": ["communication_style"]
}
```

episodic：

```xml
<summary>
  <situation>用户在深圳出差，晚上想吃清淡食物</situation>
  <intent>寻找晚餐建议</intent>
  <action>助手应推荐清淡餐厅或菜品</action>
</summary>
```

这种分策略方式适合平台型记忆系统，因为不同业务可以选择不同策略。

### Mem0 的优势

接入简单。

调用方只需要：

```python
client.add(messages, user_id="user123")
client.search("What are my dietary restrictions?", filters={"user_id": "user123"})
```

官方 Quickstart 的例子里，输入：

```text
I'm a vegetarian and allergic to nuts.
```

搜索：

```text
What are my dietary restrictions?
```

返回类似：

```json
{
  "memory": "Allergic to nuts",
  "user_id": "user123",
  "categories": ["health"]
}
```

也就是说，Mem0 更像“默认可用的一套通用记忆层”；我们更像“可按业务配置的记忆策略引擎”。

## 本质区别 3：我们缺实体层，Mem0 有实体链接

我们当前每条 memory 主要是：

```text
content + embedding + strategy/session/actor metadata
```

没有单独的实体索引。

Mem0 会把 memory 中的实体抽出来，维护一个 entity store：

```text
entity: "Tokyo"
linked_memory_ids: [m1, m7, m21]
```

### 为什么实体重要

例子：

```text
记忆 A：用户的女儿叫 Emma，正在申请 Stanford。
记忆 B：用户提到 Emma 喜欢机器人竞赛。
记忆 C：用户下周要去 Palo Alto 陪 Emma 面试。

用户问：Emma 的面试在哪？
```

纯向量检索可能命中 A、B、C 任意一条，因为它们都和 Emma 有关。

实体层可以做：

```text
query entities: Emma
Emma -> linked_memory_ids=[A,B,C]
semantic query: 面试在哪
最终 C 加分最高
```

我们当前如果没有实体 boost，只能靠 query embedding 和 rerank。对于人名、项目名、地点、产品名这类短词，向量相似度经常不稳定。

## 本质区别 4：我们 search 偏语义，Mem0 search 是多信号融合

我们当前 search：

```text
embedding -> vector search -> rerank
```

Mem0 search：

```text
embedding vector search
+ BM25 keyword search
+ entity boost
+ metadata filters
+ optional rerank
```

### 例子：关键词比语义更可靠

记忆：

```text
m1: 用户正在使用项目代号 XJ-91。
m2: 用户喜欢高性能向量检索。
```

查询：

```text
XJ-91 现在进展如何？
```

`XJ-91` 是一个代号，embedding 未必表达得好。

我们的向量检索可能把 m2 排上来，因为“进展、项目、性能、检索”语义有点近。

Mem0 的 BM25 会强力命中 `XJ-91`，让 m1 得分更高。

### 例子：实体比整句相似更可靠

记忆：

```text
m1: 用户的猫叫 Mimi。
m2: 用户同事叫 Mimi Chen。
```

查询：

```text
Mimi Chen 负责什么？
```

实体抽取可以区分 `Mimi` 和 `Mimi Chen`。没有实体层时，短名字容易混。

## 本质区别 5：我们更重视“当前正确”，Mem0 更重视“历史可追溯”

我们的 UPDATE 会让记忆更像当前画像：

```text
用户现在住在杭州。
```

Mem0 ADD-only 会保留状态变化：

```text
2025-12: 用户住在上海。
2026-03: 用户搬到杭州。
```

这影响问题类型。

### 当前状态问题

```text
用户现在住哪？
```

我们的方式容易答，因为只有最新合并结果。

Mem0 需要时间感知排序，否则可能把上海也拿出来。

### 历史追溯问题

```text
用户之前住在哪里？
```

Mem0 更容易答，因为历史事实没被覆盖。

我们的方式如果没有 history 索引，可能答不出来。

## 本质区别 6：我们的性能瓶颈更集中在 consolidation LLM

从之前真实测试看，memory-core 的主要耗时在：

```text
extraction_llm
consolidation_llm
```

尤其 consolidation 是 heavy profile，长 prompt、长输出、历史记忆多，是主要瓶颈。

Mem0 最新 ADD-only 的写入侧只有一次 LLM extraction，后续更多是：

- 批量 embedding；
- hash 去重；
- batch insert；
- entity extraction / embedding / linking。

这意味着：

- 我们写入质量更依赖 LLM 合并能力。
- Mem0 写入吞吐更容易做高。
- 我们如果想追 Mem0 的写入性能，最大的结构性优化不是继续调小函数，而是减少或异步化 consolidation。

## 对比表

| 维度 | 我们当前 memory-core | Mem0 最新公开策略 | 本质影响 |
|---|---|---|---|
| 写入 LLM 次数 | 通常 extraction + consolidation 两次 | 新算法强调 single-pass extraction | Mem0 写入更轻 |
| 写入操作 | ADD / UPDATE / SKIP | README 新算法强调 ADD-only | 我们更干净，Mem0 更可追溯 |
| 历史处理 | 写入时检索历史并交给 LLM 合并 | 写入时检索历史辅助抽取 + hash 去重 | 我们依赖 LLM 判断冲突 |
| 记忆形态 | strategy 驱动，多 parser | 通用 memory text + metadata | 我们业务可控性更强 |
| 检索方式 | vector + optional rerank | semantic + BM25 + entity + optional rerank | Mem0 召回更稳 |
| 实体关系 | 暂无单独实体索引 | entity store + linked_memory_ids | Mem0 更适合人名/地点/项目 |
| 时间能力 | 有 created_at filter，但无时间推理排序 | README 声明 time-aware retrieval | Mem0 更适合过去/现在/未来问题 |
| 性能瓶颈 | consolidation LLM | 检索融合和实体索引成本 | 我们写重，Mem0 读重 |
| 数据增长 | UPDATE 后相对克制 | ADD-only 更容易增长 | Mem0 更需要强检索和清理 |
| 可解释性 | 每条最终记忆较清晰，但历史变化弱 | 多条事实 + score details/实体可解释 | 方向不同 |

## 详细场景对比

### 场景 1：饮食偏好

对话：

```text
用户：我是素食者，对坚果过敏。
助手：好的，我会记住。
```

我们可能写入：

semantic：

```json
{"fact":"用户是素食者"}
{"fact":"用户对坚果过敏"}
```

user_preference：

```json
{
  "context": "饮食",
  "preference": "用户偏好素食，并且需要避免坚果",
  "categories": ["diet", "health"]
}
```

Mem0 可能写入：

```json
{"memory":"User is vegetarian."}
{"memory":"User is allergic to nuts."}
```

用户问：

```text
我有什么饮食限制？
```

我们：

```text
query embedding -> 命中 user_preference / semantic -> rerank
```

Mem0：

```text
semantic 命中 dietary restrictions
BM25 命中 vegetarian / nuts
entity/category 命中 health
融合排序
```

结论：

- 简单事实下两者都能工作。
- 如果 query 用词和 memory 用词差异大，Mem0 的多信号检索更稳。
- 如果业务需要把“偏好”和“事实”分开，我们的 strategy 更清楚。

### 场景 2：偏好变更

对话：

```text
1 月：用户：我喜欢 Python。
4 月：用户：最近项目都用 Go，之后代码示例优先用 Go。
```

我们可能把旧记忆 UPDATE：

```json
{"fact":"用户现在代码示例优先使用 Go；历史上喜欢 Python"}
```

或者：

```json
{"fact":"用户代码示例优先使用 Go"}
```

Mem0 可能 ADD 两条：

```json
{"memory":"User likes Python.","created_at":"2026-01"}
{"memory":"User prefers Go for code examples in current projects.","created_at":"2026-04"}
```

用户问：

```text
以后代码示例用什么语言？
```

我们容易命中新记忆。

用户问：

```text
我以前说过喜欢什么语言？
```

Mem0 更容易命中旧记忆。

结论：

- 如果目标是“用户当前画像”，我们的 consolidation 合适。
- 如果目标是“长期可追溯行为历史”，ADD-only 更安全。

### 场景 3：项目代号

对话：

```text
用户：XJ-91 这个项目先别公开。
```

用户后来问：

```text
XJ-91 有什么注意事项？
```

我们依赖向量：

```text
"XJ-91 有什么注意事项？" -> embedding -> vector search
```

问题是 `XJ-91` 这种代号没有天然语义，embedding 可能不稳定。

Mem0 会多一路 BM25：

```text
keyword_search("xj 91")
```

只要 memory 里有这个字符串，就能强召回。

结论：

对 ID、项目代号、商品名、人名、地名，BM25/keyword 是刚需，不应该只靠 embedding。

### 场景 4：多跳实体问题

记忆：

```text
m1: 用户的女儿叫 Emma。
m2: Emma 正在申请 Stanford。
m3: Stanford 面试安排在下周二。
```

用户问：

```text
我女儿的面试什么时候？
```

我们的单次向量检索可能需要直接命中 m3，但 query 里没有 Stanford。

Mem0 有实体链路后，可以更容易走：

```text
女儿 -> Emma
Emma -> Stanford
Stanford -> 面试时间
```

当然，开源 Mem0 当前不一定真的做完整图推理；但实体链接至少给了检索 boost 的基础。论文里的 graph memory 变体则是进一步把关系图显式化。

结论：

如果要解决多跳问题，我们需要的不只是更大的 top_k，而是实体/关系层。

### 场景 5：助手行为也要记忆

对话：

```text
用户：帮我订明天早上 8 点的会议提醒。
助手：已创建提醒。
```

传统用户记忆可能只记：

```text
用户想要明天早上 8 点会议提醒。
```

Mem0 README 特别提到 agent-generated facts 也是一等公民。也就是说助手确认完成的动作也应该存：

```text
Assistant created a meeting reminder for tomorrow at 8 AM.
```

这很重要，因为用户后面可能问：

```text
你刚才有没有帮我设提醒？
```

我们当前 strategy 主要从消息里抽事实，但是否把 assistant action 当一等记忆，取决于 prompt 和 strategy 配置；系统层没有单独建模。

结论：

如果 memory-access/core 未来要支撑 agent 工具调用，应该显式区分：

- 用户事实；
- 用户偏好；
- 助手承诺；
- 助手已执行动作；
- 外部工具结果。

## 我们不应该盲目照抄 Mem0 的地方

### 1. ADD-only 不是万能

如果没有强检索，ADD-only 很容易变成：

```text
用户喜欢拿铁
用户喜欢美式
用户不喝咖啡
用户又开始喝拿铁
```

最后 search 返回一堆互相冲突的记忆。

Mem0 之所以敢往 ADD-only 走，是因为它同时增强了：

- hash 去重；
- BM25；
- entity boost；
- temporal retrieval；
- over-fetch + score fusion。

如果我们只改成 ADD-only，但 search 仍然只有 vector + rerank，会退化。

### 2. 我们的 strategy 能力有价值

Mem0 的通用 API 很好用，但我们的多 strategy 更适合平台：

- 有些业务要 summary；
- 有些业务要 user preference；
- 有些业务要 episodic trace；
- 有些业务要 actor/session 隔离；
- 有些业务要定制 schema。

这不是 Mem0 可以直接替代的。

### 3. 写入合并仍适合“最终画像”

例如客服系统里的用户画像：

```text
用户当前会员等级：Gold
用户当前收货地址：杭州...
用户当前投诉偏好：电话回访
```

这类数据更像 profile，保留一条当前状态比保留所有历史更方便。

所以更合理的方向不是完全抛弃 consolidation，而是分类型：

```text
profile / preference: 可以 consolidation
event / episode / assistant action: 尽量 ADD-only
project / entity facts: ADD + entity link + temporal rank
```

## 对我们 core 的改造建议

### P0：给 search 增加 BM25 / keyword 召回

这是最直接、最不抽象、收益很高的差异点。

当前：

```text
embedding -> OpenSearch KNN
```

建议：

```text
embedding KNN top 60
keyword / BM25 top 60
合并候选
去重
score fusion
可选 rerank
返回 top_k
```

例子：

```text
query: "XJ-91 怎么处理？"
```

即使向量没召回，BM25 也能把包含 `XJ-91` 的记忆召回。

### P0：给 memory record 加 hash 去重

Mem0 写入时会计算文本 hash，避免完全重复写入。

我们当前 consolidation 能 skip，但仍依赖 LLM 判断。可以在操作执行前增加确定性去重：

```text
content_hash = md5(normalized_content)
```

同一个 strategy / actor / session 范围内，如果 hash 已存在，直接跳过。

这能减少：

- 重复 embedding；
- 重复写 OpenSearch；
- LLM 偶发重复输出带来的污染。

### P1：引入实体索引

新增一个 entity index：

```json
{
  "entity": "Tokyo",
  "entity_type": "location",
  "linked_memory_ids": ["m1", "m7"],
  "space_id": "...",
  "actor_id": "...",
  "session_id": "..."
}
```

写入时：

```text
新 memory -> entity extraction -> entity embedding -> upsert entity links
```

检索时：

```text
query -> entity extraction -> entity search -> linked memory boost
```

先不用做完整 graph memory，只做 entity boost 就足够产生收益。

### P1：按 strategy 决定是否 consolidation

不要所有策略都固定走 heavy consolidation。

建议：

```text
semantic:
  默认 extraction + lightweight dedup
  只有命中高相似冲突时再 consolidation

user_preference:
  可以 consolidation，因为偏好经常需要当前状态

summary:
  保持 consolidation，因为摘要本来就是压缩和合并

episodic:
  ADD-only，事件不应覆盖

assistant_action:
  ADD-only，保留执行轨迹
```

这样能保留我们的业务策略优势，同时吸收 Mem0 的轻写入思路。

### P1：给记忆增加 temporal semantics

现在我们有 `created_at` / `updated_at`，但这不是“事件发生时间”。

建议区分：

- `created_at`：系统写入时间。
- `updated_at`：系统更新时间。
- `event_time`：事实发生时间。
- `valid_from`：事实开始有效时间。
- `valid_until`：事实失效时间。
- `temporal_type`：past / current / future / timeless。

例子：

```json
{
  "content": "用户下周五要去东京出差",
  "created_at": "2026-07-01T10:00:00Z",
  "event_time": "2026-07-10",
  "temporal_type": "future"
}
```

用户问：

```text
我接下来有什么安排？
```

就应该 boost future memory。

用户问：

```text
我上个月去哪出差了？
```

就应该查 past event_time，而不是只看 created_at。

### P2：把 consolidation 从同步主链路拆出来

当前真实瓶颈是 consolidation LLM。可以分两种写入：

```text
fast path:
  extraction -> ADD candidate -> 可立即检索

background path:
  later consolidation -> merge / mark superseded / update profile
```

这样用户侧或 MQ 不必等 heavy LLM 完成才能前进。

例子：

```text
第 0 秒：用户说“我现在改用 Go”
第 5 秒：系统已有 ADD 记忆，可被 search 命中
第 30 秒：后台 consolidation 把旧 Python 偏好标记为 superseded
```

这比“必须等 consolidation 完成才写入”吞吐更好。

### P2：不要物理覆盖旧事实，改成 supersede 链

如果仍然 UPDATE，建议保留关系：

```json
{
  "id": "m2",
  "content": "用户当前偏好 Go 示例",
  "supersedes": ["m1"]
}
```

旧记忆 m1 不一定物理删除，可以：

```json
{
  "id": "m1",
  "content": "用户喜欢 Python",
  "status": "superseded",
  "superseded_by": "m2"
}
```

这样当前问题默认过滤 superseded，历史问题可以查出来。

## 推荐目标架构

不是完全变成 Mem0，而是做成混合策略：

```text
写入：
  message range
    -> extraction LLM
    -> candidate memories
    -> hash dedup
    -> strategy policy:
         ADD-only
         lightweight update
         heavy consolidation
    -> batch embedding
    -> vector write
    -> entity linking
    -> optional async consolidation

检索：
  query
    -> embedding vector search
    -> BM25 keyword search
    -> entity search / boost
    -> temporal boost
    -> score fusion
    -> optional rerank
    -> top_k
```

策略配置可以长这样：

```yaml
semantic:
  write_mode: add_first
  consolidation: on_conflict
  entity_linking: true
  keyword_index: true
  temporal_extraction: true

user_preference:
  write_mode: consolidate_current_profile
  consolidation: always_or_on_conflict
  entity_linking: true
  temporal_extraction: false

episodic:
  write_mode: add_only
  consolidation: never
  entity_linking: true
  temporal_extraction: true

summary:
  write_mode: consolidate_summary
  consolidation: always
  entity_linking: false
  temporal_extraction: false
```

## 最后判断

Mem0 比我们先进的关键不是“它用了更神奇的 LLM prompt”，而是它把记忆系统拆成了更完整的工程闭环：

```text
轻量写入
+ 确定性去重
+ 实体链接
+ 关键词召回
+ 向量召回
+ 时间排序
+ 分数融合
```

我们当前的强项是：

```text
策略可配置
+ 业务 schema 清楚
+ actor/session/space 隔离明确
+ 写入时 consolidation 能产出干净当前画像
```

我们的短板是：

```text
consolidation 太重
检索信号单一
缺实体层
缺时间语义
UPDATE 容易损失历史可追溯性
```

最建议优先做的不是推翻当前 core，而是三步：

1. search 增加 BM25/keyword 召回和 score fusion。
2. 写入增加 hash 去重，并按 strategy 支持 ADD-only / consolidation 两种模式。
3. 增加 entity index，用 entity boost 改善人名、地点、项目、多跳问题。

这三步做完，我们会保留现有平台型 strategy 优势，同时补上 Mem0 最核心的检索增强能力。
