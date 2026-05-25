"""测试三个模拟场景"""
from core.memory import Memory, EventStream
from core.intent import classify, STATUS, PROCESS, CHAT
from core.associative import associative_search, HitResult
from core.eventstream import query_stream
from core.store import MemoryStore

def setup():
    store = MemoryStore()
    store.clear()

    # 注册记忆
    memories = [
        # 事件流: 欠A 100块
        Memory("m001", "我答应在1.20还A 100块。", timestamp="2026-01-01",
               event_stream_id="evt_debt_A", objects=["A"], environment="厨房",
               status_update="欠A 100块，1.20到期"),
        # 事件流: 拉姆的角
        Memory("m002", "他说要治好姐姐的角...这能信吗？", timestamp="2026-01-01",
               event_stream_id="evt_ram_horn", objects=["拉姆", "用户"], environment="治疗室"),
        Memory("m003", "还要再等几天...焦躁。先生熬夜查资料，眼睛都红了，好累。", timestamp="2026-01-02",
               event_stream_id="evt_ram_horn", objects=["拉姆", "用户"], environment="治疗室"),
        Memory("m004", "成功了！姐姐的角，好了。", timestamp="2026-01-05",
               event_stream_id="evt_ram_horn", objects=["拉姆", "用户"], environment="治疗室",
               status_update="拉姆的角=已治愈"),
    ]
    for m in memories:
        store.add(m)
    return store

def test_scene1_debt(store):
    """场景一：1.20，对A说天气不错 → 应该想起欠钱"""
    print("\n" + "=" * 50)
    print("场景一：欠钱提醒 (关联索引)")
    print("=" * 50)
    user = "今天天气不错。"
    intent = classify(user)
    print(f"输入: {user}")
    print(f"意图: {intent}")
    hits = associative_search(user, "2026-01-20", ["A"], "客厅", store)
    print(f"命中记忆: {[(h.memory.memory_id, h.score, list(h.sources)) for h in hits]}")
    assert hits[0].memory.memory_id == "m001", "应该优先命中欠钱记忆!"
    print("PASS: A会提醒你还钱")

def test_scene2_status(store):
    """场景二：问恢复 → 只取治愈成功"""
    print("\n" + "=" * 50)
    print("场景二：治角状态查询 (认知视图)")
    print("=" * 50)
    user = "我看看恢复得如何。"
    intent = classify(user)
    print(f"输入: {user}")
    print(f"意图: {intent}")

    hits = associative_search(user, "2026-01-10", ["拉姆"], "治疗室", store)
    stream_mems = store.get_by_stream("evt_ram_horn")
    stream = EventStream("evt_ram_horn", stream_mems)
    result = query_stream(stream, intent)

    print(f"返回记忆: {[m.memory_id for m in result]}")
    assert result[0].memory_id == "m004", "认知视图应只返回治愈成功!"
    print("PASS: 蕾姆说角已经好了")

def test_scene2_process(store):
    """场景二：问怎么治的 → 返回完整事件链"""
    print("\n  --- 过程查询 (回忆视图) ---")
    user = "还记得我是怎么治好的吗？"
    intent = classify(user)
    print(f"输入: {user}")
    print(f"意图: {intent}")

    stream_mems = store.get_by_stream("evt_ram_horn")
    stream = EventStream("evt_ram_horn", stream_mems)
    result = query_stream(stream, intent)
    print(f"返回记忆: {[m.memory_id for m in result]}")
    assert len(result) == 3, "回忆视图应返回全部3条!"
    print("PASS: 蕾姆回忆了整个治疗过程")

def test_scene3_chat(store):
    """场景三：闲聊好累 → 混合视图保留熬夜查资料"""
    print("\n" + "=" * 50)
    print("场景三：闲聊混合视图 (CHAT)")
    print("=" * 50)
    user = "今天好累。"
    intent = classify(user)
    print(f"输入: {user}")
    print(f"意图: {intent}")

    # 模拟语义探针命中 m003 (熬夜查资料)
    stream_mems = store.get_by_stream("evt_ram_horn")
    stream = EventStream("evt_ram_horn", stream_mems)
    result = query_stream(stream, intent, semantic_hit_ids={"m003"})

    ids = [m.memory_id for m in result]
    print(f"返回记忆: {ids}")
    assert "m003" in ids, "混合视图应保留语义命中的过程节点!"
    assert "m004" in ids, "混合视图应包含最新状态作为背景!"
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
