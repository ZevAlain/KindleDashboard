# -*- coding: utf-8 -*-
"""身份验证：session 登录 + ?key= 免登录（仅 display/toggle）。"""
import hmac
from functools import wraps

from flask import redirect, request, session, url_for

from .secrets import SECRET_DISPLAY_KEY

# ----------------- 身份验证装饰器 -----------------
def check_auth():
    """检查 Session 或 URL 中传来的看板 key（供 Kindle 免登录访问）。
    ?key= 用**独立的看板 key**（SECRET_DISPLAY_KEY），与管理密码是两个不同的
    密钥——免登录链接是泄漏面最大的凭据（日志/浏览器历史/Referer 都会留 URL），
    分离后它泄漏只损失"看板+勾选"，拿它去 /login 也登不进管理页（那里能看到
    AI 的 API Key 等敏感配置）。作用域仍收窄到 display/toggle/set_land/set_panel。
    比较用 hmac.compare_digest（常数时间），不泄漏匹配进度。"""
    if session.get('logged_in'):
        return True
    key = request.args.get('key')
    if key:
        try:
            matched = hmac.compare_digest(str(key), str(SECRET_DISPLAY_KEY))
        except Exception:
            matched = False
        if matched:
            # ?key= 只放行 display / toggle / set_land / set_panel（看板+勾选+记住
            # 方向与面板），其余端点（/admin、/admin/save、/api/*）必须走密码登录，
            # 也不建立任何会话。
            if (request.endpoint or '') in ('display', 'toggle_item', 'set_land', 'set_panel'):
                return True
    return False


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not check_auth():
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function
