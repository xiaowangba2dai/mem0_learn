# Mem0 算法演进：从 v2 到 v3 完全解析

> 本文基于 Mem0 开源代码库源码分析，由浅入深讲解记忆系统的算法演进。
> 适合算法零基础读者，配有大量 Demo 和图解。

---

## 目录

- [第零层：基础概念](#第零层基础概念)
- [第一层：v2 算法（经典版）](#第一层v2-算法经典版三段式流水线)
- [第二层：v3 算法（当前版）](#第二层v3-算法当前版重新设计)
- [第三层：v3 混合搜索](#第三层v3-的混合搜索hybrid-search)
- [第四层：Entity Linking vs Graph Memory](#第四层v3-的-entity-linking-vs-v2-的-graph-memory)
- [第五层：词形还原与 BM25](#第五层词形还原lemmatizationbm25-的秘密武器)
- [总结](#总结v2--v3-的核心改进)
- [附录：关键源码索引](#附录关键源码索引)

---

## 第零层：基础概念

### 什么是"记忆系统"？

想象你有一个 AI 助手。每次你跟它聊天，它都像个**失忆症患者**——上一秒你告诉它"我叫小明，我喜欢吃火锅"，下一秒它完全不记得了。

```
# 没有记忆系统的 AI
用户: 我叫小明，我喜欢吃火锅
AI: 好的，我记住了！
用户: 我叫什么？
AI: 抱歉，我不知道你叫什么...   ← 完全忘了！
```

记忆系统就是给 AI 装上一个**笔记本**，让它把重要信息记下来，下次聊天时可以翻看。

### 什么是向量（Embedding）？

计算机不认识文字，只认识数字。所以我们需要把文字变成一串数字（向量），才能存进计算机。

```
"我喜欢吃火锅" → [0.23, -0.15, 0.87, 0.42, ...]   (比如 1536 个数字)
"我爱吃麻辣烫" → [0.21, -0.12, 0.85, 0.40, ...]   (和上面很像！)
"今天天气不错" → [-0.55, 0.33, -0.12, 0.78, ...]  (和上面差别大)
```

**关键点**：语义相似的文字 → 向量也相似。这就是"向量搜索"的基础。

### Demo：向量搜索是怎么工作的？

```
步骤 1: 用户说 "我喜欢吃火锅"
步骤 2: 用 Embedding 模型把它变成向量 [0.23, -0.15, ...]
步骤 3: 存进向量数据库（就像一个"智能笔记本"）

后来用户问: "我想吃什么？"
步骤 1: 把问题也变成向量
步骤 2: 在数据库里找"最相似的向量"
步骤 3: 找到 "我喜欢吃火锅" → 回答："你想吃火锅！"
```

### 什么是 LLM（大语言模型）？

LLM 就是 ChatGPT、Claude 这类 AI 模型。在 Mem0 中，LLM 被当作一个"智能处理器"——你把对话丢给它，它帮你提取出关键信息。Mem0 通过精心设计的 Prompt（提示词）来指导 LLM 的行为。

### 什么是向量数据库？

向量数据库是专门存储和搜索向量的数据库。和普通数据库的区别：

```
普通数据库: SELECT * FROM memories WHERE text = "火锅"     ← 精确匹配
向量数据库: 搜索和 [0.23, -0.15, ...] 最相似的向量          ← 语义匹配
```

常见的向量数据库：Qdrant、Milvus、PGVector、Chroma、Pinecone 等。

---

## 第一层：v2 算法（经典版）——三段式流水线

### 1.1 记忆写入（add）的完整流程

v2 的核心思想是：**让 LLM（大语言模型）来帮你"整理笔记"**。

```
用户对话: "我叫小明，昨天和同事老王在海底捞吃了火锅，超开心"
         |
         v
    +----------------------+
    |  第一阶段：事实提取     |  <-- LLM 调用 #1
    |  (Fact Extraction)    |
    +----------------------+
         |
         |  LLM 从对话中提取出"事实碎片"：
         |  -> ["名字叫小明",
         |      "昨天和同事老王在海底捞吃了火锅",
         |      "吃火锅时很开心"]
         |
         v
    +----------------------+
    |  第二阶段：去重判断     |  <-- 向量相似度搜索
    |  (Deduplication)      |
    +----------------------+
         |
         |  拿每条事实去数据库里搜：
         |  "已经有类似的记忆了吗？"
         |  -> "名字叫小明" 找到了相似度 0.95 的旧记忆 "名字是小明"
         |  -> 其他两条没找到相似的
         |
         v
    +----------------------+
    |  第三阶段：操作决策     |  <-- LLM 调用 #2
    |  (ADD/UPDATE/DELETE)  |
    +----------------------+
         |
         |  LLM 对比新旧事实，决定操作：
         |  -> "名字叫小明" vs "名字是小明" -> UPDATE（合并为更丰富的版本）
         |  -> "昨天和同事老王在海底捞吃了火锅" -> ADD（全新信息）
         |  -> "吃火锅时很开心" -> ADD（全新信息）
```

### 1.2 v2 的 Prompt 解析（核心中的核心）

在 `configs/prompts.py` 中有两个关键的 Prompt。

#### Prompt 1: `FACT_RETRIEVAL_PROMPT` — 事实提取

```
你是一个"个人信息整理器"。
从对话中提取事实碎片。

示例：
输入: "Hi, my name is John. I am a software engineer."
输出: {"facts": ["Name is John", "Is a Software engineer"]}

输入: "Yesterday, I had a meeting with John at 3pm."
输出: {"facts": ["Had a meeting with John at 3pm"]}
```

这个 Prompt 的角色就像一个**速记员**——只管从对话里"抓"事实，不管这些事实是否已经记过了。

#### Prompt 2: `DEFAULT_UPDATE_MEMORY_PROMPT` — 操作决策

```
你是一个智能记忆管理器。
你可以执行四种操作：ADD、UPDATE、DELETE、NONE。

示例（UPDATE 场景）：
旧记忆: [{"id": "0", "text": "I really like cheese pizza"}]
新事实: ["Loves chicken pizza"]
-> 决策: UPDATE -> 合并为 "Loves cheese and chicken pizza"

示例（DELETE 场景）：
旧记忆: [{"id": "1", "text": "Loves cheese pizza"}]
新事实: ["Dislikes cheese pizza"]
-> 决策: DELETE -> 删除旧记忆（因为矛盾了）

示例（ADD 场景）：
旧记忆: [{"id": "0", "text": "User is a software engineer"}]
新事实: ["Name is John"]
-> 决策: ADD -> 新增一条记忆
```

这个 Prompt 的角色就像一个**图书管理员**——它要决定：这本新书是该放上新书架（ADD），还是替换掉旧版本（UPDATE），还是因为内容过时而销毁（DELETE）。

### 1.3 Demo：完整 v2 add 流程走一遍

```
场景：用户 Alice 第一次使用系统

=== 第一轮对话 ===
用户: "我是 Alice，在北京做产品经理"

Step 1: LLM 提取事实
  -> ["Name is Alice", "Works in Beijing", "Is a product manager"]

Step 2: 去重搜索（数据库是空的，没有匹配）

Step 3: LLM 决策
  -> 三条全部 ADD

数据库现在有 3 条记忆：
  [mem-001] "Name is Alice"
  [mem-002] "Works in Beijing"
  [mem-003] "Is a product manager"

=== 第二轮对话 ===
用户: "我换到上海工作了，新公司在做 AI 产品"

Step 1: LLM 提取事实
  -> ["Moved to Shanghai", "New company works on AI products"]

Step 2: 去重搜索
  -> "Moved to Shanghai" 和 "Works in Beijing" 相似度 0.72（相关但不同）
  -> "New company works on AI products" 没有匹配

Step 3: LLM 决策
  -> "Moved to Shanghai" vs 旧记忆 "Works in Beijing"
    -> UPDATE: 合并为 "Previously worked in Beijing, now works in Shanghai"
  -> "New company works on AI products" -> ADD

数据库现在有 4 条记忆：
  [mem-001] "Name is Alice"                                    (NONE)
  [mem-002] "Previously worked in Beijing, now in Shanghai"    (UPDATED)
  [mem-003] "Is a product manager"                             (NONE)
  [mem-004] "New company works on AI products"                 (ADDED)
```

### 1.4 v2 的图记忆系统（Graph Memory）

> 这是图相关的重点部分，从最基础开始讲起。

#### 什么是"图"（Graph）？

这里的"图"不是图片，而是一种**数据结构**。想象你在白板上画圈圈和箭头：

```
        +------+      likes       +------+
        |Alice | ----------------> |火锅   |
        +------+                   +------+
           |                         ^
           | works_at                |
           v                         |
        +------+      located_in   +------+
        |公司 A | ----------------> |北京   |
        +------+                   +------+
```

- **节点（Node）**：每个圈圈就是一个节点，代表一个"实体"（人、地点、事物）
- **边（Edge/Relationship）**：箭头就是边，代表实体之间的"关系"
- **属性（Property）**：节点和边上还可以附带额外信息

这就是**知识图谱（Knowledge Graph）**！

#### Demo：用生活例子理解图

```
你的社交关系就是一个图：

  [你] --friend--> [小明]
   |                 |
   |                 +--works_at--> [腾讯]
   |                 |
   +--friend--> [小红] --lives_in--> [上海]
   |
   +--pet--> [旺财] --is_a--> [金毛犬]
```

有了这个图，如果你问"你朋友小明在哪里工作？"：
- 沿着 `你 -> friend -> 小明 -> works_at -> 腾讯` 这条路径，就能找到答案！

#### Neo4j 是什么？

Neo4j 是一个专门存储和查询图的数据库。它用的查询语言叫 **Cypher**，类似 SQL 但是为图设计的。

```sql
-- SQL 风格（表格思维）
SELECT company FROM employment WHERE person = '小明'

-- Cypher 风格（图思维）
MATCH (p:Person {name: '小明'})-[:WORKS_AT]->(c:Company)
RETURN c.name
```

Cypher 的直觉理解：
- `()` 代表节点（圈圈）
- `-->` 代表箭头（关系）
- `[:WORKS_AT]` 代表关系的类型

#### v2 图记忆的完整工作流

在 v2 中，每次写入记忆，系统会同时做两件事：
1. 把文字存进向量数据库（前面讲的）
2. **把实体和关系存进图数据库（Neo4j）**

```
用户: "Alice 在海底捞和老王吃了火锅"

=== 向量存储路径 ===
"Alice 在海底捞和老王吃了火锅" -> 向量 -> 存入向量数据库

=== 图存储路径（重点！）===

Step 1: LLM 提取实体（_retrieve_nodes_from_data）
  LLM 被要求："从这句话里找出所有实体和类型"
  -> alice: person
  -> 海底捞: restaurant
  -> 老王: person

  代码位置：graph_memory.py 的 _retrieve_nodes_from_data()
  它用了一个 LLM tool call:
  {
    "name": "extract_entities",
    "arguments": {
      "entities": [
        {"entity": "Alice", "entity_type": "person"},
        {"entity": "海底捞", "entity_type": "restaurant"},
        {"entity": "老王", "entity_type": "person"}
      ]
    }
  }

Step 2: LLM 提取关系（_establish_nodes_relations_from_data）
  LLM 被要求："这些实体之间有什么关系？"
  -> {source: "alice", relationship: "ate_at", destination: "海底捞"}
  -> {source: "alice", relationship: "dined_with", destination: "老王"}
  -> {source: "老王", relationship: "ate_at", destination: "海底捞"}

Step 3: 在 Neo4j 中创建/更新图节点和边
  Cypher 大致如下：
  MERGE (a:__Entity__ {name: 'alice', user_id: 'alice'})
  MERGE (b:__Entity__ {name: '海底捞', user_id: 'alice'})
  MERGE (a)-[:ATE_AT]->(b)
  ...

最终图数据库里长这样：

  [alice] --ATE_AT--> [海底捞]
     |                   ^
     |                   |
     +--DINED_WITH--> [老王] --ATE_AT--+
```

#### v2 图搜索（search）流程

```
用户问: "Alice 上次和谁吃饭？"

Step 1: LLM 提取查询中的实体
  -> ["alice"]

Step 2: 在 Neo4j 中搜索相关节点和关系
  Cypher:
  MATCH (n:__Entity__ {name: 'alice'})-[r]->(m)
  RETURN n.name, type(r), m.name
  -> 返回: [alice, ATE_AT, 海底捞], [alice, DINED_WITH, 老王]

Step 3: 用 BM25 算法对结果重排序
  查询 "Alice 上次和谁吃饭" 和 "alice DINED_WITH 老王" 最相关
  -> 返回: {source: "alice", relationship: "dined_with", destination: "老王"}
```

#### v2 图记忆的问题

```
问题 1: 需要额外的 Neo4j 数据库 -> 部署复杂
问题 2: 每次写入需要 3+ 次 LLM 调用 -> 慢且贵
  - 1 次提取事实
  - 1 次提取实体
  - 1 次提取关系
  - 1 次决定 ADD/UPDATE/DELETE
问题 3: LLM 经常提取错误的关系 -> 图数据质量差
问题 4: Cypher 查询写死了模式 -> 不够灵活
```

---

## 第二层：v3 算法（当前版）——重新设计

### 2.1 核心哲学变化

```
v2 思维: "我要精确地管理记忆——新增、修改、删除"
         -> 像数据库的 CRUD 操作

v3 思维: "我只管添加，让检索来搞定一切"
         -> 像搜索引擎的"只追加日志"模式
```

为什么？因为 v2 的 UPDATE/DELETE 经常出错：

```
v2 的错误场景：
旧记忆: "用户喜欢吃火锅"
新事实: "用户最近开始吃素了"

v2 LLM 可能判断: DELETE "喜欢吃火锅"  <-- 错误！可能用户只是暂时吃素
正确做法: 两条都保留，让检索时"最近吃素"排前面
```

### 2.2 v3 的 add 流程：8 阶段批处理流水线

v3 代码中有清晰的 Phase 0-8 注释，逐阶段解析：

```
用户对话: "我是小明，昨天去了故宫拍照，还遇到了一只可爱的猫"

=======================================================
  Phase 0: 上下文收集 (Context Gathering)
=======================================================

  做什么: 获取当前会话的历史消息（最多 10 条）
  为什么: LLM 需要看到"上下文"才能理解代词和引用

  代码:
    session_scope = "user_id=alice"
    last_messages = db.get_last_messages(session_scope, limit=10)
    parsed_messages = parse_messages(messages)
    # -> "我是小明，昨天去了故宫拍照，还遇到了一只可爱的猫"

=======================================================
  Phase 1: 检索已有记忆 (Existing Memory Retrieval)
=======================================================

  做什么: 用新对话去向量库搜索 top-10 相关的旧记忆
  为什么: 给 LLM 提供"去重上下文"，让它知道什么已经记过了

  代码:
    query_embedding = embed(parsed_messages)
    existing_results = vector_store.search(
        query=parsed_messages,
        vectors=query_embedding,
        top_k=10,
        filters={"user_id": "alice"}
    )

  关键技巧: UUID 映射为整数（防幻觉）
    真实 UUID: "a1b2c3d4-5678-..."  -> LLM 容易搞混
    映射后:     {"id": "0", "text": "..."}  -> LLM 更容易处理

=======================================================
  Phase 2: LLM 单次提取 (Single-call Extraction) 核心!
=======================================================

  v2 需要 2 次 LLM 调用（提取 + 决策）
  v3 只需要 1 次！

  代码:
    system_prompt = ADDITIVE_EXTRACTION_PROMPT
    user_prompt = generate_additive_extraction_prompt(
        existing_memories=existing_memories,  # 已有记忆（用于去重）
        new_messages=parsed_messages,          # 新对话
        last_k_messages=last_messages,         # 历史消息（用于理解上下文）
    )
    response = llm.generate_response(...)

  LLM 输出示例:
  {
    "memory": [
      {"id": "0", "text": "用户的名字叫小明"},
      {"id": "1", "text": "用户在2025年6月14日去了故宫拍照"},
      {"id": "2", "text": "用户在故宫遇到了一只可爱的猫"}
    ]
  }

  注意: 只有 ADD 操作！没有 UPDATE/DELETE！
  每条记忆都是自包含的、上下文丰富的陈述。

=======================================================
  Phase 3: 批量嵌入 (Batch Embedding)
=======================================================

  做什么: 把所有提取出的记忆文本一次性变成向量
  优化: 批量调用比逐条调用快很多

  代码:
    mem_texts = ["用户的名字叫小明", "用户...去了故宫拍照", "用户...遇到了猫"]
    mem_embeddings = embedding_model.embed_batch(mem_texts)
    # -> 一次 API 调用拿到 3 个向量

=======================================================
  Phase 4-5: 去重 + 预处理 (CPU Processing + Hash Dedup)
=======================================================

  做什么:
    1. 对每条记忆文本算 MD5 哈希
    2. 和已有记忆的哈希对比 -> 完全相同就跳过
    3. 同批次内也去重（防止 LLM 输出重复）
    4. 对文本做词形还原（lemmatization）-> 用于 BM25 搜索

  代码:
    mem_hash = hashlib.md5(text.encode()).hexdigest()
    if mem_hash in existing_hashes or mem_hash in seen_hashes:
        continue  # 跳过重复

    text_lemmatized = lemmatize_for_bm25(text)
    # "用户去了故宫拍照" -> "用户 去 故宫 拍照"（去掉停用词，词形还原）

=======================================================
  Phase 6: 批量持久化 (Batch Persist)
=======================================================

  做什么: 把所有记忆一次性写入向量数据库
  优化: 批量 insert 比逐条 insert 快

=======================================================
  Phase 7: 实体链接 (Entity Linking) v3 新特性
=======================================================

  这是取代 v2 图记忆的新方案！后面详细讲。

=======================================================
  Phase 8: 保存消息历史
=======================================================

  做什么: 把原始对话存入 SQLite，供下次 Phase 0 使用
```

### 2.3 v3 的 ADDITIVE_EXTRACTION_PROMPT 详解

这是 v3 最核心的 Prompt，长达约 500 行，精心设计了指令让 LLM 变成一个"记忆提取器"。

#### 角色定义

```
You are a Memory Extractor — a precise, evidence-bound processor
responsible for extracting rich, contextual memories from conversations.
Your sole operation is ADD: identify every piece of memorable information
and produce self-contained, contextually rich factual statements.
```

翻译：你是一个记忆提取器，唯一的操作就是 ADD（添加）。

#### 提取规则

Prompt 定义了极其详细的提取规则，举几个关键例子：

**规则 1: 提取所有维度的信息，不要被主话题带偏**

```
示例对话:
  "我领养了一只叫 Max 的小狗！它是比格犬混血。"
  "对了，我周二开始上陶艺课了。做了一个印着女儿头像的杯子。"
  "我姐姐刚搬到波特兰。我最近升职当了团队主管，又开心又有点崩溃。"

期望输出（5 条独立记忆）：
  0: "用户在2025年3月初领养了一只比格犬混血小狗叫 Max"
  1: "用户周二开始上陶艺课"
  2: "用户在陶艺课上做了一个印着女儿头像的杯子"
  3: "用户的姐姐最近搬到了波特兰"
  4: "用户在2025年3月3日左右被提升为团队主管，感到开心但有些不堪重负"
```

**规则 2: 时间锚定——把相对时间转为绝对时间**

```
对话日期 (Observation Date): 2025-03-15

用户说: "昨天去了故宫"
  错误: "用户昨天去了故宫"        <-- 3个月后"昨天"就没有意义了
  正确: "用户在2025年3月14日去了故宫"  <-- 永远有意义
```

**规则 3: 保留具体细节，不要泛化**

```
错误: "用户看了一部奇幻书"
正确: "用户在读 'A Court of Thorns and Roses'"  <-- 保留书名

错误: "用户升职了"
正确: "用户被提升为 assistant manager"  <-- 保留具体职位
```

**规则 4: 记忆链接（Memory Linking）**

```
已有记忆:
  [mem-abc] "用户有一只狗叫 Poppy，是金毛犬"

新对话: "Poppy 昨天做了体检，很健康但要减肥"

LLM 输出:
  {
    "id": "0",
    "text": "用户的狗 Poppy 在2025年3月14日做了体检，健康但需要减肥",
    "linked_memory_ids": ["mem-abc"]   <-- 链接到旧记忆！
  }
```

### 2.4 Demo：v2 vs v3 对比

```
同一段对话: "以前喜欢喝可乐，现在改喝茶了"

=== v2 处理方式 ===
LLM 调用 #1 (提取): ["Used to like cola", "Now drinks tea"]
LLM 调用 #2 (决策):
  "Used to like cola" -> ADD
  "Now drinks tea" -> UPDATE 旧记忆 "Likes cola" -> "Switched from cola to tea"

结果: 1 条记忆 "Switched from cola to tea"
问题: 如果 LLM 判断错了，可能直接删掉 "Likes cola" 这条记忆

=== v3 处理方式 ===
LLM 调用 #1 (提取):
  [
    {"id": "0", "text": "用户以前喜欢喝可乐"},
    {"id": "1", "text": "用户现在改喝茶了，不再喝可乐"}
  ]

结果: 2 条记忆都保留
好处: 即使 "现在改喝茶" 了，"以前喜欢可乐" 这个历史事实也没丢
      搜索时，最新的（时间戳更近）会排前面
```

---

## 第三层：v3 的混合搜索（Hybrid Search）

这是 v3 最大的改进之一，也是算法精华所在。

### 3.1 三种搜索信号

```
v2: 只有 1 种信号 -> 向量相似度搜索（语义搜索）
v3: 3 种信号融合 -> 语义搜索 + BM25 关键词搜索 + 实体匹配增强
```

#### 信号 1: 语义搜索（Semantic Search）

```
查询: "Alice 喜欢吃什么"
  -> 向量: [0.23, -0.15, 0.87, ...]
  -> 在向量库中找最相似的向量
  -> 找到 "Alice loves cheese pizza" (相似度 0.82)
  -> 找到 "Alice works at Google" (相似度 0.31)
```

- 优点：能理解意思（"喜欢吃" 和 "loves" 意思相近）
- 缺点：对精确关键词不敏感（比如搜人名 "张三"，可能找不到）

#### 信号 2: BM25 关键词搜索

BM25 是一个经典的信息检索算法。

```
核心思想: 一个词越"稀有"，它匹配时的权重就越大。

Demo:
  文档 1: "用户 喜欢 吃 火锅"
  文档 2: "用户 在 北京 工作"
  文档 3: "用户 养了 一只 猫"

  查询: "火锅"
  -> "火锅" 只在文档 1 出现 -> 很稀有 -> 高分！
  -> "用户" 在 3 个文档都出现 -> 很常见 -> 低分

  所以文档 1 得到最高的 BM25 分数。
```

- 优点：对精确关键词非常准确
- 缺点：不理解语义（"喜欢" 和 "爱" 在 BM25 看来是不同的词）

**v3 的巧妙之处**：把 BM25 和语义搜索**加起来**！

```
搜索 "Alice 喜欢吃什么"

结果 1: "Alice loves cheese pizza"
  语义分数: 0.82 (意思很像)
  BM25 分数: 0.60 ("Alice" 和 "喜欢" 部分匹配)
  总分: (0.82 + 0.60) / 2.0 = 0.71

结果 2: "Alice 的猫叫 Kitty"
  语义分数: 0.45 (不太相关)
  BM25 分数: 0.30 ("Alice" 匹配)
  总分: (0.45 + 0.30) / 2.0 = 0.375
```

#### 信号 3: 实体匹配增强（Entity Boost）

这是 v3 最创新的部分，也是替代 v2 图记忆的方案。后面在第四层详细讲。

### 3.2 搜索完整流程

```
搜索查询: "Marcus 在 Shopify 的工作怎么样了？"

    Step 1: 预处理查询
      -> 词形还原: "Marcus Shopify 工作"
      -> 提取实体: [("PROPER", "Marcus"), ("PROPER", "Shopify")]

    Step 2: 语义搜索（向量相似度）
      -> 取 top-60 个候选（4 倍过采样）
      -> 得到每条记忆的语义分数

    Step 3: BM25 关键词搜索
      -> 用词形还原后的查询做全文检索
      -> 用 Sigmoid 归一化到 [0, 1]

    Step 4: 实体增强
      -> 用提取的实体去实体集合中搜索
      -> 计算 boost 分数

    Step 5: 分数融合
      -> combined = (semantic + bm25 + entity_boost) / max_possible
      -> 按分数排序，取 top-k

    Step 6: 返回结果
      -> 每条结果包含: id, memory, score
      -> 如果 explain=True，还包含 score_details 分解
```

### 3.3 分数融合算法（scoring.py）

```python
# 最终的分数计算公式：

combined_score = (semantic + bm25 + entity_boost) / max_possible

# max_possible 根据可用信号动态调整：
# 只有语义:          max_possible = 1.0
# 语义 + BM25:       max_possible = 2.0
# 语义 + 实体:       max_possible = 1.5
# 语义 + BM25 + 实体: max_possible = 2.5

# 这保证了最终分数始终在 [0, 1] 范围内
```

### 3.4 BM25 归一化（Sigmoid 函数）

BM25 的原始分数是没有上限的（可能 0 到 20+），需要归一化到 [0, 1]：

```
Sigmoid 函数: normalized = 1 / (1 + e^(-steepness * (score - midpoint)))

直觉理解:
  score 远小于 midpoint -> 输出接近 0
  score 等于 midpoint   -> 输出等于 0.5
  score 远大于 midpoint -> 输出接近 1
```

查询越长（词越多），BM25 原始分数越高，所以 midpoint 要调大：

| 查询词数 | midpoint | steepness |
|---------|----------|-----------|
| <=3     | 5.0      | 0.7       |
| 4-6     | 7.0      | 0.6       |
| 7-9     | 9.0      | 0.5       |
| 10-15   | 10.0     | 0.5       |
| 15+     | 12.0     | 0.5       |

### 3.5 Demo：完整搜索走一遍

```
记忆库中有:
  [mem-001] "Marcus was promoted to Senior Engineer at Shopify"
  [mem-002] "Marcus has a wife named Elena"
  [mem-003] "Shopify stock went up 5% today"
  [mem-004] "User likes hiking on weekends"

查询: "Marcus 在 Shopify 的工作"

--- 语义搜索结果 ---
  mem-001: semantic=0.85  (非常相关)
  mem-003: semantic=0.55  (提到了 Shopify)
  mem-002: semantic=0.45  (提到了 Marcus)
  mem-004: semantic=0.20  (不相关)

--- BM25 搜索结果 ---
  mem-001: bm25=0.70  ("Marcus" + "Shopify" + "工作/Engineer" 都匹配)
  mem-003: bm25=0.40  ("Shopify" 匹配)
  mem-002: bm25=0.30  ("Marcus" 匹配)
  mem-004: bm25=0.00  (无匹配)

--- 实体增强结果 ---
  查询实体: Marcus, Shopify
  "Marcus" 关联: mem-001, mem-002  -> boost = 0.48
  "Shopify" 关联: mem-001, mem-003 -> boost = 0.47

--- 分数融合 (max_possible = 2.5) ---
  mem-001: (0.85 + 0.70 + 0.48) / 2.5 = 0.812  排名第 1
  mem-003: (0.55 + 0.40 + 0.47) / 2.5 = 0.568  排名第 2
  mem-002: (0.45 + 0.30 + 0.48) / 2.5 = 0.492  排名第 3
  mem-004: (0.20 + 0.00 + 0.00) / 2.5 = 0.080  被 threshold=0.1 过滤
```

---

## 第四层：v3 的 Entity Linking vs v2 的 Graph Memory

### 4.1 什么是实体（Entity）？

实体就是文本中提到的"具体的东西"——人名、地名、品牌、书名等。

```
"用户的小狗 Poppy 昨天去了 Central Park 散步"
  实体: Poppy (动物名), Central Park (地点)
```

### 4.2 v3 的实体提取算法（entity_extraction.py）

v3 用 **spaCy**（一个 NLP 库）从文本中提取 4 类实体：

```
类型 1: PROPER（专有名词）
  "John Smith" -> 大写开头的词序列
  "Shopify"    -> 品牌名
  "Central Park" -> 地名

类型 2: QUOTED（引号内文本）
  "The Last Dance" -> 书名/电影名
  'aerial yoga'    -> 特定术语

类型 3: COMPOUND（复合名词短语）
  "machine learning" -> 名词+名词
  "cherry tomato"    -> 修饰词+名词

类型 4: NOUN（单名词回退）
  "yoga" -> 当没有复合结构时的兜底
```

#### Demo：实体提取过程

```python
text = "Marcus was promoted to Senior Engineer at Shopify"

# spaCy 分析每个词的词性和语法角色:
# Marcus      -> PROPN (专有名词)
# was         -> AUX
# promoted    -> VERB
# to          -> ADP
# Senior      -> ADJ (大写!)
# Engineer    -> NOUN (大写!)
# at          -> ADP
# Shopify     -> PROPN (专有名词)

# 提取结果:
# ("PROPER", "Marcus")           <- 大写专有名词
# ("PROPER", "Senior Engineer")  <- 大写序列
# ("PROPER", "Shopify")          <- 大写专有名词
```

#### 实体提取的详细规则

代码中定义了很多"过滤规则"来避免提取出无意义的实体：

```
1. 过滤通用词:
   _GENERIC_HEADS = {"thing", "stuff", "way", "time", "experience", ...}
   -> "a good experience" 不会被提取（"experience" 太通用了）

2. 过滤非具体形容词:
   _NON_SPECIFIC_ADJ = {"many", "few", "good", "bad", "big", "small", ...}
   -> "big problem" 不会被提取（"big" 不够具体）

3. 过滤句首大写:
   -> "The weather is nice" 中的 "The" 不会因为大写而被提取

4. 去重和子串清理:
   -> 如果同时提取了 "Senior Engineer" 和 "Engineer"，只保留长的那个
```

### 4.3 实体存储（Entity Store）

v3 在向量数据库里创建了一个**额外的集合**来存实体：

```
主集合 (memories):            实体集合 (memories_entities):
+-------------------+        +----------------------------+
| id: mem-001       |        | id: ent-001                |
| text: "Marcus...  | <----  | data: "Marcus"             |
|       at Shopify" |  link  | entity_type: "PROPER"      |
| user_id: alice    |        | linked_memory_ids:         |
+-------------------+        |   ["mem-001"]              |
                             | user_id: alice             |
                             +----------------------------+

                             +----------------------------+
                             | id: ent-002                |
                             | data: "Shopify"            |
                             | entity_type: "PROPER"      |
                             | linked_memory_ids:         |
                             |   ["mem-001"]              |
                             | user_id: alice             |
                             +----------------------------+
```

关键设计：`linked_memory_ids` 字段记录了"这个实体出现在哪些记忆里"。

### 4.4 实体增强搜索完整流程

```
搜索查询: "Marcus 在 Shopify 的工作怎么样了？"

Step 1: 从查询中提取实体
  -> [("PROPER", "Marcus"), ("PROPER", "Shopify")]

Step 2: 对每个实体，在实体集合中搜索
  搜索 "Marcus":
    -> 找到 ent-001 (相似度 0.98)
    -> linked_memory_ids: ["mem-001", "mem-015", "mem-023"]

  搜索 "Shopify":
    -> 找到 ent-002 (相似度 0.97)
    -> linked_memory_ids: ["mem-001", "mem-008"]

Step 3: 计算实体增强分数 (Entity Boost)
  公式: boost = similarity * 0.5 * memory_count_weight

  其中 memory_count_weight = 1 / (1 + 0.001 * (n-1)^2)
  -> 如果一个实体关联了太多记忆，权重会下降（防止"常见实体"干扰）

  "Marcus" (关联 3 条记忆):
    boost = 0.98 * 0.5 * 1/(1 + 0.001*4) = 0.488

  "Shopify" (关联 2 条记忆):
    boost = 0.97 * 0.5 * 1/(1 + 0.001*1) = 0.484

Step 4: 合并到最终分数
  mem-001 "Marcus 在 Shopify 当高级工程师":
    语义: 0.75, BM25: 0.40, 实体boost: 0.488
    最终: (0.75 + 0.40 + 0.488) / 2.5 = 0.655

  mem-008 "Shopify 的股价今天涨了 5%":
    语义: 0.35, BM25: 0.20, 实体boost: 0.484
    最终: (0.35 + 0.20 + 0.484) / 2.5 = 0.414

  -> mem-001 排在前面！虽然 mem-008 也提到了 Shopify，
    但 mem-001 同时匹配了 Marcus 和 Shopify 两个实体
```

### 4.5 实体链接写入流程（Phase 7 详解）

```
当 add() 产生了 3 条新记忆后，Phase 7 开始工作：

新记忆:
  mem-001: "Marcus was promoted to Senior Engineer at Shopify"
  mem-002: "Marcus has a wife named Elena"
  mem-003: "Marcus and Elena celebrated at Osteria Francescana"

Step 7a: 全局实体去重
  对所有记忆做实体提取，合并相同实体：
  "Marcus" -> 出现在 mem-001, mem-002, mem-003
  "Shopify" -> 出现在 mem-001
  "Elena" -> 出现在 mem-002, mem-003
  "Osteria Francescana" -> 出现在 mem-003

Step 7b: 批量嵌入实体
  一次 API 调用嵌入所有实体文本

Step 7c: 批量搜索已有实体
  在实体集合中搜索每个实体是否已存在
  -> "Marcus" 已存在 (相似度 0.99)，只需更新 linked_memory_ids
  -> "Elena" 不存在，需要新建
  -> "Shopify" 已存在 (相似度 0.98)，更新 linked_memory_ids
  -> "Osteria Francescana" 不存在，需要新建

Step 7d-7e: 分别批量 update 和 insert
```

### 4.6 v2 Graph Memory vs v3 Entity Linking 对比

| 维度 | v2 Graph Memory | v3 Entity Linking |
|------|-----------------|-------------------|
| 外部依赖 | Neo4j 图数据库 | 无（复用现有向量数据库） |
| LLM 调用次数 | 3-4 次（提取+实体+关系+决策） | 1 次（提取+链接一步到位） |
| 实体提取方式 | LLM tool call | spaCy NLP（本地、快速） |
| 关系建模 | 显式的边（ATE_AT 等） | 隐式的（shared entity link） |
| 查询方式 | Cypher 图查询 | 向量搜索 + 分数增强 |
| 部署复杂度 | 高（需要额外数据库） | 低（无额外依赖） |
| 灵活性 | 低（写死了查询模式） | 高（自然融入检索流程） |
| 错误容忍度 | 低（关系提取错误导致图混乱） | 高（实体匹配不依赖精确关系） |

### 4.7 为什么 v3 放弃了显式的图？

```
v2 图的问题 Demo:

用户说: "Alice 在海底捞和老王吃了火锅"
v2 需要 LLM 提取出:
  实体: alice, 海底捞, 老王
  关系: alice -ATE_AT-> 海底捞, alice -DINED_WITH-> 老王

但如果 LLM 搞错了关系方向:
  错误: 海底捞 -ATE_AT-> alice  <- 完全反了！
  那搜索时就找不到正确结果。

v3 的做法:
  不需要显式关系！
  只需要知道 "alice"、"海底捞"、"老王" 都出现在同一条记忆里
  搜索时，只要匹配到其中任何一个实体，这条记忆就会被"增强"
```

### 4.8 Memory Linking（记忆之间的链接）

v3 虽然没有显式的图，但有一个更轻量的链接机制：

```
Prompt 中的 linked_memory_ids 字段:

已有记忆:
  [mem-abc] "用户有一只狗叫 Poppy"
  [mem-def] "用户是 Shopify 的高级工程师"

新对话: "Poppy 昨天做了体检，很健康。另外我下个月要换到支付团队了"

LLM 输出:
{
  "memory": [
    {
      "id": "0",
      "text": "用户的狗 Poppy 在2025年3月14日做了体检，很健康",
      "linked_memory_ids": ["mem-abc"]   <- 链接到旧的 Poppy 记忆！
    },
    {
      "id": "1",
      "text": "用户下个月要在 Shopify 换到支付团队",
      "linked_memory_ids": ["mem-def"]   <- 链接到旧的工作记忆！
    }
  ]
}
```

这个机制让系统可以在检索时"沿着链接"找到相关的旧记忆，形成一种**隐式的记忆图谱**。

---

## 第五层：词形还原（Lemmatization）——BM25 的秘密武器

### 5.1 为什么需要词形还原？

```
问题: "attending" 和 "attend" 在 BM25 看来是不同的词！

用户写入: "I am attending a meeting about AI"
用户搜索: "What meetings did I attend?"

如果不做词形还原:
  存储的关键词: "attending", "meeting", "about", "AI"
  搜索的关键词: "meetings", "did", "attend"
  -> "attending" != "attend" (不匹配!)
  -> "meeting" != "meetings" (不匹配!)

如果做词形还原:
  存储的关键词: "attend", "meeting", "about", "AI"
  搜索的关键词: "meeting", "do", "attend"
  -> "attend" == "attend" (匹配!)
  -> "meeting" == "meeting" (匹配!)
```

### 5.2 v3 的词形还原策略（lemmatization.py）

```python
# v3 用 spaCy 做词形还原，比传统 stemmer 更准确:

"attending"  -> "attend"     (动词还原)
"memories"   -> "memory"     (复数还原)
"went"       -> "go"         (过去时还原)
"older"      -> "old"        (比较级还原)
```

### 5.3 特殊处理：-ing 词的双保留

```
坑: "meeting" 既可以是名词（会议）也可以是动词（见面）
  -> 名词 meeting: lemma 是 "meeting"
  -> 动词 meeting: lemma 是 "meet"

spaCy 根据上下文决定，但有时会搞错。

v3 的解决方案: 两种形式都保留！
  "I am attending a meeting"
  -> "attend meeting attending meet"
  -> 既保留了原词 "attending"，也保留了词根 "attend"
  -> 搜索 "attend" 或 "attending" 都能匹配上
```

### 5.4 Demo：完整的 BM25 匹配流程

```
存储记忆: "Marcus was promoted to Senior Engineer at Shopify"
  词形还原后: "Marcus promote senior engineer Shopify"

搜索查询: "What promotion did Marcus get at Shopify?"
  词形还原后: "promotion Marcus get Shopify"

BM25 匹配:
  "Marcus"  -> 匹配! (出现在存储和查询中)
  "Shopify" -> 匹配!
  "promotion" vs "promote" -> 不匹配 (BM25 的局限)
  "get" -> 不匹配
  "senior" -> 不匹配 (只在存储中)
  "engineer" -> 不匹配 (只在存储中)

BM25 分数: 中等（匹配了 2 个词）
但语义搜索会弥补这个不足！
```

---

## 总结：v2 -> v3 的核心改进

### 整体对比表

| 维度 | v2 | v3 |
|------|----|----|
| 提取方式 | 2 次 LLM 调用 | 1 次 LLM 调用 |
| 操作决策 | ADD/UPDATE/DELETE | 仅 ADD |
| 搜索方式 | 纯语义搜索 | 语义 + BM25 + 实体增强 |
| 图存储 | Neo4j 图数据库 | 向量库内 Entity Linking |
| 实体提取 | LLM tool call (慢、贵) | spaCy NLP (本地、快) |
| 去重方式 | 向量相似度 + LLM 判断 | MD5 哈希 + LLM 去重 |
| 部署依赖 | 向量库 + Neo4j | 仅向量库 |
| 性能 | LoCoMo 71.4 | LoCoMo 91.6 (+20) |
| | LongMemEval 67.8 | LongMemEval 93.4 (+26) |

### 优雅降级机制

v3 的设计哲学是"有则更好，无也能用"：

| 缺少的依赖 | 影响 | 搜索还能工作吗？ |
|-----------|------|--------------|
| spaCy (NLP) | 无实体提取，无词形还原 | 是（纯语义搜索） |
| fastembed (Qdrant) | 无 BM25 关键词搜索 | 是（语义 + 实体） |
| 实体集合不可用 | 无实体增强 | 是（语义 + BM25） |

### 架构决策复盘

```
Q: 为什么 v3 只用 ADD 而不用 UPDATE/DELETE？
A: 因为 LLM 做 UPDATE/DELETE 经常出错。比如用户说"最近开始吃素"，
   LLM 可能删掉"喜欢吃火锅"的记忆——但用户可能只是暂时吃素。
   ADD-only 让信息不断积累，由检索来排序。

Q: 为什么 v3 用 Entity Linking 替代了 Neo4j 图？
A: (1) Neo4j 增加了部署复杂度 (2) LLM 提取关系经常出错
   (3) Entity Linking 用向量搜索实现类似效果，无需额外数据库。

Q: 为什么选择向量 + BM25 混合检索？
A: 向量搜索擅长语义理解（"喜欢" ≈ "爱"），但对精确关键词不敏感。
   BM25 擅长精确匹配（"Marcus" 就是 "Marcus"），但不理解语义。
   两者互补，融合后效果大幅提升。

Q: 为什么记忆操作由 LLM 决策而不是规则？
A: 规则无法处理语言的模糊性。比如 "Loves chicken pizza" 和
   "Likes cheese pizza" 是否矛盾？需要 LLM 的语义理解能力来判断。
```

---

## 附录：关键源码索引

### 核心文件

| 文件 | 作用 | 行数 |
|------|------|------|
| `mem0/memory/main.py` | Memory 类，整个系统的编排中心 | ~3500 |
| `mem0/configs/prompts.py` | 所有 Prompt 定义，系统的"灵魂" | ~1063 |
| `mem0/utils/entity_extraction.py` | 实体提取算法 | ~358 |
| `mem0/utils/scoring.py` | 分数融合算法 | ~140 |
| `mem0/utils/lemmatization.py` | BM25 词形还原 | ~51 |

### 关键代码位置

```
add() 流程:
  main.py:653       -> add() 入口
  main.py:761       -> _add_to_vector_store() 核心
  main.py:798       -> Phase 0: Context gathering
  main.py:806       -> Phase 1: Existing memory retrieval
  main.py:823       -> Phase 2: LLM extraction
  main.py:869       -> Phase 3: Batch embed
  main.py:883       -> Phase 4-5: CPU processing + Hash dedup
  main.py:923       -> Phase 6: Batch persist
  main.py:965       -> Phase 7: Batch entity linking

search() 流程:
  main.py:1232      -> search() 入口
  main.py:1477      -> _search_vector_store() 核心
  main.py:1483      -> Step 1: Preprocess query
  main.py:1487      -> Step 2: Embed query
  main.py:1490      -> Step 3: Semantic search
  main.py:1496      -> Step 4: Keyword search
  main.py:1501      -> Step 5: Compute BM25 scores
  main.py:1512      -> Step 6: Compute entity boosts
  main.py:1526      -> Step 8: Score and rank

Entity Linking:
  entity_extraction.py:123  -> extract_entities() 公共 API
  entity_extraction.py:147  -> extract_entities_batch() 批量 API
  entity_extraction.py:177  -> _extract_entities_from_doc() 核心算法
  main.py:502               -> _upsert_entity() 实体写入
  main.py:600               -> _link_entities_for_memory() 实体链接
  main.py:1577              -> _compute_entity_boosts() 搜索增强

Prompt 系统:
  prompts.py:15             -> FACT_RETRIEVAL_PROMPT (v2 事实提取)
  prompts.py:63             -> USER_MEMORY_EXTRACTION_PROMPT (v2 增强版)
  prompts.py:124            -> AGENT_MEMORY_EXTRACTION_PROMPT (v2 Agent版)
  prompts.py:176            -> DEFAULT_UPDATE_MEMORY_PROMPT (v2 操作决策)
  prompts.py:468            -> ADDITIVE_EXTRACTION_PROMPT (v3 核心)
  prompts.py:1016           -> generate_additive_extraction_prompt() (v3 构建)
```

### 关键设计模式

```
1. 工厂模式 (Factory Pattern)
   LlmFactory / EmbedderFactory / VectorStoreFactory
   -> 根据配置字符串动态创建对应的适配器
   -> 例: LlmFactory.create("openai", config) -> OpenAI LLM 实例

2. 策略模式 (Strategy Pattern)
   BaseLLM / BaseEmbedding / BaseVectorStore
   -> 定义统一接口，不同后端实现可插拔替换
   -> 例: QdrantVectorStore 和 MilvusVectorStore 都实现 search()

3. 编排者模式 (Orchestrator)
   Memory 类
   -> 不负责具体实现，只负责编排流程
   -> 协调 LLM + Embedder + VectorStore + EntityStore

4. Prompt Engineering as Configuration
   -> LLM 的行为完全由 Prompt 定义
   -> 修改 Prompt 就能改变系统行为
   -> 这是"用配置代替代码"的一种形式
```

---

## 第六层：论文深度解读

### 论文基本信息

```
标题: Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory
作者: Prateek Chhikara, Dev Khant, Saket Aryan, Taranjeet Singh, Deshraj Yadav
发表: 2025 年 4 月 (arXiv: 2504.19413)
会议: ECAI 2025 (European Conference on Artificial Intelligence)
提交: ICLR 2026 (OpenReview)
GitHub: github.com/mem0ai/mem0
```

### 论文要解决的核心问题

```
问题: LLM 天然是"无状态"的
  - 每次对话都是全新开始
  - 无法记住用户偏好和历史
  - 上下文窗口有限（即使 128K tokens 也装不下几个月的对话）

现有方案的不足:
  方案 A: 把全部对话塞进 Prompt
    -> 超出上下文窗口限制
    -> Token 成本极高
    -> 大量无关信息干扰

  方案 B: RAG（检索增强生成）
    -> 只做"检索"，不做"记忆整理"
    -> 无法处理信息变化（比如用户搬家了）
    -> 缺乏跨会话的连续性

  方案 C: 简单键值存储
    -> 无法处理自然语言的模糊性
    -> 无法自动提取和更新
```

### 论文的核心贡献

#### 贡献 1: 两阶段架构（Two-Phase Architecture）

论文提出了 Mem0 的核心架构——将记忆管理分为**提取阶段**和**更新阶段**。

```
                    Mem0 两阶段架构

    用户对话消息
         |
         v
    +----------------------------+
    |     Phase 1: 提取阶段       |
    |     (Extraction Phase)     |
    |                            |
    |  1. 接收对话消息             |
    |  2. LLM 从对话中提取事实     |
    |  3. 输出: 事实列表           |
    +----------------------------+
         |
         |  提取出的事实:
         |  ["名字叫小明", "在北京工作", "喜欢火锅"]
         |
         v
    +----------------------------+
    |     Phase 2: 更新阶段       |
    |     (Update Phase)         |
    |                            |
    |  1. 将新事实向量化           |
    |  2. 在向量库中搜索相似记忆   |
    |  3. LLM 对比新旧事实        |
    |  4. 决定: ADD/UPDATE/DELETE |
    |  5. 执行决策写入数据库       |
    +----------------------------+
         |
         v
    向量数据库中的记忆集合
```

**提取阶段的关键设计**：

```
输入: 用户对话消息
工具: FACT_RETRIEVAL_PROMPT (事实提取提示词)

LLM 被指导为"个人信息整理器"，负责:
  - 从对话中提取可记忆的事实
  - 区分"值得记住的信息"和"闲聊"
  - 输出结构化的事实列表

示例:
  对话: "昨天我和老王在海底捞吃了火锅，聊了很多关于AI的话题"
  提取: ["昨天和老王在海底捞吃了火锅",
         "和老王讨论了AI相关话题"]
  不提取: "昨天"(太模糊), "很多"(不是事实)
```

**更新阶段的关键设计**：

```
输入: 新提取的事实 + 数据库中已有的相似记忆
工具: DEFAULT_UPDATE_MEMORY_PROMPT (操作决策提示词)

LLM 被指导为"智能记忆管理器"，负责四种操作:

  ADD    — 全新信息，数据库中没有 → 新增
  UPDATE — 相关信息已有但需要更新 → 修改（保持同一 ID）
  DELETE — 新信息与旧记忆矛盾    → 删除旧记忆
  NONE   — 信息已存在，无需变更   → 不做任何操作

关键: LLM 必须返回结构化 JSON，包含:
  {
    "id": "记忆ID",
    "text": "记忆内容",
    "event": "ADD/UPDATE/DELETE/NONE",
    "old_memory": "旧记忆内容（仅 UPDATE 时）"
  }
```

#### 贡献 2: 记忆操作决策（ADD/UPDATE/DELETE）

这是论文的核心创新之一——用 LLM 来做"记忆管理决策"，而不是用硬编码规则。

```
为什么不用规则？

规则方式:
  if 相似度 > 0.9:
    UPDATE
  elif 相似度 > 0.7:
    让LLM判断
  else:
    ADD

问题:
  "喜欢吃火锅" 和 "最近开始吃素" 相似度可能是 0.6
  规则会判断为 ADD（新增），但实际应该 DELETE（删除旧偏好）
  因为这是语义上的"矛盾"，不是"相似"

LLM 方式:
  LLM 理解语义:
  "喜欢吃火锅" + "最近开始吃素"
  → LLM 判断这是矛盾的 → DELETE

  LLM 也理解"补充"和"替换"的区别:
  "喜欢奶酪披萨" + "也喜欢鸡肉披萨"
  → LLM 判断这是补充 → UPDATE 为 "喜欢奶酪和鸡肉披萨"

  "喜欢奶酪披萨" + "讨厌奶酪披萨"
  → LLM 判断这是矛盾 → DELETE
```

#### 贡献 3: 可扩展的记忆层设计

```
论文强调的设计原则:

1. 组件可插拔
   - LLM: OpenAI / Anthropic / Ollama / Azure...
   - 向量库: Qdrant / Milvus / PGVector / Chroma...
   - 嵌入模型: OpenAI / HuggingFace / Azure...
   - 图数据库: Neo4j / Memgraph（v2）

2. 记忆作用域隔离
   - user_id: 按用户隔离记忆
   - agent_id: 按 Agent 隔离记忆
   - run_id: 按运行会话隔离记忆
   - 可以组合使用

3. 工厂模式组装
   Memory.from_config({
     "llm": {"provider": "openai", "config": {"model": "gpt-4"}},
     "vector_store": {"provider": "qdrant", "config": {...}},
     "embedder": {"provider": "openai", "config": {...}}
   })
```

### 论文中的评估结果

#### 三大评估基准

论文在三个基准上进行了评估：

**基准 1: LoCoMo (Long Conversation Memory)**

```
来源: Snap Research, ACL 2024
论文: "Evaluating Very Long-Term Conversational Memory of LLM Agents"
规模: 10 段多会话对话，~5900 轮对话，1986 个 QA 对

五种问题类型:
  1. Single-Hop（单跳事实检索）
     "用户最喜欢的颜色是什么？" → 直接在记忆中找到答案

  2. Multi-Hop（多跳推理）
     "用户上次旅行的目的地在哪个国家？"
     → 需要先从记忆中找到"上次旅行去了巴黎"
     → 再推理出"巴黎在法国"

  3. Temporal（时间推理）
     "用户是什么时候开始学钢琴的？"
     → 需要理解时间信息并做日期计算

  4. Open-Domain（开放域）
     通用知识问题

  5. Adversarial（对抗性）
     "用户的猫叫什么名字？"（但用户从未提过猫）
     → 测试模型是否会在没有信息时编造答案

评分方法:
  - 每种类型独立评分（F1 + BLEU-1 + LLM Judge）
  - 总分 = 各类型的加权平均（按题目数量加权）
  - Adversarial 类型有时被排除在总分外

Mem0 结果: 71.4 (v2) → 91.6 (v3)  ← +20 分提升
```

**基准 2: LongMemEval**

```
来源: Wu et al., 2024
论文: "Benchmarking Chat Assistants on Long-Term Interactive Memory"
GitHub: github.com/xiaowu0162/longmemeval

五种核心能力测试:
  1. Information Extraction（信息提取）
     从过去的交互中检索特定事实

  2. Multi-hop Reasoning（多跳推理）
     组合来自多个会话/对话的信息

  3. Temporal Reasoning（时间推理）
     理解事件在对话历史中发生的时间

  4. Knowledge Update（知识更新）
     跟踪随时间的变化或修正

  5. Adversarial Resistance（对抗性抵抗）
     处理旨在探测记忆不准确的问题

主要指标: Accuracy（准确率）
特点: 模拟真实的多会话对话场景

Mem0 结果: 67.8 (v2) → 93.4 (v3)  ← +26 分提升
```

**基准 3: BEAM (Benchmark for Evaluation at Agent Memory)**

```
来源: Mem0 论文中提出
特点: 生产规模的记忆评估

测试规模:
  - 100K tokens（小规模）
  - 500K tokens（中规模）
  - 1M tokens（大规模）← Mem0: 64.1%
  - 10M tokens（超大规模）← Mem0: 48.6%

设计目标:
  - 测试记忆系统在真实生产负载下的表现
  - 10M tokens 是关键阈值——超过上下文窗口，
    必须依赖真正的记忆检索而不是暴力塞入 Prompt
  - 100 conversations x 2,000 questions

关键发现:
  - 随着 Token 量增大，所有系统性能都下降
  - Mem0 在 1M 级别仍能保持 64.1%
  - 在 10M 级别下降到 48.6%（但仍是可用的）
```

#### 结果汇总表

```
+----------------+-----------+-----------+------------+
| 基准           | v2 分数   | v3 分数   | 提升       |
+----------------+-----------+-----------+------------+
| LoCoMo         | 71.4      | 91.6      | +20.2      |
| LongMemEval    | 67.8      | 93.4      | +25.6      |
| BEAM @ 1M      | -         | 64.1      | (新基准)   |
| BEAM @ 10M     | -         | 48.6      | (新基准)   |
+----------------+-----------+-----------+------------+

Token 效率:
  v3 平均每次检索调用 < 7,000 tokens
  （相比把全部对话塞入 Prompt 的数 10 万 tokens，极大节省成本）
```

### v2 到 v3 的架构演进（论文视角）

```
v2 架构（论文原始设计）:
  ┌─────────────────────────────────────────┐
  │  对话 → 提取事实 → 向量化 → 搜索相似记忆  │
  │       → LLM 决策(ADD/UPDATE/DELETE)     │
  │       → 执行决策 → 写入向量库            │
  │                                         │
  │  同时: → LLM 提取实体 → LLM 提取关系    │
  │        → 写入 Neo4j 图数据库            │
  └─────────────────────────────────────────┘
  问题: 2-4 次 LLM 调用，慢、贵、容易出错

v3 架构（论文后续演进）:
  ┌─────────────────────────────────────────┐
  │  对话 → 检索相关记忆(top-10)             │
  │       → 单次 LLM 提取(ADD-only)         │
  │       → 批量嵌入 → 哈希去重             │
  │       → 批量写入向量库                   │
  │       → spaCy 实体提取 → 实体链接       │
  └─────────────────────────────────────────┘
  改进: 1 次 LLM 调用，快、便宜、更准确

  搜索: 语义 + BM25 + 实体增强 = 三信号融合
```

### 论文的核心论点与 Demo 解释

#### 论点 1: "记忆提取应该由 LLM 完成"

```
传统方式（规则提取）:
  if "我叫" in message:
    name = message.split("我叫")[1]
    store("用户名字是" + name)

  问题:
    "我叫小明" → 能提取
    "My name is John" → 不能提取（英文）
    "大家都叫我小明" → 错误提取（"大家都"不是名字）
    "我叫了外卖" → 错误提取（"叫了外卖"不是名字）

LLM 方式:
  所有以上情况 LLM 都能正确处理:
    "我叫小明" → ["名字是小明"]
    "My name is John" → ["Name is John"]
    "大家都叫我小明" → ["名字是小明"]（正确理解）
    "我叫了外卖" → []（正确判断不是个人信息）
```

#### 论点 2: "记忆操作决策应该由 LLM 完成"

```
场景: 用户上个月说"我住在北京"，今天说"刚搬到上海"

规则方式:
  相似度("我住在北京", "刚搬到上海") = 0.65
  → 规则判断: 相似度 < 0.7 → ADD（新增）
  → 数据库同时有"住在北京"和"搬到上海"
  → 矛盾！

LLM 方式:
  LLM 理解语义: "搬到上海"暗示"不再住北京"
  → 判断: UPDATE
  → 合并为 "Previously lived in Beijing, moved to Shanghai"
  → 数据库保持一致
```

#### 论点 3: "ADD-only 比 ADD/UPDATE/DELETE 更好"

```
这是 v3 最重要的论点。

反直觉: 不更新、不删除，只管添加，怎么可能更好？

理由:
  1. LLM 做 UPDATE/DELETE 经常出错
     旧记忆: "用户在 Google 工作"
     新事实: "用户跳槽到了 Meta"
     LLM 可能: DELETE "在 Google 工作"
     但"在 Google 工作过"是有价值的历史信息！

  2. 保留历史比修改历史更安全
     ADD-only: 保留两条
       "用户在 Google 工作" (created: 2024-01)
       "用户在 2025 年跳槽到了 Meta" (created: 2025-03)
     → 搜索"工作经历"时两条都能找到

  3. 检索排序替代记忆更新
     时间戳 + 相关性 → 最新信息自然排前面
     → "用户跳槽到了 Meta" 因为更相关会排在前面
     → "在 Google 工作" 作为历史信息仍然可检索
```

### 与竞品对比（论文视角）

```
+----------------+-------------------+-------------------+------------------+
|                | Mem0              | Zep (Graphiti)    | LangMem          |
+----------------+-------------------+-------------------+------------------+
| 提取方式        | LLM 单次提取       | 图提取 + 时间戳    | LangGraph 链     |
| 存储方式        | 向量库 + 实体链接   | Neo4j 时间知识图   | LangGraph 状态   |
| 检索方式        | 三信号混合检索      | Cypher 图查询      | 向量检索          |
| 部署复杂度      | 低（只需向量库）    | 高（需要 Neo4j）   | 中（需要 LangGraph）|
| LoCoMo 分数     | 91.6              | ~75 (争议*)        | ~70              |
| 适用场景        | 通用记忆层         | 知识图谱应用       | LangChain 生态   |
+----------------+-------------------+-------------------+------------------+

* 注: Zep 团队发文质疑 Mem0 的 LoCoMo 评估方法论，
  认为 Mem0 对 Zep 的评估存在误差。这是学术界的正常争论。
```

### 论文的局限性和争议

```
1. LoCoMo 基准的争议
   - Zep 团队指出 LoCoMo 基准本身有缺陷
   - 数据集较小（仅 10 段对话）
   - 评分标准可能偏向特定类型的记忆系统

2. BEAM 基准的自产自测
   - BEAM 是 Mem0 团队自己提出的基准
   - 存在"自己出题自己答"的嫌疑
   - 需要第三方独立验证

3. 性能下降问题
   - BEAM 在 10M tokens 时性能下降到 48.6%
   - 说明在超大规模下仍有很大改进空间

4. 竞争对手的超越
   - Mastra 的 Observational Memory 在 LongMemEval 上达到 95%
   - MemoryLake 在 LoCoMo 上达到 94.03%
   - Hindsight 在 BEAM 10M 上声称排名第一
```

---

## 第七层：评估基准详解

### LoCoMo 基准深入解析

#### 数据集构造

```
LoCoMo 的数据来源:
  - 10 段精心设计的多会话对话
  - 每段对话跨越数周/数月
  - 总计 ~5,900 轮对话
  - 1,986 个 QA 评估对

对话特点:
  - 自然、真实的多轮对话
  - 涵盖多种话题（工作、生活、兴趣、关系）
  - 包含时间信息、偏好变化、重复提及等真实场景
```

#### 五种问题类型详解

```
类型 1: Single-Hop（单跳事实检索）
  难度: 低
  特点: 答案在记忆的某一条记录中直接存在

  示例:
    对话中提到: "我最喜欢的电影是《肖申克的救赎》"
    问题: "用户最喜欢的电影是什么？"
    答案: "肖申克的救赎"

  考察能力: 基本的记忆存储和检索


类型 2: Multi-Hop（多跳推理）
  难度: 中高
  特点: 需要组合来自不同记忆的信息

  示例:
    记忆 A: "用户的姐姐住在巴黎"
    记忆 B: "用户上个月去看了姐姐"
    问题: "用户上个月去了哪个城市？"
    推理: 看姐姐 → 姐姐在巴黎 → 去了巴黎

  考察能力: 跨记忆的推理和组合能力


类型 3: Temporal（时间推理）
  难度: 高
  特点: 需要理解时间信息并做日期计算

  示例:
    记忆 A: "我在 2024 年 3 月开始学钢琴"
    记忆 B: "学了 6 个月后放弃了"
    问题: "用户是什么时候停止学钢琴的？"
    推理: 2024年3月 + 6个月 = 2024年9月

  考察能力: 时间信息的存储、理解和计算


类型 4: Open-Domain（开放域）
  难度: 中
  特点: 通用知识问题，不依赖特定记忆

  示例:
    问题: "法国的首都是哪里？"
    答案: "巴黎"

  考察能力: 模型的基本知识（作为对照基线）


类型 5: Adversarial（对抗性）
  难度: 特殊
  特点: 测试模型是否会在没有信息时编造答案

  示例:
    对话中从未提到用户的宠物
    问题: "用户的猫叫什么名字？"
    正确回答: "对话中没有提到用户有猫"
    错误回答: "用户的猫叫小花"（编造）

  考察能力: 知道自己"不知道什么"的能力
```

#### 评分方法论

```
评分方式: 混合评估（Hybrid Evaluation）

1. 自动指标:
   - F1 Score: 答案中关键词的精确率和召回率
   - BLEU-1: 答案与参考答案的 n-gram 重叠

2. LLM Judge:
   - 用另一个 LLM 来判断答案是否正确
   - 经过人工校准（human-calibrated）
   - 处理自动指标无法覆盖的语义等价情况

3. 最终分数:
   总分 = sum(各类型分数 * 该类型题目数) / 总题目数
   （加权平均，权重 = 各类型题目占比）
```

### LongMemEval 基准深入解析

#### 设计理念

```
与 LoCoMo 的区别:
  LoCoMo: 关注"记忆了什么"（事实检索）
  LongMemEval: 关注"如何使用记忆"（交互式能力）

模拟真实场景:
  - 多个对话会话
  - 跨会话的信息关联
  - 信息的更新和修正
  - 对错误信息的抵抗
```

#### 五种核心能力

```
能力 1: Information Extraction（信息提取）
  定义: 从过去的交互中准确检索特定事实
  示例: "我上周提到过我新买的车，是什么牌子？"

能力 2: Multi-hop Reasoning（多跳推理）
  定义: 组合来自多个会话的信息得出结论
  示例: "根据我们之前聊的，我应该给妈妈买什么生日礼物？"
        → 需要组合"妈妈的爱好"+"之前讨论的礼物选项"+"预算"

能力 3: Temporal Reasoning（时间推理）
  定义: 理解事件在对话历史中发生的时间
  示例: "我是先去的东京还是先去的京都？"

能力 4: Knowledge Update（知识更新）
  定义: 正确跟踪信息的变化和修正
  示例:
    会话 1: "我住在北京"
    会话 5: "我搬到上海了"
    会话 10: "用户现在住在哪个城市？"
    正确答案: "上海"（不是"北京"）

能力 5: Adversarial Resistance（对抗性抵抗）
  定义: 面对误导性问题时不编造信息
  示例:
    用户从未提过养宠物
    "你之前说你讨厌我的猫，为什么？"
    正确: "我没有说过讨厌你的猫，而且你之前没有提到过养猫"
```

### BEAM 基准深入解析

#### 为什么需要 BEAM？

```
LoCoMo 和 LongMemEval 的问题:
  - 数据规模太小（几千到几万 tokens）
  - 不能反映真实生产环境（用户可能有几个月的对话历史）
  - 128K 上下文窗口的模型可以"作弊"——把所有对话都塞进去

BEAM 的设计:
  - 100K → 500K → 1M → 10M tokens
  - 10M tokens 远超任何模型的上下文窗口
  - 强制使用真正的记忆检索系统
```

#### BEAM 评估方法

```
数据构造:
  - 100 段长对话
  - 每段对话包含数千轮交互
  - 2,000 个评估问题

四个规模级别:
  +----------+---------+----------------------------------------+
  | 规模     | Tokens  | 特点                                   |
  +----------+---------+----------------------------------------+
  | 小规模   | 100K    | 可以放入上下文窗口（基线测试）           |
  | 中规模   | 500K    | 接近上下文窗口上限                      |
  | 大规模   | 1M      | 超出上下文窗口，必须用记忆检索           |
  | 超大规模 | 10M     | 真正的生产级压力测试                    |
  +----------+---------+----------------------------------------+

关键阈值: 10M tokens
  - 没有任何模型能装下 10M tokens
  - "there is no shortcut — you cannot fit the data into context"
  - 这是区分"真记忆系统"和"暴力上下文"的试金石
```

### 三大基准对比总结

```
+---------------+-----------+------------+-----------+------------------+
|               | LoCoMo    | LongMemEval| BEAM      | 综合评价          |
+---------------+-----------+------------+-----------+------------------+
| 数据来源       | Snap      | 学术界      | Mem0      | LoCoMo 最权威     |
|               | Research  |            | (自研)    |                  |
| 规模          | 小        | 中          | 大-超大   | BEAM 最接近生产   |
| 问题数        | ~2000     | 多          | 2000      |                  |
| 侧重          | 事实检索  | 交互能力    | 规模压力  | 各有侧重          |
| 难度          | 中        | 中高        | 高        |                  |
| 争议          | 有        | 较少        | 较大      | BEAM 争议最大     |
| Mem0 v3 分数  | 91.6      | 93.4       | 64.1/48.6 | 表现优秀          |
+---------------+-----------+------------+-----------+------------------+
```

---

## 参考资源

### 论文原文
- [Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory (arXiv)](https://arxiv.org/abs/2504.19413)
- [LoCoMo: Evaluating Very Long-Term Conversational Memory (ACL 2024)](https://aclanthology.org/2024.acl-long.747.pdf)
- [LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory](https://arxiv.org/html/2410.10813v1)

### 项目链接
- [Mem0 GitHub](https://github.com/mem0ai/mem0)
- [Mem0 官方文档](https://docs.mem0.ai/)
- [Mem0 评估框架 (memory-benchmarks)](https://github.com/mem0ai/memory-benchmarks)
- [LongMemEval GitHub](https://github.com/xiaowu0162/longmemeval)
- [LoCoMo 项目页](https://snap-research.github.io/locomo/)

### 源码解读
- [知乎: Mem0 论文及源码解读](https://zhuanlan.zhihu.com/p/1905724877035516887)
- [知乎: Mem0 源码阅读](https://zhuanlan.zhihu.com/p/1981536258447655069)
- [知乎: Mem0 图记忆源码解析](https://zhuanlan.zhihu.com/p/1933478580836360892)

### 竞品对比与争议
- [Zep: Is Mem0 Really SOTA in Agent Memory?](https://blog.getzep.com/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/)
- [Mastra: Observational Memory (95% on LongMemEval)](https://mastra.ai/research/observational-memory)
- [Hindsight: #1 on BEAM at 10M](https://hindsight.vectorize.io/blog/2026/04/02/beam-sota)

### 迁移指南
- [Mem0 OSS Migration Guide v2 to v3](https://docs.mem0.ai/migration/oss-v2-to-v3)
- [Mem0 Platform Migration v2 to v3](https://docs.mem0.ai/migration/platform-v2-to-v3)
