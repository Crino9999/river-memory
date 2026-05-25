---
name: river-memory
description: 容心设计的"AstrBot RP 记忆系统 — 河 (The River)"。基于物理坐标关联索引+事件流双视图的角色扮演记忆检索系统。解决传统RAG的三大缺陷：承诺与未来事件无法被语义检索找到、时间权重扁平不认因果顺序、多对象记忆串线污染。
metadata: {"author":"容心","implemented_by":"铃兰&小九","version":"0.1.0","tech":"Python 3.10+ / Chroma / SQLite / TF-IDF / sklearn PCA"}
---

# River 记忆系统 — 河 (The River)

## 核心理念

**记忆即状态，索引即认知。没有外部状态表。**

传统RAG把记忆当"文件"检索——用户说"今天天气不错"，系统在向量库里找语义相似的记忆，永远找不到"1月20号还A 100块"这条欠债。因为"天气"和"欠钱"在向量空间里是两个星系的距离。

River的解法：给每条记忆打上物理坐标（时间/对象/环境），让它们成为和语义向量平权的索引键。同一事件链的记忆串成"事件流"，日常查询只取最新状态（认知视图），追问过程才展开全部（回忆视图），闲聊时混合展现（语义联想+状态背景）。

## 三大洞察

| 洞察 | 问题 | 解法 |
|------|------|------|
| 1. 承诺与未来 | "天气不错"永远找不到"欠钱" | 关联索引：时间+对象+环境多路探针 |
| 2. 时间权重扁平 | 承诺→等待→成功，检索时平铺不分先后 | 事件流：认知视图只取最新，回忆视图取全部 |
| 3. 记忆污染串线 | 欠A的钱和欠B的钱在向量空间重叠 | 对象坐标隔离 + 事件流分组 |

## 架构

```
用户输入
  │
  ├─→ 模块A: 意图路由 (STATUS / PROCESS / CHAT)
  │     └─ PCA降维 + 样本中心点距离 + 关键词兜底
  │
  ├─→ 模块B: 关联索引 (多路并行探针)
  │     ├─ 语义探针: embedding相似度 (权重+1)
  │     ├─ 时间探针: timestamp匹配 (权重+2)
  │     ├─ 对象探针: objects包含当前人物 (权重+3)
  │     └─ 环境探针: environment匹配 (权重+1)
  │     └─→ 多坐标交集命中者优先级最高
  │
  └─→ 模块C: 事件流索引 (视图选择)
        ├─ STATUS → 认知视图 (只取最新节点)
        ├─ PROCESS → 回忆视图 (完整事件链)
        └─ CHAT → 混合视图 (语义命中保留 + 最新状态背景)
```

## 使用方式

```python
from main import recall

# 每次对话前调用，返回带记忆上下文的LLM prompt
response = recall(
    user_input="今天天气不错。",
    current_date="2026-01-20",
    present_people=["A"],
    current_env="客厅",
    store=store,  # MemoryStore实例
)
```

## 文件结构

```
river-memory/
├── core/
│   ├── memory.py        # Memory + EventStream 数据模型
│   ├── intent.py        # 模块A: 意图路由
│   ├── associative.py   # 模块B: 关联索引
│   ├── eventstream.py   # 模块C: 事件流索引
│   └── store.py         # Chroma + SQLite 存储层
├── config.py            # API配置
├── main.py              # 入口: recall() 完整检索流程
├── test_demo.py         # 4个场景测试 (已验证全部通过)
└── SKILL.md             # 本文件
```

## 运行测试

```bash
cd river-memory
pip install chromadb scikit-learn
python test_demo.py
```

## 测试结果

```
场景一: 欠钱提醒 → m001得分6最高, 关联索引正确 ✓
场景二: 状态查询 → 认知视图只返回"治愈成功" ✓
场景三: 过程查询 → 回忆视图返回完整3条链 ✓
场景四: 混合视图 → 保留"熬夜查资料"+ 最新状态 ✓
```

## 依赖

- chromadb (向量存储)
- scikit-learn (PCA + TF-IDF)
- Python 3.10+
- 无需网络 (纯离线)

## 与Hindsight的关系

River作为上游检索层，Hindsight做底层全量语义匹配兜底。不是替代，是互补。
