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
    import os

    from kindle_dashboard.config import RUNTIME_SECRETS_FILE
    from kindle_dashboard.secrets import (SECRET_KEY_SRC, SECRET_PASSWORD,
                                          SECRET_PASSWORD_SRC)

    if SECRET_PASSWORD_SRC in ("命令行", "命令行/环境变量"):
        # 密码是你自己设置的：不再回显明文（避免留在终端/容器日志里），只提示来源
        print("[安全] 登录密码 = 已通过命令行/环境变量指定（KD_PASSWORD）")
    else:
        print("[安全] 登录密码 = %s（来源：%s）" % (SECRET_PASSWORD, SECRET_PASSWORD_SRC))
        print("[安全]   Kindle 免登录地址：http://<本机IP>:41241/display?key=%s" % SECRET_PASSWORD)
    print("[安全]   覆盖方式：--password=你的密码 或 环境变量 KD_PASSWORD")
    print("[安全] session 密钥来源 = %s（%s）" % (SECRET_KEY_SRC, os.path.basename(RUNTIME_SECRETS_FILE)))
    app.run(host='0.0.0.0', port=41241)
