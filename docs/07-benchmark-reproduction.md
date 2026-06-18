# Benchmark 复现指南

> 手把手复现 Mem0 及竞品的实验结果：LoCoMo / LongMemEval / BEAM
> 覆盖环境搭建、数据获取、评估脚本、费用估算、常见问题

---

## 目录

- [前置准备](#前置准备)
  - [硬件与软件要求](#硬件与软件要求)
  - [API Key 与费用估算](#api-key-与费用估算)
  - [环境搭建](#环境搭建)
  - [自定义模型端点](#自定义模型端点)
- [复现路线总览](#复现路线总览)
- [LoCoMo 复现](#locomo-复现)
  - [数据集获取](#locomo-数据集获取)
  - [数据格式](#locomo-数据格式)
  - [评估流水线](#locomo-评估流水线)
  - [评分方法](#locomo-评分方法)
  - [已知争议](#locomo-已知争议)
- [LongMemEval 复现](#longmemeval-复现)
  - [数据集获取](#longmemeval-数据集获取)
  - [数据格式](#longmemeval-数据格式)
  - [评估脚本](#longmemeval-评估脚本)
  - [自定义系统接入](#longmemeval-自定义系统接入)
  - [五种能力维度评分](#longmemeval-五种能力维度评分)
- [BEAM 复现](#beam-复现)
  - [数据集获取](#beam-数据集获取)
  - [四个规模级别](#beam-四个规模级别)
  - [资源需求](#beam-资源需求与成本警告)
  - [十种记忆能力](#beam-十种记忆能力测试)
- [竞品复现](#竞品复现)
- [结果记录与对比模板](#结果记录与对比模板)
- [常见问题与排错](#常见问题与排错)
- [参考资源](#参考资源)

---

## 前置准备

### 硬件与软件要求

```
最低配置:
  - Python 3.9+（推荐 3.12）
  - 8 GB 内存
  - 稳定的网络（API 调用 + 数据下载）
  - 无需 GPU（所有评估均基于 API 调用）

推荐配置（BEAM 大规模测试）:
  - 16 GB+ 内存
  - Docker + Docker Compose（用于本地 Qdrant 向量库）
  - 足够的磁盘空间（BEAM 10M 级别数据约数 GB）
```

### API Key 与费用估算

| 基准 | LLM 调用量（估算） | 使用 gpt-4o-mini | 使用 gpt-4o | 说明 |
|------|-------------------|------------------|-------------|------|
| **LoCoMo** | ~6,000 轮写入 + ~2,000 题评估 | ~$5–15 | ~$50–150 | 10 段对话，1,986 QA |
| **LongMemEval** | 类似 LoCoMo | ~$3–10 | ~$30–100 | 500 题，数据量较小 |
| **BEAM @ 100K** | 小规模 | ~$1–3 | ~$10–30 | 快速验证用 |
| **BEAM @ 1M** | 大规模 | ~$10–30 | ~$100–300 | 需要稳定网络 |
| **BEAM @ 10M** | 超大规模 | ~$50–200 | ~$500–2,000 | ⚠️ 费用高昂 |

```
必需的 API Key:
  1. OPENAI_API_KEY     — LLM 提取 + Embedding + LLM Judge 评分
  2. （可选）MEM0_API_KEY — 如果使用 Mem0 Cloud 而非自托管

注意事项:
  - 评估脚本中的 LLM Judge 也需要 API 调用（占费用的 ~30%）
  - gpt-4o-mini 足够完成评估，不需要 gpt-4o
  - 可先用 LoCoMo 的子集（1-2 段对话）做冒烟测试，花费 < $1
```

### 环境搭建

#### 方式 A：使用 Mem0 官方评估框架（推荐）

```bash
# 1. 克隆评估框架
git clone https://github.com/mem0ai/memory-benchmarks.git
cd memory-benchmarks

# 2. 安装 Python 依赖
python -m venv .venv
source .venv/bin/activate          # Linux/Mac
# .venv\Scripts\activate           # Windows

pip install -r requirements.txt

# 3. 配置 API Key
export OPENAI_API_KEY="sk-..."

# 4. （可选）自托管模式需要 Docker
docker compose up -d               # 启动 Qdrant 向量库 + Mem0 服务
```

#### 方式 B：手动搭建（更灵活，适合学习）

```bash
# 1. 克隆 Mem0 源码
git clone https://github.com/mem0ai/mem0.git
cd mem0

# 2. 安装（含 NLP 可选依赖）
uv python install 3.12
uv venv --python 3.12 .venv
uv pip install -e ".[nlp]"
python -m spacy download en_core_web_sm

# 3. 安装评估所需的额外依赖
uv pip install fastembed           # BM25 关键词搜索
uv pip install datasets            # HuggingFace 数据集加载

# 4. 配置
export OPENAI_API_KEY="sk-..."
```

### 自定义模型端点

如果你使用国内代理、本地模型（Ollama/LM Studio/vLLM）或其他 OpenAI 兼容接口，需要在**三个地方**分别配置自定义 URL 和 API Key。

#### 核心配置字段

Mem0 的 `provider: "openai"` 支持任意 OpenAI 兼容端点，关键字段是 **`openai_base_url`**（注意：不是 `base_url`，带 `openai_` 前缀）。

#### ① Mem0 本体（写入/检索记忆）

```python
from mem0 import Memory

config = {
    "llm": {
        "provider": "openai",
        "config": {
            "model": "your-model-name",
            "openai_base_url": "https://your-proxy.com/v1",
            "api_key": "sk-your-key",
            "temperature": 0,
        }
    },
    "embedder": {
        "provider": "openai",
        "config": {
            "model": "your-embedding-model",
            "openai_base_url": "https://your-proxy.com/v1",
            "api_key": "sk-your-key",
        }
    }
}

m = Memory.from_config(config)
```

常见兼容端点：

| 服务 | `openai_base_url` 示例 | 备注 |
|------|----------------------|------|
| 国内代理 | `https://api.xxx.com/v1` | 按服务商文档填写 |
| DeepSeek | `https://api.deepseek.com/v1` | 需 DeepSeek API Key |
| SiliconFlow | `https://api.siliconflow.cn/v1` | 支持多种开源模型 |
| Ollama（本地） | `http://localhost:11434/v1` | 无需 API Key，填任意值即可 |
| LM Studio（本地） | `http://localhost:1234/v1` | 无需 API Key |
| vLLM（本地） | `http://localhost:8000/v1` | 无需 API Key |
| Azure OpenAI | 用 `provider: "azure_openai"` | 配置方式不同，见官方文档 |

#### ② memory-benchmarks 评估框架

评估框架通过 `configs/` 目录下的 YAML 文件配置：

```yaml
# configs/custom.yaml
llm:
  provider: openai
  config:
    model: "your-model"
    openai_base_url: "https://your-proxy.com/v1"
    api_key: "sk-your-key"
    temperature: 0

embedder:
  provider: openai
  config:
    model: "your-embedding-model"
    openai_base_url: "https://your-proxy.com/v1"
    api_key: "sk-your-key"
```

运行时指定配置文件：

```bash
python benchmarks/locomo/run.py \
  --backend oss \
  --config configs/custom.yaml
```

#### ③ LLM Judge（评分脚本）

评估脚本中的 LLM Judge 直接读取 OpenAI SDK 的环境变量：

```bash
# Linux / Mac
export OPENAI_API_KEY="sk-your-key"
export OPENAI_BASE_URL="https://your-proxy.com/v1"

# Windows PowerShell
$env:OPENAI_API_KEY = "sk-your-key"
$env:OPENAI_BASE_URL = "https://your-proxy.com/v1"

# 然后运行评分
python benchmarks/locomo/evaluate.py --results-dir results/locomo/
```

#### 一条龙完整配置

```bash
# 1. 环境变量（给 Judge 评分脚本用）
export OPENAI_API_KEY="sk-your-key"
export OPENAI_BASE_URL="https://your-proxy.com/v1"

# 2. 运行评估（Mem0 写入/检索，读取 configs/custom.yaml）
python benchmarks/locomo/run.py --config configs/custom.yaml

# 3. 评分（Judge 自动读取上面的环境变量）
python benchmarks/locomo/evaluate.py --results-dir results/locomo/
```

```
⚠️ 注意事项:

  1. LLM Judge 对模型能力有要求
     建议评分用 GPT-4o 级别的模型以保证评分质量
     写入和检索可以用较便宜的模型（如 gpt-4o-mini 或同级替代品）

  2. Embedding 模型和 LLM 可以分开配置
     LLM 用代理，Embedding 用本地模型（或反过来）都可以

  3. 本地模型（Ollama/LM Studio）不需要真实 API Key
     但字段不能为空，填任意字符串即可: api_key: "not-needed"
```

---

## 复现路线总览

```
推荐复现顺序:

  ┌────────────────────────────────────────────────────────────┐
  │ Step 1: LongMemEval （最优先）                              │
  │                                                            │
  │   难度: ★★☆    费用: $3-10     时间: 1-2 小时              │
  │   理由: 独立评估框架，争议最少，GitHub 有完整代码            │
  │   验证: Mem0 v3 声称 93.4                                  │
  ├────────────────────────────────────────────────────────────┤
  │ Step 2: LoCoMo （经典基准）                                │
  │                                                            │
  │   难度: ★★★    费用: $5-15     时间: 2-4 小时              │
  │   理由: 最权威的基准，被最多论文引用                        │
  │   验证: Mem0 v3 声称 91.6                                  │
  │   注意: 评分方法有争议，需仔细阅读评分细节                  │
  ├────────────────────────────────────────────────────────────┤
  │ Step 3: BEAM @ 100K （可选）                               │
  │                                                            │
  │   难度: ★★☆    费用: $1-3      时间: 30 分钟               │
  │   理由: 快速验证大规模场景，100K 级别可在上下文窗口内       │
  ├────────────────────────────────────────────────────────────┤
  │ Step 4: BEAM @ 1M+ （进阶，可选）                          │
  │                                                            │
  │   难度: ★★★★   费用: $10-300   时间: 4-12 小时             │
  │   理由: 真正的生产级压力测试                                │
  │   警告: 10M 级别费用可达 $200+，请谨慎                      │
  └────────────────────────────────────────────────────────────┘
```

| 基准 | 优先级 | 难度 | 费用 | 耗时 | 争议程度 | 数据集大小 |
|------|--------|------|------|------|---------|-----------|
| LongMemEval | 🥇 高 | ★★☆ | $3–10 | 1–2h | 低 | 500 题 |
| LoCoMo | 🥈 中 | ★★★ | $5–15 | 2–4h | 中 | 1,986 题 |
| BEAM @ 100K | 🥉 可选 | ★★☆ | $1–3 | 30min | 高（自研） | 子集 |
| BEAM @ 1M+ | ⚠️ 进阶 | ★★★★ | $10–300 | 4–12h | 高（自研） | 2,000 题 |

> 关于三大基准的理论背景（问题类型、设计理念、评分方法论），请参考 [算法演进教程](01-algorithm-tutorial.md) 的第六层和第七层。本文聚焦于**实操步骤**。

---

## LoCoMo 复现

> 目标: 复现 Mem0 v3 在 LoCoMo 上的 91.6 分
> 数据来源: Snap Research, ACL 2024

### LoCoMo 数据集获取

```bash
# 方式 1: 从 GitHub 直接下载
git clone https://github.com/snap-research/LoCoMo.git
cd LoCoMo
# 数据在 data/ 目录下

# 方式 2: 通过 memory-benchmarks 自动下载
# memory-benchmarks 运行时会自动拉取数据到 datasets/ 目录
cd memory-benchmarks
python benchmarks/locomo/run.py --help
```

```
数据集概况:
  - 10 段多会话对话（每段跨数周/数月）
  - 每段平均 ~300 轮对话，~9K tokens
  - 最多 35 个会话
  - 1,986 个 QA 评估对
  - 总计 ~5,900 轮对话
```

### LoCoMo 数据格式

```json
{
  "conversation_no": 1,
  "conversation": [
    {
      "speaker": "USER",
      "text": "I just got back from my trip to Paris!"
    },
    {
      "speaker": "ASSISTANT",
      "text": "That sounds amazing! How was the food?"
    }
  ],
  "question": "Where did the user go on their most recent trip?",
  "answer": "Paris",
  "question_type": "single_hop",
  "has_answer": true
}
```

```
五种问题类型（question_type 字段）:
  - single_hop     单跳事实检索
  - multi_hop      多跳推理
  - temporal       时间推理
  - open_domain    开放域（对照基线）
  - adversarial    对抗性（用户未提过的信息）
```

### LoCoMo 评估流水线

评估分三个阶段：**写入 → 检索 → 评分**。

```
┌─────────────────────────────────────────────────────────────┐
│ Phase 1: Ingestion（写入）                                   │
│                                                             │
│   原始对话 ──→ Mem0 m.add(messages, user_id=...) ──→ 向量库  │
│                                                             │
│   每段对话的所有轮次按时间顺序写入                             │
│   Mem0 的 v3 流水线自动提取事实                              │
│   10 段对话 × 每段 ~300 轮 = ~3,000 次 add 调用              │
├─────────────────────────────────────────────────────────────┤
│ Phase 2: Search（检索）                                      │
│                                                             │
│   问题 ──→ Mem0 m.search(query, user_id=..., top_k=10)      │
│         ──→ 返回 top-10 相关记忆                            │
│         ──→ 拼接成上下文                                    │
│                                                             │
│   1,986 个问题 × 1 次 search = ~2,000 次 search 调用         │
├─────────────────────────────────────────────────────────────┤
│ Phase 3: Generation + Evaluation（生成 + 评分）              │
│                                                             │
│   检索结果 + 问题 ──→ LLM 生成答案                           │
│   生成答案 vs 参考答案 ──→ LLM Judge 评分                    │
│                                                             │
│   1,986 题 × (1 次生成 + 1 次 Judge) = ~4,000 次 LLM 调用    │
└─────────────────────────────────────────────────────────────┘
```

#### 使用 memory-benchmarks 仓库

```bash
# 假设已按"前置准备"搭好环境

# ---- 自托管模式（OSS）----
# 1. 启动本地服务
docker compose up -d

# 2. 运行 LoCoMo 评估（写入 + 检索 + 生成）
python benchmarks/locomo/run.py \
  --backend oss \
  --server-url http://localhost:8000

# 3. 评分（需要 OPENAI_API_KEY 用于 LLM Judge）
python benchmarks/locomo/evaluate.py \
  --results-dir results/locomo/

# ---- Mem0 Cloud 模式 ----
export MEM0_API_KEY="your-key"
python benchmarks/locomo/run.py \
  --backend cloud \
  --mem0-api-key $MEM0_API_KEY
```

#### 手动评估（学习用）

```python
"""
手动复现 LoCoMo 评估的核心流程
用于理解每个步骤的细节，生产评估请用 memory-benchmarks
"""
import json
from mem0 import Memory

# 1. 初始化 Mem0
config = {
    "llm": {
        "provider": "openai",
        "config": {
            "model": "gpt-4o-mini",
            "temperature": 0,
            # 自定义端点（可选，取消注释使用）:
            # "openai_base_url": "https://your-proxy.com/v1",
            # "api_key": "sk-your-key",
        }
    },
    "embedder": {
        "provider": "openai",
        "config": {
            "model": "text-embedding-3-small",
            # "openai_base_url": "https://your-proxy.com/v1",
            # "api_key": "sk-your-key",
        }
    }
}
m = Memory.from_config(config)

# 2. 写入对话历史
with open("data/locomo_conversation_1.json") as f:
    data = json.load(f)

messages = []
for turn in data["conversation"]:
    role = "user" if turn["speaker"] == "USER" else "assistant"
    messages.append({"role": role, "content": turn["text"]})

# 分批写入（避免单次调用过大）
BATCH_SIZE = 20
for i in range(0, len(messages), BATCH_SIZE):
    batch = messages[i:i+BATCH_SIZE]
    m.add(batch, user_id="locomo_conv_1")

# 3. 对每个问题检索记忆
question = data["question"]
memories = m.search(question, user_id="locomo_conv_1", top_k=10)

# 4. 用 LLM 生成答案
from openai import OpenAI
client = OpenAI(
    # 自定义端点（可选，或读取 OPENAI_BASE_URL 环境变量）:
    # base_url="https://your-proxy.com/v1",
    # api_key="sk-your-key",
)

context = "\n".join([mem["memory"] for mem in memories["results"]])
response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[
        {"role": "system", "content": f"Based on these memories:\n{context}\nAnswer the question concisely."},
        {"role": "user", "content": question}
    ]
)
generated_answer = response.choices[0].message.content

# 5. 与参考答案比较（简化版，完整评估请用 LLM Judge）
reference = data["answer"]
print(f"Question:   {question}")
print(f"Reference:  {reference}")
print(f"Generated:  {generated_answer}")
```

### LoCoMo 评分方法

```
评分使用混合评估（Hybrid Evaluation）:

1. 自动指标:
   - F1 Score: 答案中关键词的精确率和召回率
   - BLEU-1: 答案与参考答案的 n-gram 重叠

2. LLM Judge:
   - 用 GPT-4 级别模型判断答案是否语义等价
   - 处理自动指标无法覆盖的情况:
     参考答案: "The Big Apple"
     生成答案: "New York City"
     → F1/BLEU 判错，LLM Judge 判对

3. 最终分数:
   总分 = Σ(各类型分数 × 该类型题目数) / 总题目数
   （加权平均，权重 = 各类型题目占比）

   Adversarial 类型有时被单独统计，不计入总分
```

### LoCoMo 已知争议

```
⚠️ 复现前请了解以下争议:

1. Zep 团队的质疑
   - Zep 发文指出 Mem0 对 Zep/Graphiti 的 LoCoMo 评估存在误差
   - 认为评分标准偏向"扁平记忆"系统（Mem0），
     对"图记忆"系统（Zep）不够公平
   - 参考: https://blog.getzep.com/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/

2. 数据集规模
   - 仅 10 段对话，统计显著性有限
   - 单次运行的方差可能较大

3. Adversarial 类型的处理
   - 不同论文对 Adversarial 题目是否计入总分的做法不同
   - 复现时需确认自己使用的是哪种计分方式

建议: 复现时同时记录每种 question_type 的独立分数，
      方便与其他论文逐类型对比。
```

---

## LongMemEval 复现

> 目标: 复现 Mem0 v3 在 LongMemEval 上的 93.4 分
> 数据来源: Wu et al., 2024
> 特点: **争议最少**的基准，推荐首先复现

### LongMemEval 数据集获取

```bash
# 方式 1: 克隆独立评估框架（推荐）
git clone https://github.com/xiaowu0162/longmemeval.git
cd longmemeval
pip install -r requirements-lite.txt   # 基础评估
# pip install -r requirements-full.txt  # 完整系统（含 PyTorch）

# 方式 2: 通过 HuggingFace 加载数据集
from datasets import load_dataset
dataset = load_dataset("xiaowu0162/longmemeval")

# 方式 3: 通过 memory-benchmarks
cd memory-benchmarks
python benchmarks/longmemeval/run.py --help
```

### LongMemEval 数据格式

```
数据集规模:
  - 500 个问题
  - 多段对话历史（haystack_sessions）
  - 每个问题标注了 question_type 和证据轮次

单条数据示例:
{
  "question_id": "q_001",
  "question": "What car did the user buy last month?",
  "answer": "A Tesla Model 3",
  "question_type": "extraction",          # 五种能力之一
  "question_date": "2024-06-15",
  "haystack_sessions": [                  # 完整对话历史
    [
      {"role": "user", "content": "...", "has_answer": false},
      {"role": "assistant", "content": "..."},
      {"role": "user", "content": "I just bought a Tesla Model 3!",
       "has_answer": true},               # ← 证据标记
      ...
    ]
  ]
}

五种 question_type:
  - extraction       信息提取（从历史中检索事实）
  - multi_hop        多跳推理（组合多个会话的信息）
  - temporal         时间推理（理解事件的时间顺序）
  - knowledge_update 知识更新（追踪信息的修正）
  - adversarial      对抗性抵抗（面对误导性问题不编造）
```

### LongMemEval 评估脚本

```bash
# 基础评估流程（使用独立框架）

# Step 1: 写入对话历史到你的记忆系统
python src/ingestion/run.py \
  --system mem0 \
  --data data/longmemeval.json

# Step 2: 对每个问题检索记忆 + 生成答案
python src/retrieval/run.py \
  --system mem0 \
  --top_k 10

# Step 3: 评分（需要 OPENAI_API_KEY 用于 LLM Judge）
python src/evaluation/evaluate_qa.py \
  --predictions results/predictions.jsonl \
  --ground-truth data/longmemeval.json

# Step 4: 汇总指标
python src/evaluation/print_qa_metrics.py \
  --results results/evaluation.json
```

### LongMemEval 自定义系统接入

如果你想测试自己的记忆系统（非 Mem0），只需生成标准格式的预测文件：

```jsonl
{"question_id": "q_001", "hypothesis": "A Tesla Model 3"}
{"question_id": "q_002", "hypothesis": "The user went to Paris in March"}
{"question_id": "q_003", "hypothesis": "I don't have information about the user's pet"}
```

```python
"""
自定义系统接入模板
你的系统只需要实现两个方法: ingest() 和 query()
"""

class MyMemorySystem:
    def ingest(self, sessions: list[list[dict]]):
        """写入对话历史"""
        for session in sessions:
            # 你的写入逻辑
            pass

    def query(self, question: str) -> str:
        """根据检索到的记忆回答问题"""
        # 你的检索 + 生成逻辑
        return "your answer"


# 评估循环
import json

with open("data/longmemeval.json") as f:
    dataset = json.load(f)

memory = MyMemorySystem()
predictions = []

for item in dataset:
    # 写入历史
    memory.ingest(item["haystack_sessions"])
    # 回答问题
    answer = memory.query(item["question"])
    predictions.append({
        "question_id": item["question_id"],
        "hypothesis": answer
    })

# 保存预测结果
with open("results/predictions.jsonl", "w") as f:
    for pred in predictions:
        f.write(json.dumps(pred) + "\n")

# 然后用 evaluate_qa.py 评分
```

### LongMemEval 五种能力维度评分

```bash
# 按 question_type 分别统计分数
python src/evaluation/print_qa_metrics.py \
  --results results/evaluation.json \
  --by-type

# 输出示例:
# Extraction:       95.2%
# Multi-hop:        91.8%
# Temporal:         89.3%
# Knowledge Update: 94.1%
# Adversarial:      96.7%
# Overall:          93.4%
```

```
评分方法: Accuracy（准确率）
  - 每个问题二值判断: 正确 or 错误
  - LLM Judge 判断语义等价性
  - 总分 = 正确题数 / 总题数

与 LoCoMo 的区别:
  LoCoMo: F1 + BLEU + LLM Judge 混合 → 部分得分
  LongMemEval: 纯 Accuracy → 全对或全错
  → LongMemEval 的分数更"干净"，更容易横向对比
```

---

## BEAM 复现

> 目标: 复现 Mem0 v3 在 BEAM 上的 64.1 (1M) / 48.6 (10M) 分
> 数据来源: Mem0 论文自行提出
> ⚠️ 争议最大，建议作为进阶验证

### BEAM 数据集获取

```bash
# BEAM 数据通过 memory-benchmarks 自动下载
cd memory-benchmarks
python benchmarks/beam/run.py --help

# 数据会自动存入 datasets/ 目录（被 .gitignore 忽略）
```

```
数据集概况:
  - 100 段长对话
  - 每段包含数千轮交互
  - 2,000 个评估问题
  - 覆盖 10 种记忆能力
```

### BEAM 四个规模级别

```
┌──────────┬─────────────┬──────────────────────┬──────────────────┐
│ 规模     │ Tokens      │ 特点                 │ 适合场景          │
├──────────┼─────────────┼──────────────────────┼──────────────────┤
│ 小规模   │ 100K        │ 可放入上下文窗口      │ 快速冒烟测试      │
│          │             │ 基线对照              │                  │
├──────────┼─────────────┼──────────────────────┼──────────────────┤
│ 中规模   │ 500K        │ 接近上下文窗口上限    │ 中等压力测试      │
├──────────┼─────────────┼──────────────────────┼──────────────────┤
│ 大规模   │ 1M          │ 超出上下文窗口        │ 真正的记忆检索    │
│          │             │ Mem0: 64.1%           │ 必须用记忆系统    │
├──────────┼─────────────┼──────────────────────┼──────────────────┤
│ 超大规模 │ 10M         │ 远超任何上下文窗口    │ 生产级压力测试    │
│          │             │ Mem0: 48.6%           │ 费用高昂          │
└──────────┴─────────────┴──────────────────────┴──────────────────┘

关键阈值: 10M tokens
  "there is no shortcut — you cannot fit the data into context"
  这是区分"真记忆系统"和"暴力塞上下文"的试金石
```

```bash
# 运行不同规模
# 小规模（推荐首先运行）
python benchmarks/beam/run.py --scale 100k --backend oss

# 大规模
python benchmarks/beam/run.py --scale 1m --backend oss

# 超大规模（⚠️ 费用警告）
python benchmarks/beam/run.py --scale 10m --backend oss
```

### BEAM 资源需求与成本警告

```
⚠️ BEAM 是三个基准中成本最高的:

100K 规模:
  - 写入: ~100 次 LLM 调用
  - 检索 + 生成: ~200 次 LLM 调用
  - 预估费用: $1-3 (gpt-4o-mini)
  - 耗时: ~30 分钟

1M 规模:
  - 写入: ~1,000 次 LLM 调用
  - 检索 + 生成: ~2,000 次 LLM 调用
  - 预估费用: $10-30 (gpt-4o-mini)
  - 耗时: ~4 小时
  - 需要: 稳定的向量库（推荐 Qdrant Docker）

10M 规模:
  - 写入: ~10,000 次 LLM 调用
  - 检索 + 生成: ~2,000 次 LLM 调用
  - 预估费用: $50-200 (gpt-4o-mini)
  - 耗时: ~12 小时
  - 需要: 高性能向量库 + 大量磁盘空间

建议:
  1. 先用 100K 做冒烟测试
  2. 确认流水线正确后再跑 1M
  3. 10M 仅在需要生产级验证时运行
```

### BEAM 十种记忆能力测试

```
BEAM 评估 10 种不同的记忆能力:

 1. Single-Hop Retrieval      单跳检索 — 直接找到答案
 2. Multi-Hop Reasoning       多跳推理 — 组合多个记忆
 3. Temporal Reasoning         时间推理 — 理解时间顺序
 4. Knowledge Update          知识更新 — 追踪信息变化
 5. Adversarial Resistance    对抗抵抗 — 不编造信息
 6. Conversation Summary      对话摘要 — 概括对话内容
 7. Entity Tracking           实体追踪 — 追踪特定实体
 8. Preference Tracking       偏好追踪 — 追踪用户偏好
 9. Negation Handling         否定处理 — 理解否定信息
10. Cross-Session Reasoning   跨会话推理 — 关联不同会话

最终分数 = 各能力的加权平均
```

---

## 竞品复现

如果你不只想验证 Mem0，还想横向对比竞品，以下是各方案的复现入口：

### Zep / Graphiti

```bash
git clone https://github.com/getzep/graphiti.git
cd graphiti

# 需要 Neo4j
docker run -d -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/password \
  neo4j:latest

# 安装
pip install -e .

# 运行评估
# 参考 Graphiti 仓库中的 examples/ 目录
```

```
⚠️ 注意事项:
  - Zep 原始论文声称 LoCoMo 84%
  - 独立复现发现仅 58.44%
  - 差异来自评估方法的技术不一致
  - 复现时请记录你使用的具体评估脚本版本
```

### Letta (MemGPT)

```bash
git clone https://github.com/letta-ai/letta.git
cd letta
pip install -e .

# Letta 需要 Agent 主动使用记忆工具
# 评估时需要设计合适的 system prompt
# LongMemEval 上 Vectorize.io 评估得 49.0%
```

### Mastra (Observational Memory)

```bash
# Mastra 是 TypeScript 生态
git clone https://github.com/mastra-ai/mastra-observational-memory-workshop.git
cd mastra-observational-memory-workshop
npm install

# LongMemEval 上达到 94.87%（最高分）
# 评估方式参考: https://mastra.ai/research/observational-memory
```

### Hindsight

```bash
git clone https://github.com/vectorize-io/hindsight.git
cd hindsight
pip install -e .

# BEAM 10M 上声称 #1
# LoCoMo 上 89.9%
# 参考: https://hindsight.vectorize.io/blog/2026/04/02/beam-sota
```

### 公平对比原则

```
横向对比时确保公平性:

1. 相同的数据集版本
   - 确认使用的是同一版数据集（commit hash / version tag）
   - LoCoMo 数据有多个版本，注意区分

2. 相同的 LLM
   - 记忆提取用相同的 LLM（如 gpt-4o-mini）
   - 评估 Judge 用相同的 LLM（如 gpt-4o）
   - Embedding 模型也要一致

3. 相同的评分脚本
   - 使用同一个 evaluate_qa.py 评分
   - 不同系统生成的答案格式可能不同，统一预处理

4. 记录完整配置
   - LLM model + temperature
   - top_k 检索数量
   - System prompt 内容
   - 运行时间（API 可能有变化）
```

---

## 结果记录与对比模板

### 结果记录表

运行完评估后，使用以下模板记录结果：

```markdown
## 复现记录

### 环境信息
- 日期: YYYY-MM-DD
- Mem0 版本: x.y.z (commit hash)
- LLM (提取): gpt-4o-mini / temperature=0
- LLM (Judge): gpt-4o
- Embedding: text-embedding-3-small
- 向量库: Qdrant x.y.z (Docker / Cloud)
- 操作系统: Linux / Mac / Windows

### LoCoMo 结果

| 问题类型     | 题目数 | 正确数 | 分数   |
|-------------|--------|--------|--------|
| Single-Hop  |        |        |        |
| Multi-Hop   |        |        |        |
| Temporal    |        |        |        |
| Open-Domain |        |        |        |
| Adversarial |        |        |        |
| **总计**    | 1,986  |        |        |

### LongMemEval 结果

| 能力维度         | 题目数 | 正确数 | 分数   |
|-----------------|--------|--------|--------|
| Extraction      |        |        |        |
| Multi-hop       |        |        |        |
| Temporal        |        |        |        |
| Knowledge Update|        |        |        |
| Adversarial     |        |        |        |
| **总计**        | 500    |        |        |

### BEAM 结果

| 规模  | Tokens | 分数   | 耗时  | 费用   |
|-------|--------|--------|-------|--------|
| 小    | 100K   |        |       |        |
| 中    | 500K   |        |       |        |
| 大    | 1M     |        |       |        |
| 超大  | 10M    |        |       |        |
```

### 与论文声称分数的对比

```
复现完成后，将你的结果与论文声称对比:

+----------------+----------------+----------------+----------+
| 基准           | 论文声称       | 你的复现       | 差异     |
+----------------+----------------+----------------+----------+
| LoCoMo         | 91.6           | _____          | _____    |
| LongMemEval    | 93.4           | _____          | _____    |
| BEAM @ 1M      | 64.1           | _____          | _____    |
| BEAM @ 10M     | 48.6           | _____          | _____    |
+----------------+----------------+----------------+----------+

常见差异来源:
  - LLM 版本不同（GPT 模型会持续更新）
  - Embedding 模型版本不同
  - System prompt 微调
  - 评分脚本版本差异
  - 随机性（LLM 生成的答案不完全确定）

  差异在 ±3% 以内通常认为是正常波动
  差异在 ±5% 以上值得排查原因
```

---

## 常见问题与排错

### Q: API 限流（Rate Limit）

```
症状: openai.RateLimitError: You exceeded your current quota

解决:
  1. 在评估脚本中添加重试 + 退避
     import time
     for attempt in range(5):
         try:
             result = client.chat.completions.create(...)
             break
         except RateLimitError:
             time.sleep(2 ** attempt)

  2. 降低并发数（memory-benchmarks 支持 --concurrency 参数）

  3. 分批运行（先跑 1-2 段对话，确认无误再全量）

  4. 升级 OpenAI API tier（Tier 2+ 有更高的 rate limit）
```

### Q: 评分结果与论文差异较大

```
排查清单:
  □ LLM 模型是否一致？（gpt-4o-mini 的具体版本会随时间变化）
  □ temperature 是否设为 0？
  □ Embedding 模型是否一致？
  □ top_k 检索数量是否一致？
  □ 数据写入顺序是否按时间？
  □ 评分脚本版本是否一致？
  □ Adversarial 题目是否计入了总分？

  最常见原因: LLM 模型版本更新
  OpenAI 的 gpt-4o-mini 会持续迭代，不同日期的行为可能不同
```

### Q: LoCoMo 数据加载失败

```
症状: FileNotFoundError 或 JSON parse error

原因: LoCoMo 数据有多个格式版本

解决:
  1. 确认使用 memory-benchmarks 自动下载的版本
  2. 如果手动下载，检查 JSON 字段名是否与评估脚本匹配
  3. 部分数据使用 "speaker" 字段，部分使用 "role" 字段
```

### Q: BEAM 大规模测试 OOM（内存不足）

```
症状: 向量库崩溃或 Python 进程被 kill

解决:
  1. 使用 Docker 部署 Qdrant（分配足够的内存限制）
     docker run -d -p 6333:6333 \
       --memory=8g \
       qdrant/qdrant

  2. 分批写入（不要一次性加载所有对话）

  3. 考虑使用 Qdrant Cloud（有更大的资源配额）

  4. 10M 级别建议使用 SSD + 16GB+ 内存的机器
```

### Q: 评估脚本的 LLM Judge 判断不一致

```
症状: 同一个答案多次评分结果不同

原因: LLM Judge 本身有随机性

解决:
  1. 确保 Judge 的 temperature=0
  2. 如果仍有波动，运行 3 次取中位数
  3. 对于边界案例，人工复核

  这也是 Benchmark 本身的误差来源之一
```

### Q: Windows 上的编码问题

```
症状: UnicodeDecodeError 或中文乱码

解决:
  1. 设置环境变量
     set PYTHONIOENCODING=utf-8

  2. 文件读写时显式指定编码
     open("file.json", "r", encoding="utf-8")

  3. 推荐使用 WSL2 (Linux) 环境运行评估
```

---

## 参考资源

### 评估框架与数据集

| 资源 | 链接 | 说明 |
|------|------|------|
| Mem0 评估框架 | https://github.com/mem0ai/memory-benchmarks | 包含 LoCoMo / LongMemEval / BEAM 三套评估脚本 |
| LongMemEval 独立框架 | https://github.com/xiaowu0162/longmemeval | 500 题 + 评估脚本，争议最少 |
| LoCoMo 数据集 | https://github.com/snap-research/LoCoMo | 10 段对话 + 1,986 QA 对 |
| LoCoMo 项目页 | https://snap-research.github.io/locomo/ | 论文 + 数据集入口 |
| LongMemEval 数据集 | https://huggingface.co/datasets/xiaowu0162/longmemeval | HuggingFace 托管 |

### 竞品系统

| 系统 | 链接 | 说明 |
|------|------|------|
| Mem0 | https://github.com/mem0ai/mem0 | 本项目主要分析对象 |
| Zep/Graphiti | https://github.com/getzep/graphiti | 时序知识图谱 |
| Letta | https://github.com/letta-ai/letta | OS 式三层记忆 |
| Mastra | https://github.com/mastra-ai/mastra-observational-memory-workshop | 观察式记忆 |
| Hindsight | https://github.com/vectorize-io/hindsight | BEAM 之王 |

### 论文

| 论文 | 链接 |
|------|------|
| Mem0 (ECAI 2025) | https://arxiv.org/abs/2504.19413 |
| LoCoMo (ACL 2024) | https://aclanthology.org/2024.acl-long.747.pdf |
| LongMemEval | https://arxiv.org/html/2410.10813v1 |
| Hindsight | https://arxiv.org/abs/2512.12818 |

### 争议与讨论

| 资源 | 链接 |
|------|------|
| Zep 质疑 Mem0 评估 | https://blog.getzep.com/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/ |
| Zep 分数修正讨论 | https://github.com/getzep/zep-papers/issues/ |
| Hindsight BEAM SOTA | https://hindsight.vectorize.io/blog/2026/04/02/beam-sota |
| Mastra 95% LongMemEval | https://mastra.ai/research/observational-memory |

### 项目内相关文档

- [算法演进教程](01-algorithm-tutorial.md) — 第六层（论文解读）+ 第七层（评估基准理论详解）
- [源码探索](02-source-code-tour.md) — v3 流水线逐行分析
- [竞品调研](04-alternatives-research.md) — 9 大方案的 Benchmark 分数汇总与架构对比
- [实践指南](03-practice-guide.md) — Mem0 环境搭建 + Demo 运行
