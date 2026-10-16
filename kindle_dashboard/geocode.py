# -*- coding: utf-8 -*-
"""地名 -> 坐标（open-meteo 主源 + OSM Nominatim 兜底），磁盘缓存。"""
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from .config import CN_TZ
from .utils import _cache_name, _cache_read, _cache_write, _http_json

GEO_TTL = 30 * 86400           # 地名 -> 坐标缓存 30 天
GEOCODE_API = "https://geocoding-api.open-meteo.com/v1/search"
# 补充搜索源：open-meteo 只按「地名单个词」匹配（"北京 朝阳"/"杭州西湖" 都是 0 条），
# 区县与组合词交给 OSM Nominatim（同样免费无 key）。只在后台搜索时用，看板取天气不走它。
NOMINATIM_API = "https://nominatim.openstreetmap.org/search"
GEO_EMPTY_TTL = 5 * 60       # 搜索无结果只缓存 5 分钟（源恢复后能马上重试）
NOMINATIM_TIMEOUT = 5        # Nominatim 偶尔会被限流拖到十几秒，超 5 秒就放弃它，别让用户干等
NOMINATIM_RETRY_MAX = 1.5    # 主查询花这么久还没结果 = 大概率被限流，不再插空格重搜（重搜只会再等一次超时）
# 只留「行政区/地名」这类结果。实测脏数据：搜「杭州西湖」会返回台湾一条叫杭州西湖的居民路
# （addresstype=residential），那种点选了坐标是错的。
GEO_KEEP_TYPES = {
    "city", "town", "village", "suburb", "borough", "county", "district", "municipality",
    "state", "province", "region", "island", "hamlet", "quarter", "neighbourhood",
    "locality", "commune", "administrative", "lake",
}
def _geo_request(q, count=1, timeout=6):
    """单次请求 open-meteo 地名库。返回 (结果列表, 是否成功连上)。"""
    q = (q or "").strip()
    if not q:
        return [], True
    try:
        n = max(1, min(int(count or 1), 10))
        url = (GEOCODE_API + "?name=" + urllib.parse.quote(q)
               + "&count=%d&language=zh&format=json" % n)
        raw = _http_json(url, timeout=timeout)
        out = []
        for r in (raw.get("results") or []) if isinstance(raw, dict) else []:
            lat, lon = r.get("latitude"), r.get("longitude")
            if lat is None or lon is None:
                continue
            try:
                off_om = (int(r["utcOffsetSeconds"]) // 60) if r.get("utcOffsetSeconds") is not None else None
            except (TypeError, ValueError):
                off_om = None
            out.append({
                "label": str(r.get("name") or q),
                "name": str(r.get("name") or ""),
                "admin": str(r.get("admin1") or ""),
                "country": str(r.get("country") or ""),
                "lat": lat,
                "lon": lon,
                # IANA 时区 + 当前 UTC 偏移（分钟）：管理页「世界时间」搜索添加用，
                # 让任意城市都能精确算本地时间（含夏令时）。OSM 源无时区，
                # _merge_geo_results 会从同坐标结果里补上。
                "tz": str(r.get("timezone") or ""),
                "off": off_om,
                "src": "om",
            })
        return out, True
    except Exception:
        return [], False


def _geocode_raw(query, count=1, timeout=6, fallback=True):
    """open-meteo 地名搜索（不落缓存）。它只按「地名单个词」匹配，"北京 朝阳"/"杭州西湖" 都查不到，
    所以完整词没结果时再拆词 / 截后缀试一轮 —— 万一 Nominatim 也连不上，这条还能兜住。
    fallback=False 时只发一次请求（另一个源已经有足够结果，不必再猜）。
    永不抛异常；连不上就立刻返回，不再浪费请求拖慢页面。"""
    q = (query or "").strip()
    if not q:
        return []
    rs, ok = _geo_request(q, count=count, timeout=timeout)
    if rs or not ok or not fallback:
        return rs
    tries = []
    tokens = [t for t in re.split(r"[\s,，、/]+", q) if t]
    if len(tokens) > 1:
        tries = tokens[:3]
    elif re.search(u"[\u4e00-\u9fa5]", q):
        tries = [q[:-1]] + ([q[-3:], q[-2:]] if len(q) >= 4 else [])   # 杭州市→杭州；杭州西湖→西湖
    out, seen = [], set()
    for t in tries:
        rs2, ok2 = _geo_request(t, count=count, timeout=timeout)
        for r in rs2:
            k = (round(float(r["lat"]), 3), round(float(r["lon"]), 3))
            if k in seen:
                continue
            seen.add(k)
            out.append(r)
        if not ok2 or len(out) >= 8:
            break
    return out[:8]


def _nominatim_raw(query, count=6, timeout=6):
    """OSM Nominatim 搜索（区县/组合词靠它）。返回与 _geocode_raw 同结构的列表。永不抛异常。"""
    q = (query or "").strip()
    if not q:
        return []
    try:
        n = max(1, min(int(count or 6), 10))
        url = (NOMINATIM_API + "?q=" + urllib.parse.quote(q)
               + "&format=jsonv2&limit=%d&accept-language=zh-CN&addressdetails=1" % n)
        raw = _http_json(url, timeout=timeout)
        if not isinstance(raw, list):
            return []
        out = []
        for it in raw:
            if not isinstance(it, dict):
                continue
            try:
                lat, lon = float(it.get("lat")), float(it.get("lon"))
            except (TypeError, ValueError):
                continue
            # display_name 形如「朝阳区, 北京市, 中国」，用它切出上级行政区做副标题
            parts = [p.strip() for p in str(it.get("display_name") or "").split(",") if p.strip()]
            addr = it.get("address") if isinstance(it.get("address"), dict) else {}
            kinds = {str(k) for k in (it.get("addresstype"), it.get("type")) if k}
            if kinds and not (kinds & GEO_KEEP_TYPES):
                continue          # 道路/建筑之类的非地点结果直接丢掉（两个字段都缺就放行）
            label = str(it.get("name") or "").strip() or (parts[0] if parts else "")
            if not label:
                continue
            admin = ", ".join(parts[1:-1]) if len(parts) > 2 else (parts[1] if len(parts) == 2 else "")
            out.append({
                "label": label,
                "name": label,
                "admin": admin,
                "country": str(addr.get("country") or (parts[-1] if len(parts) > 1 else "")),
                "lat": lat,
                "lon": lon,
                "tz": "",      # OSM 不返回时区，合并阶段从 open-meteo 同坐标结果补
                "off": None,
                "src": "osm",
            })
        return out
    except Exception:
        return []


def _merge_geo_results(*groups, **kw):
    """多个来源的结果合并去重：坐标 3 位小数（约 100m）内视为同一地点。
    同时补全 tz/off：第一源（OSM）不带时区，从同坐标的另一源结果借用，
    让「世界时间搜索添加」能拿到 IANA 时区、精确算夏令时。"""
    cap = int(kw.get("cap", 8))
    tz_by_key = {}
    for group in groups:
        for r in (group or []):
            if r.get("tz"):
                try:
                    tz_by_key[(round(float(r.get("lat")), 3), round(float(r.get("lon")), 3))] = (r.get("tz"), r.get("off"))
                except (TypeError, ValueError):
                    continue

    def finish(lst):
        for r in lst:
            if not r.get("tz"):
                try:
                    k = (round(float(r.get("lat")), 3), round(float(r.get("lon")), 3))
                except (TypeError, ValueError):
                    continue
                if k in tz_by_key:
                    r["tz"], r["off"] = tz_by_key[k]
        return lst

    out, seen = [], set()
    for group in groups:
        for r in (group or []):
            try:
                key = (round(float(r.get("lat")), 3), round(float(r.get("lon")), 3))
            except (TypeError, ValueError):
                continue
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
            if len(out) >= cap:
                return finish(out)
    return finish(out)


def geocode(location):
    """地名 -> {lat, lon, label, ...}（看板取天气用，取唯一结果）。缓存 30 天；接口失败时用过期缓存。"""
    loc = (location or "").strip()
    if not loc:
        return None
    name = _cache_name("geo", loc)
    cached = _cache_read(name) or {}
    now_ts = int(datetime.now(CN_TZ).timestamp())
    if cached.get("lat") is not None and now_ts - int(cached.get("_t") or 0) < GEO_TTL:
        return cached
    rs = _geocode_raw(loc, count=1)
    if rs:
        r0 = rs[0]
        data = {
            "lat": r0["lat"],
            "lon": r0["lon"],
            "label": r0["label"],
            "admin": r0["admin"],
            "country": r0["country"],
            "_t": now_ts,
        }
        _cache_write(name, data)
        return data
    return cached or None     # 拿不到新坐标就用旧的；完全没有则 None


def _safe(fn, *args):
    """搜索函数都不该抛异常，这层再兜一次底，返回 []。"""
    try:
        return fn(*args)
    except Exception:
        return []


def _nominatim_search(q, count=6):
    """Nominatim：先按原词搜；被过滤成 0 条时，中文无空格的组合词在中间插空格再搜一次
    （「杭州西湖」→「杭州 西湖」，这样能命中西湖区/杭州市）。主查询一慢（被限流/超时）
    就直接收手——插空格再搜一次只会再等一次超时，白等几秒。"""
    q = (q or "").strip()
    if not q:
        return []
    t0 = time.time()
    rs = _safe(_nominatim_raw, q, count, NOMINATIM_TIMEOUT)
    if rs:
        return rs
    if time.time() - t0 > NOMINATIM_RETRY_MAX:
        return []                       # 花了这么久还没结果 = 被限流，不再插空格白等一次超时
    if " " not in q and len(q) >= 4 and re.search(u"[\u4e00-\u9fa5]", q):
        mid = len(q) // 2
        return _safe(_nominatim_raw, q[:mid] + " " + q[mid:], count, NOMINATIM_TIMEOUT)
    return []


def geocode_search(query, count=6):
    """后台「搜索地区」用：Nominatim（区县/组合词更准）+ open-meteo 两源合并。
    两源并行请求：Nominatim 被限流时单次能拖到 5 秒，串行会把整个搜索拖到十几秒，
    并行之后整链耗时 = 较慢那一源（最多 5 秒）。
    有结果缓存 30 天，无结果只缓存 5 分钟；接口失败退过期缓存，再失败返回 []。"""
    q = (query or "").strip()
    if not q:
        return []
    name = _cache_name("geoq", q.lower())
    cached = _cache_read(name) or {}
    now_ts = int(datetime.now(CN_TZ).timestamp())
    if cached.get("_t") is not None:
        ttl = GEO_TTL if cached.get("results") else GEO_EMPTY_TTL
        if now_ts - int(cached.get("_t") or 0) < ttl:
            return cached.get("results") or []
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_osm = ex.submit(_safe, _nominatim_search, q, count)
        # open-meteo 侧自己决定要不要拆词（主词 0 条才拆），不依赖 Nominatim 的结果，才能并行
        f_om = ex.submit(_safe, _geocode_raw, q, count, NOMINATIM_TIMEOUT, True)
        osm, om = f_osm.result(), f_om.result()
    results = _merge_geo_results(osm, om)
    if results:
        _cache_write(name, {"_t": now_ts, "results": results})
        return results
    _cache_write(name, {"_t": now_ts, "results": []})   # 空结果也记一下，5 分钟内不再打两个源
    return cached.get("results") or []

def geocode_cached_only(location):
    """只读地名解析的磁盘缓存（不联网）：display 请求路径用；后台线程用完整 geocode()。"""
    loc = (location or "").strip()
    if not loc:
        return None
    cached = _cache_read(_cache_name("geo", loc)) or {}
    if cached.get("lat") is None:
        return None
    return cached
