# CHANGELOG — River 记忆系统

## v0.3.0 (2026-05-26)

### 新增数据结构

| 字段 | 类型 | 说明 |
|------|------|------|
| `occurred_at` | str | 事件实际发生日期 |
| `due_at` | str? | 承诺到期日 |
| `trigger_at` | str? | 触发提醒日期 |
| `valid_from` | str | 记忆生效起始日 |
| `valid_to` | str? | 记忆失效日 |
| `lifecycle` | str | pending/active/resolved/superseded/invalid/dream |
| `confidence` | float | 入库置信度 (0-1) |
| `source_event_id` | str? | 来源事件流 |
| `supersedes` | str? | 替代了哪条旧记忆 |

### 新增机制

**生命周期治理** (`core/ingestor.py`)
- `resolve_stream(stream_id)` — 标记事件流为已完成 (resolved)
- `cancel_promise(memory_id)` — 取消承诺，整条流标为 invalid
- `mark_as_dream(memory_id)` — 标记记忆为梦境
- `merge_to_stream(memory_id, target)` — 误归流修正（回滚+重归）

**审查队列** (`core/store.py`)
- `review_queue` 表 — 低置信度记忆自动排队待审
- `add_review()` / `get_pending_reviews()` / `resolve_review()`
- 入库时 confidence < 0.5 自动入队

**状态投影** (`core/memory.py`, `core/eventstream.py`)
- `EventStream.current_state()` — 过滤 INVALID/DREAM/SUPERSEDED，只取有效最新
- STATUS 查询不再简单取 latest()，而是走 current_state()

**日期计分纠偏** (`core/time_parser.py`, `core/associative.py`)
- `date_score()` 新增 `lifecycle` 参数
- 只有 `lifecycle=pending` 的过期承诺享 +3 加分
- `lifecycle=resolved` 的旧记忆不再因过期而持续浮现

**意图路由增强** (`core/intent.py`)
- "怎么/怎样" + 状态词（恢复/情况/好/行） → STATUS
- "怎么/怎样" + 动作词（治/做/解决/过来） → PROCESS

**数据库迁移** (`core/store.py`)
- SCHEMA_VERSION=3 自动迁移
- `_row_to_memory()` 兼容旧表缺失列
- timestamp / event_stream_id 复合索引

### 新增文件

| 文件 | 说明 |
|------|------|
| `core/time_parser.py` | 中文相对/绝对时间解析 + 日期距离计分 |
| `core/ingestor.py` | LLM 物理坐标提取 + 事件流归属 + 入库 |
| `core/conversation.py` | 多会话管理 + 自动入库 |
| `core/logger.py` | 结构化日志（文件+控制台） |
| `test_counterfactual.py` | 7个反例端到端测试 |
| `demo.py` | 完整演示（含 lifecycle + 审查队列） |
| `scripts/import_history.py` | JSON/CSV 批量导入历史对话 |
| `astrbot_plugin/` | AstrBot 插件适配器 |
| `config/characters/rem.json` | 蕾姆角色预设 |
| `.env.example` | 环境变量模板 |

### 测试覆盖 (11/11 通过)

**正例 (4)**
1. 欠债提醒 — 时间+对象探针命中 pending 承诺
2. 状态查询 — STATUS 视图只取 resolved 治愈成功
3. 过程查询 — PROCESS 视图返回完整事件链
4. 混合视图 — CHAT 语义命中 + 最新状态背景

**反例 (7)**
5. 已还清债务不浮现
6. 已取消承诺不误触发
7. 梦境记忆不污染状态投影
8. A/B 债务隔离不串线
9. 低置信度记忆入审查队列
10. 生命周期 pending→resolved 流转
11. 误归流回滚再修正

### 已知缺口

- 审查队列目前只记录不自动处理，需人工或定时任务 review
- 误归流修正后不会自动重算已受影响的记忆
- 无环境锁定文件 (lock file)
- 大规模 (1000+ 条) 性能尚未测

---

## v0.1.0 → v0.2.0

- 初始版本：Memory + EventStream 模型 + 3模块（意图路由/关联索引/事件流索引）
- ChromaDB + SQLite 存储
- TF-IDF 模拟 embedding
- 4 场景单元测试

---

## v0.2.0 → v0.3.0

参见上方 v0.3.0 完整记录。核心变更：从"演示壳"升级为"有研究价值的半成品"，补充了数据模型厚度、状态投影正确性、审查闭环和反例测试。
