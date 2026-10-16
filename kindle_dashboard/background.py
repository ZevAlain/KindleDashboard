# -*- coding: utf-8 -*-
"""后台缓存刷新线程：display 请求路径永不联网，联网抓取全部在这里完成。"""
import os
import threading
import time

from .api_status import _api_record, _api_status_merge_disk
from .config import CACHE_DIR, STATIC_EMOJI_DIR, now_cn
from .data import DATA_LOCK, _get_data_locked, get_data, save_data
from .emoji import _ensure_emoji_png, _iter_emoji_spans, convert_emoji_to_html
from .geocode import geocode
from .holidays import HOLIDAY_CACHE_TTL, _load_holiday_cache, fetch_holidays
from .motto import MOTTO_TTL, _fetch_motto, _load_motto_cache, _motto_cache_path
from .utils import _atomic_write_json, _cache_name, _cache_read, _cache_write
from .weather import WEATHER_TTL, _fetch_weather_raw

# ----------------- 后台缓存刷新（display 请求永不联网） -----------------
# Kindle 页面请求必须毫秒级返回（e-ink 整页刷新要等页面加载完），所以所有"联网抓取"
# 都挪到这个后台线程：按 TTL 刷新三类缓存；失败后退避 10 分钟再试（不再让每次页面
# 刷新都陪它等超时）。display 路径只读本地文件：离线 = 显示旧缓存（鼓励语退本地语料）。
FAIL_BACKOFF_SEC = 10 * 60     # 抓取失败后 10 分钟内不重试
BG_CHECK_EVERY = 30            # 后台检查周期（秒）
_fail_state = {}               # {来源: 上次失败时间戳}，内存即可（重启后重试一次无害）


def _fail_backoff_active(source, now_ts):
    t = _fail_state.get(source)
    return t is not None and now_ts - t < FAIL_BACKOFF_SEC


def _fail_record(source, now_ts):
    _fail_state[source] = now_ts


def _bg_refresh_holidays(ts):
    for y in (now_cn().year, now_cn().year + 1):
        cached, fetched_at = _load_holiday_cache(y)
        if cached is not None and ts - fetched_at < HOLIDAY_CACHE_TTL:
            continue
        if _fail_backoff_active("holiday", ts):
            break
        try:
            fetch_holidays(y, ts)
            _fail_state.pop("holiday", None)
            _api_record("holiday", True, ts)
        except Exception as e:
            _fail_record("holiday", ts)
            _api_record("holiday", False, ts, e)
            break          # 源大概率不可达，同轮不试第二年


def _bg_refresh_motto(ts):
    text, fetched_at = _load_motto_cache(None)
    if text and ts - fetched_at < MOTTO_TTL:
        return
    if _fail_backoff_active("motto", ts):
        return
    try:
        fresh = _fetch_motto()
    except Exception as e:
        _fail_record("motto", ts)
        _api_record("motto", False, ts, e)
        return
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        _atomic_write_json(_motto_cache_path(), {"_fetched_at": ts, "text": fresh})
        _fail_state.pop("motto", None)
        _api_record("motto", True, ts)
    except Exception:
        _fail_record("motto", ts)     # 写盘失败也按失败退避：磁盘异常时别每 30s 重抓
        _api_record("motto", False, ts, "cache write failed")


def _bg_refresh_weather(data, ts):
    cfg = data.get("weather") or {}
    location = str(cfg.get("location") or "").strip()
    if not location:
        return
    lat = str(cfg.get("lat") or "").strip()
    lon = str(cfg.get("lon") or "").strip()
    if not (lat and lon):
        geo = geocode(location)      # 地名解析会联网（30 天缓存 + 失败退旧值），仅后台线程做
        if not geo or geo.get("lat") is None:
            return
        lat, lon = str(geo["lat"]), str(geo["lon"])
    name = _cache_name("weather", "%s,%s" % (lat, lon))
    cached = _cache_read(name) or {}
    if (cached.get("temp") is not None
            and ts - int(cached.get("_fetched_at") or 0) < WEATHER_TTL
            and "days" in cached
            and cached.get("v") == 3):   # v2 缓存只有今天/明天两列：重抓一次补成 昨天/今天/明天 三列
        return
    if _fail_backoff_active("weather", ts):
        return
    try:
        _cache_write(name, _fetch_weather_raw(lat, lon, ts))
        _fail_state.pop("weather", None)
        _api_record("weather", True, ts)
    except Exception as e:
        _fail_record("weather", ts)
        _api_record("weather", False, ts, e)


def _emoji_asset_ready(cp):
    """该 emoji 的本地图（png 优先 / svg 回退）是否已就位。"""
    return (os.path.exists(os.path.join(STATIC_EMOJI_DIR, cp + ".png"))
            or os.path.exists(os.path.join(STATIC_EMOJI_DIR, cp + ".svg")))


def _bg_refresh_emojis():
    """给事项里的 emoji 补本地图（自愈"离线保存丢 emoji"）：图齐了就把 text_html
    升级成 <img> 并写盘；没变化不动文件。
    顺带补 UI 固定的日历 emoji（1f5d3，节日行前缀）本地图：已有文件立即返回，不重复请求。
    三段式：锁内只收集缺失清单 → 锁外联网下载（绝不持锁打网络）→ 锁内升级 text_html 落盘。
    否则离线/CDN 超时时每个 emoji 最多卡锁 10 秒，display/toggle 全被拖死。"""
    _ensure_emoji_png("1f5d3", allow_net=True)
    # 1) 锁内：收集所有"还没本地图"的 codepoint
    with DATA_LOCK:
        data = _get_data_locked()
        need = {}
        for it in data["items"]:
            spans = _iter_emoji_spans(it.get("text", ""))
            if not spans or "<img" in (it.get("text_html") or ""):
                continue
            for _s, _e, cp in spans:
                if cp and not _emoji_asset_ready(cp):
                    need[cp] = True
    # 2) 锁外：联网下载（失败会在 _ensure_emoji_png 里落 .skip，7 天内不重试）
    for cp in need:
        _ensure_emoji_png(cp, allow_net=True)
    # 3) 锁内：图齐的事项升级 text_html 并写盘
    with DATA_LOCK:
        data = _get_data_locked()
        changed = False
        for it in data["items"]:
            text = it.get("text", "")
            spans = _iter_emoji_spans(text)
            if not spans or "<img" in (it.get("text_html") or ""):
                continue
            if any(_emoji_asset_ready(cp) for _s, _e, cp in spans):
                it["text_html"] = convert_emoji_to_html(text, allow_net=False)
                changed = True
        if changed:
            save_data(data)


def _cache_refresher_loop():
    while True:
        try:
            now = now_cn()
            ts = int(now.timestamp())
            data = get_data()
            _bg_refresh_holidays(ts)
            _bg_refresh_motto(ts)
            _bg_refresh_weather(data, ts)
            _bg_refresh_emojis()
        except Exception:
            pass    # 后台线程必须永不死
        time.sleep(BG_CHECK_EVERY)




_bg_lock_handle = None   # 跨进程文件锁句柄：必须持有引用，否则 GC 关闭文件、锁就失效了


def _start_cache_refresher():
    """启动后台刷新线程（模块导入时调用一次）。
    多进程部署（gunicorn 多 worker / Flask debug reloader 会二次导入模块）时，
    用跨进程文件锁保证全局只跑一个实例——否则多个 refresher 并发抓外部 API、
    并发写同一批缓存文件，既浪费又容易撞出撕裂写。Windows 无 fcntl 时退回直接启动
    （单进程 dev server 本来就没这个问题）。也可用环境变量 KD_NO_BG=1 显式关闭。"""
    global _bg_lock_handle
    if os.environ.get("KD_NO_BG") == "1":
        return
    try:
        import fcntl
        os.makedirs(CACHE_DIR, exist_ok=True)
        _bg_lock_handle = open(os.path.join(CACHE_DIR, ".bg.lock"), "w")
        try:
            fcntl.flock(_bg_lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)   # 持锁到进程退出
        except (BlockingIOError, OSError):
            _bg_lock_handle.close()
            _bg_lock_handle = None
            return    # 已有别的进程在跑后台刷新，本进程不重复跑
    except ImportError:
        pass
    threading.Thread(target=_cache_refresher_loop,
                     name="cache-refresher", daemon=True).start()


_start_cache_refresher()

_api_status_merge_disk()    # 后台线程跑起来之前先恢复磁盘上的 API 状态（import 时执行一次）
