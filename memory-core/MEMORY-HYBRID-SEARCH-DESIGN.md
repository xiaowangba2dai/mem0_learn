# Memory-Core 多信号混合检索实现设计

更新时间：2026-07-01

本文是给 AI coding agent 执行实现用的设计文档，同时供人类研发评审。实现时以本文为主，遇到不明确处先读当前源码，再按本文的安全和契约原则做保守实现。

## 0. 执行摘要

当前 memory-core 的记忆检索主要是：

```text
query -> embedding -> OpenSearch KNN -> optional rerank -> top_k
```

这对自然语言语义查询有效，但对项目代号、人名、地名、产品型号、短关键词不稳定。参考 Mem0 的多信号检索思路，本方案在当前 core 上实现 V1：

```text
semantic KNN
+ OpenSearch BM25 keyword
+ entity-lite exact/phrase boost
+ optional recency-lite
+ existing rerank
+ application-layer score fusion
```

V1 不做完整实体图谱，不改写入流程，不要求重建索引，不改变外部 response 结构。必须保留 vector-only fallback，并支持配置一键关闭 hybrid。

## 1. Goal

实现一版适用于当前 memory-core 的多信号混合检索，使 search 在保留现有语义检索能力的基础上，增强对精确词、代号、实体名的召回能力。

目标能力：

1. 同一个 search 请求并发执行 semantic vector recall 和 keyword recall。
2. 从 query 中提取 lightweight keywords/entities，用于 keyword boost 和 fusion boost。
3. 将多路候选按 memory id 合并，做分数归一化和加权融合。
4. 在融合后的候选上执行现有 rerank，rerank 失败时回退到 fusion 排序。
5. 所有召回路径都遵守当前 `space_id` routing、多租户过滤和 JWT 派生过滤。
6. 所有新增能力都有配置开关、日志、降级路径和测试。

## 2. Non-Goals

本次不做：

1. 不实现完整 entity index / graph memory。
2. 不改 memory extraction / consolidation 写入策略。
3. 不增加 LLM query rewrite。
4. 不重建 OpenSearch index。
5. 不改变 `/v1/core/internal/spaces/{space_id}/memories/search` 的 response schema。
6. 不对普通调用方暴露 explain。
7. 不引入外部中文分词服务。
8. 不让 keyword/entity 召回绕过现有权限和 filter。

## 3. Current System

### 3.1 当前调用链

```text
src/core/api/router.py
  search_memories()
    -> to_search_filters()
    -> MemorySearch.search()

src/core/application/memory_search.py
  MemorySearch.search()
    -> EmbeddingService.embed()
    -> MemoryVectorStoreService.search()
    -> _apply_rerank_and_filter()

src/core/application/services/memory_vector_store_service.py
  search()
    -> MemoryVectorStorePort.search()

src/core/adapters/css_adapter.py
  CSSAdapter.search()
    -> OpenSearch knn query
```

### 3.2 当前安全边界

当前 `CSSAdapter.search()` 已经具备几个必须保留的行为：

```text
routing = str(space_id)
query_filters includes {"term": {"space_id": routing}}
filters.to_query_filters() appended
_source excludes embedding
```

这些行为是多租户安全的底线。新增 keyword search 必须保持同样约束。

### 3.3 当前模型

当前 search 结果类型在：

```text
src/core/domain/types/vector_operations.py
```

关键类型：

```python
SearchFilters
SearchResult
SearchResponse
ListFilters
ListResponse
```

当前 API response 转换在：

```text
src/core/api/schemas.py
```

`SearchResult.score` 会被映射到 API 的 `MemorySearchResult.score`。

### 3.4 当前限制

当前 vector-only 的典型失败场景：

```text
memory: 用户正在推进 XJ-91 项目，要求暂不公开。
query: XJ-91 有什么注意事项？
```

`XJ-91` 是项目代号，embedding 语义不稳定。如果只有 KNN，可能召回“项目”“注意事项”相关但不包含 `XJ-91` 的记忆。

## 4. Target Behavior

### 4.1 V1 主流程

```text
MemorySearch.search(query, space_id, top_k, min_score, filters)
  1. validate query / top_k
  2. blank query -> existing list fallback
  3. hybrid disabled -> existing vector-only path
  4. QueryAnalyzer.analyze(query)
  5. concurrently:
       semantic recall
       keyword recall
  6. collect successful recall results
  7. if all recall paths failed -> SearchError
  8. merge candidates by record_id
  9. compute semantic/keyword/entity/recency normalized scores
 10. compute fused score
 11. filter by effective min_score
 12. optional rerank top N candidates
 13. return top_k SearchResponse
```

### 4.2 Expected Example

Data:

```text
m1: 用户正在推进 XJ-91 项目，要求暂不公开。
m2: 用户关注向量检索性能优化。
```

Query:

```text
XJ-91 有什么注意事项？
```

Expected recall:

```text
semantic:
  m2 score=0.82
  m1 score=0.76

keyword:
  m1 score=12.4

entity-lite:
  m1 matches XJ-91
```

Expected final ranking:

```text
m1 first
m2 lower or absent
```

### 4.3 Compatibility

When `SEARCH_HYBRID_ENABLED=false`, behavior should match current vector-only search as closely as possible.

When hybrid is enabled, API response shape remains unchanged:

```json
{
  "results": [
    {
      "record": {},
      "score": 0.73
    }
  ],
  "total": 1,
  "query": "..."
}
```

For hybrid mode:

- if rerank succeeds, score may represent rerank score or fused score depending on existing rerank return semantics;
- if rerank is disabled or fails, score is fused score;
- document this in code comments near score assignment.

## 5. Architecture

### 5.1 Module Layout

Add:

```text
src/core/application/search/
  __init__.py
  query_analysis.py
  score_fusion.py
  hybrid_search.py
  search_models.py
```

Modify:

```text
src/core/application/memory_search.py
src/core/application/services/memory_vector_store_service.py
src/core/ports/memory_vector_store.py
src/core/adapters/css_adapter.py
src/core/config.py
```

Optional:

```text
src/core/domain/types/search.py
```

If adding `domain/types/search.py` creates import churn, use `application/search/search_models.py` for V1 implementation-only models.

### 5.2 Responsibility Boundaries

`MemorySearch`

- Public use case class used by API.
- Chooses vector-only or hybrid based on config.
- Keeps blank-query list fallback.

`HybridMemorySearch`

- Orchestrates query analysis, recall, fusion, rerank, fallback.
- Does not know OpenSearch DSL.

`QueryAnalyzer`

- Local deterministic query processing.
- No LLM call.
- Extracts normalized query, keywords, entity-like tokens, intent.

`ScoreFusionService`

- Merges recall results.
- Normalizes scores.
- Applies weights.
- Computes final fused scores.

`MemoryVectorStoreService`

- Application service wrapper.
- Adds `keyword_search()` as a pass-through.

`MemoryVectorStorePort`

- Storage contract.
- Adds `keyword_search()` method.

`CSSAdapter`

- Implements OpenSearch KNN and BM25 queries.
- Must enforce routing and filters.
- Must not implement fusion policy.

## 6. Contracts

### 6.1 SearchSignal

```python
class SearchSignal(str, Enum):
    SEMANTIC = "semantic"
    KEYWORD = "keyword"
    ENTITY = "entity"
    RECENCY = "recency"
    RERANK = "rerank"
```

### 6.2 QueryEntity

```python
@dataclass(frozen=True)
class QueryEntity:
    text: str
    type: str
    confidence: float
```

Supported V1 types:

```text
code_or_identifier
email_or_handle
date_like
capitalized_name
number
plain_keyword
```

### 6.3 QueryAnalysis

```python
@dataclass(frozen=True)
class QueryAnalysis:
    normalized_query: str
    query_hash: str
    keywords: tuple[str, ...]
    entities: tuple[QueryEntity, ...]
    intent: str = "general"
```

`query_hash` is for logs only. Use SHA-256 over normalized query with a short prefix for logging, for example first 12 hex chars. Do not log raw query.

### 6.4 RecallResult

```python
@dataclass(frozen=True)
class RecallResult:
    result: SearchResult
    signal: SearchSignal
    raw_score: float
    rank: int
```

### 6.5 HybridSearchCandidate

```python
@dataclass
class HybridSearchCandidate:
    result: SearchResult
    signal_scores: dict[SearchSignal, float]
    signal_ranks: dict[SearchSignal, int]
    matched_entity_count: int = 0
    fused_score: float = 0.0
    rerank_score: float | None = None
```

### 6.6 Port Contract

Add to `MemoryVectorStorePort`:

```python
async def keyword_search(
    self,
    space_id: UUID,
    query: str,
    top_k: int = 10,
    filters: SearchFilters | None = None,
    boost_terms: list[str] | None = None,
) -> SearchResponse:
    """Run BM25 / keyword search within a single tenant scope.

    Implementations MUST:
    - route by space_id
    - include a space_id filter
    - apply all SearchFilters
    - avoid query_string with user input
    - exclude embedding from response source
    """
```

Do not add `hybrid_search()` to the port in V1. Hybrid is application policy, not storage policy.

### 6.7 Config Contract

Add to `SearchConfig`:

```python
hybrid_enabled: bool = True
hybrid_keyword_enabled: bool = True
hybrid_entity_lite_enabled: bool = True
hybrid_recency_enabled: bool = False

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

Validation:

- all weights must be `>= 0`;
- total weight must be `> 0`;
- candidate limits must be bounded, with hard maximum no higher than 500 for V1;
- timeouts must be positive and below total `search_timeout_ms`.

## 7. Security Requirements

Security is the first priority. Do not trade tenant isolation or input safety for recall quality.

### 7.1 Multi-Tenant Isolation

Every storage query path must include:

```python
routing = str(space_id)
query_filters = [{"term": {"space_id": routing}}]
query_filters.extend(filters.to_query_filters())
```

This applies to:

- semantic KNN search;
- keyword search;
- any future entity search;
- list fallback remains unchanged.

Tests must prove:

1. Data in `space_a` is not returned for `space_b`.
2. Same `space_id` but different `actor_id` / `session_id` respects filters.
3. JWT-derived filters from API are still applied.

### 7.2 Query Injection Prevention

Do not use OpenSearch `query_string` or raw string query composition with user input.

Forbidden:

```json
{"query_string": {"query": "<user query>"}}
```

Allowed:

```json
{"match": {"content": {"query": "<user query>", "operator": "or"}}}
```

Allowed:

```json
{"match_phrase": {"content": {"query": "<boost term>", "boost": 4.0}}}
```

Reason: `query_string` interprets user input as syntax. `match` treats input as analyzed text.

### 7.3 Sensitive Data Logging

Do not log:

- raw query;
- raw memory content;
- full matched terms if they may include personal data;
- JWT;
- API keys;
- Maas tokens;
- OpenSearch credentials;
- request/response body from OpenSearch.

Allowed logs:

```text
space_id
query_length
query_hash
top_k
strategy_type
has_actor_filter
has_session_filter
semantic_count
keyword_count
candidate_count
duration_ms
degraded_reason
```

### 7.4 Resource Protection

Enforce limits before remote calls:

- query length <= `max_query_length`;
- top_k <= `max_top_k`;
- keyword count <= `hybrid_max_keywords`;
- entity count <= `hybrid_max_entities`;
- term length <= `hybrid_max_term_length`;
- fusion candidates <= `hybrid_max_fusion_candidates`;
- rerank candidates <= `hybrid_rerank_candidate_limit`.

If limits are exceeded:

- reject invalid user request where existing API semantics support rejection;
- truncate internal keyword/entity lists safely;
- log only counts, not raw values.

### 7.5 External Error Handling

Single recall path failure must degrade when possible:

```text
semantic ok, keyword fail -> return semantic-based results, degraded=true
semantic fail, keyword ok -> return keyword-based results, degraded=true
semantic fail, keyword fail -> SearchError
rerank fail -> return fusion ranking
```

Never expose raw OpenSearch exceptions to API callers.

## 8. Performance Requirements

### 8.1 Concurrency

Semantic and keyword recall should run concurrently.

Use `asyncio.gather(..., return_exceptions=True)` or an equivalent helper. Do not serialize:

```text
semantic recall -> wait -> keyword recall
```

### 8.2 Candidate Limits

Default:

```text
semantic_k = min(max(top_k * 4, 40), 200)
keyword_k = min(max(top_k * 4, 40), 200)
fusion_candidate_limit = 300
rerank_candidate_limit = min(max(top_k * 3, 20), 80)
```

### 8.3 OpenSearch Efficiency

All OpenSearch queries must:

- use `routing=str(space_id)`;
- include `space_id` filter;
- exclude `embedding` from `_source`;
- use bounded `size`;
- avoid all-shard fan-out.

### 8.4 Rerank Efficiency

Rerank only the top fused candidates, never all raw candidates.

```text
fusion top N -> rerank -> final top_k
```

### 8.5 Timeouts

Apply per-path timeouts:

```text
semantic vector search timeout
keyword search timeout
rerank timeout
```

Timeout should degrade a path rather than blocking the full request beyond `search_timeout_ms`.

## 9. Query Analysis

### 9.1 Rules

`QueryAnalyzer` must be deterministic and local. Do not call LLM.

Steps:

1. Trim query.
2. Collapse repeated whitespace.
3. Preserve original casing for phrase search.
4. Generate `query_hash`.
5. Extract keywords.
6. Extract entity-like terms.
7. Classify rough intent.

### 9.2 Keyword Extraction

Extract:

- code-like tokens: `XJ-91`, `JIRA-123`, `deepseek-v3.2`, `bge-m3`;
- email or handle-like tokens, but never log raw values;
- English words excluding a small stopword set;
- short Chinese phrases as fallback, without advanced segmentation in V1.

Limits:

```text
max_keywords = 16
max_keyword_length = 64
```

### 9.3 Entity-Lite Extraction

V1 entity-lite only supports simple high-value patterns:

```text
code_or_identifier: XJ-91, JIRA-123, deepseek-v3.2
email_or_handle: user@example.com, @someone
date_like: 2026-07-01
capitalized_name: Tokyo, Stanford
number: 12345
```

No external NLP dependency.

### 9.4 Intent

Optional V1 rough intent:

```text
current: 现在, currently, latest, now
past: 以前, 之前, before, previously
future: 明天, 下周, upcoming, next
general: default
```

V1 only uses intent for small recency behavior. If uncertain, return `general`.

## 10. Recall Design

### 10.1 Semantic Recall

Use existing embedding and vector search:

```python
vector = await embedding_service.embed(query)
response = await vector_store.search(space_id, vector.values, top_k=semantic_k, filters=filters)
```

Convert to:

```python
RecallResult(signal=SearchSignal.SEMANTIC, raw_score=result.score, rank=i + 1)
```

### 10.2 Keyword Recall

Use new storage method:

```python
response = await vector_store.keyword_search(
    space_id=space_id,
    query=analysis.normalized_query,
    top_k=keyword_k,
    filters=filters,
    boost_terms=boost_terms,
)
```

`boost_terms`:

```text
analysis.keywords + [entity.text for entity in analysis.entities]
```

Then limit and sanitize according to config.

### 10.3 Entity-Lite Signal

No separate OpenSearch query in V1.

Compute entity score during fusion by checking whether candidate content contains entity text.

Rules:

- case-insensitive for ASCII terms;
- exact substring match only;
- entity terms limited before use;
- do not log candidate content or raw entity term by default.

### 10.4 Recency-Lite Signal

Default disabled in V1: `SEARCH_HYBRID_RECENCY_ENABLED=false`.

If enabled:

```text
intent=past -> recency_score = 0
intent=general/current -> small boost based on created_at
intent=future -> no special boost until event_time exists
```

Suggested scores:

```text
age <= 1 day    -> 1.00
age <= 7 days   -> 0.80
age <= 30 days  -> 0.50
age <= 180 days -> 0.20
else            -> 0.00
```

Keep weight small, default `0.05`.

## 11. CSSAdapter Keyword Search

### 11.1 DSL

Implement using structured OpenSearch DSL:

```python
query_filters = [{"term": {"space_id": routing}}]
if filters is not None:
    query_filters.extend(filters.to_query_filters())

should_clauses = []
for term in boost_terms:
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

Call:

```python
await self._client.search(index=self._index_name, body=body, routing=routing)
```

### 11.2 Empty Keyword Behavior

If normalized query is valid but extracted keywords are empty, keyword search can still run with `match` on normalized query.

Skip keyword recall only when:

```text
len(normalized_query) < 2 and no entity-like terms
```

### 11.3 Errors

Wrap OpenSearch errors:

```python
raise SearchError(f"Keyword search failed on index {self._index_name}: {exc}", space_id=space_id)
```

Do not include query text in the error.

## 12. Score Fusion

### 12.1 Merge

Merge by `SearchResult.id`.

If same id appears in multiple recall paths:

- preserve one `SearchResult`;
- store each path score under its `SearchSignal`;
- preserve best rank per signal.

### 12.2 Normalize

Do not directly add KNN and BM25 raw scores.

Semantic:

- if scores are already in `[0, 1]`, clamp to `[0, 1]`;
- otherwise use per-list min-max.

Keyword:

- use per-list min-max;
- if single keyword result, normalized score is `1.0`.

Entity:

```python
entity_norm = matched_entity_count / max(1, total_entity_count)
entity_norm = min(1.0, entity_norm)
```

Recency:

- use recency score from section 10.4.

### 12.3 Weights

Default:

```text
semantic_weight = 0.55
keyword_weight  = 0.25
entity_weight   = 0.15
recency_weight  = 0.05
```

Formula:

```python
fused_score = (
    semantic_weight * semantic_norm
    + keyword_weight * keyword_norm
    + entity_weight * entity_norm
    + recency_weight * recency_norm
)
```

Missing signal = 0.

### 12.4 Min Score

Hybrid mode uses `hybrid_default_min_score` when request `min_score` is not set.

Vector-only mode keeps current `default_min_score`.

If caller explicitly passes `min_score`, interpret it according to active mode. Add a code comment because this differs from vector-only semantics.

### 12.5 Stable Sorting

Sort by:

```text
fused_score desc
then best signal rank asc
then updated_at desc
then id asc
```

Keep tie-breaking deterministic for tests.

## 13. Rerank

Use existing `RerankService`.

Input candidates:

```text
top min(max(top_k * 3, 20), hybrid_rerank_candidate_limit) by fused_score
```

If rerank succeeds:

- return reranked order;
- keep result count bounded by `top_k`.

If rerank fails:

- log `hybrid_search_rerank_failed`;
- return fusion order.

Do not call rerank with raw candidates from all recall paths before fusion.

## 14. Failure And Degradation

### 14.1 Degradation Matrix

| Semantic | Keyword | Rerank | Behavior |
|---|---|---|---|
| success | success | success | full hybrid |
| success | success | fail | fusion fallback |
| success | fail | any | semantic degraded |
| fail | success | any | keyword degraded |
| fail | fail | any | SearchError |

### 14.2 Degraded Logging

Log:

```text
hybrid_search_degraded
reason=keyword_failed | semantic_failed | rerank_failed | timeout
query_hash
space_id
```

Do not log raw query.

### 14.3 Fallback Switch

If hybrid causes production issue:

```text
SEARCH_HYBRID_ENABLED=false
```

should restore current vector-only behavior without code rollback.

## 15. Observability

### 15.1 Logs

Required events:

```text
hybrid_search_start
hybrid_search_query_analyzed
hybrid_search_recall_completed
hybrid_search_recall_failed
hybrid_search_fusion_completed
hybrid_search_rerank_completed
hybrid_search_degraded
hybrid_search_completed
```

Recommended fields:

```text
space_id
query_hash
query_length
top_k
strategy_type
has_actor_filter
has_session_filter
semantic_count
keyword_count
candidate_count
result_count
duration_ms
degraded
```

### 15.2 Metrics

If metrics infra is available, add:

```text
memory_search_hybrid_duration_ms
memory_search_semantic_recall_duration_ms
memory_search_keyword_recall_duration_ms
memory_search_fusion_duration_ms
memory_search_rerank_duration_ms
memory_search_degraded_total
memory_search_candidates_total
```

If metrics infra is not ready, logs are mandatory and metrics can be follow-up.

## 16. Implementation Plan

### Step 1: Add Models

Create:

```text
src/core/application/search/search_models.py
```

Add:

- `SearchSignal`
- `QueryEntity`
- `QueryAnalysis`
- `RecallResult`
- `HybridSearchCandidate`

Keep these dependency-light. Avoid importing adapters.

### Step 2: Add QueryAnalyzer

Create:

```text
src/core/application/search/query_analysis.py
```

Implement:

```python
class QueryAnalyzer:
    def __init__(self, config: SearchConfig) -> None: ...
    def analyze(self, query: str) -> QueryAnalysis: ...
```

Tests:

- empty query rejected or handled before analyzer;
- whitespace collapse;
- `XJ-91`;
- `deepseek-v3.2`;
- Chinese query;
- query with `*) OR *:*`;
- max keywords/entities enforced.

### Step 3: Add ScoreFusionService

Create:

```text
src/core/application/search/score_fusion.py
```

Implement:

```python
class ScoreFusionService:
    def merge_and_score(
        self,
        analysis: QueryAnalysis,
        recalls: list[RecallResult],
        min_score: float,
    ) -> list[HybridSearchCandidate]:
        ...
```

Tests:

- semantic only;
- keyword only;
- semantic + keyword same id;
- entity boost;
- min_score filtering;
- deterministic tie-break.

### Step 4: Extend Port

Modify:

```text
src/core/ports/memory_vector_store.py
src/core/application/services/memory_vector_store_service.py
```

Add `keyword_search()`.

Service method should pass through to port. No DSL in service.

### Step 5: Implement CSSAdapter.keyword_search

Modify:

```text
src/core/adapters/css_adapter.py
```

Requirements:

- `_require_index_ready()`;
- routing;
- `space_id` filter;
- `filters.to_query_filters()`;
- `match` / `match_phrase`;
- no `query_string`;
- exclude `embedding`;
- `SearchError` wrapper;
- parse via existing `_parse_search_response()`.

Tests should assert generated query shape if helper extraction is feasible. If not, use integration-style test with a fake client capturing body.

### Step 6: Add HybridMemorySearch

Create:

```text
src/core/application/search/hybrid_search.py
```

Implement:

```python
class HybridMemorySearch:
    async def search(
        self,
        query: str,
        space_id: UUID,
        top_k: int,
        min_score: float | None,
        filters: SearchFilters | None,
    ) -> SearchResponse:
        ...
```

This class receives:

- `EmbeddingService`
- `MemoryVectorStoreService`
- `RerankService`
- `SearchConfig`
- `QueryAnalyzer`
- `ScoreFusionService`

### Step 7: Wire MemorySearch Facade

Modify:

```text
src/core/application/memory_search.py
```

Options:

1. Make existing `MemorySearch` call hybrid branch when enabled.
2. Or make `MemorySearch` a facade delegating to `VectorMemorySearch` / `HybridMemorySearch`.

Prefer minimal safe change:

```python
if self._config.hybrid_enabled:
    return await self._hybrid_search(...)
return await self._vector_search(...)
```

But keep helper methods small.

### Step 8: Add Config

Modify:

```text
src/core/config.py
.env
```

Default:

```text
SEARCH_HYBRID_ENABLED=true
SEARCH_HYBRID_KEYWORD_ENABLED=true
SEARCH_HYBRID_ENTITY_LITE_ENABLED=true
SEARCH_HYBRID_RECENCY_ENABLED=false
```

If production risk is high, set `SEARCH_HYBRID_ENABLED=false` in `.env` and enable only for testing.

### Step 9: Add Tests

Add unit tests for:

- QueryAnalyzer;
- ScoreFusionService;
- CSSAdapter keyword_search body;
- HybridMemorySearch degradation.

Add integration/behavior tests for:

- `XJ-91` exact token improves ranking;
- hybrid disabled equals vector-only path;
- keyword failure falls back to semantic;
- rerank failure falls back to fusion;
- two-space tenant isolation.

## 17. Pseudocode

### 17.1 Hybrid Search

```python
async def search(self, query, space_id, top_k, min_score, filters):
    analysis = self._query_analyzer.analyze(query)
    effective_min_score = self._effective_min_score(min_score)

    tasks = {
        SearchSignal.SEMANTIC: asyncio.create_task(
            self._semantic_recall(analysis, space_id, top_k, filters)
        )
    }
    if self._config.hybrid_keyword_enabled:
        tasks[SearchSignal.KEYWORD] = asyncio.create_task(
            self._keyword_recall(analysis, space_id, top_k, filters)
        )

    recalls = []
    degraded_reasons = []
    for signal, task in tasks.items():
        try:
            recalls.extend(await task)
        except Exception as exc:
            degraded_reasons.append(signal.value)
            logger.warning("hybrid_search_recall_failed", signal=signal.value, exc_info=True)

    if not recalls:
        raise SearchError("all recall paths failed", space_id=space_id)

    candidates = self._fusion.merge_and_score(analysis, recalls, effective_min_score)
    final_results = await self._rerank_or_fallback(query, candidates, top_k)
    return SearchResponse(results=final_results[:top_k], total=len(final_results[:top_k]))
```

### 17.2 Semantic Recall

```python
async def _semantic_recall(self, analysis, space_id, top_k, filters):
    vector = await self._embedding_service.embed(analysis.normalized_query)
    response = await self._vector_store.search(
        space_id,
        vector.values,
        top_k=self._semantic_k(top_k),
        filters=filters,
    )
    return [
        RecallResult(result=r, signal=SearchSignal.SEMANTIC, raw_score=r.score, rank=i + 1)
        for i, r in enumerate(response.results)
    ]
```

### 17.3 Keyword Recall

```python
async def _keyword_recall(self, analysis, space_id, top_k, filters):
    boost_terms = list(analysis.keywords)
    boost_terms.extend(entity.text for entity in analysis.entities)
    boost_terms = self._limit_terms(boost_terms)

    response = await self._vector_store.keyword_search(
        space_id,
        analysis.normalized_query,
        top_k=self._keyword_k(top_k),
        filters=filters,
        boost_terms=boost_terms,
    )
    return [
        RecallResult(result=r, signal=SearchSignal.KEYWORD, raw_score=r.score, rank=i + 1)
        for i, r in enumerate(response.results)
    ]
```

## 18. Test Plan

### 18.1 Unit Tests

`QueryAnalyzer`

- `XJ-91 有什么注意事项` extracts `XJ-91`.
- `deepseek-v3.2` extracts model-like identifier.
- malicious-looking query does not throw and is not interpreted as DSL.
- keyword/entity limits are enforced.
- raw query is not included in log fields.

`ScoreFusionService`

- semantic-only candidate gets score.
- keyword-only candidate gets score.
- candidate present in both paths ranks above equivalent single-signal candidate.
- entity match increases score.
- min_score filters low candidates.
- sorting is deterministic.

`CSSAdapter.keyword_search`

- includes routing.
- includes `space_id` filter.
- includes provided filters.
- excludes `embedding`.
- uses `match` / `match_phrase`.
- does not use `query_string`.

`HybridMemorySearch`

- semantic success + keyword failure returns semantic degraded.
- semantic failure + keyword success returns keyword degraded.
- both fail raises SearchError.
- rerank failure returns fusion order.

### 18.2 Integration Tests

Use a small test index or fake adapter.

Case 1: exact token

```text
memory A: 用户正在推进 XJ-91 项目，要求暂不公开。
memory B: 用户关注向量检索性能优化。
query: XJ-91 有什么注意事项？
expected: A ranked first
```

Case 2: tenant isolation

```text
space A memory: XJ-91 secret
space B memory: XJ-91 public
query in space A
expected: only space A memory
```

Case 3: actor/session filters

```text
same space, different actor/session
expected: search respects filters
```

Case 4: fallback switch

```text
SEARCH_HYBRID_ENABLED=false
expected: keyword_search not called
```

### 18.3 Security Tests

Queries:

```text
*) OR *:* OR content:*
content:(secret)
" OR "1"="1
```

Expected:

- no raw query_string DSL;
- no cross-tenant results;
- no OpenSearch syntax error leaked to caller;
- no raw query/content in logs.

### 18.4 Performance Tests

Measure:

- vector-only p50/p95;
- hybrid p50/p95;
- keyword recall duration;
- fusion duration;
- rerank duration;
- candidate counts.

Validate:

- top_k=10 and top_k=100 stay within configured limits;
- keyword timeout degrades and does not block total request;
- rerank candidate count <= configured limit.

## 19. Acceptance Criteria

Functional:

- Hybrid search returns valid `SearchResponse`.
- Hybrid disabled preserves current vector-only behavior.
- Keyword recall improves exact-token scenario.
- Rerank failure does not fail whole search.
- Keyword recall failure does not fail whole search if semantic succeeds.

Security:

- Every recall path uses `space_id` filter and routing.
- No `query_string` with user input.
- No raw query/content/token in logs.
- Tenant and actor/session isolation tests pass.

Performance:

- Candidate counts are bounded.
- Rerank input is bounded.
- Per-path timeout works.
- No embedding field returned from OpenSearch search source.

Maintainability:

- Fusion logic is unit tested separately.
- Query analysis is unit tested separately.
- CSSAdapter does not contain application-level fusion policy.
- Config names are explicit and documented.

Operational:

- Logs show recall counts, durations, degraded reason.
- `SEARCH_HYBRID_ENABLED=false` is a safe rollback.

## 20. Rollout And Rollback

### 20.1 Rollout

Recommended sequence:

1. Merge code with `SEARCH_HYBRID_ENABLED=false`.
2. Run unit and integration tests.
3. Enable in staging.
4. Run exact-token benchmark and normal semantic benchmark.
5. Check logs for degraded rate and latency.
6. Enable in production with small traffic or controlled environment.

### 20.2 Rollback

Immediate rollback:

```text
SEARCH_HYBRID_ENABLED=false
```

No index rollback should be required in V1.

## 21. Recommended Commit Split

1. `search models and config`
2. `query analyzer`
3. `score fusion service`
4. `vector store keyword search contract`
5. `css keyword search implementation`
6. `hybrid search orchestration`
7. `tests and docs`

Avoid one giant commit.

## 22. Implementation Notes For AI Agent

When implementing:

1. Read current files before editing.
2. Keep changes scoped to search path.
3. Preserve public API response schema.
4. Prefer small classes with explicit names.
5. Add tests before or alongside behavior changes.
6. Never remove existing vector-only code path.
7. Never weaken routing/filter logic.
8. Never log raw query or memory content.
9. Use existing project logging style.
10. If a design point conflicts with existing code, preserve security and compatibility first.

## 23. Future Work

After V1 is stable:

1. Add `content.keyword` / `content.ngram` / Chinese analyzer through index migration.
2. Add entity index:

```text
entity -> linked_memory_ids
```

3. Add temporal fields:

```text
event_time
valid_from
valid_until
temporal_type
```

4. Add internal-only search explain.
5. Add strategy-aware retrieval policy.
6. Add offline evaluation set for memory search quality.

## 24. Source Basis

This design is based on:

- Current memory-core search implementation.
- Mem0 public direction: semantic + BM25 + entity matching + score fusion + rerank.
- Agent instruction best practices: clear goal, non-goals, contracts, safety constraints, implementation steps, tests, acceptance criteria.

Primary local files:

```text
src/core/application/memory_search.py
src/core/adapters/css_adapter.py
src/core/ports/memory_vector_store.py
src/core/domain/types/vector_operations.py
src/core/api/router.py
src/core/api/schemas.py
src/core/config.py
```
