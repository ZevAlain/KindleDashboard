# -*- coding: utf-8 -*-
"""页脚鼓励语：一言 API + 本地语料兜底。"""
import json
import os
import urllib.request

from .config import CACHE_DIR, now_cn

# ----------------- 页脚鼓励语（一言 API + 本地语料兜底，避免永远同一句） -----------------
# 与节日倒计时同一套策略：联网取一句 → 磁盘缓存 TTL 内不重复请求 → 失败退过期缓存
# → 再退本地语料（按"天"轮换，同一天内不变，e-ink 不会每次刷新都跳字）。
MOTTO_TTL = 6 * 3600          # 每 6 小时联网换一句
MOTTO_MAX_LEN = 26            # 页脚右侧只有一小块，句子别太长（取句上限）
MOTTO_TEXT_MAX = 20           # 页脚正文显示上限：超过就按标点截断（_motto_trim），基本保持一行
MOTTO_ATTR_MAX = 20           # 署名单独占第二行，超过就截断（免得第二行又折行）
MOTTO_API = "https://v1.hitokoto.cn/?encode=json&max_length=%d&c=d&c=j" % MOTTO_MAX_LEN  # 文学 + 诗词
MOTTO_CACHE_FILE = "motto.json"

# 本地语料：接口不可用时使用，按日期取模轮换
LOCAL_MOTTOS = [
    "坚持，让更好的自己发生",
    "日拱一卒，功不唐捐",
    "你只管努力，剩下交给时间",
    "慢慢来，比较快",
    "把身体照顾好，把喜欢的事做好",
    "所有的努力都不会被辜负",
    "心有猛虎，细嗅蔷薇",
    "凡是过往，皆为序章",
    "道阻且长，行则将至",
    "行而不辍，未来可期",
    "不驰于空想，不骛于虚声",
    "星光不问赶路人",
    "你现在的努力，是未来的伏笔",
    "万物皆有裂痕，那是光照进来的地方",
    "生活晴朗，万物可爱",
    "保持热爱，奔赴山海",
    "知足且上进，温柔而坚定",
    "半山腰太挤了，我们顶峰相见",
    "但行好事，莫问前程",
    "追风赶月莫停留",
    "低头有题，抬头有星",
    "微小的坚持，是了不起的开始",
    "别慌，月亮也正在大海某处迷茫",
    "种一棵树最好的时间是十年前，其次是现在",
    "熬过无人问津，才有诗和远方",
    "天道酬勤，力耕不欺",
    "越努力，越幸运",
    "所念皆星河，星河皆可期",
    "心之所向，素履以往",
    "流水不争先，争的是滔滔不绝",
    "做自己的太阳，无需凭借谁的光",
    "你若盛开，清风自来",
    "没有一个冬天不可逾越，没有一个春天不会来临",
    "往前走，别回头",
    "日日行，不怕千万里",
    "念念不忘，必有回响",
    "既然选择了远方，便只顾风雨兼程",
    "少年不惧岁月长，彼方尚有荣光在",
    "请保持那份热爱，下一站是山海",
    "所有的好运，都是努力埋下的伏笔",
]


def _motto_cache_path():
    return os.path.join(CACHE_DIR, MOTTO_CACHE_FILE)


def _load_motto_cache(now):
    """读鼓励语缓存，返回 (text 或 None, _fetched_at)。"""
    try:
        with open(_motto_cache_path(), 'r', encoding='utf-8') as f:
            d = json.load(f)
        text = str(d.get("text") or "").strip()
        return (text or None), int(d.get("_fetched_at") or 0)
    except Exception:
        return None, 0


def _fetch_motto():
    """从一言 API 取一句（无 key、免费），失败抛异常由调用方兜底。
    返回「内容 —— 作者」整串；页脚渲染时用 split_motto 拆成上下两行。"""
    req = urllib.request.Request(MOTTO_API, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=5) as r:
        raw = json.loads(r.read().decode("utf-8", "replace"))
    text = _motto_trim(str(raw.get("hitokoto") or "").strip())
    if not text:
        raise ValueError("empty hitokoto")
    who = str(raw.get("from_who") or "").strip()
    frm = str(raw.get("from") or "").strip()
    attrib = who or frm
    if attrib:
        # 内容和作者改成各占一行后各自受长度限制，署名不再被「总长」挤掉
        if len(attrib) > MOTTO_ATTR_MAX:
            attrib = attrib[:MOTTO_ATTR_MAX - 1] + "…"
        text = "%s —— %s" % (text, attrib)
    return text


def _motto_trim(text):
    """页脚名言正文长度限制：超过 MOTTO_TEXT_MAX 时在最近的标点处截断（避免硬切半句）；
    找不到合适标点就硬切。display 对缓存里的旧数据（旧上限 40）也生效。"""
    if len(text) <= MOTTO_TEXT_MAX:
        return text
    cut = text[:MOTTO_TEXT_MAX]
    for p in "，。；：、！？":
        i = cut.rfind(p)
        if i >= MOTTO_TEXT_MAX // 2:
            return cut[:i]
    return cut


def split_motto(s):
    """「内容 —— 作者」→ (第一行: 内容, 第二行: "—— 作者")；没署名时第二行为空。

    只认拼接时用的 " —— "，并要求署名里没有句末标点——否则正文里自带破折号的句子
    （如「他说 —— 不。」）会被误拆成两行。"""
    s = (s or "").strip()
    i = s.find(" —— ")
    if i > 0:
        text, who = s[:i].strip(), s[i + 4:].strip()
        if text and who and len(who) <= 30 and not any(c in who for c in "。！？"):
            return text, "—— %s" % who
    return s, ""


def local_motto(now=None):
    """本地语料按天轮换：同一天恒定，跨天换下一句。入参可为 datetime 或 date。"""
    now = now or now_cn()
    d = now.date() if hasattr(now, "date") else now
    return LOCAL_MOTTOS[d.toordinal() % len(LOCAL_MOTTOS)]


def get_motto(now):
    """display 的唯一入口：只读本地缓存（后台线程每 6h 更新；过期也先顶着用），
    完全没缓存 → 退本地语料（按天轮换）。绝不联网，永不抛错。"""
    try:
        text, _fetched_at = _load_motto_cache(now)
        if text:
            return text
    except Exception:
        pass
    return local_motto(now)


# ----------------- 天气（open-meteo：免费、无需 key） -----------------
# 与节日/鼓励语同一套策略：后台线程联网取数 -> 磁盘缓存 TTL 内不重复请求；display 请求只读
