"""
Demo 01: 原始记忆 CRUD（不需要 LLM，不需要 GPU）
==================================================

目的: 理解向量数据库如何存储和检索记忆
模式: infer=False（跳过 LLM 提取，直接存储原始文本）
嵌入: 自定义轻量级字符 n-gram 嵌入（纯 Python，无需 torch/onnx）

你会看到:
  1. 记忆如何被向量化并存入数据库
  2. 语义搜索如何找到"意思相近"的记忆
  3. get_all / update / delete 的完整生命周期
  4. 不同 user_id 的记忆隔离
"""

import hashlib
import math
from collections import Counter
from mem0 import Memory
from mem0.embeddings.base import EmbeddingBase

# ============================================================
# 自定义轻量级嵌入模型
# 使用字符 n-gram + TF-IDF 风格的向量化
# 纯 Python 实现，无需 torch/onnx/GPU
# ============================================================

class SimpleEmbedding(EmbeddingBase):
    """基于字符 n-gram 的轻量级嵌入模型。

    原理:
      1. 将文本拆成 3-gram 字符片段（如 "hello" → "hel","ell","llo"）
      2. 用哈希映射到固定维度的向量空间
      3. 相似的文本会产生相似的 n-gram，因此向量也相似

    这不是生产级的嵌入模型，但足以演示核心概念!
    """

    def __init__(self, config=None):
        super().__init__(config)
        self.dim = 384  # 向量维度
        self.n = 3      # n-gram 大小

    def _text_to_ngrams(self, text):
        """将文本转换为字符 n-gram 列表"""
        text = text.lower().strip()
        return [text[i:i+self.n] for i in range(len(text) - self.n + 1)]

    def embed(self, text, memory_action=None):
        """生成文本的向量"""
        ngrams = self._text_to_ngrams(text)
        vector = [0.0] * self.dim

        for ngram in ngrams:
            # 用哈希将 n-gram 映射到向量位置
            h = int(hashlib.md5(ngram.encode()).hexdigest(), 16)
            idx = h % self.dim
            # 用另一个哈希决定正负号
            sign = 1 if (h // self.dim) % 2 == 0 else -1
            vector[idx] += sign * 1.0

        # L2 归一化（使向量长度为 1，方便余弦相似度计算）
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embed_batch(self, texts, memory_action=None):
        return [self.embed(t, memory_action) for t in texts]


# ============================================================
# 1. 初始化 Memory（使用自定义嵌入 + Qdrant 内存模式）
# ============================================================

print("=" * 60)
print("  Demo 01: 原始记忆 CRUD (infer=False)")
print("=" * 60)
print()
print("  使用自定义 n-gram 嵌入模型（纯 Python，无外部依赖）")
print("  这让我们能在任何机器上立即运行，无需下载大模型")

# 直接注入自定义嵌入类
from mem0.configs.base import MemoryConfig
from mem0.utils.factory import VectorStoreFactory
from mem0.memory.storage import SQLiteManager

config = MemoryConfig()
config.vector_store.provider = "qdrant"
config.vector_store.config.collection_name = "demo01_memories"
config.vector_store.config.embedding_model_dims = 384

m = Memory.__new__(Memory)
m.config = config
m.embedding_model = SimpleEmbedding()
m.vector_store = VectorStoreFactory.create("qdrant", config.vector_store.config)
m.llm = None  # infer=False 不需要 LLM
m.db = SQLiteManager(config.history_db_path)
m.collection_name = config.vector_store.config.collection_name
m.api_version = config.version
m.custom_instructions = config.custom_instructions
m.reranker = None
m._entity_store = None

print(f"\n✅ Memory 初始化完成")
print(f"   向量库: qdrant (内存模式)")
print(f"   嵌入模型: SimpleEmbedding (字符 3-gram, 维度=384)")

# ============================================================
# 2. 添加原始记忆（infer=False 跳过 LLM 提取）
# ============================================================

print("\n" + "-" * 60)
print("📝 Step 1: 添加原始记忆 (infer=False)")
print("-" * 60)
print()
print("  infer=False 意味着: 我们直接存储原始文本")
print("  不经过 LLM 提取，文本原封不动地存入向量数据库")
print()

memories_to_add = [
    "用户的名字叫小明，在北京做软件工程师",
    "小明喜欢吃火锅，尤其是海底捞",
    "小明养了一只金毛犬叫旺财，每天早晚各遛一次",
    "小明的女朋友叫小红，他们在大学认识的",
    "小明最近开始学习机器学习，每天晚上看一小时网课",
]

for text in memories_to_add:
    result = m.add(text, user_id="xiaoming", infer=False)
    mem_id = result["results"][0]["id"]
    print(f"  ✅ [{mem_id[:8]}] \"{text}\"")

# ============================================================
# 3. 查看所有记忆 (get_all)
# ============================================================

print("\n" + "-" * 60)
print("📋 Step 2: 查看所有记忆 (get_all)")
print("-" * 60)

all_memories = m.get_all(filters={"user_id": "xiaoming"}, top_k=50)
print(f"\n  数据库中共 {len(all_memories['results'])} 条记忆:\n")
for i, mem in enumerate(all_memories["results"]):
    print(f"  [{i+1}] {mem['memory']}")
    print(f"      id={mem['id'][:12]}...  hash={mem['hash'][:12]}...")

# ============================================================
# 4. 向量搜索（核心体验！）
# ============================================================

print("\n" + "-" * 60)
print("🔍 Step 3: 向量搜索体验")
print("-" * 60)
print()
print("  搜索原理:")
print("    1. 把查询文本变成 384 维向量")
print("    2. 在向量库中计算余弦相似度")
print("    3. 返回最相似的记忆")
print()

queries = [
    "小明吃什么",
    "小明的宠物",
    "小明在学什么",
    "小明的感情生活",
]

for query in queries:
    print(f"  🔎 查询: \"{query}\"")
    results = m.search(query, filters={"user_id": "xiaoming"}, top_k=2, threshold=0.0)
    for i, r in enumerate(results["results"]):
        print(f"     #{i+1} [score={r['score']:.3f}] {r['memory']}")
    print()

# ============================================================
# 5. 观察向量搜索的特点
# ============================================================

print("-" * 60)
print("🧠 Step 4: 向量搜索的特点")
print("-" * 60)
print()
print("  n-gram 嵌入的特点: 共享字符片段越多，分数越高")
print()

# 测试文本重叠
test_pairs = [
    ("火锅", "小明喜欢吃火锅，尤其是海底捞"),
    ("金毛", "小明养了一只金毛犬叫旺财"),
    ("机器学习", "小明最近开始学习机器学习"),
]

print("  逐对分析查询和结果的字符重叠:\n")
for query, expected in test_pairs:
    results = m.search(query, filters={"user_id": "xiaoming"}, top_k=1, threshold=0.0)
    if results["results"]:
        r = results["results"][0]
        shared_chars = set(query) & set(r["memory"])
        print(f"  查询: \"{query}\"")
        print(f"  最佳匹配: \"{r['memory']}\"")
        print(f"  共享字符: {shared_chars}")
        print(f"  分数: {r['score']:.3f}")
        print()

# ============================================================
# 6. 更新记忆 (update)
# ============================================================

print("-" * 60)
print("✏️  Step 5: 更新记忆 (update)")
print("-" * 60)

results = m.search("火锅", filters={"user_id": "xiaoming"}, top_k=1, threshold=0.0)
if results["results"]:
    old_mem = results["results"][0]
    mem_id = old_mem["id"]
    old_text = old_mem["memory"]

    print(f"\n  旧记忆: \"{old_text}\"")
    new_text = "小明喜欢吃火锅，最近迷上了重庆老火锅和潮汕牛肉火锅"
    print(f"  新内容: \"{new_text}\"")

    m.update(mem_id, new_text)

    updated = m.get(mem_id)
    print(f"  更新后: \"{updated['memory']}\"")
    print(f"  ✅ 更新成功! (同一 ID，内容已替换)")

# ============================================================
# 7. 删除记忆 (delete)
# ============================================================

print("\n" + "-" * 60)
print("🗑️  Step 6: 删除记忆 (delete)")
print("-" * 60)

results = m.search("机器学习", filters={"user_id": "xiaoming"}, top_k=1, threshold=0.0)
if results["results"]:
    mem = results["results"][0]
    print(f"\n  删除前共 {len(m.get_all(filters={'user_id': 'xiaoming'}, top_k=50)['results'])} 条记忆")
    print(f"  删除: \"{mem['memory']}\"")
    m.delete(mem["id"])
    print(f"  删除后共 {len(m.get_all(filters={'user_id': 'xiaoming'}, top_k=50)['results'])} 条记忆")
    print(f"  ✅ 删除成功!")

# ============================================================
# 8. 记忆隔离（user_id 的作用）
# ============================================================

print("\n" + "-" * 60)
print("🔒 Step 7: 记忆隔离 (user_id)")
print("-" * 60)
print()
print("  为不同用户添加记忆，观察隔离效果:")
print()

m.add("小李喜欢喝咖啡，每天早上一杯美式", user_id="xiaoli", infer=False)
m.add("小李在上海做产品经理，周末喜欢跑步", user_id="xiaoli", infer=False)

print("  同一个查询 \"喜欢\" 在不同用户下:\n")

print("  📌 user_id=xiaoming:")
r_xm = m.search("喜欢", filters={"user_id": "xiaoming"}, top_k=2, threshold=0.0)
for r in r_xm["results"]:
    print(f"     → {r['memory']}")

print()
print("  📌 user_id=xiaoli:")
r_xl = m.search("喜欢", filters={"user_id": "xiaoli"}, top_k=2, threshold=0.0)
for r in r_xl["results"]:
    print(f"     → {r['memory']}")

print()
print("  💡 同一个查询，不同 user_id 返回完全不同的结果!")
print("     这就是记忆隔离——每个用户的记忆空间独立，互不干扰。")

# ============================================================
# 9. 查看记忆的历史记录 (history)
# ============================================================

print("\n" + "-" * 60)
print("📜 Step 8: 记忆历史 (history)")
print("-" * 60)
print()
print("  每条记忆都有完整的变更历史:")
print()

# 拿第一条记忆看历史
first_mem = m.get_all(filters={"user_id": "xiaoming"}, top_k=1)["results"][0]
hist = m.history(first_mem["id"])
print(f"  记忆: \"{first_mem['memory'][:40]}...\"")
print(f"  历史记录数: {len(hist)}")
for h in hist:
    print(f"    事件: {h.get('event', 'N/A')}")
    if h.get("old_memory"):
        print(f"    旧值: {h['old_memory'][:40]}...")
    if h.get("new_memory"):
        print(f"    新值: {h['new_memory'][:40]}...")

# ============================================================
# 清理
# ============================================================

print("\n" + "-" * 60)
print("🧹 清理")
print("-" * 60)

m.delete_all(user_id="xiaoming")
m.delete_all(user_id="xiaoli")
print("  ✅ 所有记忆已清理")

print("\n" + "=" * 60)
print("  Demo 01 完成!")
print("=" * 60)

print("""
📌 关键收获:
  1. infer=False: 直接存储原始文本（不经过 LLM 提取）
  2. 向量搜索: 把文字变成向量，用余弦相似度找最相似的
  3. 记忆隔离: 不同 user_id 的记忆互不干扰
  4. CRUD: add → get_all → search → update → delete
  5. 历史记录: 每条记忆的变更都被追踪

🔧 本 Demo 使用的嵌入模型:
  - 自定义字符 3-gram 嵌入（纯 Python）
  - 优点: 零依赖，立即运行
  - 局限: 不理解语义（"开心"和"高兴"不会被视为相似）
  - 生产环境应使用: OpenAI / HuggingFace / FastEmbed 等

⏭️  下一步: 运行 demo_02_llm_extraction.py
   看看 LLM 如何从对话中自动提取结构化记忆!
""")
