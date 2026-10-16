# -*- coding: utf-8 -*-
"""HTTP 抓取与磁盘缓存的小工具（原子写，防撕裂读）。"""
import hashlib
import json
import os
import urllib.request

from .config import CACHE_DIR

def _http_json(url, timeout=6):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (kindle-dashboard)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _cache_read(name):
    try:
        with open(os.path.join(CACHE_DIR, name), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _atomic_write_json(path, payload):
    """tmp + os.replace 原子写 JSON：后台线程写、请求线程并发读的场景下，
    读方永远不会看到写了一半的文件（撕裂读会让天气/节日块整块闪没）。"""
    tmp_path = str(path) + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def _cache_write(name, payload):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        _atomic_write_json(os.path.join(CACHE_DIR, name), payload)
    except Exception:
        pass


def _cache_name(prefix, key):
    return "%s_%s.json" % (prefix, hashlib.md5(key.encode("utf-8")).hexdigest())
