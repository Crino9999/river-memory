"""River Memory MVP v0.3 — 端到端演示（含生命周期 + 审查队列）

演示完整流程：
1. 初始化 ConversationManager + 多轮对话自动入库
2. 事件流归属 + 物理坐标提取
3. 生命周期管理：承诺标记 → 完成 → 取消
4. 审查队列：低置信度记忆自动排队
5. 反例验证：已还钱不再误召回、梦境不污染状态
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(__file__))

from core.store import MemoryStore
from core.conversation import ConversationManager
from core.associative import set_alias_map
from core.memory import PENDING, ACTIVE, RESOLVED, INVALID, DREAM, SUPERSEDED
from main import recall
from core.logger import get_logger

log = get_logger("demo")


def load_character(path: str = "config/characters/rem.json"):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "aliases_map" in data:
            set_alias_map(data["aliases_map"])
        return data
    except FileNotFoundError:
        return {"name": "蕾姆"}


def demo():
    print("=" * 60)
    print("River 记忆系统 v0.3 — 完整演示")
    print("=" * 60)

    char = load_character()
    print(f"\n[角色] {char.get('display_name', char['name'])}")

    store = MemoryStore()
    store.clear()
    manager = ConversationManager(store)
    sid = "demo_user"

    # ============ Phase 1: 手动构建种子记忆（含 lifecycle） ============
    print("\n" + "-" * 40)
    print("Phase 1: 种子记忆（模拟 LLM 入库结果）")
    print("-" * 40)

    from core.memory import Memory
    seeds = [
        Memory("m001", "我答应在1.20还A 100块。", timestamp="2026-01-01",
               event_stream_id="evt_debt_A", objects=["A"], environment="厨房",
               status_update="欠A 100块，1.20到期",
               due_at="2026-01-20", lifecycle=PENDING, confidence=0.85),
        Memory("m002", "他说要治好姐姐的角...这能信吗？", timestamp="2026-01-01",
               event_stream_id="evt_ram_horn", objects=["拉姆", "用户"], environment="治疗室",
               lifecycle=ACTIVE),
        Memory("m003", "还要再等几天...焦躁。先生熬夜查资料，眼睛都红了，好累。", timestamp="2026-01-02",
               event_stream_id="evt_ram_horn", objects=["拉姆", "用户"], environment="治疗室",
               lifecycle=ACTIVE),
        Memory("m004", "成功了！姐姐的角，好了。", timestamp="2026-01-05",
               event_stream_id="evt_ram_horn", objects=["拉姆", "用户"], environment="治疗室",
               status_update="拉姆的角=已治愈", lifecycle=RESOLVED),
        # 低置信度模拟：欠B的钱，但LLM归流时不太确定
        Memory("m005", "欠了B 50块，下个月还。", timestamp="2026-01-08",
               event_stream_id="evt_debt_B", objects=["B"], environment="客厅",
               status_update="欠B 50块", due_at="2026-02-08",
               lifecycle=PENDING, confidence=0.42),
    ]
    for m in seeds:
        store.add(m)

    total = len(store.list_all())
    print(f"  已写入 {total} 条记忆")

    # ============ Phase 2: 事件流一览 ============
    print("\n" + "-" * 40)
    print("Phase 2: 事件流与生命周期")
    print("-" * 40)

    streams = {}
    for m in store.list_all():
        sid_key = m.event_stream_id or "_unaffiliated"
        streams.setdefault(sid_key, []).append(m)

    for eid, mems in sorted(streams.items()):
        latest_lc = mems[-1].lifecycle if mems else "?"
        print(f"  {eid} ({len(mems)}条) lifecycle={latest_lc}")
        for m in sorted(mems, key=lambda x: x.timestamp):
            due = f" due={m.due_at}" if m.due_at else ""
            print(f"    [{m.timestamp}] [{m.lifecycle}] {m.content[:50]}...{due}")

    # ============ Phase 3: 检索验证 ============
    print("\n" + "-" * 40)
    print("Phase 3: 检索验证（recall 端到端）")
    print("-" * 40)

    tests = [
        # 正例：欠债到期被时间+对象探针命中
        ("欠债提醒", "今天天气不错。", "2026-01-20", ["A"], "客厅", "m001"),
        # 正例：状态查询只取有效 RESOLVED，不取 INVALID/DREAM
        ("状态查询", "我看看恢复得如何。", "2026-01-10", ["拉姆"], "治疗室", "m004"),
        # 反例：欠B的钱不会在A面前冒出来
        ("债务隔离", "今天天气不错。", "2026-01-20", ["A"], "客厅", None),
    ]

    for name, inp, date, people, env, expect in tests:
        result = recall(inp, date, people, env, store, char["name"])
        mem_ids = [m.memory_id for m in result["memory_context"]]
        hit_ids = [h.memory.memory_id for h in result["hits"]]
        print(f"\n  [{name}] '{inp}' 意图={result['intent']}")
        print(f"  探针命中: {hit_ids}")
        print(f"  上下文记忆: {mem_ids}")

        if name == "债务隔离":
            # m005 (欠B) 不应该出现在A面前的 context 中
            if "m005" in hit_ids:
                print("  !! 债务串线！欠B的记忆在A面前被召回")
            else:
                print("  OK 正确：欠B的记忆未串线到A的对话")

    # ============ Phase 4: 生命周期操作 ============
    print("\n" + "-" * 40)
    print("Phase 4: 生命周期治理")
    print("-" * 40)

    # 4a: 完成一个承诺
    manager.ingestor.resolve_stream("evt_debt_A", "resolved", "欠A的100块已还清")
    print("  4a: evt_debt_A → resolved (已还清)")

    # 4b: 取消一个承诺
    manager.ingestor.cancel_promise("m005", "B说不用还了")
    print("  4b: m005 → invalid (B说不用还了)")

    # 验证：还清后不应再被"欠钱"相关查询高优先级命中
    result = recall("最近手头有点紧", "2026-01-25", ["A"], "客厅", store)
    mem_ids = [m.memory_id for m in result["memory_context"]]
    print(f"  4a-verify: '手头紧' → 上下文记忆: {mem_ids}")
    pending_in_context = any(m.lifecycle == PENDING for m in result["memory_context"])
    print(f"  {'OK 已还清的债务不再浮现' if not pending_in_context else '!! 已还清的记忆仍在召回中'}")

    # ============ Phase 5: 梦境隔离 ============
    print("\n" + "-" * 40)
    print("Phase 5: 梦境隔离（反例）")
    print("-" * 40)

    dream_mem = Memory(
        "m_dream", "梦里先生治好了姐姐的角，但醒来角还是坏的...",
        timestamp="2026-01-03", event_stream_id="evt_ram_horn",
        objects=["拉姆", "用户"], environment="卧室",
        lifecycle=DREAM,
    )
    store.add(dream_mem)

    result = recall("角恢复得怎么样", "2026-01-10", ["拉姆"], "治疗室", store)
    mem_ids = [m.memory_id for m in result["memory_context"]]
    print(f"  梦境记忆 m_dream (lifecycle={DREAM})")
    print(f"  状态查询上下文: {mem_ids}")
    if "m_dream" in mem_ids:
        print("  !! 梦境记忆污染了状态查询!")
    else:
        print("  OK 正确：梦境被 current_state() 过滤，不污染状态投影")

    # ============ Phase 6: 审查队列 ============
    print("\n" + "-" * 40)
    print("Phase 6: 审查队列")
    print("-" * 40)

    reviews = store.get_pending_reviews()
    if reviews:
        for r in reviews:
            print(f"  审查项 #{r['id']}: {r['content'][:50]} (conf={r['confidence']})")
    else:
        print("  无待审查项（m005 已通过 cancel_promise 处理）")
        # 手动加一条低置信度的到队列
        store.add_review("m005", "欠了B 50块", 0.42, "low confidence stream affiliation")
        reviews = store.get_pending_reviews()
        for r in reviews:
            print(f"  手动添加审查项 #{r['id']}: {r['content'][:50]} (conf={r['confidence']})")
        # 模拟审查通过
        if reviews:
            store.resolve_review(reviews[0]["id"], "approved")
            print(f"  已批准审查项 #{reviews[0]['id']}")

    # ============ Phase 7: 统计 ============
    print("\n" + "-" * 40)
    print("Phase 7: 统计")
    print("-" * 40)

    total = len(store.list_all())
    by_lc = {}
    for m in store.list_all():
        by_lc[m.lifecycle] = by_lc.get(m.lifecycle, 0) + 1

    print(f"  总记忆数: {total}")
    for lc, count in sorted(by_lc.items()):
        print(f"    {lc}: {count}")
    print(f"  审查队列剩余: {len(store.get_pending_reviews())}")

    print("\n" + "=" * 60)
    print("v0.3 演示完成!")
    print("=" * 60)


if __name__ == "__main__":
    demo()
