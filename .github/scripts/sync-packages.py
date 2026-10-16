#!/usr/bin/env python3
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request

PACKAGES = [
    {
        "name": "astrlink-bin",
        "upstream": "Calcium-Ion/AstrLink",
        "asset_pattern": r"AstrLink_.*_amd64\.deb",
    },
    {
        "name": "magpie-bin",
        "upstream": "yetone/magpie",
        "asset_pattern": r"^magpie-linux-amd64$",
    },
    {
        "name": "gopeed-bin",
        "upstream": "GopeedLab/gopeed",
        "asset_pattern": r"Gopeed-.*-linux-amd64\.deb",
    },
    {
        "name": "novelmikuer-bin",
        "upstream": "AsabaLazy/NovelMikuer",
        "asset_pattern": r"NovelMikuer_.*_amd64\.deb",
    },
    {
        "name": "antigravity-cli-bin",
        "upstream": "google-antigravity/antigravity-cli",
        "asset_pattern": r"^agy_cli_linux_x64\.tar\.gz$",
    },
    {
        "name": "dimagent-bin",
        "manifest_url": "https://dimcode.echooai.com/updates/stable/linux/x64/latest-linux.yml",
        "download_url_template": "https://dimcode.echooai.com/updates/stable/linux/x64/DimAgent-{version}.deb",
    },
]

def make_headers():
    headers = {"User-Agent": "aur-monorepo-sync"}
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers

def get_asset_sha256(upstream, tag, asset_name, download_url):
    # 1. 尝试从上游发布的 SHA256SUMS 文件中匹配
    sums_url = f"https://github.com/{upstream}/releases/download/{tag}/SHA256SUMS"
    try:
        req = urllib.request.Request(sums_url, headers=make_headers())
        sums_text = urllib.request.urlopen(req).read().decode()
        for line in sums_text.splitlines():
            if asset_name in line:
                return line.split()[0]
    except Exception:
        pass

    # 2. 回退：直接流式下载二进制计算 sha256
    print(f"  Streaming {download_url} to calculate sha256...")
    req = urllib.request.Request(download_url, headers=make_headers())
    hasher = hashlib.sha256()
    with urllib.request.urlopen(req) as resp:
        while chunk := resp.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()

def update_pkgbuild(name, version, sha256, download_url):
    pkgbuild_path = f"packages/{name}/PKGBUILD"
    if not os.path.exists(pkgbuild_path):
        print(f"  Error: {pkgbuild_path} does not exist", file=sys.stderr)
        return False

    with open(pkgbuild_path, "r") as f:
        content = f.read()

    content = re.sub(r"^pkgver=.*", f"pkgver={version}", content, flags=re.MULTILINE)
    content = re.sub(r"^pkgrel=.*", "pkgrel=1", content, flags=re.MULTILINE)
    content = re.sub(r"(sha256sums_x86_64=\(')[0-9a-fA-F]+", rf"\g<1>{sha256}", content)
    content = re.sub(r'::https://[^"]+', rf'::{download_url}', content)

    with open(pkgbuild_path, "w") as f:
        f.write(content)
    return True

def main():
    repo = os.environ.get("GITHUB_REPOSITORY", "AsabaLazy/aur-ASLZ")
    force = os.environ.get("FORCE_BUILD", "false").lower() == "true"
    updated_packages = []

    for pkg in PACKAGES:
        name = pkg["name"]

        # 处理基于独立 YAML / JSON Manifest 的软件包
        if "manifest_url" in pkg:
            print(f"Checking {name} (manifest: {pkg['manifest_url']})...")
            try:
                req = urllib.request.Request(pkg["manifest_url"], headers=make_headers())
                manifest_text = urllib.request.urlopen(req).read().decode()
                m = re.search(r"^version:\s*(.+)", manifest_text, re.MULTILINE)
                if not m:
                    print(f"  Error: version not found in manifest for {name}", file=sys.stderr)
                    continue
                version = m.group(1).strip()
            except Exception as e:
                print(f"  Error fetching manifest for {name}: {e}", file=sys.stderr)
                continue

            download_url = pkg["download_url_template"].format(version=version)

            if repo and not force:
                res = subprocess.run(
                    [
                        "gh", "release", "view", name, "--repo", repo,
                        "--json", "assets", "-q",
                        f'.assets[] | select(.name | startswith("{name}-{version}-")) | .name',
                    ],
                    capture_output=True,
                    text=True,
                )
                if res.stdout.strip():
                    print(f"  [OK] {name} is up-to-date with {version}.")
                    continue

            print(f"  [UPDATE] {name} needs build for {version}.")
            print(f"  Streaming {download_url} to calculate sha256...")
            try:
                req = urllib.request.Request(download_url, headers=make_headers())
                hasher = hashlib.sha256()
                with urllib.request.urlopen(req) as resp:
                    while chunk := resp.read(65536):
                        hasher.update(chunk)
                sha256 = hasher.hexdigest()
            except Exception as e:
                print(f"  Error calculating checksum for {download_url}: {e}", file=sys.stderr)
                continue

            if update_pkgbuild(name, version, sha256, download_url):
                updated_packages.append(name)
            continue

        # 处理基于 GitHub Releases 的软件包
        upstream = pkg["upstream"]
        print(f"Checking {name} (upstream: {upstream})...")

        req = urllib.request.Request(
            f"https://api.github.com/repos/{upstream}/releases/latest",
            headers=make_headers(),
        )
        try:
            data = json.loads(urllib.request.urlopen(req).read())
        except Exception as e:
            print(f"  Error fetching upstream for {name}: {e}", file=sys.stderr)
            continue

        tag = data.get("tag_name", "")
        if not tag:
            continue
        version = tag.lstrip("v")

        # 检查是否已打包发布过
        if repo and not force:
            res = subprocess.run(
                [
                    "gh", "release", "view", name, "--repo", repo,
                    "--json", "assets", "-q",
                    f'.assets[] | select(.name | startswith("{name}-{version}-")) | .name',
                ],
                capture_output=True,
                text=True,
            )
            if res.stdout.strip():
                print(f"  [OK] {name} is up-to-date with {tag}.")
                continue

        print(f"  [UPDATE] {name} needs build for {tag}.")

        # 匹配对应平台的 asset
        asset_info = None
        for a in data.get("assets", []):
            if re.search(pkg["asset_pattern"], a["name"]):
                asset_info = a
                break

        if not asset_info:
            print(f"  Error: No asset matching {pkg['asset_pattern']} found in {upstream} {tag}", file=sys.stderr)
            continue

        asset_name = asset_info["name"]
        download_url = asset_info["browser_download_url"]

        try:
            sha256 = get_asset_sha256(upstream, tag, asset_name, download_url)
        except Exception as e:
            print(f"  Error calculating checksum for {asset_name}: {e}", file=sys.stderr)
            continue

        if not sha256:
            print(f"  Error: sha256 for {asset_name} could not be determined", file=sys.stderr)
            continue

        if update_pkgbuild(name, version, sha256, download_url):
            updated_packages.append(name)

    skip = len(updated_packages) == 0 and not force
    print(f"\nSummary: {len(updated_packages)} package(s) updated. Skip build: {skip}")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"skip={'true' if skip else 'false'}\n")

if __name__ == "__main__":
    main()
