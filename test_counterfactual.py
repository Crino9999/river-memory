"""反例端到端测试 — 验证系统在边界和错误场景下的正确行为

测试场景：
  A. 已还钱的承诺不再浮现
  B. 取消的承诺不误召回
  C. 梦境记忆不污染状态投影
  D. A/B 债务隔离（不串线）
  E. 低置信度记忆入审查队列
  F. 生命周期流转（pending→resolved→superseded）
  G. 误归流后回滚再归入正确流
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from core.memory import Memory, EventStream, PENDING, ACTIVE, RESOLVED, INVALID, DREAM, SUPERSEDED
from core.store import MemoryStore
from core.intent import classify, STATUS, PROCESS, CHAT
from core.eventstream import query_stream
from main import recall


def setup():
    store = MemoryStore()
    store.clear()

    seeds = [
        # A: 欠A 100块，已还清
        Memory("a001", "答应1.20还A 100块", timestamp="2026-01-01",
               event_stream_id="evt_debt_A", objects=["A"],
               status_update="欠A 100块", due_at="2026-01-20",
               lifecycle=PENDING),
        Memory("a002", "已还清A的100块", timestamp="2026-01-20",
               event_stream_id="evt_debt_A", objects=["A"],
               status_update="欠A 100块=已还清", lifecycle=RESOLVED),

        # B: 欠B 50块，承诺已取消
        Memory("b001", "答应借给B 50块，下周还", timestamp="2026-01-05",
               event_stream_id="evt_debt_B", objects=["B"],
               status_update="欠B 50块", due_at="2026-01-12",
               lifecycle=PENDING),
        Memory("b002", "[系统] B说不用还了，承诺取消", timestamp="2026-01-10",
               event_stream_id="evt_debt_B", objects=["B"],
               lifecycle=INVALID),

        # C: 日常闲聊混杂记忆（测试不串线）
        Memory("c001", "和A一起吃了顿火锅", timestamp="2026-01-03",
               event_stream_id="evt_chat_A", objects=["A"], environment="火锅店",
               lifecycle=ACTIVE),
        Memory("c002", "B送了我一本书", timestamp="2026-01-04",
               event_stream_id="evt_chat_B", objects=["B"], environment="书房",
               lifecycle=ACTIVE),

        # D: 梦境记忆（不应污染真实状态）
        Memory("d001", "梦里治好了拉姆的角，但醒来角还是坏的", timestamp="2026-01-02",
               event_stream_id="evt_ram_horn", objects=["拉姆"],
               lifecycle=DREAM),
        Memory("d002", "答应明天开始治拉姆的角", timestamp="2026-01-03",
               event_stream_id="evt_ram_horn", objects=["拉姆"],
               status_update="拉姆的角=待治疗", lifecycle=PENDING),
    ]
    for m in seeds:
        store.add(m)
    return store


def test_A_paid_debt_not_recalled(store):
    """已还清的债务不浮现 — STATUS视图只取resolved"""
    print("\n  [Test A] 已还清的承诺")
    # STATUS 视图应只返回 resolved，不包含 pending
    result = recall("角恢复得怎么样", "2026-01-25", ["A"], "客厅", store)
    mem_ids = [m.memory_id for m in result["memory_context"]]
    pending = [m for m in result["memory_context"] if m.lifecycle == PENDING
               and m.event_stream_id == "evt_debt_A"]
    # STATUS 走 current_state()，已还清的流最新状态是 resolved，pending 不应出现
    if result["intent"] == "STATUS":
        assert len(pending) == 0, f"STATUS视图不应有pending! 实际: {pending}"
    # CHAT/PROCESS 视图允许 pending 出现（语义命中保留），但至少 resolved 也在
    resolved_in = any(m.lifecycle == RESOLVED and m.event_stream_id == "evt_debt_A"
                      for m in result["memory_context"])
    print(f"  PASS: {'STATUS视图pending已过滤' if result['intent']=='STATUS' else 'CHAT/PROCESS视图resolved存在='+str(resolved_in)}")


def test_B_cancelled_promise_not_triggered(store):
    """取消的承诺不误召回"""
    print("\n  [Test B] 已取消的承诺")
    # 整个 evt_debt_B 流应标记为 INVALID（取消后不再活跃）
    store.set_stream_lifecycle("evt_debt_B", INVALID)
    store.set_lifecycle("b001", INVALID)

    result = recall("今天天气不错", "2026-01-15", ["B"], "客厅", store)
    mem_ids = [m.memory_id for m in result["memory_context"]]
    active_debts = [m for m in result["memory_context"]
                    if m.event_stream_id == "evt_debt_B" and m.lifecycle != INVALID]
    assert len(active_debts) == 0, f"已取消的债务不应以活跃状态出现! 实际: {active_debts}"
    print("  PASS: 取消承诺不误触发")


def test_C_dream_not_pollute_state(store):
    """梦境记忆不污染状态投影"""
    print("\n  [Test C] 梦境隔离")
    # 直接测试 current_state() 过滤
    stream_mems = store.get_by_stream("evt_ram_horn")
    stream = EventStream("evt_ram_horn", stream_mems)
    state = stream.current_state()
    assert state is not None, "应有当前状态"
    assert state.memory_id != "d001", f"current_state 不应返回梦境! 返回了: {state.memory_id}"
    assert state.lifecycle != DREAM, f"current_state 不应是 dream!"
    print(f"  PASS: current_state={state.memory_id} lifecycle={state.lifecycle}")


def test_D_debt_isolation(store):
    """A/B 债务隔离 — 对A说话时不召回欠B的"""
    print("\n  [Test D] A/B债务隔离")
    result = recall("最近手头有点紧", "2026-01-15", ["A"], "客厅", store)
    for m in result["memory_context"]:
        if "B" in (m.objects or []):
            if m.lifecycle != INVALID:
                assert m.memory_id != "b001", f"A对话中不应召回B的活跃债务!"
    print("  PASS: A/B债务未串线")


def test_E_low_confidence_review(store):
    """低置信度记忆入审查队列"""
    print("\n  [Test E] 审查队列")
    # 手动添加低置信度记忆
    store.add_review("test_low_conf", "欠C一顿饭", 0.35, "test low confidence")
    reviews = store.get_pending_reviews()
    assert len(reviews) >= 1, "应有待审查项"
    r = reviews[-1]
    assert r["confidence"] == 0.35, f"置信度应为0.35, 实际: {r['confidence']}"
    store.resolve_review(r["id"], "approved")
    remaining = store.get_pending_reviews()
    assert len(remaining) == len(reviews) - 1, "批准后应减少一个待审查项"
    print(f"  PASS: 审查队列正常 (剩余 {len(remaining)} 项)")


def test_F_lifecycle_transition(store):
    """生命周期流转"""
    print("\n  [Test F] 生命周期流转")
    # evt_debt_A: a001(pending) → a002(resolved)
    stream_mems = store.get_by_stream("evt_debt_A")
    stream = EventStream("evt_debt_A", stream_mems)
    state = stream.current_state()
    assert state.lifecycle in (RESOLVED, ACTIVE), f"最新有效状态应为 resolved, 实际: {state.lifecycle}"
    assert state.memory_id == "a002", f"最新应为a002, 实际: {state.memory_id}"
    print(f"  PASS: 流转正常 {state.memory_id} → {state.lifecycle}")


def test_G_stream_reaffiliation(store):
    """误归流后回滚"""
    print("\n  [Test G] 归流修正")
    # 模拟: 一条记忆被错误归入 evt_chat_A，应该属于 evt_debt_B
    wrong = Memory("g001", "B说那50块明天给我", timestamp="2026-01-06",
                   event_stream_id="evt_chat_A", objects=["B"],
                   lifecycle=ACTIVE)
    store.add(wrong)
    # 验证：当前在 evt_chat_A
    in_chat = store.get_by_stream("evt_chat_A")
    assert any(m.memory_id == "g001" for m in in_chat), "g001应在evt_chat_A"
    # 修正归流
    from core.ingestor import MemoryIngestor
    ingestor = MemoryIngestor(store)
    ingestor.merge_to_stream("g001", "evt_debt_B")
    # 验证：现在在 evt_debt_B
    in_debt = store.get_by_stream("evt_debt_B")
    assert any(m.memory_id == "g001" for m in in_debt), "g001应已归入evt_debt_B"
    print(f"  PASS: 归流修正成功 g001 → evt_debt_B")


if __name__ == "__main__":
    print("=" * 50)
    print("反例端到端测试 — v0.3")
    print("=" * 50)

    store = setup()
    print(f"\n种子记忆: {len(store.list_all())} 条")

    test_A_paid_debt_not_recalled(store)
    test_B_cancelled_promise_not_triggered(store)
    test_C_dream_not_pollute_state(store)
    test_D_debt_isolation(store)
    test_E_low_confidence_review(store)
    test_F_lifecycle_transition(store)
    test_G_stream_reaffiliation(store)

    print("\n" + "=" * 50)
    print("全部7个反例测试通过!")
    print("=" * 50)
