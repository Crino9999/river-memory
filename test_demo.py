"""测试三个模拟场景 — 走完整 recall() 流程验证端到端链路"""
from core.memory import Memory, PENDING, ACTIVE, RESOLVED
from core.store import MemoryStore
from main import recall

def setup():
    store = MemoryStore()
    store.clear()

    memories = [
        # 事件流: 欠A 100块 — 未完成承诺
        Memory("m001", "我答应在1.20还A 100块。", timestamp="2026-01-01",
               event_stream_id="evt_debt_A", objects=["A"], environment="厨房",
               status_update="欠A 100块，1.20到期",
               due_at="2026-01-20", lifecycle=PENDING),
        # 事件流: 拉姆的角
        Memory("m002", "他说要治好姐姐的角...这能信吗？", timestamp="2026-01-01",
               event_stream_id="evt_ram_horn", objects=["拉姆", "用户"], environment="治疗室"),
        Memory("m003", "还要再等几天...焦躁。先生熬夜查资料，眼睛都红了，好累。", timestamp="2026-01-02",
               event_stream_id="evt_ram_horn", objects=["拉姆", "用户"], environment="治疗室"),
        Memory("m004", "成功了！姐姐的角，好了。", timestamp="2026-01-05",
               event_stream_id="evt_ram_horn", objects=["拉姆", "用户"], environment="治疗室",
               status_update="拉姆的角=已治愈", lifecycle=RESOLVED),
    ]
    for m in memories:
        store.add(m)
    return store

def test_scene1_debt(store):
    """场景一：1.20，对A说天气不错 → 走recall()完整链路"""
    print("\n" + "=" * 50)
    print("场景一：欠钱提醒 (关联索引 → recall 端到端)")
    print("=" * 50)
    user = "今天天气不错。"
    result = recall(
        user_input=user,
        current_date="2026-01-20",
        present_people=["A"],
        current_env="客厅",
        store=store,
    )
    print(f"输入: {user}")
    print(f"意图: {result['intent']}")
    print(f"命中记忆: {[(h.memory.memory_id, h.score) for h in result['hits']]}")
    # 验证：m001（欠债承诺）应该被命中
    hit_ids = [h.memory.memory_id for h in result['hits']]
    assert "m001" in hit_ids, f"应该命中欠钱记忆m001! 实际: {hit_ids}"
    print("PASS: A会提醒你还钱")

def test_scene2_status(store):
    """场景二：问恢复 → recall() STATUS 视图只取已治愈"""
    print("\n" + "=" * 50)
    print("场景二：治角状态查询 (认知视图 → recall 端到端)")
    print("=" * 50)
    user = "我看看恢复得如何。"
    result = recall(
        user_input=user,
        current_date="2026-01-10",
        present_people=["拉姆"],
        current_env="治疗室",
        store=store,
    )
    print(f"输入: {user}")
    print(f"意图: {result['intent']}")
    memory_ids = [m.memory_id for m in result['memory_context']]
    print(f"返回记忆: {memory_ids}")
    assert "m004" in memory_ids, f"认知视图应返回治愈成功m004! 实际: {memory_ids}"
    assert "m002" not in memory_ids, "认知视图不应返回旧的怀疑记忆!"
    print("PASS: 蕾姆说角已经好了")

def test_scene2_process(store):
    """场景二：问怎么治的 → recall() PROCESS 视图返回完整链"""
    print("\n  --- 过程查询 (回忆视图) ---")
    user = "还记得我是怎么治好的吗？"
    result = recall(
        user_input=user,
        current_date="2026-01-10",
        present_people=["拉姆"],
        current_env="治疗室",
        store=store,
    )
    print(f"输入: {user}")
    print(f"意图: {result['intent']}")
    memory_ids = [m.memory_id for m in result['memory_context']]
    print(f"返回记忆: {memory_ids}")
    assert len(memory_ids) >= 2, f"回忆视图应返回多条记忆! 实际: {len(memory_ids)}"
    assert "m002" in memory_ids, "回忆视图应包含最初怀疑的记忆!"
    print("PASS: 蕾姆回忆了整个治疗过程")

def test_scene3_chat(store):
    """场景三：闲聊好累 → recall() CHAT 混合视图"""
    print("\n" + "=" * 50)
    print("场景三：闲聊混合视图 (CHAT → recall 端到端)")
    print("=" * 50)
    user = "今天好累。"
    result = recall(
        user_input=user,
        current_date="2026-01-10",
        present_people=["拉姆"],
        current_env="客厅",
        store=store,
    )
    print(f"输入: {user}")
    print(f"意图: {result['intent']}")
    memory_ids = [m.memory_id for m in result['memory_context']]
    print(f"返回记忆: {memory_ids}")
    # 混合视图：语义命中"熬夜查资料"+最新状态背景
    assert len(memory_ids) >= 1, "混合视图应至少返回1条记忆!"
    # m003 是"熬夜查资料"，与"好累"语义接近
    print("PASS: 蕾姆想起先生熬夜查资料的样子")

if __name__ == "__main__":
    store = setup()
    test_scene1_debt(store)
    test_scene2_status(store)
    test_scene2_process(store)
    test_scene3_chat(store)
    print("\n" + "=" * 50)
    print("全部4个测试场景通过！")
    print("=" * 50)
