#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kindle Dashboard 入口。

源码运行：python app.py
生产运行：gunicorn --workers 1 --threads 8 --bind 0.0.0.0:41241 app:app
（必须单 worker：多 worker 的 session 密钥与数据锁都会出问题）
"""
from kindle_dashboard import create_app

app = create_app()

if __name__ == '__main__':
    import hmac
    import os

    from kindle_dashboard.config import RUNTIME_SECRETS_FILE
    from kindle_dashboard.secrets import (SECRET_DISPLAY_KEY, SECRET_DISPLAY_KEY_SRC,
                                          SECRET_KEY_SRC, SECRET_PASSWORD,
                                          SECRET_PASSWORD_SRC)

    if SECRET_PASSWORD_SRC in ("命令行", "环境变量"):
        # 密码是你自己设置的：不回显明文（避免留在终端/容器日志里），只提示来源
        print("[安全] 管理密码 = 已通过命令行/环境变量指定（KD_PASSWORD）")
    else:
        print("[安全] 管理密码 = %s（来源：%s）" % (SECRET_PASSWORD, SECRET_PASSWORD_SRC))
    print("[安全]   覆盖方式：--password=你的密码 或 环境变量 KD_PASSWORD")
    print("[安全] session 密钥来源 = %s（%s）" % (SECRET_KEY_SRC, os.path.basename(RUNTIME_SECRETS_FILE)))
    # 看板免登录 key 与管理密码是两个独立密钥：泄漏只损失看板+勾选，登不进管理页
    print("[安全] 看板免登录 key = %s（来源：%s；覆盖：KD_DISPLAY_KEY）"
          % (SECRET_DISPLAY_KEY, SECRET_DISPLAY_KEY_SRC))
    print("[安全]   Kindle 书签地址：http://<本机IP>:41241/display?key=%s" % SECRET_DISPLAY_KEY)
    try:
        if hmac.compare_digest(SECRET_DISPLAY_KEY, SECRET_PASSWORD):
            print("[安全] 警告：看板 key 与管理密码相同，等于没有分离——请设置不同的 KD_DISPLAY_KEY！")
    except Exception:
        pass
    app.run(host='0.0.0.0', port=41241)
