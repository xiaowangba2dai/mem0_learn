"""
Demo 02: 模拟 v3 完整流水线（无需 API key）
=============================================

目的: 一步步手动走完 v3 的 8 阶段流水线，看清每一步发生了什么
方式: 用模拟的 LLM 输出替代真实 API 调用，其余全部真实执行

你会看到:
  Phase 0: 上下文收集
  Phase 1: 检索已有记忆
  Phase 2: LLM 提取（模拟）→ 看看 Prompt 到底长什么样
  Phase 3: 批量嵌入
  Phase 4-5: 哈希去重
  Phase 6: 批量写入
  Phase 7: 实体链接
  Phase 8: 保存消息历史
"""

import hashlib
import json
import math
import uuid
from datetime import datetime, timezone
from copy import deepcopy

from mem0 import Memory
from mem0.embeddings.base import EmbeddingBase
from mem0.configs.prompts import (
    ADDITIVE_EXTRACTION_PROMPT,
    generate_additive_extraction_prompt,
)
from mem0.memory.utils import parse_messages

# ============================================================
# 复用轻量嵌入模型
# ============================================================

class SimpleEmbedding(EmbeddingBase):
    def __init__(self, config=None):
        super().__init__(config)
        self.dim = 384
        self.n = 3

    def _text_to_ngrams(self, text):
        text = text.lower().strip()
        return [text[i:i+self.n] for i in range(len(text) - self.n + 1)]

    def embed(self, text, memory_action=None):
        ngrams = self._text_to_ngrams(text)
        vector = [0.0] * self.dim
        for ngram in ngrams:
            h = int(hashlib.md5(ngram.encode()).hexdigest(), 16)
            idx = h % self.dim
            sign = 1 if (h // self.dim) % 2 == 0 else -1
            vector[idx] += sign * 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embed_batch(self, texts, memory_action=None):
        return [self.embed(t, memory_action) for t in texts]


# ============================================================
# 初始化
# ============================================================

print("=" * 60)
print("  Demo 02: v3 完整流水线逐步拆解")
print("=" * 60)
print()
print("  本 Demo 不用 API key!")
print("  我们手动走完 v3 的 8 个 Phase，每步都打印出来")
print("  LLM 的输出来自预设的模拟响应（基于真实 Prompt 格式）")

from mem0.configs.base import MemoryConfig
from mem0.utils.factory import VectorStoreFactory
from mem0.memory.storage import SQLiteManager

mc = MemoryConfig()
mc.vector_store.provider = "qdrant"
mc.vector_store.config.collection_name = "demo02_memories"
mc.vector_store.config.embedding_model_dims = 384

embedder = SimpleEmbedding()
vector_store = VectorStoreFactory.create("qdrant", mc.vector_store.config)
db = SQLiteManager(mc.history_db_path)
metadata = {"user_id": "zhangming"}
filters = {"user_id": "zhangming"}

print("\n✅ 组件初始化完成\n")

# ============================================================
# 模拟的 LLM 输出（基于 ADDITIVE_EXTRACTION_PROMPT 的真实格式）
# ============================================================

def mock_llm_extract(messages_text, existing_memories=None):
    """模拟 LLM 的记忆提取输出。

    在真实系统中，这是 Anthropic/OpenAI API 返回的 JSON。
    输出格式严格遵循 ADDITIVE_EXTRACTION_PROMPT 中定义的格式。
    """
    if "张明" in messages_text and "深圳" in messages_text and "腾讯" in messages_text:
        return {
            "memory": [
                {"id": "0", "text": "用户的名字叫张明，今年28岁，在深圳做前端开发", "attributed_to": "user"},
                {"id": "1", "text": "张明最近从腾讯离职，准备创业做AI产品", "attributed_to": "user"},
            ]
        }
    elif "深圳湾" in messages_text and "李华" in messages_text:
        return {
            "memory": [
                {"id": "0", "text": "张明在2026年6月14日去了深圳湾跑步，跑了10公里", "attributed_to": "user"},
                {"id": "1", "text": "张明跑步后遇到了大学同学李华，李华在华为工作，两人聊了很久", "attributed_to": "user"},
                {"id": "2", "text": "张明养了一只柯基犬叫豆豆，每天遛豆豆是他最放松的时间", "attributed_to": "user"},
            ]
        }
    elif "融资" in messages_text or "红杉" in messages_text:
        return {
            "memory": [
                {"id": "0", "text": "张明的AI产品拿到了红杉资本的天使轮融资，融资金额500万", "attributed_to": "user"},
                {"id": "1", "text": "张明决定搬到南山区的科技园办公", "attributed_to": "user"},
            ]
        }
    else:
        return {"memory": []}


# ============================================================
# 场景: 三段对话，逐步积累记忆
# ============================================================

conversations = [
    {
        "title": "场景 1: 第一次对话 — 自我介绍",
        "messages": [
            {"role": "user", "content": "你好！我叫张明，今年 28 岁，在深圳做前端开发。我最近刚从腾讯离职，准备创业做 AI 产品。"},
            {"role": "assistant", "content": "你好张明！从腾讯离职创业做 AI 产品，这个决定很有勇气。"},
        ],
    },
    {
        "title": "场景 2: 第二次对话 — 聊生活",
        "messages": [
            {"role": "user", "content": "昨天我去了深圳湾跑步，跑了 10 公里。跑完遇到了我大学同学李华，他在华为工作，我们聊了很久。"},
            {"role": "assistant", "content": "10 公里很不错！遇到老同学总是很开心。"},
            {"role": "user", "content": "是啊。对了，我养了一只柯基犬叫豆豆，每天遛它的时候是我最放松的时间。"},
            {"role": "assistant", "content": "柯基犬很可爱！豆豆一定是你的好伙伴。"},
        ],
    },
    {
        "title": "场景 3: 第三次对话 — 好消息",
        "messages": [
            {"role": "user", "content": "好消息！我的 AI 产品拿到天使轮融资了，是红杉资本投的，融了 500 万。我决定搬到南山区的科技园去办公。"},
            {"role": "assistant", "content": "恭喜张明！红杉资本的天使轮是很了不起的成就。"},
        ],
    },
]

total_memories_added = 0

for conv_idx, conv in enumerate(conversations):
    print("\n" + "=" * 60)
    print(f"  {conv['title']}")
    print("=" * 60)

    messages = conv["messages"]

    # ------ Phase 0: 上下文收集 ------
    print(f"\n{'─'*50}")
    print(f"  Phase 0: 上下文收集")
    print(f"{'─'*50}")

    session_scope = f"user_id={filters['user_id']}"
    last_messages = db.get_last_messages(session_scope, limit=10)
    parsed_messages = parse_messages(messages)

    print(f"  session_scope = \"{session_scope}\"")
    print(f"  历史消息数: {len(last_messages) if last_messages else 0} 条")
    print(f"  当前对话:\n    {parsed_messages[:80]}...")

    # ------ Phase 1: 检索已有记忆 ------
    print(f"\n{'─'*50}")
    print(f"  Phase 1: 检索已有记忆 (top-10)")
    print(f"{'─'*50}")

    query_embedding = embedder.embed(parsed_messages, "search")
    existing_results = vector_store.search(
        query=parsed_messages,
        vectors=query_embedding,
        top_k=10,
        filters=filters,
    )

    existing_memories = []
    uuid_mapping = {}
    for idx, mem in enumerate(existing_results):
        uuid_mapping[str(idx)] = mem.id
        existing_memories.append({"id": str(idx), "text": mem.payload.get("data", "")})

    print(f"  找到 {len(existing_memories)} 条相关记忆")
    for em in existing_memories:
        print(f"    [{em['id']}] {em['text'][:60]}...")

    # ------ Phase 2: LLM 提取（关键步骤！）------
    print(f"\n{'─'*50}")
    print(f"  Phase 2: LLM 单次提取 (ADDITIVE_EXTRACTION_PROMPT)")
    print(f"{'─'*50}")

    # 展示真实的 Prompt 结构
    if conv_idx == 0:
        user_prompt = generate_additive_extraction_prompt(
            existing_memories=existing_memories,
            new_messages=parsed_messages,
            last_k_messages=last_messages,
        )
        print(f"\n  ┌─ System Prompt (ADDITIVE_EXTRACTION_PROMPT)")
        print(f"  │  长度: {len(ADDITIVE_EXTRACTION_PROMPT)} 字符")
        print(f"  │  核心指令: 你是 Memory Extractor，唯一的操作是 ADD")
        print(f"  │  提取: 从 user 和 assistant 消息中提取事实")
        print(f"  │  去重: 不要重复已有记忆")
        print(f"  │  质量: 自包含、时间锚定、保留细节")
        print(f"  └─")
        print(f"  ┌─ User Prompt (构建)")
        print(f"  │  ## Summary: (空)")
        print(f"  │  ## Last k Messages: {len(last_messages) if last_messages else 0} 条")
        print(f"  │  ## Recently Extracted: []")
        print(f"  │  ## Existing Memories: {json.dumps(existing_memories, ensure_ascii=False)[:100]}")
        print(f"  │  ## New Messages: \"{parsed_messages[:80]}...\"")
        print(f"  │  ## Observation Date: {datetime.now(timezone.utc).date()}")
        print(f"  │  ## Current Date: {datetime.now(timezone.utc).date()}")
        print(f"  └─")

    # 模拟 LLM 输出
    llm_response = mock_llm_extract(parsed_messages, existing_memories)
    extracted_memories = llm_response.get("memory", [])

    print(f"\n  🤖 LLM 输出 ({len(extracted_memories)} 条):")
    for em in extracted_memories:
        print(f"    [{em['id']}] {em['text']}")
        print(f"        attributed_to={em.get('attributed_to', 'N/A')}")

    if not extracted_memories:
        print("  (无新记忆)")
        db.save_messages(messages, session_scope)
        continue

    # ------ Phase 3: 批量嵌入 ------
    print(f"\n{'─'*50}")
    print(f"  Phase 3: 批量嵌入")
    print(f"{'─'*50}")

    mem_texts = [m.get("text", "") for m in extracted_memories if m.get("text")]
    mem_embeddings = embedder.embed_batch(mem_texts, "add")
    embed_map = dict(zip(mem_texts, mem_embeddings))

    print(f"  嵌入 {len(mem_texts)} 条文本 → {len(mem_embeddings)} 个向量")
    print(f"  向量维度: {len(mem_embeddings[0])}")
    print(f"  示例向量 (前 5 维): [{', '.join(f'{v:.3f}' for v in mem_embeddings[0][:5])}, ...]")

    # ------ Phase 4-5: 哈希去重 ------
    print(f"\n{'─'*50}")
    print(f"  Phase 4-5: 哈希去重 + 预处理")
    print(f"{'─'*50}")

    existing_hashes = set()
    for mem in existing_results:
        h = mem.payload.get("hash") if hasattr(mem, "payload") and mem.payload else None
        if h:
            existing_hashes.add(h)

    records = []
    seen_hashes = set()

    for mem in extracted_memories:
        text = mem.get("text")
        if not text or text not in embed_map:
            continue

        mem_hash = hashlib.md5(text.encode()).hexdigest()
        is_dup = mem_hash in existing_hashes or mem_hash in seen_hashes
        seen_hashes.add(mem_hash)

        if is_dup:
            print(f"  ⏭️  跳过重复: \"{text[:40]}...\" (hash={mem_hash[:8]})")
        else:
            memory_id = str(uuid.uuid4())
            mem_metadata = deepcopy(metadata)
            mem_metadata["data"] = text
            mem_metadata["hash"] = mem_hash
            now = datetime.now(timezone.utc).isoformat()
            mem_metadata["created_at"] = now
            mem_metadata["updated_at"] = now

            records.append((memory_id, text, embed_map[text], mem_metadata))
            print(f"  ✅ 新增: \"{text[:50]}...\" (hash={mem_hash[:8]})")

    if not records:
        print("  (全部重复，无新增)")
        db.save_messages(messages, session_scope)
        continue

    # ------ Phase 6: 批量写入 ------
    print(f"\n{'─'*50}")
    print(f"  Phase 6: 批量写入向量库")
    print(f"{'─'*50}")

    all_vectors = [r[2] for r in records]
    all_ids = [r[0] for r in records]
    all_payloads = [r[3] for r in records]

    vector_store.insert(vectors=all_vectors, ids=all_ids, payloads=all_payloads)
    total_memories_added += len(records)

    # 写入历史
    for mid, text, _, payload in records:
        db.add_history(mid, None, text, "ADD", created_at=payload.get("created_at"))

    print(f"  批量写入 {len(records)} 条记忆到向量库")
    for mid, text, _, _ in records:
        print(f"    [{mid[:8]}] {text[:55]}...")

    # ------ Phase 7: 实体链接（简化版）------
    print(f"\n{'─'*50}")
    print(f"  Phase 7: 实体提取与链接（概念演示）")
    print(f"{'─'*50}")

    print(f"\n  ⚠️ 注意: 实体提取需要 spaCy（本机未安装）")
    print(f"  以下是如果安装了 spaCy 后会提取的实体:\n")

    # 手动模拟实体提取结果
    entity_map = {
        0: [("PROPER", "张明"), ("PROPER", "深圳")],
        1: [("PROPER", "张明"), ("PROPER", "腾讯"), ("COMPOUND", "AI product")],
        2: [("PROPER", "深圳湾"), ("PROPER", "李华"), ("PROPER", "华为")],
        3: [("PROPER", "豆豆"), ("NOUN", "柯基犬")],
        4: [("PROPER", "红杉资本"), ("COMPOUND", "angel round"), ("PROPER", "科技园"), ("PROPER", "南山区")],
        5: [("PROPER", "张明")],
        6: [("PROPER", "张明"), ("PROPER", "南山区"), ("PROPER", "科技园")],
    }

    for i, (mid, text, _, _) in enumerate(records):
        global_idx = total_memories_added - len(records) + i
        entities = entity_map.get(global_idx, [])
        print(f"  记忆 [{mid[:8]}]: \"{text[:45]}...\"")
        for etype, etext in entities:
            print(f"    → 实体: {etext} (类型: {etype})")
            print(f"      存入: {vector_store.collection_name if hasattr(vector_store, 'collection_name') else 'memories'}_entities 集合")
            print(f"      linked_memory_ids: [{mid[:8]}]")

    # ------ Phase 8: 保存消息历史 ------
    print(f"\n{'─'*50}")
    print(f"  Phase 8: 保存消息历史")
    print(f"{'─'*50}")

    db.save_messages(messages, session_scope)
    print(f"  ✅ 保存 {len(messages)} 条消息到 SQLite")

# ============================================================
# 查看最终结果
# ============================================================

print("\n" + "=" * 60)
print(f"  最终结果: 共 {total_memories_added} 条记忆")
print("=" * 60)

all_results = vector_store.list(filters=filters, top_k=50)
actual_memories = all_results[0] if isinstance(all_results, (list, tuple)) and all_results else []

print(f"\n📋 向量库中的所有记忆:\n")
for i, mem in enumerate(actual_memories):
    text = mem.payload.get("data", "")
    created = mem.payload.get("created_at", "")[:16]
    print(f"  [{i+1:2d}] {text}")
    print(f"       hash={mem.payload.get('hash', '')[:12]}  created={created}")

# ============================================================
# 搜索体验
# ============================================================

print("\n" + "-" * 60)
print("  搜索体验")
print("-" * 60)

queries = ["张明的工作", "张明的宠物", "融资"]

for query in queries:
    print(f"\n  🔎 \"{query}\"")
    q_emb = embedder.embed(query, "search")
    results = vector_store.search(query=query, vectors=q_emb, top_k=3, filters=filters)
    for i, r in enumerate(results):
        text = r.payload.get("data", "")
        print(f"     #{i+1} [score={r.score:.3f}] {text[:60]}")

# ============================================================
# 清理
# ============================================================

print("\n" + "-" * 60)
print("  清理")
print("-" * 60)

for mem in actual_memories:
    vector_store.delete(vector_id=mem.id)
print("  ✅ 已清理")

print("\n" + "=" * 60)
print("  Demo 02 完成!")
print("=" * 60)

print("""
📌 v3 流水线 8 阶段总结:

  Phase 0  上下文收集     获取最近 10 条历史消息
  Phase 1  检索已有记忆   搜索 top-10 相关记忆（给 LLM 做去重参考）
  Phase 2  LLM 提取      单次调用，只产出 ADD 操作（核心！）
  Phase 3  批量嵌入      一次性把所有新记忆变成向量
  Phase 4-5 哈希去重     MD5 哈希过滤完全重复的文本
  Phase 6  批量写入      一次性写入向量库（比逐条快）
  Phase 7  实体链接      提取实体→存入实体集合→关联记忆
  Phase 8  保存历史      原始对话存入 SQLite

🔑 关键设计:
  - UUID 映射为整数（防 LLM 幻觉）
  - 时间锚定（"昨天" → 具体日期）
  - 只有 ADD，没有 UPDATE/DELETE
  - 实体链接替代了 v2 的 Neo4j 图数据库
""")
