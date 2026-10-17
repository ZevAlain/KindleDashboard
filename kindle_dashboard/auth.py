# -*- coding: utf-8 -*-
"""身份验证：session 登录 + ?key= 免登录（仅 display/toggle）。"""
import hmac
from functools import wraps

from flask import redirect, request, session, url_for

from .secrets import SECRET_PASSWORD

# ----------------- 身份验证装饰器 -----------------
def check_auth():
    """检查 Session 或 URL 中传来的密钥（供 Kindle 免登录访问）。
    ?key= 的作用域刻意收窄到 display / toggle：免登录链接即使泄露（日志/浏览器
    历史/Referer 都会留 URL），拿到的人也只能看板+勾选，进不了管理后台——
    旧版 key 直接换 logged_in 会话，等于把管理员凭据放进了 URL。
    比较用 hmac.compare_digest（常数时间），不泄漏匹配进度。"""
    if session.get('logged_in'):
        return True
    key = request.args.get('key')
    if key:
        try:
            matched = hmac.compare_digest(str(key), str(SECRET_PASSWORD))
        except Exception:
            matched = False
        if matched:
            # ?key= 只放行 display / toggle / set_land（看板+勾选+记住屏幕方向），
            # 其余端点（/admin、/admin/save、/api/*）必须走密码登录，也不建立任何会话。
            if (request.endpoint or '') in ('display', 'toggle_item', 'set_land'):
                return True
    return False


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not check_auth():
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function
