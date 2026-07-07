# 个人主页（home.powerdaniel3000.duckdns.org）

这个目录是 Daniel 的个人主页——纯静态文件，由服务器上的 Caddy 直接提供，
和 SRS 应用共用同一台服务器、同一个 IP，但走不同的子域名。

| | SRS 应用 | 个人主页 |
|---|---|---|
| 地址 | https://powerdaniel3000.duckdns.org | https://home.powerdaniel3000.duckdns.org |
| 提供方式 | Caddy 反向代理 → FastAPI（端口 8000） | Caddy 直接提供本目录的静态文件 |
| 访问控制 | HTTP Basic Auth（FastAPI 中间件） | **公开**，无密码 |

## 为什么子域名能用？

DuckDNS 支持通配符解析：`*.powerdaniel3000.duckdns.org` 全部自动指向同一个
IP（207.180.204.135），**不需要注册任何新域名**。想再加一个站点（比如
`blog.powerdaniel3000.duckdns.org`）只需在服务器 `/etc/caddy/Caddyfile`
里加一个站点块，Caddy 会自动申请 HTTPS 证书。

## 怎么修改主页？

和改 SRS 应用完全一样的流程：

1. 在本目录改文件（`index.html` / `style.css` / `script.js`）
2. 开分支 → 提交 → 开 PR → CI 通过 → 合并到 `main`
3. 服务器 cron 每 2 分钟运行 `deploy/deploy.sh` 拉取 main
   → **合并后约 2 分钟主页自动更新**，无需登录服务器

注意：`deploy.sh` 合并后会重启 SRS 服务（`systemctl restart ankiadvanced`）。
只改主页时这个重启无害（几秒钟），但如果正在复习卡片，等复习完再合并更稳妥。

## 文件结构

```
homepage/
├── index.html   # 页面结构（导航 / Hero / 关于我 / 项目 / 联系方式 / 页脚）
├── style.css    # 全部样式；颜色集中在顶部 :root 变量里，自动深浅色
├── script.js    # 交互脚本（目前只有页脚年份）
└── README.md    # 本文件
```

## 常见扩展

- **换配色**：只改 `style.css` 顶部 `:root` 里的 `--accent` 等变量
- **加一个新页面**：新建 `blog.html` 之类的文件，用 `<a href="/blog.html">` 链接过去——Caddy 的 `file_server` 会自动提供目录里的所有文件
- **加图片**：建一个 `homepage/images/` 目录放图片，HTML 里用相对路径 `<img src="images/foto.jpg">`。注意仓库是公开的，放上去的图片全世界可见
- **加新项目卡片**：复制 `index.html` 里的一个 `<article class="card">` 块

## 服务器端配置（已完成的一次性步骤，记录备查）

`/etc/caddy/Caddyfile` 里的站点块（完整示例见 `deploy/Caddyfile.example`）：

```caddyfile
home.powerdaniel3000.duckdns.org {
    root * /home/anki/AnkiAdvanced/homepage
    file_server
}
```

改完执行 `sudo systemctl reload caddy` 生效。
