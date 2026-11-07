# Kindle Dashboard

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![CI](https://github.com/ZevAlain/KindleDashboard/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/ZevAlain/KindleDashboard/actions/workflows/docker-publish.yml)
![Docker](https://img.shields.io/badge/ghcr.io-zevalain%2Fkindledashboard-2496ED?logo=docker&logoColor=white)
![Platform](https://img.shields.io/badge/platform-amd64%20%7C%20arm64-green)
![Python](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)

Kindle 墨水屏家庭看板：把闲置的 Kindle 变成全家共用的信息板。**看板渲染路径零联网**（后台线程抓取 + 磁盘缓存），断网也秒开；纯黑白设计，专为墨水屏优化。默认监听 `41241` 端口。

## ✨ 功能

- **待办清单**——时段过滤（可跨午夜）、周期（每天 / 每周几 / 每月几号 / 仅一次）、每日零点自动重置勾选、可选随机顺序 / 序号
- **天气**——昨天 / 今天 / 明天三列对比（温度、体感、风、紫外线、空气、湿度、日出日落）+ 未来 8 小时降雨概率条带；可切换为 **AI 分析**（OpenAI 兼容接口，对比昨天指标 → 推断今天 → 穿衣建议）
- **万年历**——整月格子（农历、节气、法定假日、重要日子标记）+ 当日黄历（干支 / 建除 / 宜忌）；节气由天文算法离线计算，分钟级精度、无年份上限
- **重要日子**——公历（每年 / 每月）/ 农历（自动换算当年公历日，含闰月），当天在待办顶部弹祝贺提醒（每条可自定义提醒语）
- **鼓励模式**——Kindle 上勾选完成待办后，右下角弹「人物图 + 夸奖话术」彩蛋；话术与人物双随机，人物图后台管理（上传 / 停用 / 选固定 / 打包备份，≤8 张随机池 + 收藏库）
- **世界时钟**、**节日节气倒计时**、**每日一句**（断网退本地语料）

## 🚀 运行

```bash
# Docker：直接用 CI 构建好的镜像（main 每次提交自动更新）
docker compose -f docker-compose.image.yml up -d

# 或本地构建
docker compose up -d

# 或 docker run（volume 不存在时会自动创建）
docker run -d --name kindledashboard --restart unless-stopped \
  -p 41241:41241 -v kindle-data:/data \
  -e TZ=Asia/Shanghai -e KD_PASSWORD=管理密码 -e KD_DISPLAY_KEY=看板key \
  ghcr.io/zevalain/kindledashboard:latest

# 或源码
pip install -r requirements.txt && python app.py
```

生产用 gunicorn 时**必须 `--workers 1`**（多 worker 的 session 密钥与数据锁都会出问题；单 worker 多线程足够家用）。

## 📖 使用

- 管理页 `http://<IP>:41241/admin`，密码登录——待办 / 时段 / 天气（含 AI 接口）/ 万年历 / 鼓励模式（人物图上传）/ 数据源状态，各面板可单独保存
- Kindle 浏览器开 `http://<IP>:41241/display?key=看板key` 免登录进看板。**看板 key 与管理密码是两个独立密钥**：免登录链接是泄漏面最大的凭据（浏览器历史 / 日志 / Referer），分离后它泄漏只损失「看板 + 勾选」，登不进管理页
- 看板交互：点时钟开世界时间、点日期列开万年历（可翻月）、点天气块开对比面板；页脚可刷新 / 切屏幕方向（四态）

## ⚙️ 配置

| 环境变量 | 说明 |
|---|---|
| `KD_PASSWORD` | 管理页登录密码（缺省自动生成，写入 `.runtime_secrets`） |
| `KD_DISPLAY_KEY` | 看板免登录 key（与管理密码独立；缺省自动生成） |
| `KD_SECRET_KEY` | session 密钥（固定后重启不掉登录态） |
| `KD_HOME` | 数据目录，默认代码目录；Docker 内为 `/data` 卷 |
| `KD_NO_BG` | `1` = 关闭后台刷新线程（调试用） |

Docker 内自动生成的密钥查看：`docker exec kindledashboard cat /data/.runtime_secrets`

## 💾 数据与备份

数据全在名为 **`kindle-data`** 的 Docker volume（容器内 `/data`）：`data.json`（全部配置与勾选状态）、`.runtime_secrets`（三个密钥）、`cache/` 与 `static/emoji/`（可自动重建）。

- **配置备份**：管理页「基本 → 备份与恢复」导出 / 导入完整 JSON（**不含人物图**）
- **人物图备份**：管理页「鼓励模式 → 打包下载全部」得到 zip；换机器时把 zip 里的人物重新上传即完整恢复
- 数据含家庭隐私，勿提交到公开仓库

## 📌 注意

- 无内置 HTTPS、看板 key 以 URL 传递，按**局域网场景**设计，勿直接暴露公网
- 农历覆盖 1900–2100（节气无此限制）
- 镜像由 GitHub Actions 自动构建并推送到 GHCR（amd64 / arm64），`main` 每次提交自动重建，`latest` 始终是最新代码（带启动冒烟测试）

## License

[GPL-3.0](LICENSE)
