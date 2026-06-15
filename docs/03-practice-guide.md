# Mem0 动手实践指南

> 配套 Demo 脚本，从零开始跑通 Mem0 的核心功能

## 环境搭建

```bash
# 1. 进入项目目录
cd D:/code/mem0/mem0

# 2. 创建虚拟环境（uv 自动安装 Python 3.12）
uv python install 3.12
uv venv --python 3.12 .venv

# 3. 安装 mem0（从本地源码）
uv pip install -e .

# 4. 验证安装
.venv/Scripts/python.exe -c "import mem0; print(mem0.__version__)"
# 输出: 2.0.6
```

## Demo 脚本

### Demo 01: 原始记忆 CRUD

```bash
# 运行（设置 UTF-8 编码）
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe demo_01_raw_memory.py
```

**学到的东西:**

| 概念 | 代码 | 说明 |
|------|------|------|
| 添加记忆 | `m.add(text, user_id="x", infer=False)` | 直接存储原始文本 |
| 查看所有 | `m.get_all(filters={"user_id": "x"})` | 列出该用户的所有记忆 |
| 搜索记忆 | `m.search("query", filters={...}, top_k=3)` | 向量相似度搜索 |
| 更新记忆 | `m.update(memory_id, new_text)` | 替换记忆内容 |
| 删除记忆 | `m.delete(memory_id)` | 删除单条记忆 |
| 删除全部 | `m.delete_all(user_id="x")` | 删除某用户的所有记忆 |
| 查看历史 | `m.history(memory_id)` | 查看变更历史 |
| 记忆隔离 | `filters={"user_id": "x"}` | 不同用户的记忆互不可见 |

**关键观察:**
- `infer=False` = 原封不动存储文本（没有 LLM 参与）
- 向量搜索能找到"意思相近"但"用词不同"的内容
- 自定义 n-gram 嵌入对中文语义理解有限（生产环境用 OpenAI/HuggingFace）

---

### Demo 02: v3 完整流水线

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe demo_02_llm_extraction.py
```

**学到的东西 — v3 的 8 个 Phase:**

```
Phase 0  上下文收集     获取最近 10 条历史消息
Phase 1  检索已有记忆   搜索 top-10 相关记忆（给 LLM 做去重参考）
Phase 2  LLM 提取      单次调用，只产出 ADD 操作（核心！）
Phase 3  批量嵌入      一次性把所有新记忆变成向量
Phase 4-5 哈希去重     MD5 哈希过滤完全重复的文本
Phase 6  批量写入      一次性写入向量库（比逐条快）
Phase 7  实体链接      提取实体→存入实体集合→关联记忆
Phase 8  保存历史      原始对话存入 SQLite
```

**关键观察:**
- System Prompt 长达 33,653 字符，极其详细地指导 LLM 的行为
- UUID 被映射为整数 "0","1"... 防止 LLM 产生 UUID 幻觉
- "昨天" 被自动转换为 "2026年6月14日"（时间锚定）
- 三段对话后积累 7 条记忆，只增不减（ADD-only）

---

## 自定义嵌入模型

由于 Windows DLL 兼容性问题（torch/onnx），Demo 使用了自定义的轻量级嵌入模型：

```python
class SimpleEmbedding(EmbeddingBase):
    """基于字符 n-gram 的轻量级嵌入"""
    def __init__(self):
        self.dim = 384  # 向量维度
        self.n = 3      # 字符 n-gram 大小

    def embed(self, text, memory_action=None):
        # 1. 把文本拆成 3-gram 字符片段
        # 2. 用 MD5 哈希映射到 384 维向量
        # 3. L2 归一化
        ...
```

**生产环境推荐:**

| 嵌入模型 | 安装 | 优点 | 缺点 |
|---------|------|------|------|
| OpenAI | `pip install openai` | 质量最高 | 需要 API key，收费 |
| HuggingFace | `pip install sentence-transformers` | 免费、本地 | 需要 PyTorch |
| FastEmbed | `pip install fastembed` | 轻量、ONNX | 需要 onnxruntime |

## 文件结构

```
mem0/
├── demo_01_raw_memory.py       # Demo 01: 原始记忆 CRUD
├── demo_02_llm_extraction.py   # Demo 02: v3 完整流水线
├── mem0-algorithm-tutorial.md  # 算法教程文档（1873 行）
├── mem0-paper.pdf              # Mem0 论文 PDF
└── mem0/                       # Mem0 源码
    ├── mem0/memory/main.py     # 核心代码（v3 流水线）
    ├── mem0/configs/prompts.py # Prompt 系统
    ├── mem0/utils/scoring.py   # 分数融合算法
    └── ...
```

## 常见问题

### Q: 搜索分数全是 0？
A: 自定义 n-gram 嵌入对中文的语义理解有限。短查询（如"火锅"只有2个字）产生的 3-gram 太少，与长文本几乎没有重叠。生产环境用 OpenAI embeddings 会得到合理的分数。

### Q: 如何启用完整的混合搜索？
A: 安装可选依赖:
```bash
pip install "mem0ai[nlp]"        # spaCy 实体提取
python -m spacy download en_core_web_sm
pip install fastembed             # BM25 关键词搜索
```

### Q: 如何用 OpenAI 的 LLM 和 Embedding？
```python
config = {
    "llm": {
        "provider": "openai",
        "config": {"model": "gpt-4o-mini", "temperature": 0}
    },
    "embedder": {
        "provider": "openai",
        "config": {"model": "text-embedding-3-small"}
    }
}
m = Memory.from_config(config)
```
需要设置 `OPENAI_API_KEY` 环境变量。

### Q: DLL 加载失败怎么办？
Windows 上 torch/onnxruntime 经常有 DLL 兼容问题。解决方式:
1. 使用自定义嵌入模型（如 Demo 中的 SimpleEmbedding）
2. 安装 CPU 版 PyTorch: `pip install torch --index-url https://download.pytorch.org/whl/cpu`
3. 使用 WSL2 (Linux) 环境
