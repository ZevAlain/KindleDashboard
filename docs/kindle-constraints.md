# Kindle 咪咕版浏览器制约清单

> **用途**：在 display 页面实现任何新功能之前，先过一遍本文档，确认方案不踩已知的坑。
> 每条制约都来自真机翻车记录（见文末），不是猜测。
> admin 管理页跑在电脑/手机上，不受本文档约束；本文档只针对 **display 看板页**。

## 一、被禁用 / 不可信的 API（禁止依赖）

| # | API | 真机表现 | 替代方案 |
|---|-----|---------|---------|
| C1 | `localStorage` | 被禁用：抛异常或不落盘，读了永远是空 | 状态进 URL query（见 C7） |
| C2 | `cookie`（`document.cookie` / 会话 cookie） | 被禁用：写了不存储，服务端永远读不到 | 免登录用 `?key=`；状态进 URL query |
| C3 | `history.replaceState` / `pushState` | **三层不可信**：可能抛错；可能静默失效（地址栏不变）；甚至 JS 的 `location` 对象显示"已成功"而真实地址栏没变——**读回校验都骗不过** | **一切"JS 修改 URL"的手段都不用**。状态要进 URL 时，用 `location.href = newUrl` 真实导航（古董浏览器 100% 支持） |
| C4 | HTML5 Drag API（dragstart/drop） | 不触发（注：admin 的触屏拖动为此自写了 touch 通路；display 无拖动需求） | 触摸交互用 touchstart/move/end 自实现 |
| C5 | URL hash 片段（`#xxx`） | meta 整页刷新会**丢掉 hash**（真机验证）；可靠性低 | 状态只放 **query 参数**（`?panel=`），不放 hash |
| C6 | 新 JS 特性 | ES5 之外都可能不支持 | display 页 JS 一律 **ES5**：`var`、无箭头函数、无模板字符串、无 Promise/fetch；新 API 必须有古董兜底（如 `sendBeacon` → 同步 XHR） |

## 二、已验证可靠的能力（可放心使用）

| # | 能力 | 备注 |
|---|-----|------|
| R1 | 内联 `onclick`（`<a>` / `<span>` 上） | 真机验证可靠，项目所有交互的第一通路 |
| R2 | `location.href` 赋值导航 / `location.replace` | 最古老的导航方式，但注意 C3 的教训：**改状态用 href 赋值**，别用于"无痕改地址" |
| R3 | meta 刷新保留 query 参数 | `<meta http-equiv="refresh" content="秒数">`（不带 url）原地重载，URL query 完整保留（真机验证） |
| R4 | XMLHttpRequest（同步/异步） | 可靠；勾选的就地更新（XHR POST `?js=1`）真机在用 |
| R5 | 基础 CSS flex / `clientWidth` 量宽 | fitClock/fitViewport 在用，可靠；注意 html 100% 高度偶尔偏矮（fitViewport 有撑高补偿） |
| R6 | 服务端渲染直出 | **最可靠的手段**：能用服务端出初始状态就不要靠 JS 首帧后再改（方向/面板/字号都已这样做） |

## 三、e-ink 物理特性（设计层面约束）

| # | 特性 | 设计结论 |
|---|-----|---------|
| E1 | 任何内容变化 = 全屏重刷 | **"无刷新切换"在 Kindle 上零收益**：JS 就地改 DOM 与整页导航观感相同。用可靠性换体验时不要犹豫（面板切换已改为真实导航） |
| E2 | 细字重小字渲染发灰 | 16px 常规字重在墨水屏上视觉是灰的。要"显黑"：≥20px + font-weight 700；纯黑 #000000 |
| E3 | 整页刷新闪屏 | 首帧后的二次调整（如 JS 改字号/显隐）会多闪一次——**初始状态尽量服务端直出**（R6） |
| E4 | 弱 Wi-Fi、常离线 | display 渲染路径**永不联网**（后台线程抓取+落盘缓存），页面必须离线可渲染 |

## 四、状态持久化决策树（display 页）

```
状态需要跨刷新保持？
├─ 否 → 页内 JS 变量即可
└─ 是 → 首选：**服务端记忆**（写 data.json，如 /land 方向、/panel 面板）
     │    刷新/唤醒由服务端按记忆直出首帧，绕开 URL/cookie/localStorage 全部制约
     │    （面板切换的观感由 XHR 局部替换内容区解决：POST 返回整页 + contentStart/End
     │     标记切片 + innerHTML——页眉页脚不重绘，勾选就地更新同一通路）
     └─ 备选（服务端记忆不适用时）：URL query 参数
          ├─ 写入：location.href 真实导航（C3 教训：不用 replaceState）
          ├─ 读取：服务端读 query 首帧直出（R6）+ JS 读 query 兜底
          ├─ 所有页内链接（翻月/刷新/勾选/方向）必须保留完整 query——服务端构造 href 时拼接
          └─ 防死循环：目标状态已在当前 URL 时不再导航
```

## 五、发布与诊断流程

1. **版本标识**：CI 构建注入 git sha（`config.APP_VERSION`），admin 页底部显示——Kindle 看不到地址栏，**发版后先核对 admin 版本号**再下结论
2. **磁盘缓存顽固**：Kindle 可能长期吃旧页面（即使响应带 no-store）。真机"改了没效果"时：先确认版本号 → 清浏览器缓存/重启设备 → 再排查代码
3. **唤醒/锁屏重启**：浏览器会丢 URL 状态重新打开——书签必须带 `?key=`；屏幕方向等服务端记住（`/land/<n>` 写 default_land）

## 六、历史翻车记录（防止方案回退）

| 症状 | 根因 | 修复提交 |
|-----|------|---------|
| 面板"只能展开、收不回" | 用 localStorage 当开关，真机不落盘 | 改 URL query |
| 切方向按钮"点了没反应" | setItem 抛异常阻断同 try 里的 reload | <a>+内联 onclick，状态进地址栏 |
| 改完代码真机跑旧页面 | 磁盘缓存顽固 | no-store 响应头 + 版本号核对 |
| 时钟"先小后大"跳变 | 首帧 CSS 兜底值、load 后 JS 才放大 | 脚本执行时立即 fitClock，服务端意识 |
| 待办副标题"发灰" | 16px 细字重墨水覆盖率低 | 20px/700 加粗 |
| 面板刷新后回待办（cookie 版修复无效） | C2：真机 cookie 也被禁 | 弃 cookie |
| 面板刷新后回待办（replaceState 校验版也无效） | C3 第三层：location 对象与真实地址栏脱节 | location.href 真实导航（已被方案C取代） |
| 面板切换整页刷新、页眉页脚跟着闪 | 整页导航触发 e-ink 全屏重绘 | **方案C（现行）**：服务端记忆面板 + XHR 切片局部替换内容区，页眉页脚不重绘，勿回退 |
| 容器起不来（本地构建正常） | Dockerfile 目录 COPY 平铺包结构 | CI 加启动冒烟测试 |
