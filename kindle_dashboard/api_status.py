# -*- coding: utf-8 -*-
"""外部 API 抓取状态（后台「数据源」tab 展示）。内存 + 落盘 cache/api_status.json。"""
import json
import os

from .config import CACHE_DIR
from .utils import _atomic_write_json

# ----------------- 外部 API 状态（后台「数据源」tab 展示） -----------------
# 每个联网来源最近一次成功/失败的时间与错误。只由后台线程写（单写者），
# 内存 + 落盘 cache/api_status.json（重启后仍能看到上一轮的状态）。
API_STATUS_FILE = os.path.join(CACHE_DIR, "api_status.json")
_api_status = {}    # {来源: {"last_ok": ts|None, "last_fail": ts|None, "error": str|None}}


def _api_record(source, ok, ts, error=""):
    """记录一次抓取结果（后台线程调用）；落盘失败不影响主流程。"""
    _api_status.setdefault(source, {"last_ok": None, "last_fail": None, "error": None})
    st = _api_status[source]
    if ok:
        st["last_ok"] = ts
        st["last_fail"] = None
        st["error"] = None
    else:
        st["last_fail"] = ts
        st["error"] = str(error)[:200] or "未知错误"
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        _atomic_write_json(API_STATUS_FILE, dict(_api_status, _v=2))
    except Exception:
        pass


def _api_status_merge_disk():
    """启动时把磁盘上的最后已知状态并进内存（只补缺失项，不覆盖已有）。
    带 schema 版本号 _v：旧版状态文件（来源键布局不同，如旧版把"次年失败"
    记在 holiday 一个键里，新版拆成 holiday/holiday_next）整体弃用——
    否则旧记录会把新版的"当年"显示成失败中；后台线程 30 秒内会写入真实状态。"""
    try:
        with open(API_STATUS_FILE, encoding="utf-8") as f:
            disk = json.load(f)
        if not isinstance(disk, dict) or disk.get("_v") != 2:
            return
        for k, v in disk.items():
            if k.startswith("_") or not isinstance(v, dict):
                continue
            mem = _api_status.setdefault(k, {"last_ok": None, "last_fail": None, "error": None})
            for kk in ("last_ok", "last_fail", "error"):
                if mem.get(kk) is None and v.get(kk) is not None:
                    mem[kk] = v.get(kk)
    except Exception:
        pass
