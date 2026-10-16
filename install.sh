#!/usr/bin/env bash
set -euo pipefail

REPO="AsabaLazy/aur-packages"
ENABLE_REPO=false

for arg in "$@"; do
  case "$arg" in
    --repo|-r)
      ENABLE_REPO=true
      ;;
  esac
done

if [ "$ENABLE_REPO" = true ]; then
  if ! grep -q "^\[astrlink\]" /etc/pacman.conf 2>/dev/null; then
    echo -e "\n[astrlink]\nSigLevel = Optional TrustAll\nServer = https://asabalazy.github.io/aur-packages/" | sudo tee -a /etc/pacman.conf > /dev/null
    echo "已添加 [astrlink] 软件源到 /etc/pacman.conf"
  fi
  echo "正在通过 pacman 安装 astrlink-bin..."
  sudo pacman -Sy --noconfirm astrlink-bin
  echo "安装完成。后续可使用 sudo pacman -Syu 检查更新。"
else
  echo "正在获取最新版本下载链接..."
  URL=$(curl -sL "https://api.github.com/repos/${REPO}/releases/latest" | grep -o 'https://[^"]*pkg\.tar\.zst' | head -n 1)
  if [ -z "$URL" ]; then
    echo "未找到安装包下载链接" >&2
    exit 1
  fi
  TMP_PKG=$(mktemp --suffix=.pkg.tar.zst)
  echo "正在下载软件包..."
  curl -fL --progress-bar "$URL" -o "$TMP_PKG"
  echo "正在安装软件包..."
  sudo pacman -U --noconfirm "$TMP_PKG"
  rm -f "$TMP_PKG"
  echo "安装完成。"
fi
