"""River Memory MVP 端到端演示

演示完整流程：
1. 初始化 ConversationManager
2. 模拟多轮对话（记忆自动入库）
3. 跨会话记忆检索验证
4. 事件流归属 + 物理坐标提取验证
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(__file__))

from core.store import MemoryStore
from core.conversation import ConversationManager
from core.associative import set_alias_map
from core.time_parser import parse_time_expression, date_score
from core.logger import get_logger

log = get_logger("demo")


def load_character(path: str = "config/characters/rem.json"):
    """加载角色配置"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "aliases_map" in data:
            set_alias_map(data["aliases_map"])
        return data
    except FileNotFoundError:
        return {"name": "蕾姆", "system_prompt": "你是蕾姆，罗兹瓦尔宅邸的女仆。"}


def demo():
    print("=" * 60)
    print("River 记忆系统 — MVP 演示")
    print("=" * 60)

    char = load_character()
    print(f"\n[角色] {char.get('display_name', char['name'])}")
    print(f"[系统指令] {char.get('system_prompt', '')[:60]}...")

    store = MemoryStore()
    store.clear()

    manager = ConversationManager(store)
    session_id = "demo_user_001"

    print("\n" + "-" * 40)
    print("Phase 1: 模拟多轮对话 — 记忆自动入库")
    print("-" * 40)

    turns = [
        # (user_msg, bot_reply, date, people, env, expected_coordinates)
        ("拉姆的角还能治好吗？",
         "姐姐的角...恐怕很难，但我听说有一个方法可以试试。",
         "2026-01-01", ["拉姆", "用户"], "治疗室"),

        ("我一定会治好她的。",
         "先生...真的吗？可是这需要很长时间和精力...",
         "2026-01-01", ["拉姆", "用户"], "治疗室"),

        ("我已经查了很多资料，有办法了。",
         "还要再等几天...我好焦躁。先生熬夜查资料，眼睛都红了，好累。",
         "2026-01-02", ["拉姆", "用户"], "治疗室"),

        ("疼吗，试最后一次。",
         "成功了！姐姐的角，好了。",
         "2026-01-05", ["拉姆", "用户"], "治疗室"),

        ("对了，你能不能先借我100块，下月还。",
         "当然可以，先生。不过1月20号之前要还给我哦。",
         "2026-01-10", ["用户"], "厨房"),
    ]

    for user_msg, bot_reply, date, people, env in turns:
        session = manager.get_session(session_id, char["name"])
        session.current_date = date
        session.present_people = people
        session.current_env = env

        ingested = manager.process_turn(session_id, user_msg, bot_reply, char["name"])
        mem_ids = [m.memory_id for m in ingested]
        status = "*"
        for m in ingested:
            if m.status_update:
                status = f"status={m.status_update}"
        print(f"  [{date}] 用户: {user_msg[:30]}...")
        print(f"          → 入库: {mem_ids or '跳过(重复)'} stream={ingested[0].event_stream_id if ingested else 'N/A'} {status}")

    total = len(store.list_all())
    print(f"\n总记忆数: {total}")

    print("\n" + "-" * 40)
    print("Phase 2: 事件流归属验证")
    print("-" * 40)

    streams = {}
    for m in store.list_all():
        sid = m.event_stream_id or "_unaffiliated"
        streams.setdefault(sid, []).append(m)

    for sid, mems in sorted(streams.items()):
        print(f"  事件流: {sid} ({len(mems)}条)")
        for m in sorted(mems, key=lambda x: x.timestamp):
            print(f"    [{m.timestamp}] {m.content[:50]}...")
            if m.objects:
                print(f"            objects={m.objects} env={m.environment}")

    print("\n" + "-" * 40)
    print("Phase 3: 检索验证")
    print("-" * 40)

    test_cases = [
        {
            "name": "欠债提醒 (洞察1)",
            "input": "今天天气不错。",
            "date": "2026-01-20",
            "people": ["用户"],
            "env": "客厅",
            "expect": "物理坐标应命中欠债记忆",
        },
        {
            "name": "状态查询 (洞察2认知视图)",
            "input": "我看看恢复得如何。",
            "date": "2026-01-10",
            "people": ["拉姆"],
            "env": "治疗室",
            "expect": "应只返回治愈成功 (认知视图)",
        },
        {
            "name": "过程查询 (洞察2回忆视图)",
            "input": "还记得我是怎么治好的吗？",
            "date": "2026-01-10",
            "people": ["拉姆"],
            "env": "治疗室",
            "expect": "应返回完整事件链 (回忆视图)",
        },
        {
            "name": "闲聊混合视图 (设计修正)",
            "input": "今天好累。",
            "date": "2026-01-20",
            "people": ["拉姆"],
            "env": "治疗室",
            "expect": "应语义命中'熬夜查资料'+最新状态背景",
        },
    ]

    from core.intent import classify
    from core.associative import associative_search
    from core.eventstream import query_stream
    from core.memory import EventStream

    for tc in test_cases:
        print(f"\n  [{tc['name']}]")
        print(f"  输入: '{tc['input']}'  日期: {tc['date']}")
        intent = classify(tc["input"])
        print(f"  意图: {intent}")

        session = manager.get_session(session_id)
        session.current_date = tc["date"]
        session.present_people = tc["people"]
        session.current_env = tc["env"]

        hits = associative_search(tc["input"], tc["date"], tc["people"], tc["env"], store)
        print(f"  探针命中: {[(h.memory.memory_id, h.score, list(h.sources)) for h in hits[:5]]}")

        if hits:
            for hit in hits[:3]:
                mem = hit.memory
                print(f"    → [{mem.timestamp}] {mem.content[:60]}")
                if mem.status_update:
                    print(f"      状态更新: {mem.status_update}")
        else:
            print(f"    → 无命中，纯闲聊回复")

    print("\n" + "-" * 40)
    print("Phase 4: 时间表达式解析")
    print("-" * 40)

    time_tests = [
        ("明天还你钱", "2026-01-15"),
        ("下周还你钱", "2026-01-15"),
        ("1月20号之前还", "2026-01-15"),
        ("下个月再见", "2026-01-15"),
        ("后天能好吗", "2026-01-15"),
    ]
    for text, current in time_tests:
        result = parse_time_expression(text, current)
        print(f"  '{text}' → {result}")

        if result:
            score = date_score("2026-01-20", "2026-01-20")
            print(f"  date_score(1.20, 1.20) = {score} (同日=10)")

    print("\n" + "=" * 60)
    print("MVP 演示完成!")
    print("=" * 60)
    print("\n使用方式:")
    print("  from core.conversation import ConversationManager")
    print("  manager = ConversationManager()")
    print("  manager.process_turn(session_id, user_msg, bot_reply)")
    print("  reply = manager.recall_for_session(session_id, new_msg)")
    print()


if __name__ == "__main__":
    demo()
