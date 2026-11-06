# -*- coding: utf-8 -*-
"""AI 天气分析（OpenAI 兼容接口）：后台线程调用，display 只读缓存。

与天气/假日/语料同一套模式：联网全部在后台线程完成，结果落盘
cache/ai_weather.json，display 请求路径永不联网（弱 Wi-Fi / 离线时显示
上次结果；一次都没成功过则由模板回退降雨概率条带，绝不空块）。
接口是 OpenAI 标准的 /chat/completions（第三方兼容服务），用 urllib
直调，不引入 requests/SDK 依赖。
"""
import json
import os
import re
import urllib.error
import urllib.request

from .config import CACHE_DIR
from .utils import _atomic_write_json

AI_WEATHER_CACHE = "ai_weather.json"   # cache/ 下的缓存文件名
AI_WEATHER_PROMPT_MAX = 60            # prompt 里要求的字数上限（汉字，含标点）
AI_WEATHER_MAX = 66                   # 服务端硬截断：竖屏 2 行(34字/行)内、横屏 3 行(30字/行)内
AI_TIMEOUT = 30                       # 第三方接口可能较慢，后台线程等得起
AI_MAX_TOKENS = 300

# 清洗要剔除的字符：markdown 记号 + 常见装饰符号（墨水屏纯文本显示，这些只会变乱码/灰点）
_STRIP_CHARS_RE = re.compile(u"[*#`~_>|·•]+")
# emoji 区段（Kindle 渲染不了 emoji 字符，与 emoji.py 的 EMOJI_SPAN_RE 同源思路，这里做删除）
_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"
    "\u2600-\u27BF"
    "\u2B00-\u2BFF"
    "\u2300-\u23FF"
    "\u2190-\u21FF"
    "\u25A0-\u25FF"
    "\uFE0F"
    "]+"
)


def ai_cache_path():
    return os.path.join(CACHE_DIR, AI_WEATHER_CACHE)


def load_ai_text():
    """只读缓存（display 路径专用，不联网）。返回 (文本或 None, 缓存dict或None)。永不抛异常。"""
    try:
        with open(ai_cache_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        text = str(d.get("text") or "").strip()
        return (text or None), (d if isinstance(d, dict) else None)
    except Exception:
        return None, None


def write_ai_cache(payload):
    os.makedirs(CACHE_DIR, exist_ok=True)
    _atomic_write_json(ai_cache_path(), payload)


def ai_configured(cfg):
    """AI 分析是否已配置齐全（后台线程与 admin 状态页共用）。永不抛异常。"""
    try:
        w = cfg or {}
        return bool(str(w.get("ai_base_url") or "").strip()
                    and str(w.get("ai_api_key") or "").strip()
                    and str(w.get("ai_model") or "").strip())
    except Exception:
        return False


def normalize_chat_url(base_url):
    """Base URL -> 完整 chat/completions 地址。
    用户可能填 https://api.x.com/v1、带尾斜杠、甚至整条 endpoint——
    统一处理：去尾斜杠；不以 /chat/completions 结尾才自动补。"""
    u = str(base_url or "").strip().rstrip("/")
    if not u:
        return ""
    if not u.endswith("/chat/completions"):
        u += "/chat/completions"
    return u


def clean_ai_text(raw):
    """模型输出 -> 可上墨水屏的纯文本：去换行/markdown/emoji、折叠空白、硬截 AI_WEATHER_MAX。
    清洗后为空返回 ""（调用方视为失败）。"""
    t = str(raw or "")
    t = t.replace("\r", " ").replace("\n", " ")
    t = _EMOJI_RE.sub("", t)
    t = _STRIP_CHARS_RE.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    # 硬截断在标点后优先（避免切半句），找不到就近标点才硬切——与 _motto_trim 同思路
    if len(t) > AI_WEATHER_MAX:
        cut = t[:AI_WEATHER_MAX]
        for p in "，。；：、！？":
            i = cut.rfind(p)
            if i >= AI_WEATHER_MAX // 2:
                return cut[:i + 1]
        t = cut
    return t


def build_prompt(view):
    """天气视图数据 -> 紧凑中文摘要（喂给模型的 user 消息）。view 是 get_weather 的返回。"""
    parts = []
    if view.get("desc"):
        parts.append(u"当前%s" % view["desc"])
    if view.get("range"):
        parts.append(u"今日%s" % view["range"])
    if view.get("feel"):
        parts.append(view["feel"])
    if view.get("wind"):
        parts.append(view["wind"])
    rain = view.get("rain") or []
    if rain:
        hours = u"、".join(u"%s时%d%%" % (r["h"], int(r["p"])) for r in rain)
        parts.append(u"未来%d小时降雨概率：%s" % (len(rain), hours))
    else:
        parts.append(u"暂无逐时降雨数据")
    if view.get("air"):
        parts.append(view["air"])
    if view.get("uv"):
        parts.append(view["uv"])
    return u"；".join(parts)


AI_SYSTEM_PROMPT = (
    u"你是墨水屏天气助手。根据提供的天气数据，用一句话给出建议：先说降雨情况"
    u"（未来数小时降雨概率的变化、要不要带伞），再给穿衣建议（结合气温与体感）。"
    u"要求：纯文本，不超过%d个汉字（含标点），不分段不换行，不用markdown、"
    u"不用任何emoji或装饰符号，直接输出内容本身。" % AI_WEATHER_PROMPT_MAX
)


def fetch_ai_weather(cfg, view, ts=None, user_prompt=None):
    """调用 OpenAI 兼容接口生成一句天气建议（仅后台线程 / admin 测试路径调用）。
    成功返回清洗截断后的文本（不落盘，由调用方决定：后台线程写缓存、测试路径只回显）；
    失败抛异常（调用方负责退避与状态记录）。
    view 可为 None（admin 测试时天气还没配置/没缓存）：退化为连通性测试 prompt。"""
    w = cfg or {}
    url = normalize_chat_url(w.get("ai_base_url"))
    key = str(w.get("ai_api_key") or "").strip()
    model = str(w.get("ai_model") or "").strip()
    if not (url and key and model):
        raise ValueError(u"AI 接口未配置完整（需要 Base URL / API Key / 模型）")
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": AI_SYSTEM_PROMPT},
            {"role": "user", "content": (user_prompt if user_prompt is not None
                                         else (build_prompt(view) if view
                                               else u"连通性测试：请回复「接口可用」"))},
        ],
        "max_tokens": AI_MAX_TOKENS,
        "temperature": 0.5,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + key,
        "User-Agent": "Mozilla/5.0 (kindle-dashboard)",
    })
    try:
        with urllib.request.urlopen(req, timeout=AI_TIMEOUT) as r:
            raw = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        raise ValueError(u"接口返回 HTTP %d%s" % (e.code, (u"：%s" % detail) if detail else ""))
    except Exception as e:
        raise ValueError(u"连接失败：%s" % e)
    try:
        content = str(((raw.get("choices") or [{}])[0].get("message") or {})
                      .get("content") or "")
    except Exception:
        content = ""
    text = clean_ai_text(content)
    if not text:
        raise ValueError(u"接口响应里没有可用文本（choices[0].message.content 为空）")
    # 落盘：_w_fetched_at 由调用方（后台线程）补写天气缓存的抓取时间，
    # 用于"天气更新了才重新生成"；admin 测试路径不落盘（只验证连通）。
    return text
