# Kindle Dashboard

Kindle 墨水屏家庭看板：待办清单（时段过滤/每日重置）、天气（昨天/今天/明天）、农历黄历万年历、世界时钟、节日节气倒计时、每日一句。看板渲染路径不联网（后台线程抓取+缓存），离线也能秒开。默认监听 `41241` 端口。

## 运行

```bash
# Docker：直接用 CI 构建好的镜像（main 每次提交自动更新）
docker compose -f docker-compose.image.yml up -d

# 或本地构建
docker compose up -d

# 或 docker run（bind mount：数据落在当前目录 data/，备份就是复制文件夹）
docker run -d --name kindledashboard --restart unless-stopped \
  -p 41241:41241 -v $(pwd)/data:/data \
  -e TZ=Asia/Shanghai -e KD_PASSWORD=你的密码 \
  ghcr.io/zevalain/kindledashboard:latest

# 或源码
pip install -r requirements.txt && python app.py
```

生产用 gunicorn 时**必须 `--workers 1`**（多 worker 的 session 密钥与数据锁都会出问题）。

## 使用

- 管理页 `http://<IP>:41241/admin`，密码登录，配置待办/地区/时段
- Kindle 浏览器开 `http://<IP>:41241/display?key=密码` 免登录进看板（此链接只能看板+勾选，进不了管理页）

## 配置

| 环境变量 | 说明 |
|---|---|
| `KD_PASSWORD` | 登录密码 / 免登录 key（缺省自动生成，写入 `.runtime_secrets`） |
| `KD_SECRET_KEY` | session 密钥（固定后重启不掉登录态） |
| `KD_HOME` | 数据目录，默认代码目录；Docker 内为 `/data` 卷 |
| `KD_NO_BG` | `1` = 关闭后台刷新线程（调试用） |

## 数据与备份

全在 `KD_HOME`（Docker 内为 `/data` 卷）：`data.json`（待办与配置，`.bak` 为上一版本，损坏自动回退）、`.runtime_secrets`（密码与 session 密钥）、`cache/`、`static/emoji/`（这两项删了会自动重建）。数据含家庭隐私，勿提交仓库。

**日常备份只需 `data.json` 一个文件**（原子写，容器运行中直接拷也安全）：

```bash
# bind mount（compose / 推荐）：就是普通目录
cp data/data.json ~/backup/data-$(date +%Y%m%d).json
# named volume（docker run -v kindle-data:/data）：
docker cp kindledashboard:/data/data.json ~/backup/data-$(date +%Y%m%d).json

# 导入
cp ~/backup/data-20261016.json data/data.json && docker compose restart
# 或
docker cp ~/backup/data-20261016.json kindledashboard:/data/data.json && docker restart kindledashboard
```

**整卷迁移**（连密码/缓存一起搬）：

```bash
# 导出
docker run --rm -v kindle-data:/data:ro -v $(pwd):/backup alpine \
  tar czf /backup/kindle-data.tar.gz -C /data .
# 新机器导入
docker volume create kindle-data
docker run --rm -v kindle-data:/data -v $(pwd):/backup alpine \
  sh -c "rm -rf /data/* && tar xzf /backup/kindle-data.tar.gz -C /data"
```

## 已知限制

- 农历覆盖 1900–2100；节气表收录至 2027（之后节气行不显示）
- 单密码认证、无内置 HTTPS，按局域网场景设计，勿直接暴露公网
- 镜像由 GitHub Actions 自动构建到 GHCR（amd64/arm64），`main` 每次提交自动重建，`latest` 始终是最新代码

## License

[MIT](LICENSE)
