# -*- coding: utf-8 -*-
"""Kindle Dashboard：Flask 应用工厂。"""
import os

from flask import Flask

from .config import DATA_DIR
from .secrets import SECRET_KEY


def create_app():
    app = Flask(__name__, static_folder=os.path.join(DATA_DIR, 'static'))
    app.secret_key = SECRET_KEY
    # 会话 cookie 加固：SameSite=Lax 阻断跨站请求携带会话（防 CSRF 打 /toggle /admin/save）；
    # HttpOnly 防 JS 读 cookie（老 Kindle WebKit 不支持 SameSite 也不影响正常使用）。
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

    from .views import register_views
    register_views(app)

    # 后台刷新线程（跨进程文件锁保证全局单实例；KD_NO_BG=1 可关闭）
    from .background import _start_cache_refresher
    _start_cache_refresher()
    return app
