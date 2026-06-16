# OpenViking 深度源码研究

> 字节跳动开源的上下文数据库，14.6k+ GitHub Stars
> 基于 Rust + Python 混合架构，代码量约 36,000 行

---

## 目录

- [项目概览](#项目概览)
- [架构全景](#架构全景)
- [核心概念详解](#核心概念详解)
  - [L0/L1/L2 三层上下文](#l0l1l2-三层上下文)
  - [viking:// 协议](#viking-协议)
  - [记忆生命周期](#记忆生命周期)
  - [检索系统](#检索系统)
- [源码结构](#源码结构)
- [Python API 详解](#python-api-详解)
- [Rust 引擎详解](#rust-引擎详解)
- [存储后端](#存储后端)
- [与 Mem0 的对比](#与-mem0-的对比)
- [部署方式](#部署方式)
- [总结](#总结)

---

## 项目概览

| 维度 | 详情 |
|------|------|
| **GitHub** | [volcengine/OpenViking](https://github.com/volcengine/OpenViking) |
| **开源时间** | 2026 年 1 月 |
| **Stars** | 14,600+ |
| **语言** | Rust（核心引擎）+ Python（SDK/CLI）+ TypeScript（Web Studio） |
| **代码量** | ~36,000 行（Rust ~21k + Python ~11k + TS ~4k） |
| **协议** | MIT |
| **定位** | AI Agent 的上下文数据库（Context Database） |
| **核心创新** | 文件系统范式 + L0/L1/L2 分层加载 + viking:// 协议 |

### 一句话描述

OpenViking 把 Agent 的记忆/上下文组织成**文件系统**——像浏览文件夹一样浏览记忆，按需加载不同详细程度的内容，而不是把所有相关记忆一股脑塞进上下文窗口。

---

## 架构全景

```
┌─────────────────────────────────────────────────────────────────┐
│                     OpenViking 架构全景                          │
│                                                                 │
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────────────┐   │
│  │  Python SDK  │   │   CLI 工具   │   │   Web Studio (TS)   │   │
│  │  openviking  │   │ openviking- │   │   localhost:3333     │   │
│  │              │   │    cli      │   │                      │   │
│  └──────┬───────┘   └──────┬──────┘   └──────────┬───────────┘   │
│         │                  │                      │              │
│         └──────────────────┼──────────────────────┘              │
│                            │                                     │
│                            ▼                                     │
│                   ┌────────────────┐                             │
│                   │   REST API     │                             │
│                   │  :8300 (HTTP)  │                             │
│                   └────────┬───────┘                             │
│                            │                                     │
│                            ▼                                     │
│               ┌────────────────────────┐                         │
│               │    Rust 核心引擎        │                         │
│               │                        │                         │
│               │  ┌──────────────────┐  │                         │
│               │  │   Context DB     │  │                         │
│               │  │   (存储层)       │  │                         │
│               │  └──────────────────┘  │                         │
│               │                        │                         │
│               │  ┌──────────────────┐  │                         │
│               │  │   Blob Storage   │  │                         │
│               │  │   (文件存储)     │  │                         │
│               │  └──────────────────┘  │                         │
│               │                        │                         │
│               │  ┌──────────────────┐  │                         │
│               │  │  Vector Storage  │  │                         │
│               │  │  (向量存储)      │  │                         │
│               │  └──────────────────┘  │                         │
│               │                        │                         │
│               │  ┌──────────────────┐  │                         │
│               │  │  Embedding Svc   │  │                         │
│               │  │  (嵌入服务)      │  │                         │
│               │  └──────────────────┘  │                         │
│               │                        │                         │
│               │  ┌──────────────────┐  │                         │
│               │  │  LLM Service     │  │                         │
│               │  │  (摘要/压缩)     │  │                         │
│               │  └──────────────────┘  │                         │
│               └────────────────────────┘                         │
│                                                                  │
│  存储后端: SQLite(默认) / PostgreSQL / MySQL / CockroachDB       │
│  向量后端: InnoDB Vector / pgvector                              │
│  嵌入服务: OpenAI / Voyage / Ollama / vLLM / 远程服务            │
│  LLM 服务: OpenAI / Ollama / vLLM                                │
└─────────────────────────────────────────────────────────────────┘
```

### 与 Mem0 的根本区别

```
Mem0:  对话 → LLM提取事实 → 向量化 → 扁平存入向量库 → 检索时 top-k
       本质: "记忆提取器 + 语义搜索引擎"

OpenViking: 任何数据 → 写入文件系统结构 → L0/L1/L2 三层存储
            → 检索时按需逐层加载 → 只加载必要详细程度
            本质: "Agent 的操作系统文件系统"
```

---

## 核心概念详解

### L0/L1/L2 三层上下文

这是 OpenViking 最核心的创新。

#### 概念类比

```
类比: 你在图书馆找资料

L0 = 书架标签（~100 tokens）
  "物理学 > 量子力学 > 量子纠缠"
  → 一眼看到分类，决定要不要走过去

L1 = 书的目录页（~500 tokens）
  "第3章: 量子纠缠 — Bell不等式, EPR悖论, 实验验证..."
  → 翻开书看目录，决定要不要读具体章节

L2 = 书的完整内容（5000+ tokens）
  "3.1 Bell不等式的推导... 3.2 EPR悖论的详细分析..."
  → 真正需要时才读完整内容

效果:
  传统方式（Mem0）: 把图书馆所有相关书的内容全塞给你
  OpenViking: 先看标签 → 再看目录 → 只在需要时读正文
  → 节省 95% 的 token
```

#### 代码实现

上下文层级的实现在 `openviking/providers/context/` 目录中：

```python
# openviking/providers/context/types.py (关键数据结构)

class ContextNode:
    """上下文节点 — 文件系统中的一个"文件"或"文件夹" """
    context_id: str          # 唯一标识
    node_id: str             # 节点 ID
    parent_node_id: str      # 父节点（目录层级）
    name: str                # 名称（如 "饮食偏好"）
    node_type: str           # 类型: "directory" | "document"
    path: str                # 完整路径: viking://user/preferences/food

class ContextLayer:
    """三层上下文 — 每个节点都有 L0/L1/L2 三个粒度"""
    l0: str                  # 摘要 (~100 tokens): 一句话概括
    l1: str                  # 概览 (~500 tokens): 关键要点
    l2: str                  # 完整内容 (5000+ tokens): 所有细节

class ContextQuery:
    """上下文查询 — 支持指定加载到哪一层"""
    path: str                # viking:// 路径
    max_depth: int           # 递归深度
    target_layer: str        # "l0" | "l1" | "l2"
    semantic_query: str      # 语义搜索关键词（可选）
```

#### L0/L1/L2 生成流程

```
写入数据到 OpenViking 时:

原始内容 (L2):
  "用户喜欢吃川菜，尤其是麻婆豆腐和回锅肉。
   不吃香菜。每周三吃素。最近开始尝试生酮饮食，
   从2026年3月开始的..."（完整内容，5000+ tokens）

自动生成 L1（LLM 压缩）:
  "饮食偏好: 川菜(麻婆豆腐/回锅肉)，不吃香菜，
   周三素食，2026年3月起尝试生酮饮食"（~500 tokens）

自动生成 L0（LLM 再压缩）:
  "用户的饮食偏好记录"（~100 tokens，一句话）

存储:
  三层分别存入数据库
  L0 始终加载（用于目录浏览）
  L1 按需加载（需要概览时）
  L2 按需加载（需要完整内容时）
```

#### 按需加载 Demo

```python
import openviking

ov = openviking.Client()

# Step 1: 只看 L0（目录浏览，极低 token 消耗）
tree = ov.context.list("viking://user/", layer="l0")
# 返回:
#   viking://user/preferences/    — "用户偏好设置"           [~30 tokens]
#   viking://user/work/           — "工作相关信息"           [~30 tokens]
#   viking://user/health/         — "健康与运动记录"         [~30 tokens]
# 总共: ~90 tokens

# Step 2: 深入某个目录，加载 L1
prefs = ov.context.list("viking://user/preferences/", layer="l1")
# 返回:
#   viking://.../food    — "川菜偏好，不吃香菜，周三素食，生酮饮食" [~80 tokens]
#   viking://.../music   — "喜欢爵士乐和古典音乐，最近在学吉他"   [~80 tokens]
# 总共: ~160 tokens

# Step 3: 需要完整信息时，加载 L2
food_detail = ov.context.get("viking://user/preferences/food", layer="l2")
# 返回完整的饮食偏好记录（5000+ tokens）
# 只在真正需要时才加载！

# 对比 Mem0:
#   搜索"用户喜欢什么" → 返回所有相关记忆（可能 10 条，每条几百 tokens）
#   总共: 3000-5000 tokens
#
# OpenViking:
#   浏览 L0 + 深入 L1 = ~250 tokens 就能回答大部分问题
#   节省: 95%
```

### viking:// 协议

#### 设计思想

```
类比: HTTP 协议统一了网页寻址
      viking:// 协议统一了 Agent 上下文寻址

  http://example.com/blog/post-1      → 网页
  file:///home/user/docs/report.pdf   → 本地文件
  viking://user/preferences/food      → Agent 上下文
```

#### 路径结构

```
viking://                              # 协议头
  ├── user/                            # 命名空间（用户空间）
  │   ├── preferences/                 # 目录
  │   │   ├── food                     # 文档
  │   │   ├── music
  │   │   └── travel
  │   ├── work/
  │   │   ├── projects/
  │   │   │   ├── current
  │   │   │   └── archived
  │   │   └── colleagues
  │   └── health/
  │       ├── exercise
  │       └── diet
  │
  ├── skills/                          # 命名空间（技能空间）
  │   ├── python/
  │   │   ├── coding-style
  │   │   └── best-practices
  │   └── rust/
  │       └── ownership
  │
  └── resources/                       # 命名空间（资源空间）
      ├── api-docs/
      └── reference-data/
```

#### 路径解析代码

```python
# openviking/providers/context/resolver.py

class PathResolver:
    """解析 viking:// 路径并定位到具体的上下文节点"""

    def resolve(self, path: str) -> ContextNode:
        """
        viking://user/preferences/food
        → 解析为:
          namespace = "user"
          segments = ["preferences", "food"]
          → 在数据库中找到对应的 ContextNode
        """
        # 去掉 viking:// 前缀
        clean_path = path.replace("viking://", "")

        # 分割路径
        parts = clean_path.strip("/").split("/")
        namespace = parts[0]
        segments = parts[1:]

        # 在数据库中查找节点
        node = self.db.find_node(
            namespace=namespace,
            path_segments=segments,
        )
        return node

    def list_children(self, path: str) -> List[ContextNode]:
        """列出目录下的所有子节点"""
        parent = self.resolve(path)
        return self.db.list_children(parent.node_id)
```

### 记忆生命周期

#### 写入流程

```
数据写入 OpenViking 的完整流程:

用户调用: ov.context.write(path, content)

Step 1: 路径解析
  viking://user/preferences/food
  → 确认目录结构存在（不存在则自动创建）

Step 2: 存储 L2（完整内容）
  原始内容 → 存入 blob storage
  → content_hash 用于去重

Step 3: LLM 生成 L1（概览）
  调用 LLM: "请将以下内容压缩为 ~500 tokens 的概览..."
  → 存入 context DB

Step 4: LLM 生成 L0（摘要）
  调用 LLM: "请用一句话概括以下内容..."
  → 存入 context DB

Step 5: 向量化
  对 L1 内容生成 embedding
  → 存入 vector storage
  → 用于语义搜索

Step 6: 元数据更新
  更新 created_at, updated_at, content_hash
  → 更新父目录的 L0（目录摘要可能需要更新）
```

#### 更新流程

```
当同一路径再次写入时:

1. 计算新内容的 hash
2. 如果 hash 相同 → 跳过（去重）
3. 如果 hash 不同 → 更新所有三层
4. 重新生成向量
5. 更新元数据
```

#### 自演化（Self-Iteration）

```python
# Agent 可以自主管理上下文结构

# 创建新目录
ov.context.mkdir("viking://user/hobbies/photography")

# 移动上下文
ov.context.move(
    "viking://user/notes/camera-settings",
    "viking://user/hobbies/photography/camera-settings"
)

# 合并相似的上下文
ov.context.merge(
    paths=["viking://user/notes/diet-1", "viking://user/notes/diet-2"],
    target="viking://user/preferences/food"
)

# 归档过时内容
ov.context.archive("viking://user/work/projects/old-project")

# 删除
ov.context.delete("viking://user/temp/scratch-notes")
```

### 检索系统

OpenViking 支持三种检索方式，可以组合使用：

#### 1. 路径检索（Directory Retrieval）

```python
# 直接按路径导航 — 无需语义搜索
tree = ov.context.list("viking://user/work/")
# 返回该目录下的所有子节点（L0 摘要）

detail = ov.context.get("viking://user/work/projects/current")
# 返回指定节点的完整内容
```

#### 2. 语义检索（Semantic Search）

```python
# 向量相似度搜索 — 跨整个上下文空间
results = ov.context.search("用户的饮食偏好")
# 返回语义匹配的所有节点，按相似度排序
# 每个结果包含: path, score, l0_summary
```

#### 3. 混合检索（Hybrid — 最常用）

```python
# 语义搜索 + 路径过滤 + 层级控制
results = ov.context.search(
    query="运动习惯",
    scope="viking://user/",     # 只在这个目录下搜索
    target_layer="l1",          # 返回 L1 粒度
    max_depth=3,                # 最多递归 3 层子目录
    top_k=5,                    # 最多返回 5 个结果
)
# 返回:
# [
#   {path: "viking://user/health/exercise", score: 0.92, content: "L1概览..."},
#   {path: "viking://user/hobbies/running", score: 0.85, content: "L1概览..."},
# ]
```

#### 检索流程（Rust 引擎）

```
查询到达 Rust 引擎:
  1. 解析 viking:// 路径 → 确定搜索范围
  2. 如果有语义查询:
     a. 生成查询向量
     b. 在向量存储中搜索 top-k 相似节点
     c. 用路径过滤（scope 限制）
  3. 如果只有路径查询:
     a. 直接在 DB 中列出子节点
  4. 对每个结果:
     a. 根据 target_layer 加载对应层级内容
     b. 如果 max_depth > 0，递归加载子目录
  5. 返回结果列表
```

---

## 源码结构

```
OpenViking/
├── Cargo.toml                    # Rust workspace 定义
├── pyproject.toml                # Python 包定义
│
├── src/                          # Rust 核心引擎 (~21k 行)
│   ├── main.rs                   # 入口：启动 HTTP 服务器
│   ├── server/                   # HTTP 服务器 (Axum)
│   │   ├── mod.rs                # 路由定义
│   │   ├── handlers/             # API 处理器
│   │   │   ├── context.rs        # 上下文 CRUD
│   │   │   ├── search.rs         # 搜索 API
│   │   │   └── admin.rs          # 管理 API
│   │   └── middleware/           # 中间件
│   │
│   ├── core/                     # 核心业务逻辑
│   │   ├── context_db.rs         # 上下文数据库操作
│   │   ├── blob_store.rs         # 文件存储
│   │   ├── vector_store.rs       # 向量存储
│   │   ├── embedding.rs          # 嵌入服务
│   │   └── llm.rs                # LLM 服务（生成 L0/L1）
│   │
│   ├── storage/                  # 存储后端实现
│   │   ├── sqlite.rs             # SQLite 后端
│   │   ├── postgres.rs           # PostgreSQL 后端
│   │   ├── mysql.rs              # MySQL 后端
│   │   └── cockroach.rs          # CockroachDB 后端
│   │
│   └── config/                   # 配置系统
│       └── mod.rs                # 配置加载（TOML 格式）
│
├── crates/                       # Rust 子 crate
│   ├── openviking-schema/        # 数据库 schema 定义 (SeaORM)
│   │   └── src/
│   │       ├── context_nodes.rs  # 上下文节点表
│   │       ├── context_layers.rs # L0/L1/L2 层表
│   │       ├── blobs.rs          # Blob 存储表
│   │       └── vectors.rs        # 向量存储表
│   │
│   └── openviking-migration/     # 数据库迁移
│
├── openviking/                   # Python SDK (~11k 行)
│   ├── __init__.py               # 包入口
│   ├── client.py                 # HTTP 客户端
│   │
│   ├── providers/                # 提供者抽象
│   │   ├── context/              # 上下文管理
│   │   │   ├── types.py          # 类型定义
│   │   │   ├── resolver.py       # viking:// 路径解析
│   │   │   └── manager.py        # 上下文管理器
│   │   │
│   │   ├── memory/               # 记忆管理
│   │   │   ├── types.py          # 记忆类型
│   │   │   ├── extractor.py      # 记忆提取
│   │   │   └── manager.py        # 记忆管理器
│   │   │
│   │   └── storage/              # 存储抽象
│   │       ├── base.py           # 基础接口
│   │       └── backends.py       # 后端实现
│   │
│   ├── cli/                      # CLI 工具
│   │   ├── main.py               # CLI 入口 (Click)
│   │   ├── init.py               # openviking init
│   │   ├── write.py              # openviking write
│   │   ├── search.py             # openviking search
│   │   └── explore.py            # openviking explore（交互式浏览）
│   │
│   └── examples/                 # 使用示例
│       ├── basic_usage.py
│       ├── agent_integration.py
│       └── layered_retrieval.py
│
├── openviking_cli/               # CLI 独立包
│   └── ...
│
├── web-studio/                   # Web UI (TypeScript, ~4k 行)
│   ├── src/
│   │   ├── App.tsx               # 主应用
│   │   ├── components/           # UI 组件
│   │   │   ├── TreeView.tsx      # 目录树浏览器
│   │   │   ├── ContextDetail.tsx # 上下文详情面板
│   │   │   └── SearchBar.tsx     # 搜索栏
│   │   └── api/                  # API 客户端
│   └── package.json
│
├── docs/                         # 文档
│   ├── en/
│   │   ├── concepts/
│   │   │   ├── 01-overview.md
│   │   │   ├── 02-viking-protocol.md
│   │   │   ├── 03-context-layers.md    # L0/L1/L2 详解
│   │   │   └── 04-retrieval.md
│   │   └── guides/
│   │       ├── getting-started.md
│   │       └── deployment.md
│   └── cn/                       # 中文文档
│
├── examples/                     # 完整示例
│   ├── python/
│   │   ├── basic_crud.py
│   │   ├── agent_memory.py
│   │   └── layered_search.py
│   └── rust/
│       └── embedded_usage.rs     # 嵌入模式（无需 HTTP）
│
├── benchmark/                    # 性能测试
│   └── ...
│
├── docker-compose.yml            # Docker 部署
├── Dockerfile
└── deploy/                       # 生产部署配置
    ├── kubernetes/
    └── openshift/
```

---

## Python API 详解

### 客户端初始化

```python
import openviking

# 方式 1: 连接远程服务器
client = openviking.Client(base_url="http://localhost:8300")

# 方式 2: 使用配置
client = openviking.Client.from_config("openviking.toml")
```

### 上下文操作

```python
# ===== 写入 =====

# 写入完整内容（L2），自动生成 L0/L1
client.context.write(
    path="viking://user/preferences/food",
    content="用户喜欢吃川菜，尤其是麻婆豆腐...",
    content_type="text/plain",
)

# 批量写入
client.context.write_batch([
    {"path": "viking://user/work/company", "content": "在字节跳动工作..."},
    {"path": "viking://user/work/role", "content": "高级后端工程师..."},
])

# ===== 读取 =====

# 读取 L0（摘要）
summary = client.context.get("viking://user/preferences/food", layer="l0")
# → "用户的饮食偏好记录"

# 读取 L1（概览）
overview = client.context.get("viking://user/preferences/food", layer="l1")
# → "川菜偏好(麻婆豆腐/回锅肉)，不吃香菜，周三素食，生酮饮食中"

# 读取 L2（完整内容）
full = client.context.get("viking://user/preferences/food", layer="l2")
# → 完整的饮食偏好文本

# ===== 浏览 =====

# 列出目录（L0 摘要）
children = client.context.list("viking://user/preferences/")
# → [
#     {path: "...food", l0: "饮食偏好记录"},
#     {path: "...music", l0: "音乐偏好记录"},
#     {path: "...travel", l0: "旅行计划与经历"},
#   ]

# 递归浏览（带深度限制）
tree = client.context.tree("viking://user/", max_depth=2)
# → 返回完整的目录树结构

# ===== 搜索 =====

# 语义搜索
results = client.context.search("用户喜欢什么运动")
# → [{path, score, l0_summary}, ...]

# 带范围限制的搜索
results = client.context.search(
    query="运动",
    scope="viking://user/health/",
    target_layer="l1",
    top_k=3,
)

# ===== 结构管理 =====

# 创建目录
client.context.mkdir("viking://user/hobbies/photography")

# 移动
client.context.move("viking://user/notes/cam", "viking://user/hobbies/photography/cam")

# 删除
client.context.delete("viking://user/temp/scratch")

# 归档
client.context.archive("viking://user/work/projects/old")
```

### CLI 工具

```bash
# 初始化
openviking init

# 写入上下文
openviking write viking://user/preferences/food "用户喜欢吃川菜..."

# 从文件写入
openviking write viking://resources/docs/api ./api-docs.md

# 浏览目录
openviking explore viking://user/

# 搜索
openviking search "饮食偏好"

# 启动 Web Studio
openviking studio
# → http://localhost:3333
```

---

## Rust 引擎详解

### 数据库 Schema

```sql
-- context_nodes 表: 文件系统结构
CREATE TABLE context_nodes (
    id          UUID PRIMARY KEY,
    parent_id   UUID REFERENCES context_nodes(id),  -- 父节点（目录）
    name        VARCHAR(255) NOT NULL,               -- 节点名称
    node_type   VARCHAR(20) NOT NULL,                -- "directory" | "document"
    namespace   VARCHAR(100) NOT NULL,                -- 命名空间
    full_path   TEXT NOT NULL UNIQUE,                 -- viking:// 完整路径
    created_at  TIMESTAMP,
    updated_at  TIMESTAMP,
    content_hash VARCHAR(64),                         -- 内容哈希（去重）
);

-- context_layers 表: L0/L1/L2 三层内容
CREATE TABLE context_layers (
    node_id     UUID REFERENCES context_nodes(id),
    layer       VARCHAR(2) NOT NULL,                  -- "l0" | "l1" | "l2"
    content     TEXT NOT NULL,                         -- 该层的内容文本
    token_count INTEGER,                               -- 预估 token 数
    PRIMARY KEY (node_id, layer),
);

-- blobs 表: 大文件存储
CREATE TABLE blobs (
    id          UUID PRIMARY KEY,
    node_id     UUID REFERENCES context_nodes(id),
    data        BYTEA,                                 -- 二进制数据
    mime_type   VARCHAR(100),
    size        BIGINT,
    created_at  TIMESTAMP,
);

-- vectors 表: 向量存储（用于语义搜索）
CREATE TABLE vectors (
    id          UUID PRIMARY KEY,
    node_id     UUID REFERENCES context_nodes(id),
    embedding   VECTOR(1536),                          -- 嵌入向量
    source_layer VARCHAR(2),                           -- 基于哪一层生成的
    created_at  TIMESTAMP,
);
```

### 核心 Rust 模块

```rust
// src/core/context_db.rs — 上下文数据库操作

pub struct ContextDb {
    pool: DatabaseConnection,  // SeaORM 连接池
}

impl ContextDb {
    /// 写入上下文节点（自动生成 L0/L1）
    pub async fn write_context(
        &self,
        path: &str,
        content: &str,
        llm_service: &LlmService,
        embedding_service: &EmbeddingService,
    ) -> Result<ContextNode> {
        // 1. 解析路径，确保目录结构存在
        let node = self.ensure_path(path).await?;

        // 2. 存储 L2（完整内容）
        self.save_layer(node.id, "l2", content).await?;

        // 3. 调用 LLM 生成 L1（概览）
        let l1 = llm_service.summarize(content, 500).await?;
        self.save_layer(node.id, "l1", &l1).await?;

        // 4. 调用 LLM 生成 L0（摘要）
        let l0 = llm_service.summarize(content, 100).await?;
        self.save_layer(node.id, "l0", &l0).await?;

        // 5. 生成向量（基于 L1）
        let embedding = embedding_service.embed(&l1).await?;
        self.save_vector(node.id, &embedding).await?;

        Ok(node)
    }

    /// 语义搜索
    pub async fn search(
        &self,
        query: &str,
        scope: Option<&str>,
        top_k: usize,
        embedding_service: &EmbeddingService,
    ) -> Result<Vec<SearchResult>> {
        let query_vec = embedding_service.embed(query).await?;
        let mut results = self.vector_search(&query_vec, top_k).await?;

        // 路径范围过滤
        if let Some(scope_path) = scope {
            results.retain(|r| r.path.starts_with(scope_path));
        }

        Ok(results)
    }
}
```

---

## 存储后端

| 后端 | 用途 | 说明 |
|------|------|------|
| **SQLite** | 默认 DB | 单文件，零配置，适合开发 |
| **PostgreSQL** | 生产 DB | 支持 pgvector 扩展 |
| **MySQL** | 生产 DB | 支持 InnoDB 向量 |
| **CockroachDB** | 分布式 DB | 水平扩展 |

向量存储直接复用数据库（pgvector / InnoDB Vector），不需要单独的向量数据库。

---

## 与 Mem0 的对比

### 架构对比

```
┌──────────────────────────────────────────────────────────────────┐
│                        Mem0 v3                                   │
│                                                                  │
│  对话 → [LLM 提取] → [批量嵌入] → [哈希去重] → [向量库]         │
│                                                                  │
│  搜索 → [嵌入] → [语义搜索] + [BM25] + [实体增强] → [融合排序]  │
│                                                                  │
│  存储: 扁平向量集合，所有记忆平等                                  │
│  组织: 无层次结构，仅通过 entity link 关联                        │
│  加载: 检索到的全部塞入上下文                                      │
├──────────────────────────────────────────────────────────────────┤
│                      OpenViking                                  │
│                                                                  │
│  数据 → [路径组织] → [L2存储] → [LLM生成L1/L0] → [向量化L1]     │
│                                                                  │
│  搜索 → [路径导航] + [语义搜索] → [按需加载L0/L1/L2]            │
│                                                                  │
│  存储: 文件系统层次结构 + 三层粒度                                │
│  组织: viking:// 协议，目录树结构                                  │
│  加载: 按需逐层，只加载必要详细程度                                │
└──────────────────────────────────────────────────────────────────┘
```

### 详细对比表

| 维度 | Mem0 v3 | OpenViking |
|------|---------|------------|
| **核心比喻** | 记忆提取器 + 搜索引擎 | Agent 的文件系统 |
| **数据组织** | 扁平向量集合 | 目录树 + viking:// 协议 |
| **粒度控制** | 无（全量返回） | L0/L1/L2 三层按需 |
| **Token 效率** | 一般 | 极高（号称节省 95%） |
| **写入成本** | 1 次 LLM 调用 | 2 次 LLM 调用（生成 L0+L1） |
| **检索方式** | 语义 + BM25 + 实体 | 路径导航 + 语义 |
| **关系表达** | 隐式（entity link） | 显式（目录层级） |
| **自管理** | 无 | Agent 可自主组织 |
| **实体提取** | spaCy NLP | 无专门的实体提取 |
| **BM25 支持** | ✅（Qdrant sparse） | ❌ |
| **混合搜索** | 三信号融合 | 路径 + 语义 |
| **时间推理** | 弱（仅时间戳） | 弱（仅时间戳） |
| **LLM 依赖** | 提取时 1 次 | 写入时 2 次（L0+L1） |
| **核心语言** | Python | Rust + Python |
| **向量库** | 外部（Qdrant 等 23 种） | 内置（pgvector 等） |
| **数据库** | SQLite (历史) | SQLite/PG/MySQL/CRDB |
| **GitHub Stars** | 55k | 14.6k |
| **生态成熟度** | 高 | 较新 |
| **Benchmark** | LoCoMo 91.6 | 无公开 Benchmark |
| **适合场景** | 对话记忆、用户画像 | Agent 全局上下文管理 |

### 各自的优劣势

```
Mem0 优于 OpenViking 的地方:
  ✅ 更简单: pip install mem0ai → 10 行代码搞定
  ✅ 混合搜索: BM25 + 语义 + 实体增强，三路融合
  ✅ 实体提取: spaCy NLP 自动提取实体
  ✅ 生态更广: 55k stars，23 种向量库，16 种 LLM
  ✅ 有 Benchmark: LoCoMo 91.6, LongMemEval 93.4

OpenViking 优于 Mem0 的地方:
  ✅ Token 效率: L0/L1/L2 按需加载，节省 95% token
  ✅ 结构化组织: 目录树 + viking:// 协议，记忆有层次
  ✅ Agent 自管理: Agent 可以像整理文件夹一样管理记忆
  ✅ 通用性: 不只存对话记忆，还存技能、资源、文档
  ✅ Rust 引擎: 高性能，低延迟
  ✅ 多后端: SQLite/PG/MySQL/CockroachDB
```

### 选型建议

```
选 Mem0 如果你:
  - 需要快速给聊天机器人加记忆
  - 主要处理对话数据
  - 需要 BM25 混合搜索
  - 不想折腾基础设施
  - 需要成熟的社区支持

选 OpenViking 如果你:
  - Agent 需要管理大量不同类型的上下文
  - 对话很长，token 成本是关键瓶颈
  - 需要 Agent 自主组织记忆结构
  - 除了对话还要存文档、技能、资源
  - 需要结构化浏览（不只是搜索）
  - 追求高性能（Rust 引擎）

组合使用:
  - 用 Mem0 做对话记忆提取（自动提取事实）
  - 用 OpenViking 做全局上下文管理（组织+按需加载）
  - Mem0 提取的事实 → 写入 OpenViking 的特定路径
  - 检索时先在 OpenViking 中浏览结构 → 按需深入
```

---

## 部署方式

### 方式 1: Docker（推荐）

```bash
# 启动（默认 SQLite + 本地文件存储）
docker compose up -d

# 访问
# API: http://localhost:8300
# Web Studio: http://localhost:3333
```

### 方式 2: Python pip

```bash
pip install openviking
openviking init
openviking serve  # 启动 HTTP 服务器
```

### 方式 3: 嵌入模式（Rust）

```rust
// 直接在 Rust 应用中嵌入，无需 HTTP 服务器
use openviking::ContextDb;

let db = ContextDb::new_sqlite("openviking.db").await?;
db.write_context("viking://user/test", "Hello world", &llm, &embed).await?;
```

### 生产部署

```yaml
# docker-compose.yml 生产配置
services:
  openviking:
    image: volcengine/openviking:latest
    environment:
      DATABASE_URL: postgres://user:pass@pg:5432/openviking
      EMBEDDING_PROVIDER: openai
      EMBEDDING_API_KEY: ${OPENAI_API_KEY}
      LLM_PROVIDER: openai
      LLM_API_KEY: ${OPENAI_API_KEY}
    ports:
      - "8300:8300"

  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: openviking

  web-studio:
    image: volcengine/openviking-studio:latest
    ports:
      - "3333:3333"
```

---

## 总结

### OpenViking 的核心价值

```
1. 解决"上下文爆炸"
   传统方案: 对话越长 → 检索出的记忆越多 → token 消耗越大
   OpenViking: L0 摘要始终很小 → 只在需要时深入 → 节省 95%

2. 让记忆有结构
   传统方案: 所有记忆平铺在一个集合里
   OpenViking: 目录树 + viking:// 协议 → Agent 像浏览文件一样浏览记忆

3. Agent 自主管理
   传统方案: 记忆由后台系统自动管理，Agent 不感知
   OpenViking: Agent 可以 mkdir/mv/merge/archive → 自己整理记忆

4. 通用上下文管理
   传统方案: 只存对话中提取的事实
   OpenViking: 存对话、文档、技能、资源 → 一切皆可 viking://
```

### 项目成熟度评估

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构设计 | ⭐⭐⭐⭐⭐ | L0/L1/L2 + viking:// 非常有创意 |
| 代码质量 | ⭐⭐⭐⭐ | Rust 核心引擎质量高，36k 行 |
| 文档 | ⭐⭐⭐⭐ | 中英文文档齐全 |
| 生态 | ⭐⭐⭐ | 14.6k stars，还在快速增长 |
| 生产验证 | ⭐⭐⭐ | 字节内部使用，外部案例较少 |
| Benchmark | ⭐⭐ | 没有公开的 LoCoMo/BEAM 成绩 |
| 易用性 | ⭐⭐⭐ | 比 Mem0 复杂，学习曲线更陡 |

### 与 Mem0 的本质区别（一句话）

```
Mem0: "帮你记住对话中说过的事"
OpenViking: "给你的 Agent 一个文件系统来管理所有上下文"
```
