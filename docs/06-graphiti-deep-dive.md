# Zep / Graphiti 深度源码研究

> 时序知识图谱记忆架构，有论文支撑（arXiv:2501.13956）
> 开源引擎 Graphiti + 商业服务 Zep

---

## 目录

- [项目概览](#项目概览)
- [核心概念：双时态建模](#核心概念双时态建模)
- [架构全景](#架构全景)
- [数据模型](#数据模型)
- [写入流水线](#写入流水线)
- [LLM Prompt 系统](#llm-prompt-系统)
- [搜索系统](#搜索系统)
- [源码结构](#源码结构)
- [论文核心内容](#论文核心内容)
- [与 Mem0 的对比](#与-mem0-的对比)
- [与 OpenViking 的对比](#与-openviking-的对比)
- [总结](#总结)

---

## 项目概览

| 维度 | 详情 |
|------|------|
| **GitHub** | [getzep/graphiti](https://github.com/getzep/graphiti) |
| **公司** | Zep Software, Inc. |
| **论文** | [Zep: A Temporal Knowledge Graph Architecture for Agent Memory](https://arxiv.org/abs/2501.13956) (Jan 2025) |
| **语言** | Python |
| **图数据库** | Neo4j（核心依赖） |
| **协议** | Apache 2.0 |
| **定位** | AI Agent 的时序知识图谱记忆引擎 |
| **核心创新** | 双时态建模（valid_at + created_at）+ 非破坏性更新 |

### 一句话描述

Graphiti 把每条记忆建模为**带时间线的图关系**——不是覆盖旧信息，而是记录"什么时候是真的"和"什么时候知道的"，形成完整的知识演化历史。

---

## 核心概念：双时态建模

这是 Graphiti 最核心的创新，也是区别于所有其他方案的本质特征。

### 两条时间线

```
每个事实（Edge）记录两个时间:

  valid_at:    事实在真实世界中成立的时间
               "用户2025年3月搬到上海" → valid_at = 2025-03

  created_at:  系统获知这个事实的时间（入库时间）
               用户在6月的对话中提到搬家 → created_at = 2025-06

为什么需要两条?

  场景: 用户在6月说 "我3月搬到了上海"

  只有 created_at:
    "用户搬到上海" created_at=2025-06
    → 系统不知道搬家发生在3月

  双时态:
    "用户搬到上海" valid_at=2025-03, created_at=2025-06
    → 系统知道: 搬家发生在3月，系统在6月才知道
```

### 时间线演示

```
用户的知识演化时间线:

2024-01  valid_at                    事实: "住在北京"
         ─────────────────────────────────────────────

2024-06  valid_at                    事实: "换了新工作"
         ─────────────────────────────────────────────

2025-03  valid_at                    事实: "搬到上海"
         ─────────────────────────────────────────────

2025-06  created_at                  系统获知上述事实
         ↑↑↑ 用户在这一次对话中告诉了系统所有信息

查询 "用户2024年底住在哪里?"
  → 图遍历找到 "住在北京" (valid_at=2024-01, 之后没有被 invalidate)
  → 返回: "北京"

查询 "用户什么时候搬去上海的?"
  → 图遍历找到 "搬到上海" (valid_at=2025-03)
  → 返回: "2025年3月"
```

### 非破坏性更新

```
传统方式（Mem0 v2 UPDATE）:
  旧: "用户住在北京" → 被覆盖为 → "用户住在上海"
  问题: "住在北京" 这个历史信息永久丢失了

Mem0 v3 ADD-only:
  旧: "用户住在北京" → 保留
  新: "用户搬到上海" → 新增
  问题: 两条信息并存，但不知道哪条是最新的

Graphiti 双时态:
  旧: "用户住在北京" valid_at=2024-01, invalid_at=2025-03
  新: "用户住在上海" valid_at=2025-03, invalid_at=null
  → 旧信息没有被删除，而是标记了失效时间
  → 查询时可以按时间线精确还原任何时刻的状态
```

---

## 架构全景

```
┌─────────────────────────────────────────────────────────────────┐
│                     Graphiti 架构全景                            │
│                                                                 │
│  输入: Episode（一段对话/文档/事件）                              │
│    │                                                            │
│    ▼                                                            │
│  ┌──────────────────────────────────────────┐                   │
│  │         Step 1: 创建 Episodic Node        │                   │
│  │  存储原始 episode 内容 + 时间戳            │                   │
│  └──────────────────┬───────────────────────┘                   │
│                     │                                           │
│                     ▼                                           │
│  ┌──────────────────────────────────────────┐                   │
│  │         Step 2: 提取 Entity Nodes         │                   │
│  │  LLM 调用 #1: 从文本中提取实体            │                   │
│  │  → name, entity_type, summary             │                   │
│  └──────────────────┬───────────────────────┘                   │
│                     │                                           │
│                     ▼                                           │
│  ┌──────────────────────────────────────────┐                   │
│  │         Step 3: 实体去重                  │                   │
│  │  向量搜索 + LLM 调用 #2: 判断是否重复      │                   │
│  │  → 合并重复实体或创建新实体                │                   │
│  └──────────────────┬───────────────────────┘                   │
│                     │                                           │
│                     ▼                                           │
│  ┌──────────────────────────────────────────┐                   │
│  │         Step 4: 提取 Edges（关系/事实）    │                   │
│  │  LLM 调用 #3: 提取实体间的关系            │                   │
│  │  → source, target, relation_type,         │                   │
│  │    fact, valid_at, invalid_at             │                   │
│  └──────────────────┬───────────────────────┘                   │
│                     │                                           │
│                     ▼                                           │
│  ┌──────────────────────────────────────────┐                   │
│  │         Step 5: Edge 去重/解决冲突        │                   │
│  │  LLM 调用 #4: 判断新旧关系是否矛盾        │                   │
│  │  → 如果有矛盾，旧 edge 设 invalid_at      │                   │
│  │  → 不删除！标记失效时间（非破坏性更新）    │                   │
│  └──────────────────┬───────────────────────┘                   │
│                     │                                           │
│                     ▼                                           │
│  ┌──────────────────────────────────────────┐                   │
│  │         Step 6: 写入 Neo4j                │                   │
│  │  Cypher: MERGE nodes + CREATE edges       │                   │
│  │  + 向量嵌入 + 全文索引                     │                   │
│  └──────────────────────────────────────────┘                   │
│                                                                 │
│  总计: 4-5 次 LLM 调用 per episode                              │
└─────────────────────────────────────────────────────────────────┘
```

---

## 数据模型

### 三种节点类型

```python
# graphiti_core/nodes.py

class EpisodicNode:
    """事件节点 — 存储原始对话/文档"""
    uuid: str                    # 唯一标识
    name: str                    # 事件名称
    group_id: str                # 分组（多租户隔离）
    source: str                  # 来源（"message" / "json" / "text"）
    source_description: str      # 来源描述
    content: str                 # 原始内容
    reference_time: datetime     # 事件发生的参考时间
    created_at: datetime         # 入库时间
    valid_at: datetime           # 事件有效时间

class EntityNode:
    """实体节点 — 人、地点、组织、事物"""
    uuid: str
    name: str                    # 实体名称（如 "张明"）
    group_id: str
    name_embedding: list[float]  # 名称的向量嵌入（用于去重搜索）
    summary: str                 # 实体的摘要描述
    labels: list[str]            # 类型标签（如 ["Person", "Employee"]）
    attributes: dict             # 自定义属性
    created_at: datetime

class CommunityNode:
    """社区节点 — 实体聚类（用于高级检索）"""
    uuid: str
    name: str                    # 社区名称
    group_id: str
    summary: str                 # 社区摘要
    name_embedding: list[float]
    created_at: datetime
```

### 一种边类型

```python
# graphiti_core/edges.py

class EntityEdge:
    """实体关系边 — 连接两个实体的事实"""
    uuid: str
    source_node_uuid: str        # 源实体
    target_node_uuid: str        # 目标实体
    name: str                    # 关系名称（如 "WORKS_AT"）
    fact: str                    # 自然语言事实描述
    group_id: str
    valid_at: datetime | None    # 事实成立的时间 ⭐
    invalid_at: datetime | None  # 事实失效的时间 ⭐
    created_at: datetime         # 入库时间
    fact_embedding: list[float]  # 事实的向量嵌入（用于语义搜索）
    attributes: dict             # 自定义属性
    episodes: list[str]          # 来源 episode UUIDs
```

### 图 Schema

```
Neo4j 中的图结构:

  (EpisodicNode)          存储原始事件
       │
       │ MENTIONS
       ▼
  (EntityNode) ──EntityEdge──> (EntityNode)
  "张明"        WORKS_AT        "字节跳动"
                valid_at: 2024-01
                invalid_at: 2025-03

  (EntityNode) ──EntityEdge──> (EntityNode)
  "张明"        LIVES_IN        "北京"
                valid_at: 2024-01
                invalid_at: 2025-03

  (EntityNode) ──EntityEdge──> (EntityNode)
  "张明"        LIVES_IN        "上海"
                valid_at: 2025-03
                invalid_at: null  ← 当前有效

  (CommunityNode) ←──BELONGS_TO── (EntityNode)
  "字节员工"                        "张明"
```

---

## 写入流水线

### 完整 `add_episode()` 流程

```python
# graphiti_core/graphiti.py — 核心入口

async def add_episode(
    self,
    group_id: str,           # 租户隔离
    content: str,            # 原始文本
    reference_time: datetime,# 事件发生时间
    source: str = "message",
    source_description: str = "",
) -> dict:
    """
    将一个 episode 写入知识图谱
    返回: 创建的节点和边的 UUID 列表
    """
```

**6 步骤详细流程:**

```
Step 1: 创建 Episodic Node
─────────────────────────
  内容: 原始对话/文档
  嵌入: content embedding
  存储: Neo4j + 全文索引

Step 2: LLM 提取实体 (extract_nodes)
─────────────────────────────────────
  Prompt: "从当前消息中提取所有实体..."
  输入: episode content + previous episodes (上下文)
  输出: [{name, entity_type_id, episode_indices}, ...]

  实体类型由用户定义（可自定义）:
    0: Generic (通用)
    1: Person (人)
    2: Organization (组织)
    3: Location (地点)
    4: Product (产品)
    5: Event (事件)
    ...

Step 3: 实体去重 (dedupe_nodes)
───────────────────────────────
  对每个提取的实体:
    a. 向量搜索: 用 name_embedding 在 Neo4j 中找相似实体
    b. LLM 判断: 是否是同一个实体?
       Prompt: "新实体 '张明明' 和已有实体 '张明' 是同一个人吗?"
       输出: {duplicate_candidate_id: 0} 或 {-1: 不是重复}
    c. 如果重复 → 合并（更新名称和摘要）
    d. 如果不重复 → 创建新 EntityNode

Step 4: LLM 提取关系 (extract_edges)
─────────────────────────────────────
  Prompt: "从当前消息中提取实体之间的关系..."
  输入: episode content + entities + reference_time
  输出: [Edge(source, target, relation_type, fact, valid_at, invalid_at), ...]

  关键: LLM 需要同时提取时间信息!
  例: "我3月搬到了上海"
  → Edge(source="张明", target="上海", relation="LIVES_IN",
          fact="张明搬到上海居住",
          valid_at="2025-03-01T00:00:00Z",
          invalid_at=null)

Step 5: 边去重/冲突解决 (dedupe_edges)
──────────────────────────────────────
  对每条新 edge:
    a. 搜索: 找已有的相似 edge
    b. LLM 判断:
       - 是否重复? → 跳过
       - 是否矛盾? → 旧 edge 设 invalid_at = 新 edge 的 valid_at
       - 是否独立? → 创建新 edge

  例: 旧 edge "张明 LIVES_IN 北京" (valid_at=2024-01)
      新 edge "张明 LIVES_IN 上海" (valid_at=2025-03)
      LLM 判断: 矛盾!
      → 旧 edge.invalid_at = 2025-03  (不是删除，是标记失效!)
      → 新 edge 正常写入

Step 6: 持久化到 Neo4j
──────────────────────
  Cypher:
    MERGE (e:Entity {uuid: $uuid})
    SET e.name = $name, e.summary = $summary
    ...
    CREATE (s)-[r:WORKS_AT {
      uuid: $edge_uuid,
      fact: $fact,
      valid_at: datetime($valid_at),
      invalid_at: datetime($invalid_at),
      created_at: datetime($created_at)
    }]->(t)
```

### LLM 调用次数统计

| 步骤 | LLM 调用 | 说明 |
|------|---------|------|
| 实体提取 | 1 次 | extract_nodes prompt |
| 实体去重 | N 次 | 每个候选实体一次（可并行） |
| 关系提取 | 1 次 | extract_edges prompt |
| 边去重/冲突 | M 次 | 每条候选边一次（可并行） |
| **总计** | **2 + N + M** | 通常 4-8 次/episode |

---

## LLM Prompt 系统

### Prompt 1: 实体提取 (extract_nodes.py)

```python
system = "You are an entity extraction specialist..."

user = """
<CURRENT_MESSAGE>
{episode_content}
</CURRENT_MESSAGE>

<ENTITY TYPES>
{entity_types}
</ENTITY TYPES>

提取规则:
- 不要提取代词（你、我、他）
- 不要提取抽象概念（快乐、成长、韧性）
- 不要提取通用名词（东西、时间、人们）
- 不要提取形容词（"amazing", "something different"）
- 亲属关系要加限定词: 提取 "Nisha's dad" 而不是 "dad"
- 代词引用要消歧: "he" → 具体人名
"""
```

**与 Mem0 的区别:**
- Graphiti 提取**命名实体**（人、地、组织）
- Mem0 提取**事实碎片**（"用户喜欢火锅"）
- Graphiti 的实体是图的**节点**，需要结构化
- Mem0 的事实是向量库中的**文本**

### Prompt 2: 关系提取 (extract_edges.py)

```python
system = "You are an expert fact extractor that extracts fact triples..."

user = """
<CURRENT_MESSAGE>
{episode_content}
</CURRENT_MESSAGE>

<ENTITIES>
{nodes}
</ENTITIES>

<REFERENCE_TIME>
{reference_time}  # ISO 8601
</REFERENCE_TIME>

提取规则:
1. 每条事实必须涉及 ENTITIES 列表中的两个不同实体
2. relation_type 用 SCREAMING_SNAKE_CASE（如 WORKS_AT, LIVES_IN）
3. fact 用自然语言描述关系
4. 提取 valid_at 和 invalid_at 时间戳!
"""
```

**输出格式:**
```json
{
  "edges": [
    {
      "source_entity_name": "张明",
      "target_entity_name": "字节跳动",
      "relation_type": "WORKS_AT",
      "fact": "张明在字节跳动担任高级后端工程师",
      "valid_at": "2024-01-15T00:00:00Z",
      "invalid_at": null
    }
  ]
}
```

### Prompt 3: 实体去重 (dedupe_nodes.py)

```python
system = "You are an entity deduplication assistant..."

user = """
<NEW ENTITY>
{name: "张明明", summary: "..."}
</NEW ENTITY>

<EXISTING ENTITIES>
[{id: 0, name: "张明", summary: "字节跳动工程师"},
 {id: 1, name: "张明明", summary: "用户的朋友"}]
</EXISTING ENTITIES>

新实体和哪个已有实体是同一个人/事物?
返回 duplicate_candidate_id，没有重复则返回 -1
"""
```

### Prompt 4: 边冲突解决 (dedupe_edges.py)

```python
# 这是 Graphiti 独有的——判断新旧事实是否矛盾

user = """
<NEW FACT>
"张明住在上海" valid_at: 2025-03
</NEW FACT>

<EXISTING FACTS>
[{id: 0, fact: "张明住在北京", valid_at: "2024-01", invalid_at: null},
 {id: 1, fact: "张明喜欢跑步", valid_at: "2024-06", invalid_at: null}]
</EXISTING FACTS>

新事实和哪条已有事实矛盾?
矛盾的事实会被标记 invalid_at = 新事实的 valid_at
"""
```

---

## 搜索系统

### 搜索方法

Graphiti 支持 **5 种搜索策略**，可以组合使用：

```python
# search/search_config.py

class EdgeSearchMethod(Enum):
    cosine_similarity = 'cosine_similarity'  # 向量语义搜索
    bm25 = 'bm25'                            # 全文关键词搜索
    bfs = 'breadth_first_search'             # 图遍历（BFS）

class EdgeReranker(Enum):
    rrf = 'reciprocal_rank_fusion'           # 倒数排名融合
    node_distance = 'node_distance'          # 节点距离重排
    episode_mentions = 'episode_mentions'    # episode 引用次数
    mmr = 'mmr'                              # 最大边际相关性
    cross_encoder = 'cross_encoder'          # 交叉编码器重排
```

### 搜索流程

```python
# search/search.py

async def search(
    query: str,
    group_ids: list[str],
    config: SearchConfig,
    center_node_uuid: str | None = None,    # 中心节点（BFS 起点）
    bfs_origin_node_uuids: list[str] | None,
) -> SearchResults:
    """
    多路并行搜索 + 结果融合
    """
```

```
查询: "张明的工作情况"

Step 1: 生成查询向量
  query_embedding = embed("张明的工作情况")

Step 2: 多路并行搜索
  ┌─ 路径 A: 语义搜索 (cosine_similarity)
  │   → 在 fact_embedding 中找最相似的 edges
  │   → 返回: ["张明在字节跳动工作", "张明负责后端架构", ...]
  │
  ├─ 路径 B: 全文搜索 (bm25)
  │   → 在 Neo4j 全文索引中搜索
  │   → 返回: ["张明工作努力", "工作内容包括...", ...]
  │
  └─ 路径 C: 图遍历 (bfs)
      → 如果提供了 center_node_uuid (如 "张明" 的节点)
      → 从该节点出发做 BFS，收集 N 跳内的所有 edges
      → 返回: ["张明-works_at->字节跳动", "张明-lives_in->上海", ...]

Step 3: 结果融合
  → Reciprocal Rank Fusion (RRF)
     对每条结果: score = Σ 1/(k + rank_in_each_list)
  → 或 Cross Encoder 重排序（用另一个模型精排）
  → 或 MMR（最大边际相关性，去冗余）

Step 4: 时间过滤（可选）
  → 只返回 valid_at 在指定时间范围内的结果
  → "2024年底张明在哪里工作?" → 过滤 invalid_at > 2024-12 的 edges

Step 5: 返回 SearchResults
  → edges: [EntityEdge, ...]
  → nodes: [EntityNode, ...]
  → episodes: [EpisodicNode, ...]
  → communities: [CommunityNode, ...]
```

### 搜索配方 (search_config_recipes.py)

```python
# 预设的搜索配置"配方"

# 配方 1: 快速语义搜索
SEARCH_RECIPE_FAST = SearchConfig(
    edge_search=EdgeSearchConfig(
        search_methods=[EdgeSearchMethod.cosine_similarity],
        reranker=EdgeReranker.rrf,
    ),
    # ...
)

# 配方 2: 混合搜索（语义 + BM25）
SEARCH_RECIPE_HYBRID = SearchConfig(
    edge_search=EdgeSearchConfig(
        search_methods=[
            EdgeSearchMethod.cosine_similarity,
            EdgeSearchMethod.bm25,
        ],
        reranker=EdgeReranker.rrf,
    ),
)

# 配方 3: 图遍历搜索（从某个实体出发）
SEARCH_RECIPE_GRAPH = SearchConfig(
    edge_search=EdgeSearchConfig(
        search_methods=[EdgeSearchMethod.bfs],
        reranker=EdgeReranker.node_distance,
        bfs_max_depth=3,
    ),
)
```

---

## 源码结构

```
graphiti/
├── graphiti_core/              # 核心引擎 (~8,000 行)
│   ├── graphiti.py               # ⭐ 主类 Graphiti（入口）
│   ├── nodes.py                  # 节点类型: Entity, Episodic, Community
│   ├── edges.py                  # 边类型: EntityEdge
│   ├── graph_queries.py          # Neo4j Cypher 查询封装
│   │
│   ├── prompts/                  # ⭐ LLM Prompt 系统
│   │   ├── extract_nodes.py      # 实体提取 prompt
│   │   ├── extract_edges.py      # 关系提取 prompt
│   │   ├── dedupe_nodes.py       # 实体去重 prompt
│   │   ├── dedupe_edges.py       # 边去重/冲突解决 prompt
│   │   ├── summarize_nodes.py    # 实体摘要生成
│   │   ├── summarize_sagas.py    # 事件链摘要
│   │   └── models.py             # Prompt 数据模型
│   │
│   ├── search/                   # ⭐ 搜索系统
│   │   ├── search.py             # 多路并行搜索 + 融合
│   │   ├── search_config.py      # 搜索配置（方法 + 重排器）
│   │   ├── search_config_recipes.py # 预设搜索配方
│   │   ├── search_filters.py     # 时间/属性过滤
│   │   └── search_utils.py       # BFS, 全文, 向量, RRF, MMR
│   │
│   ├── driver/                   # Neo4j 驱动封装
│   │   └── driver.py             # Cypher 执行、事务管理
│   │
│   ├── llm_client/               # LLM 客户端
│   │   ├── client.py             # 统一接口
│   │   └── openai_client.py      # OpenAI 实现
│   │
│   ├── embedder/                 # 嵌入服务
│   │   └── client.py             # 向量嵌入接口
│   │
│   ├── cross_encoder/            # 交叉编码器（重排序）
│   │   └── client.py             # Cross-encoder 接口
│   │
│   └── models/                   # 数据模型
│
├── server/                       # HTTP 服务器
├── mcp_server/                   # MCP 协议服务器
├── examples/                     # 使用示例
│   ├── quickstart/               # 快速入门
│   ├── podcast/                  # 播客转记忆
│   ├── ecommerce/                # 电商场景
│   └── langgraph-agent/          # LangGraph 集成
│
└── tests/                        # 测试
```

---

## 论文核心内容

### 论文信息

```
标题: Zep: A Temporal Knowledge Graph Architecture for Agent Memory
arXiv: 2501.13956 (Jan 2025)
作者: Daniel Chalef, Travis Fischer, Siddharth Vashishtha (Zep Software)
会议: 投稿至 ICLR/ACL (具体录用情况待确认)
```

### 核心贡献

```
贡献 1: 双时态知识图谱架构
  - valid_at (事实时间) + created_at (入库时间)
  - 非破坏性更新: 旧事实标记 invalid_at 而非删除
  - 完整的历史可追溯性

贡献 2: 增量式 Episode 处理
  - 每条新信息作为离散 Episode 处理
  - 增量折叠进已有图谱
  - 不需要批处理或重建

贡献 3: 多路搜索融合
  - 语义搜索 + 全文搜索 + 图遍历
  - 时间范围过滤
  - 交叉编码器重排序
```

### Benchmark 成绩

| 基准 | 成绩 | 说明 |
|------|------|------|
| LoCoMo | 原始声称 84% | 独立复现仅 58.44% |
| 修正后 | ~58-71% | 不同评估方法差异大 |

**争议**: 原始论文声称 LoCoMo 84%，但[独立复现](https://github.com/getzep/zep-papers/issues/)发现评估方法有技术不一致，修正后仅 58.44%。Zep 团队反过来[质疑 Mem0 的评估方法论](https://blog.getzep.com/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/)。

---

## 与 Mem0 的对比

### 架构对比

```
┌──────────────────────────────────────────────────────────────┐
│                     Mem0 v3                                  │
│                                                              │
│  对话 → [LLM 1次] → ADD-only 事实                            │
│       → [批量嵌入] → [向量库]                                 │
│                                                              │
│  存储: 扁平向量，无图结构                                     │
│  时间: 仅 created_at 时间戳                                  │
│  矛盾处理: 新旧并存，由检索排序                               │
│  LLM 调用: 1 次/对话                                        │
├──────────────────────────────────────────────────────────────┤
│                  Graphiti                                    │
│                                                              │
│  对话 → [LLM 4-8次] → 实体 + 关系 + 时间                    │
│       → [向量+全文+图] → [Neo4j]                             │
│                                                              │
│  存储: 知识图谱（节点+边+时间线）                             │
│  时间: valid_at + created_at + invalid_at（三时态）          │
│  矛盾处理: 非破坏性更新（标记 invalid_at）                    │
│  LLM 调用: 4-8 次/episode                                   │
└──────────────────────────────────────────────────────────────┘
```

### 详细对比表

| 维度 | Mem0 v3 | Graphiti |
|------|---------|---------|
| **核心比喻** | 记忆提取器 | 知识图谱构建器 |
| **数据结构** | 扁平向量集合 | 图（节点+边+时间线） |
| **时间建模** | 单时态（created_at） | 双时态（valid_at + created_at） |
| **矛盾处理** | ADD-only（新旧并存） | 非破坏性更新（标记失效时间） |
| **关系表达** | 隐式（entity link） | 显式（typed edges） |
| **实体提取** | spaCy NLP（本地） | LLM tool call（API） |
| **关系提取** | 无 | LLM（每次 1 调用） |
| **LLM 调用** | 1 次/对话 | 4-8 次/episode |
| **延迟** | 快（~500ms） | 慢（~3-5s） |
| **成本** | 低 | 高（多 LLM 调用） |
| **依赖** | 向量库（23种） | Neo4j（必须） |
| **搜索** | 语义+BM25+实体增强 | 语义+BM25+BFS+时间过滤 |
| **重排序** | 无（分数加法融合） | RRF/MMR/Cross-Encoder |
| **论文** | v2 有，v3 无 | ✅ 有（arXiv:2501.13956） |
| **LoCoMo** | 91.6（自报） | 58-71（争议） |
| **适合场景** | 快速集成、通用记忆 | 时序推理、关系追踪 |

### 各自的优势

```
Mem0 优于 Graphiti:
  ✅ 速度快 10x（1次 vs 4-8次 LLM 调用）
  ✅ 成本低 10x
  ✅ 部署简单（不需要 Neo4j）
  ✅ 支持 23 种向量库
  ✅ API 简单（10 行代码）
  ✅ 混合搜索（BM25 + 实体增强）

Graphiti 优于 Mem0:
  ✅ 双时态建模 → 可以回答"什么时候"的问题
  ✅ 非破坏性更新 → 历史不丢失
  ✅ 显式关系 → 可以回答"A 和 B 是什么关系"
  ✅ 图遍历 → 多跳推理（A→B→C）
  ✅ 有论文支撑 → 学术验证
  ✅ 时间范围过滤 → "2024年底的情况"
```

### 关键场景对比

```
场景 1: "张明现在住在哪里?"
  Mem0: 搜索 "张明 住" → 返回 ["住在北京", "搬到上海"]
        → 两条都返回，靠时间戳判断哪个更新
  Graphiti: 查询 LIVES_IN where invalid_at=null
        → 精确返回 "住在上海" (唯一有效记录)

场景 2: "张明是什么时候搬到上海的?"
  Mem0: 搜索 "张明 搬 上海" → 可能找到
        → 但记忆中没有结构化的时间信息
  Graphiti: 查询 LIVES_IN "上海" 的 valid_at
        → 精确返回 "2025-03-01"

场景 3: "张明2024年底住在哪里?"
  Mem0: 困难! 无法按时间过滤记忆
  Graphiti: 查询 LIVES_IN where valid_at <= 2024-12
            and (invalid_at > 2024-12 or invalid_at=null)
        → 精确返回 "北京"

场景 4: "张明认识谁?"
  Mem0: 搜索 "张明" → 返回相关记忆
        → 需要人工从记忆文本中提取人物关系
  Graphiti: 图遍历 from "张明" node
        → 精确返回所有关系边:
          [WORKS_AT→字节跳动, LIVES_IN→上海, FRIEND→李华, ...]
```

---

## 与 OpenViking 的对比

| 维度 | Graphiti | OpenViking |
|------|---------|------------|
| **核心比喻** | 知识图谱构建器 | Agent 的文件系统 |
| **数据结构** | 图（节点+边+时间线） | 目录树（L0/L1/L2） |
| **时间建模** | 双时态（强） | 单时态（弱） |
| **关系表达** | 显式 typed edges | 目录层级 |
| **核心语言** | Python | Rust |
| **依赖** | Neo4j（必须） | SQLite/PG（内置） |
| **Token 效率** | 低（全量返回） | 高（L0/L1/L2 按需） |
| **LLM 调用** | 4-8 次/episode | 2 次/写入 |
| **适合场景** | 时序推理、关系追踪 | 上下文管理、token 节省 |

**三者定位完全不同:**
```
Mem0:      "帮你记住对话中说过的事"     (记忆提取器)
Graphiti:  "帮你构建知识的时间演化图谱"  (时序知识图谱)
OpenViking:"给你的 Agent 一个文件系统"   (上下文数据库)
```

---

## 总结

### Graphiti 的核心价值

```
1. 双时态建模
   唯一能精确回答"什么时候"的记忆系统
   valid_at + created_at + invalid_at = 完整时间线

2. 非破坏性更新
   旧信息永远不会被删除
   只会被标记失效时间
   → 完整的历史可追溯性

3. 显式关系
   typed edges (WORKS_AT, LIVES_IN, ...)
   → 支持多跳推理和图遍历
   → 可以回答"A 和 B 什么关系"

4. 有论文支撑
   唯一有正式学术论文的 Agent 记忆方案
   虽然 Benchmark 有争议，但架构设计经过同行评审
```

### Graphiti 的局限

```
1. 慢且贵: 每个 episode 4-8 次 LLM 调用
2. 依赖 Neo4j: 需要额外部署和维护图数据库
3. Benchmark 争议: LoCoMo 成绩从 84% 被修正到 58%
4. 存储开销大: 每个对话约 600,000 tokens（vs Mem0 的 1,764）
5. 复杂度高: 理解图 schema + Cypher + 双时态需要学习成本
```

### 选型建议

```
选 Graphiti 如果你:
  - 需要精确的时间推理（"什么时候发生"）
  - 需要追踪关系变化（"A 和 B 的关系怎么演变"）
  - 需要多跳推理（"A 的朋友在哪里工作"）
  - 愿意为更强的推理能力付出延迟和成本
  - 有 Neo4j 运维能力

选 Mem0 如果你:
  - 需要快速集成、低成本
  - 主要做事实记忆（不需要关系图）
  - 延迟敏感（<1s 响应）

选 OpenViking 如果你:
  - 管理大量不同类型的上下文
  - Token 成本是关键瓶颈
  - 需要 Agent 自主管理记忆结构
```
