# aur-ASLZ

English | [简体中文](README_zh.md)

A collection of Arch Linux binary packages maintained by ASLZ.

[![Build Status](https://github.com/AsabaLazy/aur-ASLZ/actions/workflows/aur.yml/badge.svg)](https://github.com/AsabaLazy/aur-ASLZ/actions/workflows/aur.yml)

> Automatically checks and syncs upstream releases every hour.

## Usage

Add the following section to the end of `/etc/pacman.conf`:

```ini
[aslz]
SigLevel = Optional TrustAll
Server = https://github.com/AsabaLazy/aur-ASLZ/releases/download/repo
```

Or run this command in your terminal:

```bash
grep -q '^\[aslz\]' /etc/pacman.conf || printf '\n[aslz]\nSigLevel = Optional TrustAll\nServer = https://github.com/AsabaLazy/aur-ASLZ/releases/download/repo\n' | sudo tee -a /etc/pacman.conf
```

Refresh the package database:

```bash
sudo pacman -Sy
```

Use `sudo pacman -Syy` to force a full database refresh if switching from an older mirror or cache.

## Packages

| Package &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Install Command &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
| :--- | :--- |
| [`astrlink-bin`](https://github.com/Calcium-Ion/AstrLink) | `sudo pacman -S astrlink-bin` |
| [`magpie-bin`](https://github.com/yetone/magpie) | `sudo pacman -S magpie-bin` |
| [`gopeed-bin`](https://github.com/GopeedLab/gopeed) | `sudo pacman -S gopeed-bin` |
| [`gopeed-fluent-bin`](https://github.com/AsabaLazy/gopeed-fluent) | `sudo pacman -S gopeed-fluent-bin` |
| [`novelmikuer-bin`](https://github.com/AsabaLazy/NovelMikuer) | `sudo pacman -S novelmikuer-bin` |
| [`antigravity-cli-bin`](https://github.com/google-antigravity/antigravity-cli) | `sudo pacman -S antigravity-cli-bin` |
| [`antigravity-cli-fix-bin`](https://github.com/google-antigravity/antigravity-cli) | `sudo pacman -S antigravity-cli-fix-bin` |
| [`antigravity-cli-512k-bin`](https://github.com/google-antigravity/antigravity-cli) | `sudo pacman -S antigravity-cli-512k-bin` |
| [`antigravity-bin`](https://antigravity.google/) | `sudo pacman -S antigravity-bin` |
| [`antigravity-hans-bin`](https://github.com/yuexps/Antigravity-Hans) | `sudo pacman -S antigravity-hans-bin` |
| [`dimagent-bin`](https://dimagent.cn) | `sudo pacman -S dimagent-bin` |
| [`leinux-bin`](https://github.com/AsabaLazy/Leinux) | `sudo pacman -S leinux-bin` |

## Q&A

**Q: Why not submit these packages to the official AUR?**

A: I don't really enjoy the feeling of waiting for manual review, though the main reason is simply that I couldn't log into my AUR account when I tried to submit, and their registration channel was closed at the time.
