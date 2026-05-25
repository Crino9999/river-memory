"""模块A：意图路由 - 使用固定TF-IDF坐标系"""
import numpy as np
from sklearn.decomposition import PCA
from core.store import embed_texts

STATUS, PROCESS, CHAT = "STATUS", "PROCESS", "CHAT"

_SAMPLES = {
    STATUS: [
        "恢复得怎么样了", "现在什么状态", "角好了吗", "情况怎么样",
        "最近还好吗", "现在在哪里", "身体怎么样了", "还有没有事",
        "处理完了吗", "现在能走了吗", "好点了吗", "还在疼吗",
        "伤口愈合了吗", "已经好了吗", "现在安全了吗",
    ],
    PROCESS: [
        "你是怎么治好的", "怎么做到的", "当时发生了什么",
        "还记得过程吗", "怎么治的", "用了什么方法",
        "怎么修复的", "当时怎么想的", "怎么解决的",
        "怎么打赢的", "那段经历是怎样的", "当初怎么发现的",
        "怎么学会的", "怎么过来的", "怎么熬过去的",
    ],
    CHAT: [
        "今天天气不错", "我好累", "想吃东西", "昨晚没睡好",
        "今天真开心", "心情不好", "饿了", "好无聊",
        "周末去哪玩", "今天看到一只猫", "月亮好圆",
        "想喝奶茶", "下雨了好烦", "好困", "天气真好",
    ],
}

_pca: PCA = None
_centers: dict = None

def _ensure_loaded():
    global _pca, _centers
    if _pca is None:
        all_samples = _SAMPLES[STATUS] + _SAMPLES[PROCESS] + _SAMPLES[CHAT]
        embs = np.array(embed_texts(all_samples))
        _pca = PCA(n_components=3).fit(embs)
        reduced = _pca.transform(embs)
        n = len(_SAMPLES[STATUS])
        _centers = {
            STATUS:  reduced[:n].mean(axis=0),
            PROCESS: reduced[n:2*n].mean(axis=0),
            CHAT:    reduced[2*n:].mean(axis=0),
        }

def classify(text: str) -> str:
    """判断意图：STATUS / PROCESS / CHAT"""
    _ensure_loaded()
    emb = np.array(embed_texts([text]))
    point = _pca.transform(emb)[0]
    dist = {k: np.linalg.norm(point - c) for k, c in _centers.items()}
    intent = min(dist, key=dist.get)

    # 兜底规则
    for kw in ["怎么", "如何", "怎样"]:
        if kw in text:
            for v in ["治", "做", "弄", "发生", "修", "打", "学"]:
                if v in text:
                    return PROCESS
    return intent
