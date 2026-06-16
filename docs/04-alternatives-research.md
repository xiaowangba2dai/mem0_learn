# AI Agent 记忆方案竞品调研（2026 年 6 月）

> 覆盖 9 个主流方案，从架构原理到 Benchmark 得分全面对比

---

## 一、方案总览

| 方案 | 架构类型 | 开源 | 论文 | LoCoMo | LongMemEval | BEAM@10M | 适合场景 |
|------|---------|------|------|--------|-------------|----------|---------|
| **Mem0 v3** | 向量 + 实体链接 | ✅ 55k⭐ | v2有,v3无 | 91.6 | 93.4 | 48.6 | 快速集成，通用记忆层 |
| **Zep/Graphiti** | 时序知识图谱 | ✅ (Graphiti) | ✅ | ~58-71 | - | - | 时序推理，关系追踪 |
| **Letta** (MemGPT) | OS式三层记忆 | ✅ 22k⭐ | ✅ | - | - | - | 全控制有状态Agent |
| **LangMem** | 语义/情景/程序性 | ✅ | ❌ | ~70 | - | - | LangGraph 生态 |
| **Cognee** | 知识图谱 + 向量 | ✅ | ❌ | - | - | - | 开源图记忆 |
| **Mastra** | 观察式记忆 | ✅ | ❌ | - | **94.87** | - | 长对话，低成本 |
| **Hindsight** | 统一长期回忆 | ✅ | ✅ | 89.9 | - | **#1** | 超大规模(10M+) |
| **Synapse** | 扩散激活图 | ✅ | ✅ | **87.8** | - | - | 复杂时序推理 |
| **MemPalace** | 分层宫殿 | 部分 | ❌ | - | - | 73.9@1M | 中等规模 |

---

## 二、各方案详细分析

### 2.1 Zep / Graphiti — 时序知识图谱

**论文**: [Zep: A Temporal Knowledge Graph Architecture for Agent Memory](https://arxiv.org/abs/2501.13956) (Jan 2025)
**GitHub**: [getzep/graphiti](https://github.com/getzep/graphiti)
**架构**: Neo4j 时序知识图谱

#### 核心创新：双时态（Bi-temporal）建模

```
每个事实记录两条时间线:

  valid_at:    事实在真实世界中成立的时间
               "用户在2025年3月搬到上海" → valid_at = 2025-03

  created_at:  系统获知这个事实的时间
               用户在6月的对话中提到搬家 → created_at = 2025-06

为什么重要?
  - 知道"什么时候是真的" → 可以做时间推理
  - 知道"什么时候知道的" → 可以追溯信息来源
  - 旧事实不会被覆盖，而是形成时间线
```

#### 工作流程

```
对话/文档 → Episode（离散事件）
           → LLM 提取实体+关系
           → 增量折叠进图（不覆盖旧事实）
           → 矛盾检测 → 创建时间线条目（而非删除）

检索: 图遍历 + 向量相似 + 时间过滤 = 三路混合
```

#### 与 Mem0 的关键区别

| 维度 | Mem0 v3 | Zep/Graphiti |
|------|---------|-------------|
| 图结构 | 无（实体链接是平面的） | 有（完整的节点+边+时间线） |
| 时间推理 | 弱（仅时间戳） | 强（双时态 + 时间线遍历） |
| 矛盾处理 | ADD-only（新旧并存） | 非破坏性更新（时间线条目） |
| 存储开销 | 低（~1,764 tokens/对话） | 高（~600,000 tokens/对话） |
| 部署 | 仅需向量库 | 需要 Neo4j |
| LoCoMo | 91.6（自报） | 58-71（有争议） |

#### Benchmark 争议

Zep 原始论文声称 LoCoMo **84%**，但[独立复现](https://github.com/getzep/zep-papers/issues/)发现实际只有 **58.44%**。Zep 团队反过来[质疑 Mem0 的评估方法论](https://blog.getzep.com/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/)。双方互指对方数据有问题——这恰恰说明 Benchmark 本身不够标准化。

---

### 2.2 Letta（原 MemGPT）— OS 式三层记忆

**论文**: [MemGPT: Towards LLMs as Operating Systems](https://arxiv.org/abs/2310.08560) (Oct 2023)
**GitHub**: [letta-ai/letta](https://github.com/letta-ai/letta) (22k⭐)
**架构**: 受操作系统启发的虚拟上下文管理

#### 核心创新：Agent 自己管理记忆

```
类比: 操作系统管理内存层次

  ┌─────────────────────────────────┐
  │  Core Memory（主记忆）            │ ← 始终在上下文中
  │  - 用户信息块                     │    Agent 可以主动编辑
  │  - Agent 身份信息                 │    类似 RAM
  └─────────────────────────────────┘
            ↕ Agent 主动换入换出
  ┌─────────────────────────────────┐
  │  Recall Memory（回忆记忆）        │ ← 可搜索的历史
  │  - 过去的对话记录                 │    类似 Swap
  │  - 可全文检索                     │
  └─────────────────────────────────┘
            ↕ Agent 主动存取
  ┌─────────────────────────────────┐
  │  Archival Memory（归档记忆）      │ ← 外部长期存储
  │  - 向量数据库                     │    类似磁盘
  │  - 无容量限制                     │
  └─────────────────────────────────┘
```

**关键区别**: Mem0 是"帮你记"（后台自动提取），Letta 是"你自己记"（Agent 主动调用记忆管理工具）。

#### 适用场景

- 需要 Agent **主动控制**自己记住什么、忘记什么
- 长时间运行的有状态 Agent（如个人助理）
- 需要 Agent 修改自己的行为（程序性记忆）

#### 局限

- Agent 必须学会使用记忆管理工具（需要好的 system prompt）
- 没有自动提取 → 如果 Agent 忘了存，信息就丢了
- 比 Mem0 复杂得多 → 学习曲线陡

---

### 2.3 LangMem — LangChain 生态的记忆层

**GitHub**: [langchain-ai/langmem](https://github.com/langchain-ai/langmem)
**文档**: [LangMem Conceptual Guide](https://langchain-ai.github.io/langmem/concepts/conceptual_guide/)
**架构**: 认知科学启发的三种记忆类型

#### 三种记忆类型

```
1. Semantic Memory（语义记忆）
   "用户喜欢 Python" → 提取事实，存入向量库
   类似 Mem0 的记忆提取

2. Episodic Memory（情景记忆）
   "上周二用户讨论了机器学习项目" → 存储具体事件
   带有时间戳和上下文的完整对话片段

3. Procedural Memory（程序性记忆）⭐ 最独特
   "用户喜欢简洁的回答" → 修改 system prompt
   Agent 根据经验改变自己的行为方式
```

**程序性记忆是 LangMem 的杀手特性**: 它不只是记住事实，还能让 Agent 从经验中学习行为模式，动态修改自己的 system prompt。

#### 适用场景

- 已经在用 LangGraph 的项目（原生集成）
- 需要 Agent 行为随经验演化（程序性记忆）
- 需要同时管理语义+情景+程序性记忆

#### 局限

- 深度绑定 LangChain 生态 → 换框架就换记忆
- 没有独立的 Benchmark 成绩
- 相对 Mem0/Zep 社区较小

---

### 2.4 Cognee — 开源知识图谱记忆

**GitHub**: [topoteretes/cognee](https://github.com/topoteretes/cognee)
**架构**: 知识图谱 + 向量混合

#### 核心特点

```
1. Memory-First Design
   RAG 系统约 40% 的时间会失败 → Cognee 用知识图谱提高可靠性

2. 统一向量+图搜索
   LanceDB 向量搜索 + 知识图谱遍历
   支持任意格式文件导入

3. Memify 后处理流水线（2025年10月）
   模块化、可扩展的图谱优化管道
```

#### 适用场景

- 需要结构化的知识图谱（不只是扁平记忆）
- 想完全自托管（开源 + 无外部依赖）
- 处理文档/文件（不只是对话）

#### 与 Mem0 对比

Cognee 更像是 "Mem0 + 图" 的开源替代品。Mem0 v3 去掉了图，Cognee 把图作为核心。

---

### 2.5 Mastra — 观察式记忆（Observational Memory）

**研究页面**: [mastra.ai/research/observational-memory](https://mastra.ai/research/observational-memory)
**GitHub Workshop**: [mastra-ai/mastra-observational-memory-workshop](https://github.com/mastra-ai/mastra-observational-memory-workshop)
**架构**: 人类启发的压缩观察

#### 核心创新：消除检索

```
传统方式（Mem0/RAG）:
  存储原始事实 → 查询时检索相关记忆 → 塞入上下文
  问题: 检索可能漏掉关键信息

Mastra 的方式:
  两个后台 Agent 持续压缩对话历史 → 生成密集的"观察日志"
  查询时: 观察日志直接在上下文中 → 不需要检索！

  对话: "我叫小明...我喜欢火锅...我在北京工作..."
    ↓ 后台压缩
  观察: "用户小明，北京工作，喜欢火锅"

  关键: 上下文窗口大小恒定！不管对话多长
```

#### 为什么 LongMemEval 得分最高 (94.87%)

1. **无检索失败**: 所有信息都已被压缩到上下文中
2. **稳定上下文**: 窗口大小不随对话增长
3. **10x 成本降低**: 不需要大量 token 做检索

#### 局限

- 压缩可能丢失细节（"416 页" 变成 "很长的书"）
- 需要后台 Agent 持续运行
- 目前只在 TypeScript 生态（Mastra 框架）
- 没有 LoCoMo/BEAM 成绩

---

### 2.6 Hindsight — BEAM 之王

**论文**: [Hindsight is 20/20](https://arxiv.org/abs/2512.12818) (Dec 2025)
**GitHub**: [vectorize-io/hindsight](https://github.com/vectorize-io/hindsight)
**架构**: 统一长期回忆 + 偏好条件推理

#### 核心特点

```
1. BEAM 10M tokens 排名第一
   在 10M token 级别，任何暴力塞上下文的方法都失败
   Hindsight 用真正的记忆架构取胜

2. 偏好条件推理
   不只是记住事实，还能根据用户偏好调整推理方式

3. +155% vs 基线
   相比无记忆的 vanilla baseline 提升巨大
```

#### 适用场景

- 超大规模长期对话（10M+ tokens）
- 需要记忆在极端规模下仍然可靠
- 开源，可自托管

#### 局限

- 社区和生态还在早期
- 缺少 LoCoMo/LongMemEval 横向对比数据

---

### 2.7 Synapse — 扩散激活图记忆

**论文**: [SYNAPSE: Empowering LLM Agents with Episodic-Semantic Memory via Spreading Activation](https://arxiv.org/abs/2601.02744) (Jan 2026)
**架构**: 认知科学启发的动态图

#### 核心创新：Spreading Activation（扩散激活）

```
灵感: Collins & Loftus 1975 的人类语义记忆模型

传统方式:
  查询 → 向量相似度 → 返回最近邻
  问题: 只能找到直接相关的记忆

Synapse:
  查询 → 激活相关节点 → 激活向邻居扩散 → 衰减
  效果: 能找到间接相关的记忆！

示例:
  查询: "Alice 的工作"
  直接激活: Alice
  扩散到: Alice → friend_of → Bob → works_at → Google
  结果: "Alice 的朋友 Bob 在 Google 工作" ← 间接相关但可能有价值
```

#### LoCoMo: 87.8%

在复杂时序推理上显著优于其他方法。

#### 适用场景

- 需要多跳推理（A → B → C 的链式推理）
- 需要发现间接关联
- 学术研究和实验

#### 局限

- 计算开销大（扩散激活需要多轮图遍历）
- 实际生产部署案例少
- 主要是学术成果

---

## 三、架构分类与权衡

### 3.1 五种架构范式

```
┌──────────────────────────────────────────────────────────────┐
│ 范式 1: 纯向量 (Mem0 v2, 原始 RAG)                          │
│                                                              │
│ 对话 → LLM提取事实 → 向量化 → 存入向量库 → 语义检索         │
│                                                              │
│ 优点: 简单、快、部署容易                                     │
│ 缺点: 无关系、无时间推理、可能丢信息                         │
│ 代表: Mem0 v2                                                │
├──────────────────────────────────────────────────────────────┤
│ 范式 2: 向量 + 实体链接 (Mem0 v3)                           │
│                                                              │
│ 对话 → LLM提取 → 向量存储 + spaCy实体链接 → 三信号混合检索  │
│                                                              │
│ 优点: 快、便宜、无需图数据库                                 │
│ 缺点: 关系是隐式的、无时间线                                 │
│ 代表: Mem0 v3                                                │
├──────────────────────────────────────────────────────────────┤
│ 范式 3: 时序知识图谱 (Zep/Graphiti, Cognee)                 │
│                                                              │
│ 对话 → 实体+关系提取 → Neo4j 图 → 双时态建模 → 图+向量检索  │
│                                                              │
│ 优点: 关系清晰、时间推理强、可追溯                           │
│ 缺点: 慢、贵、需要 Neo4j、LLM 调用多                        │
│ 代表: Zep/Graphiti                                           │
├──────────────────────────────────────────────────────────────┤
│ 范式 4: OS 式虚拟上下文 (Letta/MemGPT)                      │
│                                                              │
│ Agent 主动管理 Core/Recall/Archival 三层记忆                 │
│                                                              │
│ 优点: Agent 完全控制、最灵活                                 │
│ 缺点: 复杂、依赖 Agent 能力、可能漏记                        │
│ 代表: Letta                                                  │
├──────────────────────────────────────────────────────────────┤
│ 范式 5: 压缩观察 (Mastra)                                   │
│                                                              │
│ 后台 Agent 持续压缩历史 → 密集观察日志 → 直接入上下文        │
│                                                              │
│ 优点: 无需检索、上下文稳定、成本低                           │
│ 缺点: 压缩可能丢细节、需要后台 Agent                         │
│ 代表: Mastra Observational Memory                            │
└──────────────────────────────────────────────────────────────┘
```

### 3.2 核心权衡

```
                    简单性
                      ↑
                      |
           Mem0 v3 ●  |
         Mem0 v2 ●    |
                      |
                      |          ● Mastra
        速度 →        |              ● LangMem
                      |
                      |  ● Cognee
                      |     ● Zep
                      |         ● Synapse
                      |  ● Letta
                      |              ● Hindsight
                      |
                    关系/时间推理能力 →
```

**没有银弹**。每个方案都在不同维度上有取舍。

---

## 四、Benchmark 成绩汇总

### 4.1 LoCoMo（长期对话记忆）

| 排名 | 方案 | 分数 | 来源 | 争议 |
|------|------|------|------|------|
| 1 | Mem0 v3 | 91.6 | Mem0 自报 | Zep 质疑方法论 |
| 2 | Hindsight | 89.9 | Reddit 开源 | 独立验证 |
| 3 | Synapse | 87.8 | arXiv 论文 | 学术同行评审 |
| 4 | Zep/Graphiti | 58-71 | 多方评估 | 原始声称 84% 被修正 |
| 5 | LangMem | ~70 | 第三方评估 | 数据较少 |

### 4.2 LongMemEval（长期交互记忆）

| 排名 | 方案 | 分数 | 来源 |
|------|------|------|------|
| 1 | **Mastra** | **94.87** | Mastra 研究 |
| 2 | Mem0 v3 | 93.4 | Mem0 自报 |
| 3 | Letta | 49.0 | Vectorize.io 评估 |

### 4.3 BEAM（生产规模记忆）

| 排名 | 方案 | 1M tokens | 10M tokens | 来源 |
|------|------|-----------|------------|------|
| 1 | **Hindsight** | - | **#1** | Hindsight 博客 |
| 2 | MemPalace | 73.9 | 优雅降级 | Vectorize.io |
| 3 | Mem0 v3 | 64.1 | 48.6 | Mem0 自报 |

---

## 五、选型建议

### 5.1 按场景推荐

```
"我只想快速给 Agent 加记忆"
  → Mem0 v3
  理由: 最简单的 API，最广的生态，10 行代码搞定

"我需要理解时间线和关系变化"
  → Zep/Graphiti
  理由: 双时态建模，非破坏性更新，图查询强大

"我要 Agent 自己决定记什么"
  → Letta
  理由: Agent 主动管理记忆，最灵活，OS 式控制

"我已经在用 LangGraph"
  → LangMem
  理由: 原生集成，程序性记忆（行为演化）

"我要最低成本运行"
  → Mastra
  理由: 10x 成本降低，无检索开销，LongMemEval 最高分

"我要处理超长对话（10M+ tokens）"
  → Hindsight
  理由: BEAM 10M 第一名，专为超大规模设计

"我要做学术研究和实验"
  → Synapse
  理由: 扩散激活是创新范式，认知科学基础

"我要开源的图记忆"
  → Cognee
  理由: 开源图+向量混合，无需商业依赖
```

### 5.2 按成熟度推荐

| 成熟度 | 方案 | 说明 |
|--------|------|------|
| ⭐⭐⭐ 生产就绪 | Mem0, Zep | 广泛使用，文档完善，有云服务 |
| ⭐⭐ 可用但需调优 | Letta, LangMem, Cognee | 开源活跃，需要更多配置 |
| ⭐ 前沿实验 | Mastra, Hindsight, Synapse | 创新架构，生产案例较少 |

### 5.3 与 Mem0 的具体对比

```
Mem0 vs Zep:
  Mem0 更快更便宜更简单 → 适合大多数场景
  Zep 时间推理更强 → 适合需要追踪变化的场景
  注意: Zep 的存储开销是 Mem0 的 340 倍！

Mem0 vs Letta:
  Mem0 = 后台自动记忆（Agent 不感知）
  Letta = Agent 主动管理记忆（Agent 是记忆的主人）
  选择: 取决于你想要"自动驾驶"还是"手动挡"

Mem0 vs LangMem:
  如果你已经在 LangGraph 里 → LangMem 更自然
  如果你需要跨框架通用 → Mem0 更灵活
  LangMem 的程序性记忆（修改 system prompt）是独有优势

Mem0 vs Mastra:
  Mem0 = 存储+检索范式（可能漏检）
  Mastra = 压缩+直接入上下文范式（不检索，不怕漏）
  Mastra 在 LongMemEval 上略胜（94.87 vs 93.4）
  但 Mem0 的生态和社区远大于 Mastra
```

---

## 六、2025-2026 趋势

### 6.1 五大趋势

```
趋势 1: 记忆从"附加功能"变成"核心架构"
  2024: 记忆是 Agent 的可选插件
  2026: 记忆是 Agent 架构设计的第一要素

趋势 2: ADD-only 成为主流
  v2 时代的 UPDATE/DELETE 被证明容易出错
  大多数系统转向"只增不改"+ 检索排序

趋势 3: 混合检索成为标配
  纯语义搜索不够 → 语义 + BM25 + 实体增强
  Mastra 甚至完全消除检索（压缩后直接入上下文）

趋势 4: 时间推理越来越重要
  Zep 的双时态、Synapse 的扩散激活
  用户不只是问"什么"，还问"什么时候"

趋势 5: Benchmark 标准化仍在进行中
  LoCoMo/LongMemEval/BEAM 各有缺陷
  各系统自报成绩，互指对方作弊
  需要独立的第三方评估机构
```

---

## 七、参考链接

### 论文
- [Mem0 论文 (arXiv:2504.19413)](https://arxiv.org/abs/2504.19413) — ECAI 2025
- [Zep/Graphiti 论文 (arXiv:2501.13956)](https://arxiv.org/abs/2501.13956) — Jan 2025
- [MemGPT/Letta 论文 (arXiv:2310.08560)](https://arxiv.org/abs/2310.08560) — Oct 2023
- [Hindsight 论文 (arXiv:2512.12818)](https://arxiv.org/abs/2512.12818) — Dec 2025
- [Synapse 论文 (arXiv:2601.02744)](https://arxiv.org/abs/2601.02744) — Jan 2026

### GitHub
- [Mem0](https://github.com/mem0ai/mem0) — 55k⭐
- [Letta](https://github.com/letta-ai/letta) — 22k⭐
- [Graphiti](https://github.com/getzep/graphiti) — Zep 的开源图引擎
- [Cognee](https://github.com/topoteretes/cognee) — 开源知识图谱记忆
- [Hindsight](https://github.com/vectorize-io/hindsight) — BEAM 之王
- [LangMem](https://github.com/langchain-ai/langmem) — LangChain 生态
- [Awesome Memory for Agents](https://github.com/TsinghuaC3I/Awesome-Memory-for-Agents) — 学术资源汇总

### 对比文章
- [5 个系统 6 维对比 (Medium)](https://medium.com/@wasowski.jarek/i-compared-5-ai-agent-memory-systems-across-6-dimensions-none-wins-6a658335ed0a)
- [Mem0 vs Zep vs LangMem (Dev.to)](https://dev.to/anajuliabit/mem0-vs-zep-vs-langmem-vs-memoclaw-ai-agent-memory-comparison-2026-1l1k)
- [6 大框架深度评测 (ML Mastery)](https://machinelearningmastery.com/the-6-best-ai-agent-memory-frameworks-you-should-try-in-2026/)
- [State of AI Agent Memory 2026 (Mem0 Blog)](https://mem0.ai/blog/state-of-ai-agent-memory-2026)
- [Context Engineering Survey (Entropi)](https://entropi.ai/blog/context-engineering-ai-memory-landscape-2026)
