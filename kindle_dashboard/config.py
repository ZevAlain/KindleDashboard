# -*- coding: utf-8 -*-
"""全局配置与路径：数据目录(KD_HOME)、时区、共享常量。"""
import os
from datetime import datetime, timedelta, timezone

# BASE_DIR = 项目根（本包的上一级，即入口 app.py 所在目录）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# DATA_DIR = 数据目录（data.json / cache / static/emoji / .runtime_secrets 落在这里）。
# 默认与代码同目录；Docker 等场景用 KD_HOME=/data 把数据拆到独立卷。
DATA_DIR = os.environ.get("KD_HOME") or BASE_DIR
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except OSError:
    pass

RUNTIME_SECRETS_FILE = os.path.join(DATA_DIR, ".runtime_secrets")
DATA_FILE = os.path.join(DATA_DIR, 'data.json')
CACHE_DIR = os.path.join(DATA_DIR, 'cache')
STATIC_EMOJI_DIR = os.path.join(DATA_DIR, 'static', 'emoji')
os.makedirs(STATIC_EMOJI_DIR, exist_ok=True)
TWEMOJI_URL = "https://cdnjs.cloudflare.com/ajax/libs/twemoji/14.0.2/svg/{cp}.svg"

# 中国标准时间（东八区，无夏令时）。固定偏移量，不依赖服务器系统时区，
# 也不依赖 tzdata 数据库，确保"当前中国时间"始终正确。
CN_TZ = timezone(timedelta(hours=8), name='Asia/Shanghai')


def now_cn():
    return datetime.now(CN_TZ)


WEEKDAY_CN = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}

# 应用版本（代码内管理，发版时手动递增；admin 页底部显示）：
# 涉及 display 的改动发布前记得 bump——Kindle 磁盘缓存顽固，版本号是确认
# 设备吃到新页面的唯一手段（docs/kindle-constraints.md 第五节）
APP_VERSION = "v0.0.1"

# 后台「数据源」tab 用的来源说明（纯静态文案，状态来自 api_status）。
API_SOURCES = {
    "weather": {
        "title": "天气（open-meteo）",
        "endpoint": "api.open-meteo.com（免 key，30 分钟缓存）",
    },
    "holiday": {
        "title": "法定节假日 · 当年（timor.tech）",
        "endpoint": "timor.tech/api/holiday/year",
    },
    "holiday_next": {
        "title": "法定节假日 · 次年（timor.tech）",
        "endpoint": "timor.tech/api/holiday/year（次年安排通常年底才发布，失败属正常）",
    },
    "motto": {
        "title": "每日鼓励语（hitokoto.cn）",
        "endpoint": "v1.hitokoto.cn",
    },
}
