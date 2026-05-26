# CHANGELOG — River 记忆系统

## v0.5.0 (2026-05-26) — 记忆v3 设计对齐

基于 `记忆v3.txt` 设计文档，完成以下 v3 机制实现：

### 数据模型扩展 (12 新字段)

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `salience` | int | 5 | 综合显著性 1-10，max(三维)×0.7+mean(三维)×0.3 |
| `volatility` | str | "medium" | 情感易逝性 low/medium/high |
| `stability` | float | 2.3 | FSRS 稳定性：检索成功率降到 90% 所需天数 |
| `difficulty` | float | 5.0 | FSRS 难度 1-10，D_init = 10 - salience |
| `last_accessed` | str | "" | 最后被检索命中的时间戳 |
| `access_count` | int | 0 | 累计被命中次数 |
| `version` | int | 0 | Pending 阶段版本号（竞态校验预留） |
| `provenance_round_hash` | str | "" | 固化时所属对话轮次的哈希 |
| `provenance_context` | str | "" | 入库时的上下文摘要 |
| `reinterpretation` | str? | null | 事后重新诠释（解读重写） |
| `correction_history` | list? | null | 事实更正记录 [{old, new, corrected_at}] |
| `related_streams` + `link_strength` | list | [] | 跨流因果关联 + 对应强度 |

### Memory 新增方法

| 方法 | 说明 |
|------|------|
| `retrievability(current_date)` | FSRS 检索可用性 R = (1 + elapsed/S)^(-0.1542) |
| `retrieval_strength` (property) | R + 惊喜加分（钟形曲线，R≈0.5 最优） |
| `reinforce(current_date)` | 检索后强化：FSRS 更新 S_new + access_count |

### EventStream 新增

| 属性/方法 | 说明 |
|-----------|------|
| `status` | "open" / "closed"，事件流是否已终结 |
| `peak_end_sample(k=2)` | 峰终采样：起点+终点+status_update节点+salience top-K |

### 模块改动

| 模块 | 改动 | 文件 |
|------|------|------|
| **三因子评分** | 固定权重 → 语义相关性 + salience/10 + retrieval_strength；过度拉取 top_k×5；多坐标命中（≥2根）置顶 | `core/associative.py` |
| **FSRS 检索强化** | recall() 命中后调用 `reinforce()` + `store.reinforce_memory()` 更新 stability | `main.py` `core/store.py` `core/memory.py` |
| **跨流因果激活** | `_propagate_related()` BFS 按 link_strength 衰减；salience≥8 激活 ×1.5；activation<0.2 剪枝；DFS≤3 | `main.py` |
| **记忆工厂 v3** | LLM prompt 新增 salience 三维评分 / volatility / related_streams / reinterpretation / correction；`_fsrs_init()` 初始化 stability/difficulty | `core/ingestor.py` |
| **动态融流** | `merge_orphan_streams()` 三道安检：status_update→豁免、被引用→豁免、salience 前 30%→豁免 | `core/store.py` |
| **反思模块 D** | `ReflectionEngine`：salience 累积达 150 触发 → LLM 生成 3 问 × 5 洞察 → 入库 reflection 记忆 | `core/reflect.py` (新) |
| **数据库** | SCHEMA_VERSION=4，v4 migration 自动添加 13 列；`_safe_json()` 保护解析 | `core/store.py` |

### v0.3 检测与修复

| 问题 | 状态 |
|------|------|
| `retrievability` @property 带参数 current_date 且突变 self.stability | 已修复：改为普通方法，局部变量 max(s, 0.1) |
| `retrieval_strength` 调用错误的属性引用 | 已修复：`self.retrievability` → `self.retrievability()` |
| `correction_history` / `objects` json.loads 无保护 | 已修复：增加 `_safe_json()` |
| test_A 在 CHAT 视图下 pending 误报 | 已修复：按 intent 分支断言 |

### 测试覆盖 (11/11)

**正例 (4)**
1. 欠债提醒 — 物理坐标多命中置顶
2. 状态查询 — STATUS → current_state() 过滤
3. 过程查询 — PROCESS → peak_end_sample() 峰终采样
4. 混合视图 — CHAT 语义命中 + 最新状态

**反例 (7)**
5. 已还清债务 STATUS 视图不浮现 pending
6. 已取消承诺整条流标 INVALID
7. 梦境被 current_state() 过滤
8. A/B 债务对象探针隔离
9. 审查队列正常流转
10. 生命周期 pending→resolved
11. 误归流 merge_to_stream 修正

### 跳过的 v3 功能

- **Pending Zone** — 滑动窗口缓冲区 + 竞态校验 (大工程，预留给 v4)
- **持有状态插件** — 与记忆系统解耦的独立模块
- **表格数据源整合** — 外部表格到索引的映射

---

## v0.3.0 (2026-05-26)

### 新增数据结构

| 字段 | 说明 |
|------|------|
| `occurred_at` / `due_at` / `trigger_at` | 事件日期 / 承诺到期 / 触发提醒 |
| `valid_from` / `valid_to` | 记忆有效期 |
| `lifecycle` | pending/active/resolved/superseded/invalid/dream |
| `confidence` | 入库置信度 (0-1) |
| `source_event_id` / `supersedes` | 来源追溯 / 替代关系 |

### 新增机制

- **生命周期治理**: `resolve_stream()` / `cancel_promise()` / `mark_as_dream()` / `merge_to_stream()`
- **审查队列**: `review_queue` 表 + 自动入队 (confidence < 0.5)
- **状态投影**: `EventStream.current_state()` 过滤无效/梦境/废弃
- **日期计分纠偏**: 仅 pending 享过期加分
- **意图路由增强**: "怎么/怎样" + 状态词 → STATUS
- **数据库迁移**: SCHEMA_VERSION=3 自动兼容

### 新增文件

`core/time_parser.py`, `core/ingestor.py`, `core/conversation.py`, `core/logger.py`, `test_counterfactual.py`, `demo.py`, `scripts/import_history.py`, `astrbot_plugin/`, `config/characters/rem.json`, `.env.example`

---

## v0.1.0 → v0.2.0

- 初始版本：Memory + EventStream 模型 + 3 模块（意图路由/关联索引/事件流索引）
- ChromaDB + SQLite 存储 / TF-IDF embedding / 4 场景测试

---

## v0.2.0 → v0.3.0

从"演示壳"升级为"有研究价值的半成品"：数据模型厚度、状态投影正确性、审查闭环、反例测试。
