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
        "name": "gopeed-fluent-bin",
        "upstream": "AsabaLazy/gopeed-fluent",
        "asset_pattern": r"Gopeed_Fluent_.*_linux_amd64\.deb",
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
        "name": "antigravity-bin",
        # Official feed used by antigravity.google/download. The manifest is the
        # app's electron-updater config; the page is a last-resort fallback.
        "release_feed": "https://antigravity-hub-auto-updater-974169037036.us-central1.run.app/releases",
        "manifest_url": "https://antigravity-hub-auto-updater-974169037036.us-central1.run.app/manifest/latest-x64-linux.yml",
        "manifest_build_regex": r"antigravity-hub/[0-9.]+-([0-9]+)/",
        "download_page_url": "https://antigravity.google/download",
        "page_artifact_regex": r"antigravity-hub/([0-9.]+)-([0-9]+)/linux-x64/Antigravity\.tar\.gz",
        "download_url_template": "https://storage.googleapis.com/antigravity-public/antigravity-hub/{version}-{build}/linux-x64/Antigravity.tar.gz",
    },
    {
        "name": "dimagent-bin",
        "manifest_url": "https://dimcode.echooai.com/updates/stable/linux/x64/latest-linux.yml",
        "download_url_template": "https://dimcode.echooai.com/updates/stable/linux/x64/DimAgent-{version}.deb",
    },
]

def make_headers(url):
    headers = {"User-Agent": "aur-monorepo-sync"}
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token and (url.startswith("https://github.com/") or url.startswith("https://api.github.com/")):
        headers["Authorization"] = f"Bearer {token}"
    return headers

def fetch_text(url):
    req = urllib.request.Request(url, headers=make_headers(url))
    with urllib.request.urlopen(req) as resp:
        return resp.read().decode()

def version_tuple(version):
    return tuple(int(part) for part in re.findall(r"\d+", version))

REQUIRED_DB_ASSETS = {"aslz.db", "aslz.db.tar.gz", "aslz.files", "aslz.files.tar.gz"}

def get_pkgbuild_var(name, var):
    path = f"packages/{name}/PKGBUILD"
    if not os.path.exists(path):
        return None
    with open(path) as f:
        m = re.search(rf"^{re.escape(var)}=(.+)", f.read(), re.MULTILINE)
    return m.group(1).strip().strip("'\"") if m else None

def get_release_assets(repo):
    if not repo:
        return None
    res = subprocess.run(
        [
            "gh", "release", "view", "repo", "--repo", repo,
            "--json", "assets", "-q", ".assets[].name",
        ],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        print(f"  Warning: gh release view failed: {res.stderr.strip()}", file=sys.stderr)
        return set()
    return {line.strip() for line in res.stdout.splitlines() if line.strip()}

def already_published(release_assets, name, version, force):
    if release_assets is None or force:
        return False
    pattern = re.compile(rf"^{re.escape(name)}-{re.escape(version)}-[^-]+-[^-]+\.pkg\.tar\.zst$")
    return any(pattern.match(asset) for asset in release_assets)

def has_repo_drift(release_assets):
    if release_assets is None:
        return True

    drift = False
    missing_db = sorted(REQUIRED_DB_ASSETS - release_assets)
    if missing_db:
        print(f"  [SYNC] Missing repo database asset(s): {', '.join(missing_db)}")
        drift = True

    expected_patterns = []
    if os.path.isdir("packages"):
        for entry in sorted(os.listdir("packages")):
            pkgbuild_path = os.path.join("packages", entry, "PKGBUILD")
            if entry == "repo" or not os.path.isfile(pkgbuild_path):
                continue
            pkgname = get_pkgbuild_var(entry, "pkgname") or entry
            pkgver = get_pkgbuild_var(entry, "pkgver")
            pkgrel = get_pkgbuild_var(entry, "pkgrel") or "1"
            if not pkgver:
                continue
            pat = re.compile(
                rf"^{re.escape(pkgname)}-{re.escape(pkgver)}-{re.escape(pkgrel)}-[^-]+\.pkg\.tar\.zst$"
            )
            expected_patterns.append(pat)
            if not any(pat.match(a) for a in release_assets):
                print(f"  [SYNC] Missing release package: {pkgname}-{pkgver}-{pkgrel}")
                drift = True

    for asset in sorted(release_assets):
        if asset.endswith(".pkg.tar.zst") and not any(p.match(asset) for p in expected_patterns):
            print(f"  [SYNC] Obsolete release package: {asset}")
            drift = True

    return drift

def stream_sha256(download_url):
    print(f"  Streaming {download_url} to calculate sha256...")
    req = urllib.request.Request(download_url, headers=make_headers(download_url))
    hasher = hashlib.sha256()
    with urllib.request.urlopen(req) as resp:
        while chunk := resp.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()

def resolve_feed_release(pkg):
    feed_version = feed_build = None
    manifest_version = manifest_build = None

    try:
        feed = json.loads(fetch_text(pkg["release_feed"]))
        release = feed[0] if isinstance(feed, list) else feed["versions"][0]
        feed_version = str(release.get("version", "")).strip()
        feed_build = str(release.get("execution_id", "")).strip()
        if not feed_version or not feed_build:
            raise ValueError("version or execution_id missing")
    except Exception as e:
        print(f"  Warning: release feed unavailable: {e}", file=sys.stderr)

    manifest_url = pkg.get("manifest_url")
    if manifest_url:
        try:
            manifest_text = fetch_text(manifest_url)
            m = re.search(r"^version:\s*(.+)", manifest_text, re.MULTILINE)
            manifest_version = m.group(1).strip() if m else ""
            build_regex = pkg.get("manifest_build_regex")
            m = re.search(build_regex, manifest_text) if build_regex else None
            manifest_build = m.group(1) if m else ""
            if not manifest_version or not manifest_build:
                raise ValueError("version or build missing")
        except Exception as e:
            print(f"  Warning: manifest unavailable: {e}", file=sys.stderr)
            manifest_version = manifest_build = None

    if feed_version and feed_build and manifest_version and manifest_build:
        if version_tuple(manifest_version) > version_tuple(feed_version):
            print(f"  Warning: manifest {manifest_version} is newer than feed {feed_version}")
            version, build = manifest_version, manifest_build
        else:
            version, build = feed_version, feed_build
            if manifest_version != feed_version:
                print(f"  Warning: feed {feed_version} is newer than manifest {manifest_version}")
            elif manifest_build != feed_build:
                print("  Warning: feed and manifest build ids differ; using feed")
    elif feed_version and feed_build:
        version, build = feed_version, feed_build
    elif manifest_version and manifest_build:
        print("  Warning: using manifest because the release feed is unavailable")
        version, build = manifest_version, manifest_build
    else:
        page_url = pkg.get("download_page_url")
        if not page_url:
            raise RuntimeError("no release source available")
        page_text = fetch_text(page_url)
        page_regex = pkg.get("page_artifact_regex")
        m = re.search(page_regex, page_text) if page_regex else None
        if not m:
            raise RuntimeError("could not parse download page")
        version, build = m.group(1), m.group(2)
        print("  Warning: using download page fallback (may be stale)")

    return version, build

def get_asset_sha256(upstream, tag, asset_name, download_url):
    # 1. 尝试从上游发布的 SHA256SUMS 文件中匹配
    sums_url = f"https://github.com/{upstream}/releases/download/{tag}/SHA256SUMS"
    try:
        sums_text = fetch_text(sums_url)
        for line in sums_text.splitlines():
            if asset_name in line:
                return line.split()[0]
    except Exception:
        pass

    # 2. 回退：直接流式下载二进制计算 sha256
    return stream_sha256(download_url)

def update_pkgbuild(name, version, sha256, download_url, build=None, bump_pkgrel=False):
    pkgbuild_path = f"packages/{name}/PKGBUILD"
    if not os.path.exists(pkgbuild_path):
        print(f"  Error: {pkgbuild_path} does not exist", file=sys.stderr)
        return False

    with open(pkgbuild_path, "r") as f:
        content = f.read()

    old_ver = re.search(r"^pkgver=(.+)", content, re.MULTILINE)
    same_ver = bool(old_ver and old_ver.group(1).strip().strip("'\"") == version)
    content = re.sub(r"^pkgver=.*", f"pkgver={version}", content, flags=re.MULTILINE)
    m = re.search(r"^pkgrel=(\d+)", content, re.MULTILINE)
    if bump_pkgrel:
        pkgrel = str(int(m.group(1)) + 1) if m else "1"
    elif same_ver and m:
        pkgrel = m.group(1)
    else:
        pkgrel = "1"
    content = re.sub(r"^pkgrel=.*", f"pkgrel={pkgrel}", content, flags=re.MULTILINE)
    if build is not None:
        content = re.sub(r"^_build=.*", f"_build={build}", content, flags=re.MULTILINE)
    content = re.sub(r"(sha256sums_x86_64=\(')[^']+", rf"\g<1>{sha256}", content)
    content = re.sub(r'::https://[^"]+', rf'::{download_url}', content)

    with open(pkgbuild_path, "w") as f:
        f.write(content)
    return True

def main():
    repo = os.environ.get("GITHUB_REPOSITORY", "AsabaLazy/aur-ASLZ")
    force = os.environ.get("FORCE_BUILD", "false").lower() == "true"
    release_assets = get_release_assets(repo)
    updated_packages = []

    for pkg in PACKAGES:
        name = pkg["name"]

        # 处理基于自定义 release feed 的软件包
        if "release_feed" in pkg:
            print(f"Checking {name} (release feed)...")
            try:
                version, build = resolve_feed_release(pkg)
            except Exception as e:
                print(f"  Error resolving release for {name}: {e}", file=sys.stderr)
                continue

            current_version = get_pkgbuild_var(name, "pkgver")
            current_build = get_pkgbuild_var(name, "_build")
            if current_version and version_tuple(version) < version_tuple(current_version):
                print(f"  [SKIP] {name} source reports {version}, older than packaged {current_version}.")
                continue

            rebuild = current_version == version and current_build != build
            download_url = pkg["download_url_template"].format(version=version, build=build)

            if not rebuild and already_published(release_assets, name, version, force):
                print(f"  [OK] {name} is up-to-date with {version}.")
                continue

            print(f"  [UPDATE] {name} needs build for {version} (build {build}).")
            try:
                sha256 = stream_sha256(download_url)
            except Exception as e:
                print(f"  Error calculating checksum for {download_url}: {e}", file=sys.stderr)
                continue

            if update_pkgbuild(name, version, sha256, download_url, build=build, bump_pkgrel=rebuild):
                updated_packages.append(name)
            continue

        # 处理基于独立 YAML / JSON Manifest 的软件包
        if "manifest_url" in pkg:
            print(f"Checking {name} (manifest: {pkg['manifest_url']})...")
            try:
                manifest_text = fetch_text(pkg["manifest_url"])
                m = re.search(r"^version:\s*(.+)", manifest_text, re.MULTILINE)
                if not m:
                    print(f"  Error: version not found in manifest for {name}", file=sys.stderr)
                    continue
                version = m.group(1).strip()
            except Exception as e:
                print(f"  Error fetching manifest for {name}: {e}", file=sys.stderr)
                continue

            download_url = pkg["download_url_template"].format(version=version)

            if already_published(release_assets, name, version, force):
                print(f"  [OK] {name} is up-to-date with {version}.")
                continue

            print(f"  [UPDATE] {name} needs build for {version}.")
            try:
                sha256 = stream_sha256(download_url)
            except Exception as e:
                print(f"  Error calculating checksum for {download_url}: {e}", file=sys.stderr)
                continue

            if update_pkgbuild(name, version, sha256, download_url):
                updated_packages.append(name)
            continue

        # 处理基于 GitHub Releases 的软件包
        upstream = pkg["upstream"]
        print(f"Checking {name} (upstream: {upstream})...")

        api_url = f"https://api.github.com/repos/{upstream}/releases/latest"
        req = urllib.request.Request(api_url, headers=make_headers(api_url))
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
        if already_published(release_assets, name, version, force):
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

    repo_drift = has_repo_drift(release_assets)
    skip = len(updated_packages) == 0 and not repo_drift and not force
    print(
        f"\nSummary: {len(updated_packages)} package(s) updated, "
        f"repo drift: {repo_drift}. Skip build: {skip}"
    )

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"skip={'true' if skip else 'false'}\n")

if __name__ == "__main__":
    main()
