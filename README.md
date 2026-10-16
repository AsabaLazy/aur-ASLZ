# Arch Linux Packages & Repository

Arch Linux 软件包构建与个人 Pacman 软件源仓库。

## 维护列表

| 软件包                                  | 上游项目                                                        | 说明                            |
| :-------------------------------------- | :-------------------------------------------------------------- | :------------------------------ |
| [`astrlink-bin`](packages/astrlink-bin) | [Calcium-Ion/AstrLink](https://github.com/Calcium-Ion/AstrLink) | AI API Gateway & Desktop Client |

## 安装方式

### 模式一：直接单次安装（不改动系统配置）

```bash
curl -fsSL https://raw.githubusercontent.com/AsabaLazy/aur-packages/main/install.sh | bash
```

### 模式二：添加 Pacman 软件源（支持 `pacman -Syu` 检查更新）

```bash
curl -fsSL https://raw.githubusercontent.com/AsabaLazy/aur-packages/main/install.sh | bash -s -- --repo
```

或手动在 `/etc/pacman.conf` 追加：

```ini
[astrlink]
SigLevel = Optional TrustAll
Server = https://asabalazy.github.io/aur-packages/
```

刷新并安装：

```bash
sudo pacman -Sy astrlink-bin
```

## 新增软件包说明

在 `packages/<包名>/` 下新建目录并放置 `PKGBUILD`，推送至 `main` 分支后，GitHub Actions 会自动编译并同步至软件源数据库。
