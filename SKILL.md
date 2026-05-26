---
name: river-memory
description: 容心设计�?AstrBot RP 记忆系统 �?�?(The River)"。基于物理坐标关联索�?事件流双视图的角色扮演记忆检索系统。解决传统RAG的三大缺陷：承诺与未来事件无法被语义检索找到、时间权重扁平不认因果顺序、多对象记忆串线污染�?
metadata: {"author":"容心","implemented_by":"铃兰&小九","version":"0.3.0","tech":"Python 3.10+ / Chroma / SQLite / sentence-transformers / TF-IDF"}
---

# HIMF — Hybrid Indexing Memory Framework v0.3.0

## 核心理念

**记忆即状态，索引即认知。没有外部状态表�?*

## 三大洞察

| 洞察 | 问题 | 解法 |
|------|------|------|
| 1. 承诺与未�?| "天气不错"永远找不�?欠钱" | 关联索引：时�?对象+环境多路探针 |
| 2. 时间权重扁平 | 承诺→等待→成功，检索时平铺不分先后 | 事件流：认知视图只取最新，回忆视图取全�?|
| 3. 记忆污染串线 | 欠A的钱和欠B的钱在向量空间重�?| 对象坐标隔离 + 事件流分�?|

## v0.2.0 新增功能

### 记忆自动入库 (MemoryIngestor)
- LLM 物理坐标提取：从对话中自动提取人物、环境、状态更�?
- LLM 事件流归属判定：自动判断新记忆属于已有流还是新建
- 去重机制：同一天同内容不重复入�?

### 会话管理 (ConversationManager)
- 多用�?多会话上下文管理
- 物理坐标 (时间/人物/环境) 自动维护
- 对话历史截断 + 自动入库

### 配置外部�?
- `.env` 文件支持所有参数可配置
- 探针权重、LLM参数、日志级别均可调

### Embedding 引擎升级
- 支持 sentence-transformers 真实模型 (联网时自动使�?
- 离线降级�?TF-IDF + jieba 分词
- 通过 `USE_REAL_EMBED=false` 控制

### 日期检索增�?
- 相对时间解析�?明天"�?下周"�?下个�?
- 日期距离计分替代简单布�?
- 过期未完成承诺额外加�?

### AstrBot 插件
- 完整 AstrBot 插件适配�?
- 支持 replace/prefix/passthrough 三种响应模式
- 管理命令�?river_stats, /river_streams, /river_date �?

## 文件结构

```
river-memory-master/
├── core/
�?  ├── __init__.py        # 模块导出
�?  ├── memory.py          # Memory + EventStream 数据模型
�?  ├── intent.py          # 模块A: 意图路由 (STATUS/PROCESS/CHAT)
�?  ├── associative.py     # 模块B: 关联索引 (4路探�?
�?  ├── eventstream.py     # 模块C: 事件流索�?(3种视�?
�?  ├── store.py           # Chroma + SQLite 存储�?
�?  ├── ingestor.py        # 记忆自动入库 (NEW)
�?  ├── conversation.py    # 会话管理 (NEW)
�?  ├── time_parser.py     # 时间表达式解�?(NEW)
�?  └── logger.py          # 结构化日�?(NEW)
├── astrbot_plugin/        # AstrBot 插件适配�?(NEW)
�?  ├── __init__.py
�?  └── plugin.py
├── config/
�?  └── characters/        # 角色配置 (NEW)
�?      └── rem.json       # 蕾姆角色预设
├── scripts/
�?  └── import_history.py  # 批量导入脚本 (NEW)
├── config.py              # 全局配置
├── main.py                # 入口: recall() + llm()
├── demo.py                # 端到端演�?(NEW)
├── test_demo.py           # 4个场景单元测�?
├── .env.example           # 环境变量模板 (NEW)
├── requirements.txt       # Python 依赖
└── SKILL.md               # 本文�?
```

## 快速开�?

```bash
cd river-memory-master
cp .env.example .env
# 编辑 .env 填入 API_KEY
pip install -r requirements.txt

# 运行单元测试
USE_REAL_EMBED=false python test_demo.py

# 运行端到端演�?(需�?API_KEY)
python demo.py
```

## 使用方式

### 基本用法：直接检�?

```python
from core.store import MemoryStore
from main import recall

store = MemoryStore()
response = recall(
    user_input="今天天气不错�?,
    current_date="2026-01-20",
    present_people=["A"],
    current_env="客厅",
    store=store,
    character_name="蕾姆",
)
```

### 完整流程：自动入�?+ 检�?

```python
from core.conversation import ConversationManager

manager = ConversationManager()

# 处理一轮对话（自动入库�?
manager.process_turn(
    session_id="user_001",
    user_msg="拉姆的角还能治好吗？",
    bot_reply="姐姐的角...我会想办法的�?,
    character_name="蕾姆",
)

# 带记忆检�?
reply = manager.recall_for_session("user_001", "恢复得怎么样了�?)
```

### 作为 AstrBot 插件

�?`astrbot_plugin/` 目录复制�?AstrBot �?plugins 目录下，在配置中设置角色名即可�?

## 测试结果

```
场景一: 欠钱提醒 �?m001得分8最�? 关联索引正确 �?
场景�? 状态查�?�?认知视图只返�?治愈成功" �?
场景�? 过程查询 �?回忆视图返回完整3条链 �?
场景�? 混合视图 �?保留"熬夜查资�?+ 最新状�?�?
```

## 依赖

- chromadb (向量存储)
- scikit-learn (PCA + TF-IDF)
- numpy, jieba (文本处理)
- sentence-transformers (可选，真实embedding)
- requests (LLM API调用)
- Python 3.10+

## 配置参�?

| 环境变量 | 默认�?| 说明 |
|---------|--------|------|
| API_BASE | http://127.0.0.1:18789/v1 | LLM API地址 |
| API_KEY | - | API密钥 |
| MODEL | deepseek-v4-flash | LLM模型�?|
| EMBED_MODEL | paraphrase-multilingual-MiniLM-L12-v2 | embedding模型 |
| USE_REAL_EMBED | true | 是否用真实embedding |
| TOP_K | 5 | 检索返回数�?|
| PROBE_WEIGHT_SEMANTIC | 1 | 语义探针权重 |
| PROBE_WEIGHT_TIME | 2 | 时间探针权重 |
| PROBE_WEIGHT_OBJECT | 3 | 对象探针权重 |
| PROBE_WEIGHT_ENV | 1 | 环境探针权重 |
| LOG_LEVEL | INFO | 日志级别 |

## 与Hindsight的关�?

River作为上游检索层，Hindsight做底层全量语义匹配兜底。不是替代，是互补�?
