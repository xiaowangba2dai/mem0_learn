# Mem0 深度学习资料库

> 从零开始掌握 Mem0 记忆系统：算法原理、源码分析、动手实践

## 项目内容

```
.
├── docs/                               # 教程文档
│   ├── 01-algorithm-tutorial.md        # 算法演进教程（1873行）
│   │                                   #   从基础概念到 v2/v3 完整解析
│   ├── 02-source-code-tour.md          # 端到端源码探索（1769行）
│   │                                   #   逐文件逐方法分析，聚焦知识图谱
│   ├── 03-practice-guide.md            # 动手实践指南
│   │                                   #   环境搭建 + Demo 运行说明
│   ├── 04-alternatives-research.md     # 竞品调研（9大方案对比）
│   ├── 05-openviking-deep-dive.md      # OpenViking 深度源码研究
│   ├── 06-graphiti-deep-dive.md        # Zep/Graphiti 时序知识图谱研究
│   ├── 07-benchmark-reproduction.md    # Benchmark 复现指南（991行）
│   │                                   #   LoCoMo / LongMemEval / BEAM 实操
│   └── mem0-paper.pdf                  # Mem0 论文原文
│
├── demos/                              # 实践脚本
│   ├── demo_01_raw_memory.py           # Demo 01: 原始记忆 CRUD
│   └── demo_02_llm_extraction.py       # Demo 02: v3 完整流水线
│
├── v2-source/                          # v2 源码（从 git 历史恢复）
│   ├── graph_memory.py                 #   MemoryGraph 类 (744行)
│   ├── graphs_tools.py                 #   LLM tool 定义 (371行)
│   └── graphs_utils.py                 #   图 Prompt (97行)
│
└── README.md                           # 本文件
```

## 快速开始

### 阅读教程（推荐顺序）

1. **[算法演进教程](docs/01-algorithm-tutorial.md)** — 从零基础到深入理解
   - 第零层：向量、Embedding、LLM 基础概念
   - 第一层：v2 三段式流水线 + 图记忆系统
   - 第二层：v3 ADD-only 哲学 + 8 阶段流水线
   - 第三层：混合搜索（语义 + BM25 + 实体增强）
   - 第四层：Entity Linking vs Graph Memory 详细对比
   - 第五层：词形还原与 BM25
   - 第六层：论文深度解读
   - 第七层：评估基准（LoCoMo / LongMemEval / BEAM）

2. **[源码探索](docs/02-source-code-tour.md)** — 逐行源码分析
   - v2 图记忆：graph_memory.py, Neo4j Cypher, LLM tool call
   - v3 实体链接：entity_extraction.py, scoring.py, lemmatization.py
   - 支撑系统：factory.py, qdrant.py, storage.py, prompts.py
   - v2 → v3 架构决策复盘

3. **[实践指南](docs/03-practice-guide.md)** — 动手跑代码
   - 环境搭建（uv + Python 3.12）
   - Demo 01: 原始记忆 CRUD
   - Demo 02: v3 完整流水线

4. **[Benchmark 复现指南](docs/07-benchmark-reproduction.md)** — 复现实验结果
   - LoCoMo / LongMemEval / BEAM 三大基准实操
   - 环境搭建、数据获取、评估脚本、费用估算
   - 竞品横向对比方法 + 结果记录模板

### 运行 Demo

```bash
# 1. Clone 本仓库
git clone <repo-url>
cd mem0-tutorial

# 2. Clone Mem0 源码（Demo 依赖）
git clone https://github.com/mem0ai/mem0.git mem0-repo

# 3. 安装环境
cd mem0-repo
uv python install 3.12
uv venv --python 3.12 .venv
uv pip install -e .

# 4. 复制 Demo 到 mem0 目录
cp ../demos/*.py .

# 5. 运行
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe demo_01_raw_memory.py
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe demo_02_llm_extraction.py
```

## 核心知识点速览

### v2 vs v3 对比

| 维度 | v2 | v3 |
|------|----|----|
| 提取 | 2-4 次 LLM 调用 | 1 次 LLM 调用 |
| 操作 | ADD / UPDATE / DELETE | 仅 ADD |
| 搜索 | 纯语义 | 语义 + BM25 + 实体增强 |
| 图存储 | Neo4j 图数据库 | 向量库内 Entity Linking |
| 实体提取 | LLM tool call | spaCy NLP（本地） |
| LoCoMo | 71.4 | 91.6 (+20) |
| LongMemEval | 67.8 | 93.4 (+26) |

### v3 八阶段流水线

```
Phase 0  上下文收集     获取最近 10 条历史消息
Phase 1  检索已有记忆   搜索 top-10 相关记忆
Phase 2  LLM 提取      单次调用，只产出 ADD
Phase 3  批量嵌入      一次性变成向量
Phase 4-5 哈希去重     MD5 过滤重复
Phase 6  批量写入      写入向量库
Phase 7  实体链接      spaCy 提取 → 实体集合
Phase 8  保存历史      存入 SQLite
```

## 参考资源

- [Mem0 GitHub](https://github.com/mem0ai/mem0)
- [Mem0 论文 (arXiv)](https://arxiv.org/abs/2504.19413)
- [Mem0 官方文档](https://docs.mem0.ai/)
- [迁移指南 v2→v3](https://docs.mem0.ai/migration/oss-v2-to-v3)
- [LoCoMo 基准](https://snap-research.github.io/locomo/)
- [LongMemEval 基准](https://github.com/xiaowu0162/longmemeval)

## License

本仓库中的教程文档和实践脚本仅供学习参考。Mem0 源码版权归 [mem0ai](https://github.com/mem0ai) 所有。
