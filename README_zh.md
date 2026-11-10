# aur-ASLZ

[English](README.md) | 简体中文

由 ASLZ 维护的 Arch Linux 预编译软件源。

[![Build Status](https://github.com/AsabaLazy/aur-ASLZ/actions/workflows/aur.yml/badge.svg)](https://github.com/AsabaLazy/aur-ASLZ/actions/workflows/aur.yml)

> 仓库每小时自动检测并同步上游最新版本。

## 使用方法

编辑 `/etc/pacman.conf`，在文件末尾添加：

```ini
[aslz]
SigLevel = Optional TrustAll
Server = https://github.com/AsabaLazy/aur-ASLZ/releases/download/repo
```

或者在终端中执行以下命令添加：

```bash
grep -q '^\[aslz\]' /etc/pacman.conf || printf '\n[aslz]\nSigLevel = Optional TrustAll\nServer = https://github.com/AsabaLazy/aur-ASLZ/releases/download/repo\n' | sudo tee -a /etc/pacman.conf
```

更新软件包缓存：

```bash
sudo pacman -Sy
```

两次 `y`（`sudo pacman -Syy`）可强制刷新数据库缓存，避免从旧源切换时出现同步问题。

## 软件包列表

| 软件包 &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 安装命令 &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
| :--- | :--- |
| [`antigravity-bin`](https://antigravity.google/) | `sudo pacman -S antigravity-bin` |
| [`antigravity-cli-512k-bin`](https://github.com/google-antigravity/antigravity-cli) | `sudo pacman -S antigravity-cli-512k-bin` |
| [`antigravity-cli-bin`](https://github.com/google-antigravity/antigravity-cli) | `sudo pacman -S antigravity-cli-bin` |
| [`antigravity-cli-fix-bin`](https://github.com/google-antigravity/antigravity-cli) | `sudo pacman -S antigravity-cli-fix-bin` |
| [`antigravity-hans-bin`](https://github.com/yuexps/Antigravity-Hans) | `sudo pacman -S antigravity-hans-bin` |
| [`astrlink-bin`](https://github.com/Calcium-Ion/AstrLink) | `sudo pacman -S astrlink-bin` |
| [`dimagent-bin`](https://dimagent.cn) | `sudo pacman -S dimagent-bin` |
| [`gopeed-bin`](https://github.com/GopeedLab/gopeed) | `sudo pacman -S gopeed-bin` |
| [`gopeed-fluent-bin`](https://github.com/AsabaLazy/gopeed-fluent) | `sudo pacman -S gopeed-fluent-bin` |
| [`leinux-bin`](https://github.com/AsabaLazy/Leinux) | `sudo pacman -S leinux-bin` |
| [`magpie-bin`](https://github.com/yetone/magpie) | `sudo pacman -S magpie-bin` |
| [`novelmikuer-bin`](https://github.com/AsabaLazy/NovelMikuer) | `sudo pacman -S novelmikuer-bin` |

## Q&A

**Q: 为什么不提交到官方 AUR 仓库？**

A: 我不太喜欢那种需要被审核的感觉，尽管主要原因是在我提交 aur 时账户无法登陆，且他们的注册通道当时关闭了，就这么简单。
