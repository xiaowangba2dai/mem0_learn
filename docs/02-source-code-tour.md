# Mem0 源码端到端探索：知识图谱从 v2 到 v3

> 逐文件、逐方法、逐行的源码分析
> 聚焦知识图谱/实体系统的演进

---

## 目录

- [全局架构总览](#全局架构总览)
- [第一部分：v2 图记忆系统完整源码分析](#第一部分v2-图记忆系统完整源码分析)
  - [graph_memory.py — MemoryGraph 类](#11-graph_memorypy--memorygraph-类)
  - [graphs/tools.py — LLM 工具定义](#12-graphstoolspy--llm-工具定义)
  - [graphs/utils.py — 图相关 Prompt](#13-graphsutilspy--图相关-prompt)
  - [v2 main.py 中的图记忆集成](#14-v2-mainpy-中的图记忆集成)
- [第二部分：v3 实体链接系统完整源码分析](#第二部分v3-实体链接系统完整源码分析)
  - [entity_extraction.py — spaCy 实体提取](#21-entity_extractionpy--spacy-实体提取)
  - [scoring.py — 混合检索评分](#22-scoringpy--混合检索评分)
  - [lemmatization.py — 词形还原](#23-lemmatizationpy--词形还原)
  - [main.py 中的实体存储与链接](#24-mainpy-中的实体存储与链接)
  - [main.py 中的混合搜索](#25-mainpy-中的混合搜索)
- [第三部分：支撑系统源码分析](#第三部分支撑系统源码分析)
  - [factory.py — 工厂模式](#31-factorypy--工厂模式)
  - [configs/base.py — 配置系统](#32-configsbasepy--配置系统)
  - [vector_stores/qdrant.py — Qdrant 向量库](#33-vector_storesqdrantpy--qdrant-向量库)
  - [memory/storage.py — SQLite 历史管理](#34-memorystoragepy--sqlite-历史管理)
  - [configs/prompts.py — Prompt 演进](#35-configspromptspy--prompt-演进)
- [v2 → v3 架构决策对比](#v2--v3-架构决策对比)

---

## 全局架构总览

### 项目文件结构

```
mem0/
├── mem0/                           # 核心 SDK
│   ├── memory/                     # 记忆核心模块
│   │   ├── main.py                 # Memory 类 — v3 编排中心 (3496行)
│   │   ├── base.py                 # MemoryBase 抽象类
│   │   ├── storage.py              # SQLiteManager 历史管理 (348行)
│   │   ├── utils.py                # 工具函数 (305行)
│   │   └── setup.py / telemetry.py
│   │
│   ├── configs/                    # 配置系统
│   │   ├── base.py                 # MemoryConfig (82行)
│   │   ├── prompts.py              # ⭐ 所有 Prompt (1063行)
│   │   ├── enums.py                # MemoryType 枚举
│   │   ├── llms/                   # LLM 配置 (anthropic, openai, ...)
│   │   ├── embeddings/             # 嵌入配置
│   │   ├── vector_stores/          # 向量库配置
│   │   └── rerankers/              # 重排序配置
│   │
│   ├── vector_stores/              # 向量数据库适配器 (23种)
│   │   ├── base.py                 # VectorStoreBase 抽象类 (101行)
│   │   ├── qdrant.py               # ⭐ Qdrant 实现 (562行)
│   │   ├── pgvector.py / milvus.py / chroma.py / ...
│   │   └── configs.py
│   │
│   ├── llms/                       # LLM 适配器 (16种)
│   │   ├── base.py                 # LLMBase 抽象类
│   │   ├── anthropic.py            # Anthropic 适配器 (113行)
│   │   ├── openai.py / ollama.py / ...
│   │   └── configs.py
│   │
│   ├── embeddings/                 # 嵌入模型适配器 (12种)
│   │   ├── base.py / openai.py / huggingface.py / fastembed.py / ...
│   │   └── configs.py
│   │
│   ├── utils/                      # 工具模块
│   │   ├── entity_extraction.py    # ⭐ v3 实体提取 (358行)
│   │   ├── scoring.py              # ⭐ v3 评分融合 (140行)
│   │   ├── lemmatization.py        # ⭐ v3 BM25 词形还原 (51行)
│   │   ├── spacy_models.py         # spaCy 模型加载
│   │   └── factory.py              # ⭐ 工厂模式 (268行)
│   │
│   ├── reranker/                   # 重排序器
│   └── exceptions.py
│
├── [v2 已删除文件 — 从 git 历史恢复]
│   ├── memory/graph_memory.py      # v2 MemoryGraph 类 (744行)
│   ├── graphs/tools.py             # v2 LLM tool 定义 (371行)
│   └── graphs/utils.py             # v2 图 Prompt (97行)
│
├── server/                         # FastAPI 服务器
├── mem0-ts/                        # TypeScript SDK
└── tests/
```

### 数据流全景

```
                        Mem0 数据流全景图

用户对话 ──→ add() ──→ Phase 0: 上下文收集 (SQLite)
                    ──→ Phase 1: 检索已有记忆 (向量库)
                    ──→ Phase 2: LLM 提取 (LLM API)
                    ──→ Phase 3: 批量嵌入 (嵌入模型)
                    ──→ Phase 4-5: 哈希去重 (CPU)
                    ──→ Phase 6: 批量写入 (向量库)
                    ──→ Phase 7: 实体链接 (spaCy + 实体集合)
                    ──→ Phase 8: 保存历史 (SQLite)

用户查询 ──→ search() ──→ Step 1: 预处理 (词形还原 + 实体提取)
                       ──→ Step 2: 嵌入查询 (嵌入模型)
                       ──→ Step 3: 语义搜索 (向量库)
                       ──→ Step 4: BM25 搜索 (向量库 sparse)
                       ──→ Step 5: 归一化 BM25 (Sigmoid)
                       ──→ Step 6: 实体增强 (实体集合)
                       ──→ Step 7: 构建候选集
                       ──→ Step 8: 分数融合 (score_and_rank)
                       ──→ Step 9: 格式化返回
```

---

## 第一部分：v2 图记忆系统完整源码分析

> 以下代码来自 git commit `59880a6f^` (v3 迁移前的最后一个版本)
> 通过 `git show 59880a6f^:mem0/memory/graph_memory.py` 恢复

### 1.1 graph_memory.py — MemoryGraph 类

**文件**: `mem0/memory/graph_memory.py` (744行)

这是 v2 中图记忆的核心实现，基于 Neo4j 图数据库。

#### 1.1.1 初始化 `__init__`

```python
class MemoryGraph:
    def __init__(self, config):
        self.config = config
        self.graph = Neo4jGraph(
            url=self.config.graph_store.config.url,
            username=self.config.graph_store.config.username,
            password=self.config.graph_store.config.password,
            database=self.config.graph_store.config.database,
            refresh_schema=False,
            driver_config={"notifications_min_severity": "OFF"},
        )
```

**关键点:**
- 直接使用 `langchain_neo4j` 的 `Neo4jGraph` 封装
- 需要用户提供 Neo4j 的 url/username/password
- `refresh_schema=False` 避免每次启动都重新加载 schema（性能优化）
- `notifications_min_severity="OFF"` 关闭 Neo4j 的提示通知

```python
        self.embedding_model = EmbedderFactory.create(
            self.config.embedder.provider, self.config.embedder.config,
            self.config.vector_store.config
        )
        self.node_label = ":`__Entity__`" if self.config.graph_store.config.base_label else ""
```

- 图节点使用 `__Entity__` 作为基础标签（base_label=True 时）
- 反引号包裹防止与 Neo4j 保留字冲突

```python
        if self.config.graph_store.config.base_label:
            try:
                self.graph.query(
                    f"CREATE INDEX entity_single IF NOT EXISTS "
                    f"FOR (n {self.node_label}) ON (n.user_id)"
                )
            except Exception:
                pass
            try:
                self.graph.query(
                    f"CREATE INDEX entity_composite IF NOT EXISTS "
                    f"FOR (n {self.node_label}) ON (n.name, n.user_id)"
                )
            except Exception:
                pass
```

**索引策略:**
- 单字段索引: `user_id`（最常用的过滤字段）
- 复合索引: `(name, user_id)`（实体名 + 用户 ID，最精确的查找）
- 复合索引仅 Neo4j Enterprise 支持，Community 版会失败（被 try/except 忽略）

```python
        self.llm_provider = "openai"
        if self.config.llm and self.config.llm.provider:
            self.llm_provider = self.config.llm.provider
        if self.config.graph_store and self.config.graph_store.llm \
                and self.config.graph_store.llm.provider:
            self.llm_provider = self.config.graph_store.llm.provider
```

**LLM 提供商优先级:**
1. 图专属 LLM（`graph_store.llm.provider`）→ 最高优先级
2. 全局 LLM（`config.llm.provider`）
3. 默认 "openai"

```python
        self.threshold = self.config.graph_store.threshold \
            if hasattr(self.config.graph_store, 'threshold') else 0.7
```

- 节点匹配阈值默认 0.7（嵌入相似度）
- 高于此阈值认为是同一个实体，执行 MERGE
- 低于此阈值认为是新实体，执行 CREATE

#### 1.1.2 添加记忆到图 `add()`

```python
    def add(self, data, filters):
        entity_type_map = self._retrieve_nodes_from_data(data, filters)
        to_be_added = self._establish_nodes_relations_from_data(
            data, filters, entity_type_map
        )
        search_output = self._search_graph_db(
            node_list=list(entity_type_map.keys()), filters=filters
        )
        to_be_deleted = self._get_delete_entities_from_search_output(
            search_output, data, filters
        )
        deleted_entities = self._delete_entities(to_be_deleted, filters)
        added_entities = self._add_entities(to_be_added, filters, entity_type_map)
        return {"deleted_entities": deleted_entities, "added_entities": added_entities}
```

**流程图:**

```
用户文本: "Alice 在海底捞和老王吃了火锅"
    │
    ├─ Step 1: _retrieve_nodes_from_data()
    │   LLM 调用 #1: 提取实体
    │   → {alice: person, 海底捞: restaurant, 老王: person}
    │
    ├─ Step 2: _establish_nodes_relations_from_data()
    │   LLM 调用 #2: 提取关系
    │   → [{alice, ate_at, 海底捞}, {alice, dined_with, 老王}]
    │
    ├─ Step 3: _search_graph_db()
    │   Cypher 查询: 在 Neo4j 中搜索这些实体的已有关系
    │   → 返回已有的 (source, relationship, destination) 三元组
    │
    ├─ Step 4: _get_delete_entities_from_search_output()
    │   LLM 调用 #3: 对比新旧关系，判断哪些旧关系应删除
    │   → 如果新信息与旧信息矛盾，标记删除
    │
    ├─ Step 5: _delete_entities()
    │   Cypher: 软删除标记的关系
    │
    └─ Step 6: _add_entities()
        Cypher: MERGE 节点 + CREATE 关系
```

**关键设计: 3 次 LLM 调用！** 这是 v2 最大的性能瓶颈。

#### 1.1.3 实体提取 `_retrieve_nodes_from_data()`

```python
    def _retrieve_nodes_from_data(self, data, filters):
        _tools = [EXTRACT_ENTITIES_TOOL]
        if self.llm_provider in ["azure_openai_structured", "openai_structured"]:
            _tools = [EXTRACT_ENTITIES_STRUCT_TOOL]
        search_results = self.llm.generate_response(
            messages=[
                {
                    "role": "system",
                    "content": f"You are a smart assistant who understands "
                               f"entities and their types in a given text. "
                               f"If user message contains self reference such "
                               f"as 'I', 'me', 'my' etc. then use "
                               f"{filters['user_id']} as the source entity. "
                               f"Extract all the entities from the text. "
                               f"***DO NOT*** answer the question itself if "
                               f"the given text is a question.",
                },
                {"role": "user", "content": data},
            ],
            tools=_tools,
        )
```

**LLM 指令分析:**
- 角色: "理解实体和类型的智能助手"
- 特殊规则: "I/me/my" → 替换为 `user_id`（如 "I like pizza" → 实体是 "alice"）
- 关键指令: "不要回答问题本身"（防止 LLM 把 "你喜欢什么?" 当成问题来回答）

**工具调用 (Tool Call):**

```python
        entity_type_map = {}
        for tool_call in search_results["tool_calls"]:
            if tool_call["name"] != "extract_entities":
                continue
            for item in tool_call.get("arguments", {}).get("entities", []):
                entity_type_map[item["entity"]] = item["entity_type"]
```

LLM 被要求调用 `extract_entities` 工具，返回:
```json
{
  "entities": [
    {"entity": "Alice", "entity_type": "person"},
    {"entity": "海底捞", "entity_type": "restaurant"},
    {"entity": "老王", "entity_type": "person"}
  ]
}
```

```python
        entity_type_map = {
            k.lower().replace(" ", "_"): v.lower().replace(" ", "_")
            for k, v in entity_type_map.items()
        }
```

**实体标准化:**
- 全部转小写
- 空格替换为下划线
- 例: "John Smith" → "john_smith"

#### 1.1.4 关系提取 `_establish_nodes_relations_from_data()`

```python
    def _establish_nodes_relations_from_data(self, data, filters, entity_type_map):
        user_identity = f"user_id: {filters['user_id']}"
        if filters.get("agent_id"):
            user_identity += f", agent_id: {filters['agent_id']}"
```

```python
        if self.config.graph_store.custom_prompt:
            system_content = EXTRACT_RELATIONS_PROMPT.replace("USER_ID", user_identity)
            system_content = system_content.replace(
                "CUSTOM_PROMPT", f"4. {self.config.graph_store.custom_prompt}"
            )
            messages = [
                {"role": "system", "content": system_content},
                {"role": "user", "content": data},
            ]
        else:
            messages = [
                {"role": "system", "content": system_content},
                {"role": "user", "content": f"List of entities: "
                                            f"{list(entity_type_map.keys())}. "
                                            f"\n\nText: {data}"},
            ]
```

**两种模式:**
1. **自定义 Prompt 模式**: 直接使用用户提供的额外指令
2. **默认模式**: 把提取到的实体列表 + 原始文本一起发给 LLM

LLM 输出:
```json
{
  "entities": [
    {"source": "alice", "relationship": "ate_at", "destination": "海底捞"},
    {"source": "alice", "relationship": "dined_with", "destination": "老王"}
  ]
}
```

#### 1.1.5 图数据库搜索 `_search_graph_db()`

```python
    def _search_graph_db(self, node_list, filters, top_k=100):
        result_relations = []
        node_props = ["user_id: $user_id"]
        if filters.get("agent_id"):
            node_props.append("agent_id: $agent_id")
```

**Cypher 查询模板:**

```cypher
MATCH (n {name: $name, user_id: $user_id})-[r]->(m {user_id: $user_id})
WHERE r.valid IS NULL OR r.valid = true
RETURN n.name AS source, type(r) AS relationship, m.name AS destination
LIMIT $limit
```

**查询逻辑:**
- `MATCH`: 从指定实体出发，找到所有出边
- `WHERE r.valid IS NULL OR r.valid = true`: 只返回"有效"的关系（软删除机制）
- `type(r)`: 返回关系类型（如 "ate_at", "dined_with"）
- 结果格式: `[{source, relationship, destination}, ...]`

#### 1.1.6 图搜索 `search()`

```python
    def search(self, query, filters, top_k=100):
        entity_type_map = self._retrieve_nodes_from_data(query, filters)
        search_output = self._search_graph_db(
            node_list=list(entity_type_map.keys()), filters=filters
        )
        if not search_output:
            return []
```

```python
        search_outputs_sequence = [
            [item["source"], item["relationship"], item["destination"]]
            for item in search_output
        ]
        bm25 = BM25Okapi(search_outputs_sequence)
        tokenized_query = query.split(" ")
        reranked_results = bm25.get_top_n(
            tokenized_query, search_outputs_sequence, n=5
        )
```

**BM25 重排序:**
- 使用 `rank_bm25` 库的 `BM25Okapi` 实现
- 把每个 `(source, relationship, destination)` 三元组当作一个"文档"
- 用查询的词来匹配三元组中的词
- 返回 top-5 最相关的三元组

```python
        search_results = []
        for item in reranked_results:
            search_results.append({
                "source": item[0],
                "relationship": item[1],
                "destination": item[2],
            })
        return search_results
```

**返回格式:**
```json
[
  {"source": "alice", "relationship": "dined_with", "destination": "老王"},
  {"source": "alice", "relationship": "ate_at", "destination": "海底捞"}
]
```

### 1.2 graphs/tools.py — LLM 工具定义

**文件**: `mem0/graphs/tools.py` (371行)

定义了 LLM 可以调用的"工具"（Tool Use / Function Calling）。

#### EXTRACT_ENTITIES_TOOL

```python
EXTRACT_ENTITIES_TOOL = {
    "type": "function",
    "function": {
        "name": "extract_entities",
        "description": "Extract entities and their types from the given text",
        "parameters": {
            "type": "object",
            "properties": {
                "entities": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "entity": {"type": "string"},
                            "entity_type": {"type": "string"}
                        },
                        "required": ["entity", "entity_type"]
                    }
                }
            }
        }
    }
}
```

**作用**: 指导 LLM 返回 `[{entity, entity_type}]` 格式

#### RELATIONS_TOOL

```python
RELATIONS_TOOL = {
    "type": "function",
    "function": {
        "name": "establish_relations",
        "description": "Establish relations between entities",
        "parameters": {
            "type": "object",
            "properties": {
                "entities": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source": {"type": "string"},
                            "relationship": {"type": "string"},
                            "destination": {"type": "string"}
                        },
                        "required": ["source", "relationship", "destination"]
                    }
                }
            }
        }
    }
}
```

**作用**: 指导 LLM 返回 `[{source, relationship, destination}]` 格式

#### DELETE_MEMORY_TOOL_GRAPH

```python
DELETE_MEMORY_TOOL_GRAPH = {
    "type": "function",
    "function": {
        "name": "delete_entities",
        "description": "Delete entities from graph that contradict",
        "parameters": {
            "type": "object",
            "properties": {
                "entities": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source": {"type": "string"},
                            "relationship": {"type": "string"},
                            "destination": {"type": "string"}
                        }
                    }
                }
            }
        }
    }
}
```

**作用**: 指导 LLM 识别矛盾关系并标记删除

### 1.3 graphs/utils.py — 图相关 Prompt

**文件**: `mem0/graphs/utils.py` (97行)

#### EXTRACT_RELATIONS_PROMPT

```python
EXTRACT_RELATIONS_PROMPT = """
You are a smart assistant that understands relationships between entities.

Given a list of entities and a text, extract all the relationships
between entities.

USER_ID is the user identifier for filtering.

Instructions:
1. Identify all relationships between entities in the text.
2. Use clear, descriptive relationship labels.
3. Only include relationships explicitly mentioned.
4. CUSTOM_PROMPT

Return the entities with their relationships.
"""
```

**分析:**
- `USER_ID` 占位符在运行时被替换为实际用户标识
- `CUSTOM_PROMPT` 占位符允许用户添加自定义规则
- 指令强调"只包含显式提到的关系"

#### get_delete_messages()

```python
def get_delete_messages(search_output, data, filters):
    return f"""You are a smart assistant that can identify if the new
    information contradicts existing relationships.

    Existing relationships:
    {format_entities(search_output)}

    New text: {data}

    Identify relationships that contradict the new information.
    Return the entities to delete.
    """
```

**作用**: 让 LLM 对比新旧关系，找出矛盾的部分

### 1.4 v2 main.py 中的图记忆集成

在 v2 的 `Memory.__init__()` 中:

```python
# v2 初始化（已删除的代码）
self.graph_memory = None
if self.config.graph_store.config and self.config.enable_graph:
    from mem0.memory.graph_memory import MemoryGraph
    self.graph_memory = MemoryGraph(self.config)
```

在 v2 的 `add()` 中:

```python
# v2 add() 的图记忆部分（已删除的代码）
if self.graph_memory:
    graph_result = self.graph_memory.add(data, filters)
    # graph_result = {"deleted_entities": [...], "added_entities": [...]}
```

在 v2 的 `search()` 中:

```python
# v2 search() 的图搜索部分（已删除的代码）
if self.graph_memory and enable_graph:
    graph_entities = self.graph_memory.search(query, filters, top_k)
    # 合并向量搜索结果和图搜索结果
    for mem in vector_results:
        mem["relations"] = []
        for ge in graph_entities:
            if ge["source"] in mem["memory"].lower():
                mem["relations"].append(ge)
```

**v2 的问题总结:**

| 问题 | 具体表现 |
|------|---------|
| 3+ 次 LLM 调用 | 提取实体 + 提取关系 + 判断删除 = 慢且贵 |
| 依赖 Neo4j | 需要额外部署和维护图数据库 |
| LLM 关系提取不准 | 经常提取错误方向的关系 |
| Cypher 写死 | 查询模式固定，不够灵活 |
| BM25 用 rank_bm25 库 | 与向量搜索的 BM25 不统一 |
| 返回 relations 字段 | 暴露了图结构细节给用户 |

---

## 第二部分：v3 实体链接系统完整源码分析

### 2.1 entity_extraction.py — spaCy 实体提取

**文件**: `mem0/utils/entity_extraction.py` (358行)

这是 v3 最核心的创新——用 spaCy NLP 替代 LLM 做实体提取。

#### 2.1.1 公共 API

```python
def extract_entities(text: str) -> List[Tuple[str, str]]:
    """
    返回: [(entity_type, entity_text), ...]
    类型: PROPER, QUOTED, COMPOUND, NOUN
    """
    from mem0.utils.spacy_models import get_nlp_full
    nlp = get_nlp_full()
    if nlp is None:
        return []  # spaCy 不可用 → 优雅降级
    doc = nlp(text)
    return _extract_entities_from_doc(doc)
```

```python
def extract_entities_batch(texts: List[str], batch_size: int = 32):
    """批量提取 — 用 spaCy 的 nlp.pipe() 比逐条快 5-10x"""
    nlp = get_nlp_full()
    if nlp is None:
        return [[] for _ in texts]
    results = []
    for doc in nlp.pipe(texts, batch_size=batch_size):
        results.append(_extract_entities_from_doc(doc))
    return results
```

**优化**: `nlp.pipe()` 是 spaCy 的批处理接口，内部用多线程+GPU 加速。

#### 2.1.2 PROPER 专有名词检测 (line 186-222)

```python
    # === PROPER NOUN SEQUENCES ===
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.text in _FORMATTING_MARKERS:
            i += 1
            continue
        is_cap = tok.text and tok.text[0].isupper()
        is_label = i + 1 < len(tokens) and tokens[i + 1].text == ":"
```

**算法:**
1. 遍历每个 token
2. 跳过了格式化标记（`*`, `-`, `#` 等）
3. 检查首字母大写 (`is_cap`)
4. 排除标签模式（如 `Note:` 不是实体）

```python
        if is_cap and not is_label and tok.pos_ in {"PROPN", "NOUN", "ADJ"}:
            seq = [(tok, i)]
            j = i + 1
            while j < len(tokens):
                t = tokens[j]
                if (t.text and t.text[0].isupper()) or t.text.lower() in {
                    "'s", "of", "the", "in", "and", "for", "at", "is",
                }:
                    seq.append((t, j))
                    j += 1
                else:
                    break
```

**贪心扩展:**
- 从首字母大写的词开始
- 向右扩展：只要下一个词也是大写，或者是功能词（of/the/in 等）
- 例: "Senior Engineer at Shopify" → 整体被捕获

```python
            # Strip trailing function words
            while seq and seq[-1][0].text.lower() in {
                "of", "the", "in", "and", "for", "at", "is", "'s"
            }:
                seq.pop()
```

**清理尾部:** 去掉末尾的功能词（"University of" → "University"）

```python
            if seq:
                has_mid_cap = any(
                    not _is_sentence_start(tokens, idx)
                    for (t, idx) in seq
                    if t.text[0].isupper()
                    and t.text.lower() not in {"'s", "of", "the", ...}
                )
                if has_mid_cap:
                    phrase = "".join(t.text_with_ws for (t, idx) in seq).strip()
                    if len(phrase) > 2:
                        entities.append(("PROPER", phrase))
```

**句首检测 (`has_mid_cap`):**
- 如果大写词**不在句首**，那它的大写是有意义的（专有名词）
- 如果大写词**在句首**，可能只是因为句首自动大写（"The weather" 的 "The"）
- `_is_sentence_start()` 检查 token 是否在句号/问号/感叹号/换行之后

#### 2.1.3 QUOTED 引号文本提取 (line 225-231)

```python
    # === QUOTED TEXT ===
    for m in re.finditer(r'"([^"]+)"', text):
        if len(m.group(1).strip()) > 2:
            entities.append(("QUOTED", m.group(1).strip()))
    for m in re.finditer(
        r"(?:^|[\s\(\[{,;])'([^']+)'(?=[\s\.,;:!?\)\]]|$)", text
    ):
        if len(m.group(1).strip()) > 2:
            entities.append(("QUOTED", m.group(1).strip()))
```

**两种引号模式:**
1. 双引号: `"The Last Dance"` → 直接匹配
2. 单引号: `'aerial yoga'` → 需要更复杂的正则，确保前面是空格/括号/逗号等

**为什么提取引号文本?**
- 书名、电影名通常用引号: "User read 'The Nightingale'"
- 特定术语: "User practices 'aerial yoga'"
- 这些往往是高价值的搜索关键词

#### 2.1.4 COMPOUND 复合名词短语检测 (line 233-307)

```python
    # === NOUN-NOUN COMPOUNDS ===
    for chunk in doc.noun_chunks:
        chunk_tokens = list(chunk)
        split_indices: list = []
        poss_splits: list = []
        for idx, tok in enumerate(chunk_tokens):
            if tok.dep_ == "case" and tok.text in {"'s", "’s", "'"}:
                split_indices.append(idx)
                poss_splits.append(idx)
```

**spaCy noun_chunks:**
- spaCy 自动识别名词短语（noun chunks）
- 例: "the user's cherry tomato garden" → 一个完整的 noun chunk

** Possessive 分割:**
- "user's garden" 被 `'s` 分成两部分: "user" 和 "garden"
- 只对"被拥有的"部分（garden）继续处理

```python
            head = next(
                (t for t in reversed(group) if t.pos_ in {"NOUN", "PROPN"}),
                None
            )
            if not head:
                continue
            head_generic = head.lemma_.lower() in _GENERIC_HEADS
```

**头部词检测:**
- 在 noun chunk 中找最后一个名词/专有名词作为"头部词"
- 检查头部词是否太通用（`_GENERIC_HEADS` = {"thing", "stuff", "time", ...}）
- 如果头部词太通用，需要检查是否有具体的修饰词

```python
            compound_toks = [t for t in content if t.dep_ == "compound"]
            adj_toks = [t for t in content if t.pos_ == "ADJ" or t.dep_ == "amod"]
            has_spec_adj = any(
                t.lemma_.lower() not in _NON_SPECIFIC_ADJ for t in adj_toks
            )
            if head_generic and not has_spec_adj and not compound_toks:
                continue  # 跳过：通用头部 + 无具体修饰 → 无价值
```

**过滤逻辑:**
- "a good experience" → head="experience"(通用) + adj="good"(非具体) → **跳过**
- "machine learning" → head="learning"(通用) + compound="machine" → **保留**
- "cherry tomato" → head="tomato"(不通用) → **保留**

```python
            if compound_toks:
                is_circ = any(
                    t.lemma_.lower() in _CIRCUMSTANTIAL_MODS
                    for t in compound_toks
                )
                if is_circ:
                    val = head.lemma_ if head.pos_ == "NOUN" else head.text
                    if len(val) > 2:
                        entities.append(("NOUN", val))
                else:
                    filtered = _strip_generic_ending(
                        [t for t in content
                         if not (t.pos_ == "ADJ"
                                 and t.lemma_.lower() in _NON_SPECIFIC_ADJ)]
                    )
                    if filtered:
                        phrase = _lemmatize_compound(filtered)
                        if len(phrase) > 3 and " " in phrase:
                            entities.append(("COMPOUND", phrase))
```

**环境修饰词处理 (`_CIRCUMSTANTIAL_MODS`):**
- "solo trip" → "solo" 是环境修饰词 → 只保留 "trip"（NOUN 类型）
- "cherry tomato" → "cherry" 不是环境修饰词 → 保留 "cherry tomato"（COMPOUND 类型）

**`_lemmatize_compound()`:** 对名词做词形还原
- "cherry tomatoes" → "cherry tomato"（复数→单数）
- "machine learning" → "machine learning"（不变）

**`_strip_generic_ending()`:** 去掉通用尾部
- "machine learning techniques" → "machine learning"（去掉 "techniques"）

#### 2.1.5 NOUN 单名词回退 (line 286-292)

当环境修饰词被检测到，且没有复合结构时，只保留头部名词:
```python
                if is_circ:
                    val = head.lemma_
                    if len(val) > 2:
                        entities.append(("NOUN", val))
```

#### 2.1.6 VERB 误标记回退 (line 309-324)

```python
    # === FALLBACK: Mis-tagged VERB heads ===
    processed = {e[1].lower() for e in entities if e[0] == "COMPOUND"}
    generic_verb_heads = _GENERIC_HEADS | {
        "find", "buy", "purchase", "sale", "deal", "trip", "visit"
    }
    for tok in doc:
        if tok.pos_ == "VERB" and tok.dep_ in {"pobj", "dobj", "nsubj"}:
            comps = sorted(collect_compounds(tok), key=lambda t: t.i)
            if comps:
                phrase_toks = comps if tok.lemma_.lower() in generic_verb_heads \
                    else comps + [tok]
                phrase = " ".join(t.text for t in phrase_toks)
                if phrase.lower() not in processed and len(phrase) > 3 \
                        and " " in phrase:
                    entities.append(("COMPOUND", phrase))
```

**解决的问题:** spaCy 有时把名词误标为动词
- "I went shopping" → "shopping" 被标为 VERB
- 但它有 compound 依赖（如 "grocery shopping"）
- 这个回退把它重新捕获为 COMPOUND 实体

#### 2.1.7 去重与清理 (line 326-357)

```python
    # === DEDUPLICATION & CLEANUP ===
    seen: set = set()
    deduped = []
    for t, e in entities:
        k = e.lower().strip()
        if k not in seen and len(k) > 2:
            seen.add(k)
            deduped.append((t, e))
```

**第一步: 大小写不敏感去重**

```python
    cleaned: List[Tuple[str, str]] = []
    for etype, etext in deduped:
        txt = re.sub(r"^\*+\s*|\s*\*+$", "", etext.strip())
        txt = re.sub(r"\s*:+$", "", txt)
        txt = re.sub(r"^\d+\s*\.\s*", "", txt)
        if not txt or len(txt) <= 2 or _has_artifacts(txt):
            continue
```

**第二步: 清理格式标记**
- 去掉 `*`、`**` markdown 标记
- 去掉末尾的 `:`
- 去掉开头的编号 `1.`

```python
    # Keep best type per entity (PROPER > COMPOUND > QUOTED > NOUN)
    type_pri = {"PROPER": 0, "COMPOUND": 1, "QUOTED": 2, "NOUN": 3, "VERB": 4}
    best: dict = {}
    for t, e in cleaned:
        k = e.lower()
        if k not in best or type_pri.get(t, 99) < type_pri.get(best[k][0], 99):
            best[k] = (t, e)
    deduped = list(best.values())
```

**第三步: 类型优先级去重**
- 同一个文本可能被多种方式捕获
- "Shopify" 可能是 PROPER（专有名词）也可能是 COMPOUND
- 保留优先级最高的类型: PROPER > COMPOUND > QUOTED > NOUN

```python
    # Remove entities that are substrings of longer entities
    all_lower = [e[1].lower() for e in deduped]
    return [(t, e) for t, e in deduped
            if not any(e.lower() != o and e.lower() in o for o in all_lower)]
```

**第四步: 子串清理**
- 如果同时有 "Senior Engineer" 和 "Engineer"，只保留长的
- 防止子实体干扰搜索评分

### 2.2 scoring.py — 混合检索评分

**文件**: `mem0/utils/scoring.py` (140行)

#### 2.2.1 BM25 参数选择

```python
def get_bm25_params(query: str, *, lemmatized=None) -> tuple:
    if lemmatized is None:
        lemmatized = lemmatize_for_bm25(query)
    num_terms = len(lemmatized.split()) if lemmatized else 1

    if num_terms <= 3:
        return 5.0, 0.7
    elif num_terms <= 6:
        return 7.0, 0.6
    elif num_terms <= 9:
        return 9.0, 0.5
    elif num_terms <= 15:
        return 10.0, 0.5
    else:
        return 12.0, 0.5
```

**设计思路:**
- `midpoint`: BM25 原始分数在 midpoint 时归一化为 0.5
- 查询词越多 → BM25 原始分数越高 → midpoint 要调大
- `steepness`: 控制 S 曲线的陡峭程度
- 短查询用更高的 steepness（0.7），让少量匹配就有高分

#### 2.2.2 Sigmoid 归一化

```python
def normalize_bm25(raw_score: float, midpoint: float, steepness: float) -> float:
    return 1.0 / (1.0 + math.exp(-steepness * (raw_score - midpoint)))
```

**数学直觉:**
```
raw_score = 0:      exp(-0.7 * (0-5))  = exp(3.5) ≈ 33  → 1/34 ≈ 0.03
raw_score = 5 (=mid): exp(0) = 1                       → 1/2  = 0.50
raw_score = 10:     exp(-0.7 * (10-5)) = exp(-3.5) ≈ 0.03 → 1/1.03 ≈ 0.97
```

#### 2.2.3 分数融合 `score_and_rank()`

```python
ENTITY_BOOST_WEIGHT = 0.5

def score_and_rank(semantic_results, bm25_scores, entity_boosts,
                   threshold, top_k, explain=False):
    has_bm25 = bool(bm25_scores)
    has_entity = bool(entity_boosts)

    max_possible = 1.0
    if has_bm25:
        max_possible += 1.0
    if has_entity:
        max_possible += ENTITY_BOOST_WEIGHT
```

**动态 max_possible:**

| 可用信号 | max_possible | 含义 |
|---------|-------------|------|
| 仅语义 | 1.0 | semantic 直接作为最终分数 |
| 语义+BM25 | 2.0 | 两个信号各占一半 |
| 语义+实体 | 1.5 | 实体最多贡献 0.5/1.5=33% |
| 语义+BM25+实体 | 2.5 | 三个信号按比例融合 |

```python
    for result in semantic_results:
        semantic_score = result.get("score") or 0.0
        if semantic_score < threshold:
            continue  # 语义分数低于阈值 → 直接排除
```

**关键设计:** threshold 只门控语义分数。BM25 和实体增强**不能**把低于阈值的候选"救回来"。

```python
        bm25_score = bm25_scores.get(mem_id_str, 0.0)
        entity_boost = entity_boosts.get(mem_id_str, 0.0)
        raw_combined = semantic_score + bm25_score + entity_boost
        combined = min(raw_combined / max_possible, 1.0)
```

**最终公式:** `final = min((semantic + bm25 + entity) / max_possible, 1.0)`

### 2.3 lemmatization.py — 词形还原

**文件**: `mem0/utils/lemmatization.py` (51行)

```python
def lemmatize_for_bm25(text: str) -> str:
    nlp = get_nlp_lemma()
    if nlp is None:
        return text  # spaCy 不可用 → 返回原文（降级）
    doc = nlp(text.lower())
    tokens = []
    for token in doc:
        if token.is_punct or token.is_stop:
            continue
        lemma = token.lemma_
        if lemma.isalnum():
            tokens.append(lemma)
        # -ing 双保留策略
        if token.text.endswith("ing") and token.text != lemma \
                and token.text.isalnum():
            tokens.append(token.text)
    return " ".join(tokens)
```

**-ing 双保留 Demo:**

```
输入: "I am attending a meeting"

逐词处理:
  "i"       → is_stop → 跳过
  "am"      → is_stop → 跳过
  "attending" → lemma="attend" → 添加 "attend"
               以 -ing 结尾且不同 → 也添加 "attending"
  "a"       → is_stop → 跳过
  "meeting"  → lemma="meeting" (名词不变) → 添加 "meeting"
               以 -ing 结尾但 lemma 相同 → 不重复添加

输出: "attend attending meeting"

效果:
  搜索 "attend"  → 匹配 "attend"
  搜索 "attending" → 匹配 "attending"
  两种形式都能找到这条记忆！
```

### 2.4 main.py 中的实体存储与链接

#### 2.4.1 实体集合的懒加载 (line 474-500)

```python
    @property
    def entity_store(self):
        if self._entity_store is None:
            entity_config = _safe_deepcopy_config(self.config.vector_store.config)
            entity_collection = _entity_collection_name(
                self.config.vector_store.provider, self.collection_name
            )
            if hasattr(entity_config, 'collection_name'):
                entity_config.collection_name = entity_collection
```

**集合命名:**
```python
def _entity_collection_name(provider, collection_name):
    separator = "-" if provider == "s3_vectors" else "_"
    return f"{collection_name}{separator}entities"
```

- 默认: `mem0_entities`
- S3 Vectors: `mem0-entities`（S3 不支持下划线）

```python
            # For Qdrant, share the existing client to avoid RocksDB lock
            if self.config.vector_store.provider == "qdrant" \
                    and hasattr(self.vector_store, "client"):
                if hasattr(entity_config, "client"):
                    entity_config.client = self.vector_store.client
```

**Qdrant 客户端共享:**
- Qdrant 本地模式用 RocksDB
- 两个客户端同时打开同一个 RocksDB 会导致锁冲突
- 解决方案: 共享同一个 `QdrantClient` 实例

#### 2.4.2 实体 Upsert `_upsert_entity()` (line 502-543)

```python
    def _upsert_entity(self, entity_text, entity_type, memory_id, filters):
        entity_embedding = self.embedding_model.embed(entity_text, "add")
        search_filters = {k: v for k, v in filters.items()
                         if k in ("user_id", "agent_id", "run_id") and v}
        existing = self.entity_store.search(
            query=entity_text, vectors=entity_embedding,
            top_k=1, filters=search_filters,
        )
```

```python
        if existing and existing[0].score >= 0.95:
            # 更新已有实体的 linked_memory_ids
            match = existing[0]
            payload = match.payload or {}
            linked_ids = payload.get("linked_memory_ids", [])
            if memory_id not in linked_ids:
                linked_ids.append(memory_id)
                payload["linked_memory_ids"] = linked_ids
                self.entity_store.update(
                    vector_id=match.id, vector=None, payload=payload,
                )
        else:
            # 创建新实体
            entity_id = str(uuid.uuid4())
            entity_payload = {
                "data": entity_text,
                "entity_type": entity_type,
                "linked_memory_ids": [memory_id],
                **search_filters,
            }
            self.entity_store.insert(
                vectors=[entity_embedding],
                ids=[entity_id],
                payloads=[entity_payload],
            )
```

**实体存储格式:**
```json
{
  "id": "ent-uuid",
  "data": "张明",
  "entity_type": "PROPER",
  "linked_memory_ids": ["mem-uuid-1", "mem-uuid-2", "mem-uuid-3"],
  "user_id": "zhangming"
}
```

**0.95 阈值的含义:**
- 嵌入相似度 >= 0.95 → 认为是同一个实体（只是表述略有不同）
- 例: "张明" 和 "张明" → 0.99 → 合并
- 例: "张明" 和 "张明明" → 0.80 → 分开

#### 2.4.3 实体清理 `_remove_memory_from_entity_store()` (line 545-598)

当一条记忆被删除或更新时，需要从实体集合中解除关联:

```python
    def _remove_memory_from_entity_store(self, memory_id, filters):
        if self._entity_store is None:
            return
        listed = self.entity_store.list(filters=search_filters, top_k=10000)
        for row in rows:
            payload = getattr(row, "payload", None) or {}
            linked = payload.get("linked_memory_ids", [])
            if memory_id not in linked:
                continue
            remaining = [mid for mid in linked if mid != memory_id]
            if not remaining:
                # 没有其他记忆链接到这个实体 → 删除实体
                self.entity_store.delete(vector_id=row.id)
            else:
                # 还有其他链接 → 更新（去掉当前 memory_id）
                new_payload = {**payload, "linked_memory_ids": remaining}
                self.entity_store.update(
                    vector_id=row.id, vector=vec, payload=new_payload,
                )
```

**清理逻辑:**
- 遍历所有实体（top_k=10000）
- 找到 `linked_memory_ids` 包含当前 `memory_id` 的实体
- 移除该 ID
- 如果列表变空 → 删除整个实体记录
- 否则 → 更新 payload

#### 2.4.4 Phase 7: 批量实体链接 (line 965-1054)

这是 add() 流水线中最复杂的阶段:

```python
        # Phase 7: Batch entity linking
        all_texts = [r[1] for r in records]
        all_entities = extract_entities_batch(all_texts)

        # 7a: 全局去重
        global_entities = {}
        for idx, (memory_id, text, embedding, payload) in enumerate(records):
            entities = all_entities[idx]
            for entity_type, entity_text in entities:
                key = entity_text.strip().lower()
                if key in global_entities:
                    global_entities[key][2].add(memory_id)
                else:
                    global_entities[key] = [entity_type, entity_text, {memory_id}]
```

**为什么全局去重?**
- 3 条新记忆可能都提到 "张明"
- 不全局去重 → 3 次搜索 + 3 次更新
- 全局去重 → 1 次搜索 + 1 次更新（memory_ids 一次性关联）

```python
        # 7b: 批量嵌入所有唯一实体
        entity_embeddings = self.embedding_model.embed_batch(entity_texts, "add")

        # 7c: 批量搜索已有实体
        existing_matches = self.entity_store.search_batch(
            queries=valid_texts, vectors_list=valid_vectors,
            top_k=1, filters=search_filters,
        )

        # 7d: 分离为 insert vs update
        for j, key in enumerate(valid_keys):
            if matches and matches[0].score >= 0.95:
                # 更新: 把新 memory_ids 合并到已有实体
                linked |= memory_ids
                self.entity_store.update(...)
            else:
                # 新建: 收集起来批量插入
                to_insert_vectors.append(...)
                to_insert_payloads.append(...)

        # 7e: 批量插入新实体
        if to_insert_vectors:
            self.entity_store.insert(
                vectors=to_insert_vectors,
                ids=to_insert_ids,
                payloads=to_insert_payloads,
            )
```

**性能对比:**

| | 逐条处理 | 批量处理 (Phase 7) |
|--|---------|-------------------|
| 嵌入调用 | N 次 | 1 次 (embed_batch) |
| 搜索调用 | N 次 | 1 次 (search_batch) |
| 写入调用 | N 次 | 2 次 (update + insert) |
| 延迟 | ~N * 200ms | ~500ms |

### 2.5 main.py 中的混合搜索

#### 2.5.1 `_search_vector_store()` (line 1477-1575)

```python
    def _search_vector_store(self, query, filters, limit,
                             threshold=0.1, explain=False):
        # Step 1: 预处理
        query_lemmatized = lemmatize_for_bm25(query)
        query_entities = extract_entities(query)

        # Step 2: 嵌入查询
        embeddings = self.embedding_model.embed(query, "search")

        # Step 3: 语义搜索 (过采样 4x)
        internal_limit = max(limit * 4, 60)
        semantic_results = self.vector_store.search(
            query=query, vectors=embeddings,
            top_k=internal_limit, filters=filters
        )
```

**过采样策略:** `internal_limit = max(limit * 4, 60)`
- 如果用户要 top-5，实际搜索 top-60
- 因为 BM25 和实体增强可能改变排名
- 更宽的候选池 → 最终排名更准确

```python
        # Step 4: BM25 关键词搜索
        keyword_results = self.vector_store.keyword_search(
            query=query_lemmatized, top_k=internal_limit, filters=filters
        )
```

**注意:** 传给 BM25 的是**词形还原后**的查询。

```python
        # Step 5: 归一化 BM25 分数
        bm25_scores = {}
        if keyword_results is not None:
            midpoint, steepness = get_bm25_params(query, lemmatized=query_lemmatized)
            for mem in keyword_results:
                raw_score = mem.score
                if raw_score and raw_score > 0:
                    bm25_scores[mem_id] = normalize_bm25(
                        raw_score, midpoint, steepness
                    )
```

```python
        # Step 6: 实体增强
        entity_boosts = {}
        if query_entities:
            entity_boosts = self._compute_entity_boosts(query_entities, filters)
```

```python
        # Step 8: 分数融合
        scored_results = score_and_rank(
            semantic_results=candidates,
            bm25_scores=bm25_scores,
            entity_boosts=entity_boosts,
            threshold=threshold,
            top_k=limit,
            explain=explain,
        )
```

#### 2.5.2 实体增强计算 `_compute_entity_boosts()` (line 1577-1657)

```python
    def _compute_entity_boosts(self, query_entities, filters):
        seen = set()
        deduped = []
        for entity_type, entity_text in query_entities[:8]:
            key = entity_text.strip().lower()
            if key and key not in seen:
                seen.add(key)
                deduped.append((entity_type, entity_text))
```

**限制: 最多 8 个实体** — 防止查询太长时做太多搜索。

```python
        entity_texts = [text for _, text in deduped]
        embeddings = self.embedding_model.embed_batch(entity_texts, "search")
```

**批量嵌入查询实体。**

```python
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(_search_entity, text, emb): text
                for text, emb in zip(entity_texts, embeddings)
            }
            for future in concurrent.futures.as_completed(futures):
                matches = future.result()
                for match in matches:
                    similarity = match.score
                    if similarity < 0.5:
                        continue
                    payload = match.payload
                    linked_memory_ids = payload.get("linked_memory_ids", [])
                    num_linked = max(len(linked_memory_ids), 1)
                    memory_count_weight = 1.0 / (1.0 + 0.001 * ((num_linked - 1) ** 2))
                    boost = similarity * ENTITY_BOOST_WEIGHT * memory_count_weight
                    for memory_id in linked_memory_ids:
                        memory_boosts[memory_key] = max(
                            memory_boosts.get(memory_key, 0.0), boost
                        )
```

**Boost 计算公式:**

```
boost = similarity × 0.5 × memory_count_weight

memory_count_weight = 1 / (1 + 0.001 × (n-1)²)

其中 n = linked_memory_ids 数量
```

**memory_count_weight 的作用:**

| 关联记忆数 n | weight | 含义 |
|-------------|--------|------|
| 1 | 1.000 | 唯一实体，最高 boost |
| 10 | 0.991 | 几乎无影响 |
| 100 | 0.165 | 常见实体，大幅降低 |
| 1000 | 0.001 | 极常见，几乎无 boost |

**为什么?** 如果一个实体（如"用户"）关联了 1000 条记忆，它就不是有区分度的信号。

---

## 第三部分：支撑系统源码分析

### 3.1 factory.py — 工厂模式

**文件**: `mem0/utils/factory.py` (268行)

4 个工厂: `LlmFactory`, `EmbedderFactory`, `VectorStoreFactory`, `RerankerFactory`

**核心方法 `load_class()`:**

```python
def load_class(class_type):
    module_path, class_name = class_type.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)
```

**动态导入**: 通过字符串路径（如 `"mem0.llms.anthropic.AnthropicLLM"`）动态加载类。

**支持的提供商数量:**

| 工厂 | 数量 | 提供商 |
|------|------|--------|
| LlmFactory | 16 | openai, anthropic, ollama, azure, gemini, deepseek, ... |
| EmbedderFactory | 11 | openai, huggingface, fastembed, ollama, gemini, ... |
| VectorStoreFactory | 23 | qdrant, chroma, pgvector, milvus, pinecone, redis, ... |
| RerankerFactory | 5 | cohere, sentence_transformer, huggingface, llm, ... |

### 3.2 configs/base.py — 配置系统

**文件**: `mem0/configs/base.py` (82行)

```python
class MemoryConfig(BaseModel):
    vector_store: VectorStoreConfig    # 向量库配置
    llm: LlmConfig                     # LLM 配置
    embedder: EmbedderConfig           # 嵌入模型配置
    history_db_path: str               # SQLite 路径 (~/.mem0/history.db)
    reranker: Optional[RerankerConfig] # 重排序器
    version: str = "v1.1"              # API 版本
    custom_instructions: Optional[str] # 自定义指令
```

**v2 有但 v3 删除的配置:**
- `enable_graph: bool` — 是否启用图记忆
- `graph_store: GraphStoreConfig` — Neo4j 连接配置
- `custom_fact_extraction_prompt` → 改名 `custom_instructions`
- `custom_update_memory_prompt` → 废弃

### 3.3 vector_stores/qdrant.py — Qdrant 向量库

**文件**: `mem0/vector_stores/qdrant.py` (562行)

#### BM25 稀疏向量集成

```python
    def create_col(self, vector_size, on_disk, distance=Distance.COSINE):
        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=VectorParams(size=vector_size, distance=distance, on_disk=on_disk),
            sparse_vectors_config={
                "bm25": SparseVectorParams(modifier=models.Modifier.IDF),
            },
        )
```

**Qdrant 的命名向量:**
- `""` (默认) → 稠密向量 (semantic embedding)
- `"bm25"` → 稀疏向量 (BM25 keyword)
- 同一个 point 同时存储两种向量！

```python
    def insert(self, vectors, payloads=None, ids=None):
        for idx, vector in enumerate(vectors):
            named_vectors = {"": vector}  # 稠密向量
            if self._has_bm25_slot:
                text_for_bm25 = payload.get("text_lemmatized") or payload.get("data", "")
                sparse = self._encode_bm25(text_for_bm25)
                if sparse is not None:
                    named_vectors["bm25"] = sparse  # 稀疏向量
```

**BM25 编码器:**

```python
    def _get_bm25_encoder(self):
        from fastembed import SparseTextEmbedding
        self._bm25_encoder = SparseTextEmbedding(model_name="Qdrant/bm25")
```

使用 fastembed 的 `Qdrant/bm25` 模型，输出稀疏向量（大部分位置为 0）。

#### 高级过滤器

```python
    def _build_field_condition(self, key, value):
        if not isinstance(value, dict):
            if value == "*":
                return None  # 通配符 → 不过滤
            return FieldCondition(key=key, match=MatchValue(value=value))

        # 操作符支持
        if "eq" in value:   MatchValue(value["eq"])
        if "ne" in value:   MatchExcept(**{"except": [value["ne"]]})
        if "in" in value:   MatchAny(any=value["in"])
        if "nin" in value:  MatchExcept(**{"except": value["nin"]})
        if "gt" in value:   Range(gt=value["gt"])
        if "gte" in value:  Range(gte=value["gte"])
        if "contains" in value: MatchText(text=value["contains"])
```

支持的操作符: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `nin`, `contains`, `icontains`

逻辑组合: `AND`, `OR`, `NOT`

### 3.4 memory/storage.py — SQLite 历史管理

**文件**: `mem0/memory/storage.py` (348行)

#### 两个表

**history 表** — 记录记忆的变更历史:
```sql
CREATE TABLE history (
    id           TEXT PRIMARY KEY,
    memory_id    TEXT,       -- 关联的记忆 ID
    old_memory   TEXT,       -- 变更前内容
    new_memory   TEXT,       -- 变更后内容
    event        TEXT,       -- ADD/UPDATE/DELETE/NONE
    created_at   DATETIME,
    updated_at   DATETIME,
    is_deleted   INTEGER,    -- 0=否, 1=是
    actor_id     TEXT,       -- 操作者
    role         TEXT        -- 角色
)
```

**messages 表** — 保存最近的对话消息:
```sql
CREATE TABLE messages (
    id            TEXT PRIMARY KEY,
    session_scope TEXT,       -- "user_id=alice&agent_id=bot1"
    role          TEXT,       -- user/assistant/system
    content       TEXT,
    name          TEXT,       -- 发言者名称
    created_at    DATETIME
)
```

#### 消息保留策略

```python
    def save_messages(self, messages, session_scope):
        for message in messages:
            self.connection.execute("INSERT INTO messages ...")
        # 只保留最近 10 条！
        self.connection.execute(
            "DELETE FROM messages WHERE session_scope = ? AND id NOT IN ("
            "  SELECT id FROM ("
            "    SELECT id FROM messages WHERE session_scope = ? "
            "    ORDER BY created_at DESC LIMIT 10"
            "  )"
            ")"
        )
```

**设计决策:** 每个 session_scope 只保留最近 10 条消息。这些消息在 Phase 0 被加载，用于帮助 LLM 理解上下文。

### 3.5 configs/prompts.py — Prompt 演进

**文件**: `mem0/configs/prompts.py` (1063行)

#### v2 Prompt 体系 (3 个 Prompt)

**1. FACT_RETRIEVAL_PROMPT (line 15-60)**

```
角色: "Personal Information Organizer"
任务: 从对话中提取事实碎片
输出: {"facts": ["fact1", "fact2", ...]}
```

特点:
- 7 类信息（偏好、个人详情、计划、活动、健康、职业、杂项）
- Few-shot 示例
- 只提取，不判断 ADD/UPDATE/DELETE

**2. DEFAULT_UPDATE_MEMORY_PROMPT (line 176-324)**

```
角色: "Smart Memory Manager"
任务: 对比新旧事实，决定操作
输出: {"memory": [{id, text, event, old_memory}]}
```

特点:
- 4 种操作: ADD, UPDATE, DELETE, NONE
- 详细的示例（每种操作一个示例）
- 复杂度高 → LLM 容易出错

**3. get_update_memory_messages() (line 406-460)**

构建用户端 Prompt，把旧记忆和新事实组合起来。

#### v3 Prompt 体系 (1 个 Prompt)

**ADDITIVE_EXTRACTION_PROMPT (line 468-943)**

```
角色: "Memory Extractor"
任务: 提取所有可记忆的信息 + 链接到已有记忆
输出: {"memory": [{id, text, attributed_to, linked_memory_ids}]}
```

**长度: 约 500 行**（vs v2 的 3 个 Prompt 总共约 350 行）

**v3 Prompt 的核心创新:**

1. **单一操作 ADD**: 不再需要判断 UPDATE/DELETE
2. **时间锚定**: "Observation Date" + 严格的时间转换规则
3. **记忆链接**: `linked_memory_ids` 字段建立记忆间的关系
4. **多维提取**: 要求从 user 和 assistant 消息中都提取
5. **质量控制**: 极其详细的质量标准（自包含、保留细节、不泛化）
6. **反模式**: 明确列出"不要做什么"（No Fabrication, No Echo 等）

**v2 vs v3 Prompt 对比:**

| 维度 | v2 | v3 |
|------|----|----|
| Prompt 数量 | 3 个 | 1 个 |
| LLM 调用次数 | 2 次 | 1 次 |
| 操作类型 | ADD/UPDATE/DELETE/NONE | 仅 ADD |
| 记忆链接 | 无 | linked_memory_ids |
| 时间处理 | 无 | Observation Date + 绝对时间 |
| 属性归属 | 无 | attributed_to (user/assistant) |
| 质量标准 | 简短 | 极其详细（~200行） |

---

## v2 → v3 架构决策对比

### 知识图谱方案对比

```
┌──────────────────────────────────────────────────────────────────┐
│                    v2: Neo4j 图记忆                              │
│                                                                  │
│  用户文本 → LLM提取实体 → LLM提取关系 → Neo4j MERGE/CREATE      │
│           (tool call)    (tool call)    (Cypher 查询)            │
│                                                                  │
│  查询    → LLM提取实体 → Neo4j MATCH → BM25重排序               │
│           (tool call)    (Cypher 查询)  (rank_bm25 库)           │
│                                                                  │
│  LLM 调用: 3-4 次  |  外部依赖: Neo4j                           │
│  关系: 显式边       |  查询: Cypher 写死                         │
├──────────────────────────────────────────────────────────────────┤
│                    v3: Entity Linking                            │
│                                                                  │
│  用户文本 → spaCy提取实体 → 嵌入实体 → 向量库 upsert             │
│           (纯 NLP)        (embed_batch)  (search_batch + insert) │
│                                                                  │
│  查询    → spaCy提取实体 → 嵌入实体 → 实体库搜索 → boost 融合   │
│           (纯 NLP)        (embed_batch)  (ThreadPool)  (加法)    │
│                                                                  │
│  LLM 调用: 0 次    |  外部依赖: 无（复用向量库）                 │
│  关系: 隐式链接    |  查询: 向量搜索（灵活）                     │
└──────────────────────────────────────────────────────────────────┘
```

### 提取流水线对比

```
v2 提取流水线 (2-4 次 LLM 调用):
  ┌──────────────────────────────────────────────┐
  │ 对话 → [LLM #1] → 事实列表                   │
  │       → 向量化 → 搜索相似记忆                │
  │       → [LLM #2] → ADD/UPDATE/DELETE 决策    │
  │       → 执行决策 → 写入向量库                │
  │       → [LLM #3] → 图实体提取                │
  │       → [LLM #4] → 图关系提取                │
  │       → Cypher 写入 Neo4j                    │
  └──────────────────────────────────────────────┘

v3 提取流水线 (1 次 LLM 调用):
  ┌──────────────────────────────────────────────┐
  │ 对话 → 搜索 top-10 相关记忆 (Phase 0-1)     │
  │       → [LLM #1] → ADD-only 提取 (Phase 2)  │
  │       → 批量嵌入 (Phase 3)                   │
  │       → 哈希去重 (Phase 4-5)                 │
  │       → 批量写入向量库 (Phase 6)             │
  │       → spaCy 实体提取 + 链接 (Phase 7)     │
  │       → 保存消息历史 (Phase 8)               │
  └──────────────────────────────────────────────┘
```

### 搜索流水线对比

```
v2 搜索 (1-2 次 LLM 调用):
  ┌──────────────────────────────────────────────┐
  │ 查询 → 向量化 → 语义搜索                    │
  │       → [LLM] → 图实体提取                  │
  │       → Cypher 搜索 Neo4j                   │
  │       → BM25 重排序图结果                   │
  │       → 合并: 向量结果 + 图关系              │
  │       → 返回 (每条结果附带 relations 字段)   │
  └──────────────────────────────────────────────┘

v3 搜索 (0 次 LLM 调用):
  ┌──────────────────────────────────────────────┐
  │ 查询 → 词形还原 + spaCy 实体提取             │
  │       → 向量化                               │
  │       → 语义搜索 (过采样 4x)                 │
  │       → BM25 稀疏搜索 (Qdrant named vectors) │
  │       → Sigmoid 归一化 BM25                  │
  │       → 实体增强 (ThreadPool 并行搜索)       │
  │       → 三信号加法融合                       │
  │       → 按分数排序 → 返回 top-k              │
  └──────────────────────────────────────────────┘
```

### 关键设计决策复盘

```
Q1: 为什么 v3 用 spaCy 替代 LLM 做实体提取？

A: LLM 提取实体的问题:
   1. 慢: 每次提取需要 500-2000ms（API 调用）
   2. 贵: 每次提取消耗 tokens
   3. 不稳定: LLM 可能漏提取或错误分类
   4. 需要 tool call: 增加复杂度

   spaCy 的优势:
   1. 快: ~5-10ms（本地推理）
   2. 免费: 不需要 API 调用
   3. 确定性: 相同输入 → 相同输出
   4. 不需要 tool call: 直接调用 Python 函数

Q2: 为什么 v3 用隐式链接替代显式关系？

A: v2 的显式关系问题:
   1. LLM 经常搞错关系方向
   2. 关系类型（ate_at, dined_with）太细碎
   3. Cypher 查询必须指定关系类型 → 不灵活
   4. 用户必须理解图结构才能查询

   v3 隐式链接的优势:
   1. 不需要判断方向
   2. 只要"出现在同一条记忆中"就有关联
   3. 搜索时自动增强 → 用户无感知
   4. 不暴露内部结构

Q3: 为什么 v3 的搜索不需要 LLM？

A: v2 搜索需要 LLM 提取查询中的实体（tool call）
   v3 用 spaCy 提取 → 0 次 LLM 调用 → 更快更便宜

Q4: 为什么 entity boost 用乘法衰减？

A: memory_count_weight = 1/(1+0.001*(n-1)^2)

   如果一个实体（如"用户"）关联了所有记忆，
   它就不是有区分度的信号。衰减公式确保:
   - 罕见实体（n=1-10）: 几乎不衰减
   - 常见实体（n=100+）: 显著衰减
   - 极常见（n=1000+）: 几乎无 boost
```
