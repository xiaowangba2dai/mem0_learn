# Memory-Core 多信号混合检索方案设计

更新时间：2026-07-01

面向读者：

- 主要读者：负责实现的 AI / 工程代理。
- 次要读者：人类研发、评审、测试、运维。

设计目标：

- 在当前 memory-core 上实现一版参考 Mem0 思路的多信号混合检索。
- 不推翻现有架构，不破坏现有 API 行为。
- 优先保证安全可靠，其次保证可维护、可扩展和性能。

参考资料：

- Mem0 README：`https://github.com/mem0ai/mem0`
- Mem0 README 中 2026-04 新算法说明：single-pass ADD-only extraction、entity linking、multi-signal retrieval、temporal reasoning。
- Mem0 当前源码 `mem0/memory/main.py`：使用 `lemmatize_for_bm25`、`extract_entities`、BM25、entity boost、score fusion、rerank。
- 当前 core 源码：
  - `src/core/application/memory_search.py`
  - `src/core/adapters/css_adapter.py`
  - `src/core/ports/memory_vector_store.py`
  - `src/core/domain/types/vector_operations.py`
  - `src/core/api/router.py`
  - `src/core/api/schemas.py`

## 1. 总览

### 1.1 当前 core 检索方式

当前 search 链路：

```text
POST /v1/core/internal/spaces/{space_id}/memories/search
  -> API request 转 SearchFilters
  -> MemorySearch.search()
  -> EmbeddingService.embed(query)
  -> MemoryVectorStoreService.search()
  -> CSSAdapter.search()
  -> OpenSearch KNN
  -> min_score 过滤
  -> optional RerankService.rerank()
  -> top_k
```

当前优点：

- 链路清晰。
- 多租户隔离明确：OpenSearch 查询都带 `space_id` filter 和 routing。
- 已有 metadata filter：strategy、actor、assistant、session、memory_type、time range。
- 已有 rerank 降级逻辑。

当前短板：

- 召回信号单一，主要依赖 embedding。
- 对项目代号、产品型号、人名、地点、短关键词不稳定。
- 没有 BM25 / keyword 召回。
- 没有实体信号。
- 没有时间/新鲜度排序策略。
- rerank 的候选来自单一路径，候选池本身可能漏掉关键记忆。

### 1.2 目标检索方式

目标 V1 链路：

```text
query
  -> validate / normalize
  -> parallel:
       semantic vector search
       keyword / BM25 search
       entity-lite search
  -> merge candidates by record_id
  -> normalize each signal score
  -> weighted score fusion
  -> metadata / min_score / time policy filtering
  -> optional rerank
  -> stable final ranking
  -> top_k
```

核心思想：

```text
不要只问“这句话语义像不像？”
还要问：
  - 关键词有没有命中？
  - 人名、地名、项目名、代号有没有命中？
  - 这条记忆是不是更近、更适合当前问题？
  - 多路信号综合后谁最可靠？
```

### 1.3 V1 范围

V1 必做：

1. 新增 hybrid search 应用服务编排。
2. 在 vector store port 增加 keyword search / hybrid support 的契约。
3. CSSAdapter 增加 OpenSearch BM25 查询。
4. 增加候选合并、分数归一化、加权融合。
5. 增加 entity-lite：不依赖新索引，先从 query 中提取关键 token / code / proper noun，并用于 keyword boost。
6. 保留现有 API response，不强制外部 API 变更。
7. 增加配置开关，支持一键回退到当前 vector-only。
8. 增加安全、限流、降级、日志和指标设计。

V1 不做：

1. 不引入完整实体图谱。
2. 不改变写入策略。
3. 不改变 memory record 主体模型。
4. 不要求重建 OpenSearch index。
5. 不暴露调试分数给普通外部调用方。

V1.1 / V2 再做：

1. 独立 entity index。
2. 中文 analyzer / ngram 子字段迁移。
3. temporal fields：event_time、valid_from、valid_until、temporal_type。
4. score explain API。
5. query intent classifier：current / past / future / exact_lookup。

## 2. 白盒要求落实

### 2.1 安全可靠

最高优先级。

必须遵守：

- 所有 search 查询必须始终带 `space_id` filter。
- 所有 OpenSearch 查询必须始终带 `routing=str(space_id)`。
- 用户可控字段不能拼接成 raw query string。
- 不使用 OpenSearch `query_string` 处理用户输入。
- keyword search 使用 `match`、`multi_match`、`term`、`bool` 等结构化 DSL。
- query 长度、top_k、候选池大小必须有上限。
- 任何单路召回失败都不能导致整体不可用，除非 semantic vector search 也失败且无可用降级。
- 日志不能打印完整 query、完整 memory content、token、认证信息。
- 多租户数据不能跨 `space_id`、actor、session 泄漏。
- 错误返回不能包含底层连接串、认证信息、OpenSearch 原始异常细节。
- 资源保护必须前置：限制 top_k、expansion_factor、keyword_candidate_limit、entity_candidate_limit。

安全边界：

```text
API 层：
  校验 top_k / query / filters
  JWT filter logic

Application 层：
  控制检索策略
  控制候选池上限
  控制降级

VectorStore Adapter 层：
  强制 space_id filter + routing
  禁止无租户查询
  结构化 OpenSearch DSL
```

### 2.2 代码整洁

要求：

- 不把 hybrid 逻辑塞进 `MemorySearch` 一个大函数。
- 不让 CSSAdapter 了解业务排序策略。
- 不让 API schema 直接依赖 OpenSearch 细节。
- 类型命名必须表达意图。
- 每个模块只做一件事。

建议新增模块：

```text
src/core/application/search/
  __init__.py
  hybrid_search.py
  query_analysis.py
  score_fusion.py
  search_models.py

src/core/domain/types/search.py
  或继续放在 vector_operations.py，但建议拆出 search.py
```

推荐职责：

- `HybridMemorySearch`：编排多路召回。
- `QueryAnalyzer`：query normalize、entity-lite 提取、关键词提取。
- `ScoreFusionService`：归一化、加权融合、排序。
- `MemoryVectorStorePort`：定义存储层 search 契约。
- `CSSAdapter`：只负责把契约翻译为 OpenSearch DSL。

### 2.3 易扩展

V1 要为后续扩展留接口：

```text
RecallSignal:
  semantic
  keyword
  entity
  temporal
  rerank
```

后续新增一种信号，不应该改 API 层，也不应该重写排序主流程，只新增：

- 一个 retriever；
- 一个 score normalizer；
- 一个 weight 配置项。

### 2.4 高性能

要求：

- 多路召回并发执行。
- 每路召回有独立 timeout。
- 候选池上限固定。
- 避免跨 shard 查询：OpenSearch 必须使用 routing。
- 避免拉 embedding 字段：`_source.excludes=["embedding"]`。
- 避免无限 rerank：rerank 只对融合后的 top N 执行。
- 避免重复结果重复 rerank：合并后去重。

V1 默认候选规模建议：

```text
top_k: 用户请求，默认 10，最大 100
semantic_k: max(top_k * 4, 40)，最大 200
keyword_k: max(top_k * 4, 40)，最大 200
entity_k: max(top_k * 2, 20)，最大 100
fusion_candidate_limit: 300
rerank_candidate_limit: min(max(top_k * 3, 20), 80)
```

### 2.5 契约化

对外 API：

- V1 不改变现有 `/memories/search` response。
- 新能力通过配置默认启用或灰度启用。
- 可选增加 internal-only debug 参数，但默认不暴露。

内部契约：

- Port 返回 domain model，不返回 OpenSearch 原始 hit。
- 分数融合输入输出有明确类型。
- 降级行为可预测。

### 2.6 可维护

必须有：

- 分阶段日志。
- 每路召回耗时。
- 每路候选数。
- fusion 后候选数。
- rerank 耗时。
- 降级原因。
- search trace id / request id。

日志不包含完整 query 和完整 memory content，只记录长度、hash、数量、耗时。

推荐日志事件：

```text
hybrid_search_start
hybrid_search_query_analyzed
hybrid_search_recall_completed
hybrid_search_recall_failed
hybrid_search_fusion_completed
hybrid_search_rerank_completed
hybrid_search_completed
hybrid_search_degraded
```

## 3. 设计原则

### 3.1 不破坏现有行为

现有 vector-only 作为 fallback：

```text
SEARCH_HYBRID_ENABLED=false
  -> 走当前 MemorySearch 逻辑
```

如果 hybrid 打开但 keyword/entity 失败：

```text
semantic 成功 -> 返回 semantic + rerank 结果
semantic 失败、keyword 成功 -> 返回 keyword fusion 结果，标记 degraded
semantic 和 keyword 都失败 -> 抛 SearchError
```

### 3.2 安全默认值

默认：

- 不返回 explain。
- 不记录原文。
- 不允许超大 top_k。
- 不允许空 query 走 hybrid；空 query 仍走 list fallback。
- 不允许跨 tenant 查询。

### 3.3 多信号只提升召回，不绕过权限

无论 semantic、keyword、entity 哪一路：

```text
space_id filter 必须有
JWT 推导出的 actor/session/assistant filter 必须有
strategy/memory_type/time filter 必须有
routing 必须有
```

任何一路不得自己构造缺少权限条件的查询。

## 4. 目标架构

### 4.1 模块视图

```text
API Router
  -> MemorySearchFacade
      -> VectorOnlyMemorySearch       # 当前逻辑保留
      -> HybridMemorySearch           # 新逻辑
          -> QueryAnalyzer
          -> EmbeddingService
          -> MemoryVectorStoreService
              -> MemoryVectorStorePort
                  -> CSSAdapter
          -> ScoreFusionService
          -> RerankService
```

可选实现方式：

1. 保持类名 `MemorySearch`，内部按 config 选择 vector-only 或 hybrid。
2. 新增 `HybridMemorySearch`，由 dependencies 按配置注入。

建议采用方式 2，但保留现有 `MemorySearch` 的 public contract。

### 4.2 请求主流程

```text
MemorySearch.search(query, space_id, top_k, min_score, filters)
  1. validate query
  2. if blank -> list fallback
  3. if hybrid disabled -> vector-only
  4. analyze query
  5. concurrently:
       semantic recall
       keyword recall
       entity-lite recall
  6. collect successful recall results
  7. merge by memory_id
  8. normalize scores per signal
  9. calculate fused score
 10. apply min_score policy
 11. optional rerank on top N
 12. return top_k
```

### 4.3 数据流示意

输入：

```json
{
  "query": "XJ-91 这个项目有什么注意事项？",
  "top_k": 10,
  "strategy_type": "semantic",
  "actor_id": "u1"
}
```

QueryAnalyzer 输出：

```json
{
  "normalized_query": "XJ-91 这个项目有什么注意事项？",
  "query_hash": "sha256:...",
  "keywords": ["XJ-91", "项目", "注意事项"],
  "entities": [
    {"text": "XJ-91", "type": "code_or_identifier", "confidence": 1.0}
  ],
  "intent": "general"
}
```

多路召回：

```text
semantic:
  m2 score=0.82  content="用户关注向量检索性能..."
  m7 score=0.79  content="XJ-91 暂不公开..."

keyword:
  m7 score=12.4  content="XJ-91 暂不公开..."

entity-lite:
  m7 score=1.0   entity_match="XJ-91"
```

融合：

```text
m7:
  semantic_norm=0.79
  keyword_norm=1.00
  entity_norm=1.00
  fused=0.89

m2:
  semantic_norm=0.82
  keyword_norm=0
  entity_norm=0
  fused=0.45
```

最终：

```text
m7 排第一
```

## 5. Domain Model 设计

### 5.1 新增 SearchSignal

建议新增：

```python
class SearchSignal(str, Enum):
    SEMANTIC = "semantic"
    KEYWORD = "keyword"
    ENTITY = "entity"
    TEMPORAL = "temporal"
    RERANK = "rerank"
```

### 5.2 新增 RecallResult

```python
@dataclass(frozen=True)
class RecallResult:
    result: SearchResult
    signal: SearchSignal
    raw_score: float
    rank: int
    matched_terms: tuple[str, ...] = ()
```

说明：

- `result` 是现有 SearchResult。
- `signal` 表示来源。
- `raw_score` 是该路原始分。
- `rank` 是该路内部排名，从 1 开始。
- `matched_terms` 只存短 token，不存完整 query。

### 5.3 新增 HybridSearchCandidate

```python
@dataclass
class HybridSearchCandidate:
    result: SearchResult
    signal_scores: dict[SearchSignal, float]
    signal_ranks: dict[SearchSignal, int]
    matched_terms: set[str]
    fused_score: float = 0.0
    rerank_score: float | None = None
```

说明：

- 合并 key 是 `result.id`。
- 同一个 memory 被多路召回时合并成一个 candidate。
- `fused_score` 是 rerank 前排序分。
- `rerank_score` 是 rerank 后分数。

### 5.4 新增 QueryAnalysis

```python
@dataclass(frozen=True)
class QueryAnalysis:
    normalized_query: str
    query_hash: str
    keywords: tuple[str, ...]
    entities: tuple[QueryEntity, ...]
    intent: str = "general"
```

### 5.5 新增 QueryEntity

```python
@dataclass(frozen=True)
class QueryEntity:
    text: str
    type: str
    confidence: float
```

V1 entity type：

- `code_or_identifier`
- `email_or_handle`
- `date_like`
- `capitalized_name`
- `number`
- `plain_keyword`

## 6. Port 契约设计

### 6.1 现有 Port

当前：

```python
async def search(
    self,
    space_id: UUID,
    query_vector: list[float],
    top_k: int = 10,
    filters: SearchFilters | None = None,
) -> SearchResponse:
    ...
```

### 6.2 新增 keyword_search

建议在 `MemoryVectorStorePort` 增加：

```python
async def keyword_search(
    self,
    space_id: UUID,
    query: str,
    top_k: int = 10,
    filters: SearchFilters | None = None,
    boost_terms: list[str] | None = None,
) -> SearchResponse:
    """Run BM25 / keyword search within the shared index, scoped to a space.

    Implementations MUST:
    - route by space_id
    - filter by space_id
    - apply all filters before scoring
    - avoid raw query_string with user input
    - exclude embedding from _source
    """
```

### 6.3 新增 entity_search_lite

V1 可以不在 Port 里单独加 entity method，而是在 keyword_search 里通过 `boost_terms` 实现。

如果要显式：

```python
async def entity_lite_search(
    self,
    space_id: UUID,
    terms: list[str],
    top_k: int = 10,
    filters: SearchFilters | None = None,
) -> SearchResponse:
    ...
```

建议 V1 不增加这个 Port 方法，降低改动面。

### 6.4 是否新增 hybrid_search 到 Port

不建议 V1 在 Port 增加 `hybrid_search()`。

原因：

- hybrid 是应用层排序策略，不应该绑死到 OpenSearch。
- 未来可能换 vector store 或增加 entity store。
- 应用层更容易做降级、日志、权重配置。

Port 只提供基础召回能力：

```text
semantic vector search
keyword search
get/list/write
```

## 7. CSSAdapter 设计

### 7.1 当前索引可用字段

当前 mapping：

```json
{
  "embedding": "knn_vector",
  "space_id": "keyword",
  "content": "text",
  "strategy_id": "keyword",
  "strategy_type": "keyword",
  "actor_id": "keyword",
  "assistant_id": "keyword",
  "session_id": "keyword",
  "isolation_level": "keyword",
  "memory_type": "keyword",
  "created_at": "date",
  "updated_at": "date"
}
```

V1 可以直接对 `content` 做 `match` / `multi_match`，不需要重建索引。

限制：

- 默认 analyzer 对中文效果有限。
- 对英文、数字、代号、空格分词较有效。
- 对纯中文短 query 可能召回一般。

### 7.2 keyword_search OpenSearch DSL

禁止：

```json
{"query_string": {"query": "<user input>"}}
```

使用：

```json
{
  "size": 40,
  "query": {
    "bool": {
      "filter": [
        {"term": {"space_id": "<space_id>"}},
        "... filters ..."
      ],
      "must": [
        {
          "match": {
            "content": {
              "query": "<normalized_query>",
              "operator": "or"
            }
          }
        }
      ],
      "should": [
        {"match_phrase": {"content": {"query": "XJ-91", "boost": 4.0}}},
        {"match": {"content": {"query": "注意事项", "boost": 1.5}}}
      ]
    }
  },
  "_source": {"excludes": ["embedding"]}
}
```

如果 `boost_terms` 为空：

```json
"should": []
```

如果 query 特别短，仍允许 match，但候选数受限。

### 7.3 entity-lite boost

QueryAnalyzer 提取：

```text
XJ-91
JIRA-123
user@example.com
MaaS
DeepSeek-V3.2
```

CSSAdapter keyword_search 把这些放进 `match_phrase should boost`：

```json
{
  "match_phrase": {
    "content": {
      "query": "XJ-91",
      "boost": 6.0
    }
  }
}
```

注意：

- boost term 数量限制，默认最多 8 个。
- 单个 boost term 长度限制，默认最多 64 字符。
- 不记录完整 boost term 到普通日志；仅记录 count 和类型分布。

### 7.4 search response 解析

复用 `_parse_search_response()`，但需要确认 score 来源：

- KNN score 是向量相似分。
- BM25 score 是文本相关分。

两者不能直接比较，所以必须在应用层按 signal 归一化。

## 8. QueryAnalyzer 设计

### 8.1 职责

`QueryAnalyzer` 只做轻量、本地、确定性处理：

- trim。
- 长度限制。
- query hash。
- keyword tokenize。
- entity-lite extraction。
- intent 粗分类。

V1 不调用 LLM。

原因：

- search 是在线路径，不能引入额外 LLM 时延。
- LLM query rewrite 有安全和稳定性风险。
- 本地处理更容易做熔断和资源控制。

### 8.2 输入校验

规则：

```text
query must be str
query.strip() must not be empty
len(query) <= SEARCH_MAX_QUERY_LENGTH
top_k <= SEARCH_MAX_TOP_K
```

如果超长：

- API 层已有 max query length 时直接 400。
- 如果历史 schema 没有限制，Application 层截断或拒绝。

建议：

- 对用户 API：超长返回 400。
- 对内部兼容：可配置是否截断，默认拒绝。

### 8.3 normalize

V1 normalize：

- strip。
- 合并连续空白。
- 保留原始大小写用于 exact phrase。
- 生成 lowercase 版本用于 token 分析。

不要做：

- 删除标点导致项目代号变形。
- 全角/半角复杂转换，除非有测试覆盖。

### 8.4 keyword tokenize

V1 简单规则：

```text
1. 提取 code-like token：
   [A-Za-z]+[-_][A-Za-z0-9._-]+
   [A-Za-z]{2,}[0-9]+
   [A-Za-z0-9]{2,}[-_][A-Za-z0-9]{2,}

2. 提取 email/handle/url-like token，注意不要记录敏感完整值到日志。

3. 提取英文单词，过滤 stop words。

4. 提取中文连续片段：
   - V1 不做复杂分词。
   - 长度 2-12 的连续中文片段可以作为 phrase。
   - 更好的中文分词放 V1.1。
```

限制：

```text
max_keywords = 16
max_keyword_length = 64
```

### 8.5 entity-lite extraction

V1 entity-lite 不追求 NLP 完整性，只解决高价值场景：

- 项目代号：`XJ-91`、`JIRA-123`。
- 模型名：`deepseek-v3.2`、`bge-m3`。
- 邮箱/账号：只用于检索，不记录明文日志。
- 日期：`2026-07-01`、`next Friday` 先只作为 keyword。
- 英文大写实体：`Stanford`、`Tokyo`。

输出：

```python
QueryEntity(text="XJ-91", type="code_or_identifier", confidence=1.0)
```

安全：

- entity text 可能包含敏感信息。
- 日志只记录 entity_count 和 entity_types。

### 8.6 intent 粗分类

V1 可选：

```text
current:
  现在 / current / currently / latest / now

past:
  之前 / 以前 / last time / before / previously

future:
  接下来 / 明天 / 下周 / upcoming / next

general:
  default
```

V1 只用于轻量 recency boost，不改变过滤结果。

## 9. 多路召回设计

### 9.1 Semantic Recall

复用当前：

```text
embedding_service.embed(query)
memory_vector_store_service.search(space_id, vector, semantic_k, filters)
```

候选数：

```python
semantic_k = min(
    max(top_k * semantic_expansion_factor, min_semantic_candidates),
    max_semantic_candidates,
)
```

默认：

```text
semantic_expansion_factor=4
min_semantic_candidates=40
max_semantic_candidates=200
```

超时：

```text
embedding_timeout_ms
vector_search_timeout_ms
```

失败：

- embedding 失败：semantic recall 失败。
- 如果 keyword recall 成功，可以 degraded 返回。
- 如果所有 recall 失败，抛 SearchError。

### 9.2 Keyword Recall

新增：

```text
memory_vector_store_service.keyword_search(
  space_id=space_id,
  query=analysis.normalized_query,
  top_k=keyword_k,
  filters=filters,
  boost_terms=analysis.keywords + entity texts
)
```

候选数：

```text
keyword_k = min(max(top_k * 4, 40), 200)
```

适用：

- 项目代号。
- 精确词。
- 人名。
- 地名。
- 产品名。
- 关键词表达。

失败：

- keyword search 失败时记录 degraded。
- 不影响 semantic recall。

### 9.3 Entity-Lite Recall

V1 不单独访问 entity store。

实现方式：

```text
entity terms -> keyword_search boost_terms
```

同时在 fusion 阶段给命中 entity terms 的候选额外 entity score。

如何判断候选命中 entity：

- 不做复杂高亮。
- CSSAdapter 可通过 OpenSearch highlight 返回 matched snippets，但 V1 不建议引入。
- 应用层可以对候选 content 做大小写不敏感的 substring check。

安全：

- 只在内存中做 substring。
- 不记录 content。
- entity term 数量和长度受限。

entity score：

```python
entity_score = matched_entity_count / total_entity_count
```

或：

```python
entity_score = max(confidence for matched entity)
```

建议 V1：

```text
entity_score = min(1.0, matched_entity_count / max(1, total_entity_count))
```

### 9.4 Temporal / Recency Signal

V1 没有 event_time，只能使用 `created_at` / `updated_at`。

不做强排序，只做小权重 boost。

规则：

```text
intent=current/general:
  新记忆轻微加分

intent=past:
  不加新鲜度 boost，避免误伤历史问题

intent=future:
  V1 暂不做，因为没有 event_time
```

recency score：

```text
age_days <= 1      -> 1.00
age_days <= 7      -> 0.80
age_days <= 30     -> 0.50
age_days <= 180    -> 0.20
else               -> 0.00
```

权重必须小，默认 0.05-0.10。

原因：

- created_at 是写入时间，不一定是事实发生时间。
- 不能让新但无关的记忆盖过旧但准确的记忆。

## 10. Score Fusion 设计

### 10.1 为什么不能直接相加

不同分数尺度不同：

- KNN score：通常 0-1 或类似范围。
- BM25 score：可能 0-几十。
- Entity score：0-1。
- Recency score：0-1。
- Rerank score：模型自定义范围。

所以必须归一化。

### 10.2 归一化方案

V1 推荐 rank-based + min-max 混合。

对 semantic：

```python
semantic_norm = clamp(raw_score, 0.0, 1.0)
```

如果实际 OpenSearch KNN score 不在 0-1，则使用 per-list min-max：

```python
norm = (score - min_score) / (max_score - min_score)
```

对 keyword：

```python
keyword_norm = per-list min-max
```

如果该路只有一个结果：

```python
keyword_norm = 1.0
```

对 rank fallback：

```python
rank_norm = 1 / (rank + rank_constant)
```

默认 `rank_constant=1`。

### 10.3 权重

默认权重建议：

```text
semantic_weight = 0.55
keyword_weight  = 0.25
entity_weight   = 0.15
recency_weight  = 0.05
```

如果 query 中存在强 entity/code token：

```text
semantic_weight = 0.45
keyword_weight  = 0.30
entity_weight   = 0.20
recency_weight  = 0.05
```

如果 query 很长、没有实体：

```text
semantic_weight = 0.65
keyword_weight  = 0.20
entity_weight   = 0.05
recency_weight  = 0.10
```

V1 可以先固定一套权重，不做动态权重；但代码结构要支持动态。

### 10.4 融合公式

```python
fused_score = (
    semantic_weight * semantic_norm
    + keyword_weight * keyword_norm
    + entity_weight * entity_norm
    + recency_weight * recency_norm
)
```

缺失信号按 0 处理。

### 10.5 min_score 策略

现有 `min_score` 是 vector similarity 分数。

Hybrid 后需要重新定义。

V1 兼容方案：

- 如果 hybrid disabled：保持现有语义。
- 如果 hybrid enabled：
  - `min_score` 应用于 `fused_score`。
  - 默认 `SEARCH_DEFAULT_MIN_SCORE` 需要重新调低，例如 `0.35`。

为了避免破坏现有调用，建议新增配置：

```text
SEARCH_HYBRID_DEFAULT_MIN_SCORE=0.30
SEARCH_VECTOR_DEFAULT_MIN_SCORE=0.75
```

API request 里传入 `min_score` 时：

- 解释为当前 search mode 的 score。
- 文档要明确。

### 10.6 Rerank 后排序

如果 rerank enabled：

流程：

```text
fusion candidates sorted by fused_score
  -> take rerank_candidate_limit
  -> rerank(query, candidates)
  -> final sort by rerank score
  -> top_k
```

如果 rerank 失败：

```text
fallback to fused_score ranking
```

不要 rerank 所有候选，避免成本过高。

## 11. 配置设计

在 `SearchConfig` 增加：

```python
hybrid_enabled: bool = True

hybrid_semantic_enabled: bool = True
hybrid_keyword_enabled: bool = True
hybrid_entity_lite_enabled: bool = True
hybrid_recency_enabled: bool = True

hybrid_semantic_weight: float = 0.55
hybrid_keyword_weight: float = 0.25
hybrid_entity_weight: float = 0.15
hybrid_recency_weight: float = 0.05

hybrid_semantic_expansion_factor: int = 4
hybrid_keyword_expansion_factor: int = 4
hybrid_min_semantic_candidates: int = 40
hybrid_min_keyword_candidates: int = 40
hybrid_max_semantic_candidates: int = 200
hybrid_max_keyword_candidates: int = 200
hybrid_max_fusion_candidates: int = 300
hybrid_rerank_candidate_limit: int = 80

hybrid_keyword_timeout_ms: int = 2000
hybrid_vector_timeout_ms: int = 5000
hybrid_fusion_timeout_ms: int = 1000

hybrid_max_keywords: int = 16
hybrid_max_entities: int = 8
hybrid_max_term_length: int = 64

hybrid_default_min_score: float = 0.30
```

权重校验：

```text
每个权重 >= 0
总权重 > 0
建议总和约等于 1，但不要强依赖
```

候选数校验：

```text
max candidates <= 500
timeout <= reasonable upper bound
```

## 12. API 设计

### 12.1 V1 不改公开响应

现有 response：

```json
{
  "results": [
    {
      "record": {...},
      "score": 0.82
    }
  ],
  "total": 1,
  "query": "..."
}
```

V1 中 `score` 返回：

- hybrid enabled 且 rerank disabled：`fused_score`。
- hybrid enabled 且 rerank enabled：优先返回 rerank 后 score；如果 rerank service 返回的是重排后的原 SearchResult score，则需要统一处理。
- vector-only：保持原 score。

为避免语义混乱，建议 domain SearchResult 增加可选字段前先不改 API schema，内部用 candidate 分数覆盖最终 SearchResult.score。

### 12.2 Debug Explain

V1 不对普通 API 开放。

可以后续增加 internal-only：

```http
POST /v1/core/internal/spaces/{space_id}/memories/search:explain
```

或 header：

```text
X-Debug-Search-Explain: true
```

但必须：

- 仅内部环境可用。
- 需要 internal token。
- 不返回敏感 content 片段。
- explain 里只返回分数、signal、rank、matched_terms 的脱敏版本。

V1 文档先预留，不实现。

## 13. 安全设计细节

### 13.1 多租户隔离

每一路召回都必须：

```python
query_filters = [{"term": {"space_id": str(space_id)}}]
query_filters.extend(filters.to_query_filters())
client.search(..., routing=str(space_id))
```

禁止：

- keyword_search 忘记 routing。
- entity search 使用全局 index 无 space filter。
- debug explain 返回其他 tenant 信息。

测试必须覆盖：

- 两个 space 写入相似内容，只能查到当前 space。
- 同 space 不同 actor/session，根据 JWT filter 只能查到允许范围。

### 13.2 注入攻击防护

OpenSearch DSL 使用结构化查询。

禁止：

```python
{"query_string": {"query": user_query}}
```

允许：

```python
{"match": {"content": {"query": user_query}}}
```

原因：

- query_string 支持特殊语法，容易被用户输入影响查询结构。
- match 把用户输入作为文本分析，不作为 DSL。

### 13.3 敏感数据保护

日志禁止：

- 完整 query。
- 完整 memory content。
- access token。
- auth token。
- API key。
- OpenSearch password。
- JWT。

日志允许：

```text
query_length
query_hash
top_k
space_id
strategy_type
actor/session 是否存在
candidate counts
duration_ms
degraded reason code
```

matched_terms：

- 默认不打日志。
- debug explain 中需要脱敏。

### 13.4 资源保护

限制：

- `max_query_length`
- `max_top_k`
- `max_keywords`
- `max_entities`
- `max_term_length`
- `max_fusion_candidates`
- `rerank_candidate_limit`

超限行为：

- query/top_k 超限：返回 400 或按现有 schema 校验。
- keywords/entities 超限：截断，记录 count。
- OpenSearch timeout：该路降级。

### 13.5 错误容错

错误分类：

```text
semantic_error:
  embedding failure
  vector search failure

keyword_error:
  OpenSearch keyword search failure

entity_error:
  query analysis failure

fusion_error:
  local scoring bug

rerank_error:
  rerank service failure
```

降级策略：

```text
semantic ok, keyword fail:
  return semantic-based hybrid degraded

semantic fail, keyword ok:
  return keyword-based degraded

semantic fail, keyword fail:
  raise SearchError

rerank fail:
  return fusion ranking
```

### 13.6 自愈

可自愈手段：

- 单路失败自动降级。
- OpenSearch transient error 交给 adapter / service retry 策略。
- timeout 后不阻塞整体。
- config 可一键关闭 hybrid。
- readiness 不因 keyword path 单次失败变 down，但指标应体现 degraded。

## 14. 性能设计细节

### 14.1 并发执行

使用 `asyncio.gather(..., return_exceptions=True)` 或结构化 helper。

伪代码：

```python
semantic_task = create_task(self._semantic_recall(...))
keyword_task = create_task(self._keyword_recall(...))

results = await gather_with_timeout([
    ("semantic", semantic_task),
    ("keyword", keyword_task),
])
```

不要串行：

```text
vector search 完了再 keyword search
```

### 14.2 超时控制

每一路独立 timeout：

```python
await asyncio.wait_for(keyword_search(), timeout=keyword_timeout)
```

总 search timeout：

- 可以复用现有 `search_timeout_ms`。
- 单路 timeout 不能超过总 timeout。

### 14.3 网络和 shard 优化

OpenSearch 查询：

- 必须 routing。
- 必须 filter space_id。
- `_source` exclude embedding。
- size 控制。

这能避免：

- 全 shard fan-out。
- 大字段返回。
- 跨租户扫描。

### 14.4 Rerank 成本控制

Rerank 只处理融合后的前 N：

```text
N = min(max(top_k * 3, 20), hybrid_rerank_candidate_limit)
```

不要：

```text
semantic 200 + keyword 200 全部 rerank
```

### 14.5 内存控制

候选合并用 dict：

```python
candidates: dict[UUID, HybridSearchCandidate]
```

最大条数：

```text
hybrid_max_fusion_candidates
```

超出时：

- 先按每路 rank 截断。
- 再融合。

## 15. 实现步骤

### 15.1 Step 1：新增类型

新增文件建议：

```text
src/core/domain/types/search.py
```

放入：

- `SearchSignal`
- `RecallResult`
- `HybridSearchCandidate`
- `QueryAnalysis`
- `QueryEntity`

如果项目倾向少文件，也可先放入 `vector_operations.py`，但长期建议拆出。

### 15.2 Step 2：扩展 Port 和 Service

修改：

```text
src/core/ports/memory_vector_store.py
src/core/application/services/memory_vector_store_service.py
```

新增：

```python
async def keyword_search(...)
```

Service 只转发，不做 OpenSearch 细节。

### 15.3 Step 3：CSSAdapter 实现 keyword_search

修改：

```text
src/core/adapters/css_adapter.py
```

要求：

- `_require_index_ready()`
- routing。
- space_id filter。
- filters.to_query_filters()。
- match / match_phrase DSL。
- `_source.excludes=["embedding"]`。
- SearchError 包装。
- parse response 复用。

### 15.4 Step 4：QueryAnalyzer

新增：

```text
src/core/application/search/query_analysis.py
```

实现：

- validate。
- normalize。
- hash。
- keywords。
- entity-lite。
- intent。

必须有单元测试覆盖：

- 空 query。
- 超长 query。
- `XJ-91`。
- `deepseek-v3.2`。
- 中文 query。
- 包含换行和特殊符号 query。

### 15.5 Step 5：ScoreFusionService

新增：

```text
src/core/application/search/score_fusion.py
```

实现：

- merge recall results。
- normalize per signal。
- entity score。
- recency score。
- fused score。
- sort。

必须有单元测试覆盖：

- 单 semantic。
- semantic + keyword。
- keyword 高分覆盖语义弱相关。
- entity boost。
- 缺失信号。
- 空候选。

### 15.6 Step 6：HybridMemorySearch

新增：

```text
src/core/application/search/hybrid_search.py
```

或修改现有：

```text
src/core/application/memory_search.py
```

建议：

- 保留现有 `MemorySearch` 为 facade。
- 新增 `VectorMemorySearch` 和 `HybridMemorySearch`。

第一版也可以少改：

```text
MemorySearch.search()
  if config.hybrid_enabled:
      return await self._hybrid_search(...)
  return await self._vector_search(...)
```

但函数不要过长。

### 15.7 Step 7：配置接入

修改：

```text
src/core/config.py
.env
```

加 hybrid 配置。

默认建议：

```text
SEARCH_HYBRID_ENABLED=true
SEARCH_HYBRID_KEYWORD_ENABLED=true
SEARCH_HYBRID_ENTITY_LITE_ENABLED=true
SEARCH_HYBRID_RECENCY_ENABLED=false
```

V1 可以先关闭 recency，避免 created_at 引起误排序；测试稳定后打开。

### 15.8 Step 8：日志和指标

日志：

```python
logger.info(
    "hybrid_search_completed",
    space_id=str(space_id),
    query_length=len(query),
    query_hash=analysis.query_hash,
    top_k=top_k,
    semantic_count=len(semantic_results),
    keyword_count=len(keyword_results),
    candidate_count=len(candidates),
    result_count=len(results),
    degraded=degraded,
    duration_ms=duration,
)
```

不要打：

```text
query
content
raw OpenSearch body
```

指标建议：

```text
memory_search_hybrid_duration_ms
memory_search_semantic_recall_duration_ms
memory_search_keyword_recall_duration_ms
memory_search_fusion_duration_ms
memory_search_rerank_duration_ms
memory_search_degraded_total
memory_search_candidates_total
```

### 15.9 Step 9：测试

单元测试：

- QueryAnalyzer。
- ScoreFusionService。
- CSSAdapter keyword DSL 构造。
- MemorySearch 降级。

集成测试：

- 写入几条 memory，分别测试 vector-only / keyword / hybrid。
- 两个 space 隔离。
- actor/session filter。
- keyword path failure fallback。
- rerank failure fallback。

性能测试：

- top_k=10。
- top_k=100。
- query 包含 16 个 keywords。
- 多租户并发。

安全测试：

- query 包含 OpenSearch query_string 特殊字符：

```text
*) OR *:* OR content:*
```

预期：

- 不越权。
- 不报原始 DSL 错误。
- 不返回其他 tenant 数据。

## 16. 伪代码

### 16.1 HybridMemorySearch

```python
class HybridMemorySearch:
    async def search(self, query, space_id, top_k, min_score, filters):
        effective_query = self._validate_query(query)
        analysis = self._query_analyzer.analyze(effective_query)

        semantic_task = asyncio.create_task(
            self._semantic_recall(analysis, space_id, top_k, filters)
        )
        keyword_task = asyncio.create_task(
            self._keyword_recall(analysis, space_id, top_k, filters)
        )

        recall_results = []
        degraded_reasons = []

        for signal, task in await collect_tasks(...):
            if task.failed:
                degraded_reasons.append(signal)
                continue
            recall_results.extend(task.results)

        if not recall_results:
            raise SearchError("all recall paths failed", space_id=space_id)

        candidates = self._fusion.merge_and_score(
            analysis=analysis,
            recalls=recall_results,
            min_score=effective_min_score,
        )

        rerank_input = candidates[:self._config.hybrid_rerank_candidate_limit]
        final_results = await self._rerank_or_fallback(effective_query, rerank_input)

        return SearchResponse(
            results=final_results[:top_k],
            total=min(len(final_results), top_k),
        )
```

### 16.2 Keyword Recall

```python
async def _keyword_recall(self, analysis, space_id, top_k, filters):
    keyword_k = self._compute_keyword_k(top_k)
    boost_terms = list(analysis.keywords)
    boost_terms.extend(entity.text for entity in analysis.entities)
    boost_terms = limit_terms(boost_terms)

    response = await self._vector_store.keyword_search(
        space_id=space_id,
        query=analysis.normalized_query,
        top_k=keyword_k,
        filters=filters,
        boost_terms=boost_terms,
    )

    return [
        RecallResult(
            result=r,
            signal=SearchSignal.KEYWORD,
            raw_score=r.score,
            rank=i + 1,
        )
        for i, r in enumerate(response.results)
    ]
```

### 16.3 Score Fusion

```python
def merge_and_score(self, analysis, recalls, min_score):
    by_id = {}

    grouped = group_by_signal(recalls)
    normalized = normalize_scores(grouped)

    for recall in recalls:
        candidate = by_id.setdefault(
            recall.result.id,
            HybridSearchCandidate(result=recall.result, ...)
        )
        candidate.signal_scores[recall.signal] = normalized[recall]
        candidate.signal_ranks[recall.signal] = recall.rank

    for candidate in by_id.values():
        candidate.signal_scores[SearchSignal.ENTITY] = compute_entity_score(
            candidate.result.content,
            analysis.entities,
        )
        candidate.signal_scores[SearchSignal.TEMPORAL] = compute_recency_score(
            candidate.result.created_at,
            analysis.intent,
        )
        candidate.fused_score = weighted_sum(candidate.signal_scores)

    results = [
        c for c in by_id.values()
        if c.fused_score >= min_score
    ]
    return sorted(results, key=lambda c: (-c.fused_score, c.result.created_at))
```

## 17. OpenSearch DSL 细节

### 17.1 KNN 保持现状

现有 CSSAdapter.search 已经做对：

- `_require_index_ready`
- `space_id` filter
- routing
- excludes embedding

保持。

### 17.2 Keyword Search DSL

示例：

```python
query_filters = [{"term": {"space_id": routing}}]
if filters:
    query_filters.extend(filters.to_query_filters())

should_clauses = []
for term in boost_terms[:max_boost_terms]:
    should_clauses.append({
        "match_phrase": {
            "content": {
                "query": term,
                "boost": 4.0,
            }
        }
    })

body = {
    "size": top_k,
    "query": {
        "bool": {
            "filter": query_filters,
            "must": [
                {
                    "match": {
                        "content": {
                            "query": query,
                            "operator": "or",
                        }
                    }
                }
            ],
            "should": should_clauses,
        }
    },
    "_source": {"excludes": ["embedding"]},
}
```

### 17.3 空 keyword 处理

如果 query analysis 没有关键词：

- 仍可用 normalized query 做 match。
- 如果 normalized query 太短且没有有效 token，可以跳过 keyword recall。

规则：

```text
len(normalized_query) < 2 and no entity -> skip keyword
```

## 18. 例子

### 18.1 项目代号

记忆：

```text
m1: 用户正在推进 XJ-91 项目，要求暂不公开。
m2: 用户关注向量检索性能优化。
```

查询：

```text
XJ-91 有什么注意事项？
```

Vector-only 风险：

- `XJ-91` 没有稳定语义。
- 可能召回 m2。

Hybrid：

```text
semantic:
  m2=0.82
  m1=0.76

keyword:
  m1=10.3

entity:
  m1 matches XJ-91

fusion:
  m1 > m2
```

### 18.2 用户偏好

记忆：

```text
m1: 用户是素食者。
m2: 用户对坚果过敏。
m3: 用户希望回答简短。
```

查询：

```text
我有什么饮食限制？
```

Hybrid：

- semantic 命中 m1/m2。
- keyword 可能弱。
- entity 无明显强信号。
- 最终 semantic 主导。

说明：

Hybrid 不会牺牲普通语义查询。

### 18.3 中文短词

记忆：

```text
m1: 用户的女儿叫 Emma，正在申请 Stanford。
m2: 用户同事叫 Emily，负责预算。
```

查询：

```text
Emma 申请哪所学校？
```

Hybrid：

- entity-lite 提取 `Emma`。
- keyword boost 命中 m1。
- semantic 命中 m1。
- m1 排前。

### 18.4 历史问题

记忆：

```text
m1: 用户 2025 年住在上海。
m2: 用户 2026 年搬到杭州。
```

查询：

```text
我以前住在哪里？
```

V1：

- intent=past。
- 不启用 recency boost。
- 靠 semantic/keyword/rerank。

未来 V2：

- 使用 event_time 和 temporal_type。
- past query boost 过去事件。

## 19. 与 Mem0 的对应关系

| Mem0 能力 | 当前 V1 设计 | 说明 |
|---|---|---|
| semantic retrieval | semantic vector search | 复用当前 KNN |
| BM25 keyword | keyword_search | 新增 CSSAdapter BM25 |
| entity matching | entity-lite boost | V1 不建 entity index |
| score fusion | ScoreFusionService | 应用层融合 |
| rerank | RerankService | 复用当前能力 |
| temporal reasoning | recency-lite | V1 只做轻量新鲜度 |
| ADD-only extraction | 不在本方案范围 | 属于写入策略 |

## 20. 风险和取舍

### 20.1 中文 BM25 效果风险

当前 `content` 是默认 text analyzer，对中文分词可能一般。

V1 取舍：

- 先实现 keyword recall，对英文、代号、混合 token 立即有收益。
- 中文更好的分词放 V1.1。

V1.1 方案：

- 增加 `content.ngram` 子字段。
- 或接入中文 analyzer。
- 需要 index migration。

### 20.2 分数阈值语义变化

Hybrid score 和 vector score 不同。

处理：

- hybrid 使用独立 default min score。
- 文档说明。
- 如果用户显式传 min_score，按当前 mode 解释。

### 20.3 Rerank 分数不统一

RerankService 可能返回重排结果，但分数含义和 fused score 不一致。

处理：

- 最终 SearchResult.score 可以设置为 rerank score。
- 如果 rerank 不提供稳定分数，则保留 fused_score，只用 rerank 顺序调整。
- 需要看当前 RerankService 返回结构后实现。

### 20.4 keyword 召回噪声

BM25 可能召回关键词相同但语义不相关的记忆。

处理：

- fusion 中 keyword 权重不超过 semantic。
- rerank 打开时能二次过滤。
- min_score 控制。

### 20.5 性能风险

多一路 OpenSearch 查询会增加延迟。

处理：

- 并发执行。
- routing 限 shard。
- timeout。
- 候选上限。
- keyword 可配置关闭。

## 21. 验收标准

### 21.1 功能验收

必须满足：

- hybrid enabled 时 search 正常返回。
- hybrid disabled 时行为等价当前 vector-only。
- 项目代号类 query 能通过 keyword/entity-lite 提升召回。
- 普通语义 query 不明显退化。
- rerank 失败时仍返回 fusion 排序。
- keyword 失败时仍返回 semantic 排序。

### 21.2 安全验收

必须满足：

- 不跨 space。
- 不跨 actor/session。
- 不使用 query_string。
- 日志无完整 query/content/token。
- top_k/query length 超限受控。
- OpenSearch 异常不泄漏连接信息。

### 21.3 性能验收

建议目标：

```text
top_k=10:
  hybrid p50 <= vector-only p50 + 50ms + keyword query cost
  hybrid p95 不因 keyword timeout 无限放大

keyword path timeout:
  整体仍在 search_timeout_ms 内返回 degraded

rerank enabled:
  rerank candidates <= configured limit
```

### 21.4 可观测验收

必须能从日志判断：

- 本次是否 hybrid。
- 每路召回数量。
- 每路耗时。
- 是否 degraded。
- fusion candidate 数。
- rerank 是否成功。

## 22. 推荐提交拆分

建议按以下 PR / commit 拆：

1. `search types + config`
2. `vector store keyword_search contract`
3. `css keyword_search implementation`
4. `query analyzer`
5. `score fusion`
6. `hybrid memory search orchestration`
7. `tests + docs`

不要把所有改动压成一个大提交。

## 23. 第一版实现重点

实现 AI 应优先完成：

1. 保留 vector-only fallback。
2. 确保所有 keyword 查询都带 routing 和 filters。
3. 不使用 query_string。
4. 实现 ScoreFusionService 的单元测试。
5. 实现两个集成级场景：
   - `XJ-91` 项目代号召回。
   - 两个 space 隔离。
6. 加日志但不泄漏 query/content。

不要第一版就做：

- 实体图谱。
- LLM query rewrite。
- 大规模 index migration。
- 对外 explain API。

## 24. 最终推荐

第一版多信号混合检索应采用：

```text
semantic KNN
+ OpenSearch BM25 keyword
+ entity-lite exact/phrase boost
+ optional recency-lite
+ existing rerank
+ application-layer score fusion
```

这版方案能在当前 core 上低风险落地，且能补上最明显的召回短板：

- 项目代号。
- 人名地名。
- 产品型号。
- 精确关键词。
- embedding 漏召回。

等 V1 稳定后，再演进到：

```text
entity index
content ngram / Chinese analyzer
temporal fields
search explain
strategy-aware search policy
```

这样既参考了 Mem0 的先进方向，又不会破坏 memory-core 当前多策略、强隔离、可配置的工程基础。
