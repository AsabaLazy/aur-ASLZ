#!/usr/bin/env python3
"""Synchronize upstream binary releases with local PKGBUILDs and GitHub Releases."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
from typing import Callable, TypeVar
import urllib.request

PACKAGES_DIR = Path("packages")
HTTP_TIMEOUT = 30
CHUNK_SIZE = 65536
USER_AGENT = "aur-aslz-sync/2.0"
REQUIRED_DB_ASSETS = frozenset({"aslz.db", "aslz.db.tar.gz", "aslz.files", "aslz.files.tar.gz"})

AGY_FEED_URL = "https://antigravity-hub-auto-updater-974169037036.us-central1.run.app/releases"
AGY_MANIFEST_URL = (
    "https://antigravity-hub-auto-updater-974169037036.us-central1.run.app/manifest/latest-x64-linux.yml"
)
AGY_MANIFEST_BUILD_RE = re.compile(r"antigravity-hub/[0-9.]+-([0-9]+)/")
AGY_PAGE_URL = "https://antigravity.google/download"
AGY_PAGE_ARTIFACT_RE = re.compile(r"antigravity-hub/([0-9.]+)-([0-9]+)/linux-x64/Antigravity\.tar\.gz")
AGY_DOWNLOAD_TPL = (
    "https://storage.googleapis.com/antigravity-public/antigravity-hub/{version}-{build}/linux-x64/Antigravity.tar.gz"
)

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Package Specifications
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GithubRelease:
    name: str
    upstream: str
    asset_pattern: str


@dataclass(frozen=True, slots=True)
class AntigravityFeed:
    name: str
    overlay_repo: str | None = None
    overlay_path: str | None = None


@dataclass(frozen=True, slots=True)
class ManifestRelease:
    name: str
    manifest_url: str
    download_tpl: str


@dataclass(frozen=True, slots=True)
class LeinuxSource:
    name: str
    upstream: str
    version_ini_url: str
    binary_url: str
    xdb_url: str


type PackageSpec = GithubRelease | AntigravityFeed | ManifestRelease | LeinuxSource

PACKAGES: tuple[PackageSpec, ...] = (
    GithubRelease("astrlink-bin", "Calcium-Ion/AstrLink", r"AstrLink_.*_amd64\.deb"),
    GithubRelease("magpie-bin", "yetone/magpie", r"^magpie-linux-amd64$"),
    GithubRelease("gopeed-bin", "GopeedLab/gopeed", r"Gopeed-.*-linux-amd64\.deb"),
    GithubRelease("gopeed-fluent-bin", "AsabaLazy/gopeed-fluent", r"Gopeed_Fluent_.*_linux_amd64\.deb"),
    GithubRelease("novelmikuer-bin", "AsabaLazy/NovelMikuer", r"NovelMikuer_.*_amd64\.deb"),
    GithubRelease("antigravity-cli-bin", "google-antigravity/antigravity-cli", r"^agy_cli_linux_x64\.tar\.gz$"),
    GithubRelease("antigravity-cli-fix-bin", "google-antigravity/antigravity-cli", r"^agy_cli_linux_x64\.tar\.gz$"),
    GithubRelease("antigravity-cli-512k-bin", "google-antigravity/antigravity-cli", r"^agy_cli_linux_x64\.tar\.gz$"),
    AntigravityFeed("antigravity-bin"),
    AntigravityFeed(
        "antigravity-hans-bin",
        overlay_repo="yuexps/Antigravity-Hans",
        overlay_path="antigravity-hans-overlay.js",
    ),
    ManifestRelease(
        "dimagent-bin",
        manifest_url="https://dimcode.echooai.com/updates/stable/linux/x64/latest-linux.yml",
        download_tpl="https://dimcode.echooai.com/updates/stable/linux/x64/DimAgent-{version}.deb",
    ),
    LeinuxSource(
        "leinux-bin",
        upstream="AsabaLazy/Leinux",
        version_ini_url="https://raw.githubusercontent.com/AsabaLazy/Leinux/main/opt/leigod/config/acc_version.ini",
        binary_url="http://119.3.40.126/acc-gw.router.amd64",
        xdb_url="http://119.3.40.126/ipdatacloud_country.xdb",
    ),
)


# ---------------------------------------------------------------------------
# Thread-Safe Singleflight Cache & HTTP Helpers
# ---------------------------------------------------------------------------


class SingleflightCache:
    """Deduplicates concurrent in-flight work for identical cache keys."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._values: dict[str, object] = {}
        self._key_locks: dict[str, threading.Lock] = {}

    def get_or_compute(self, key: str, fn: Callable[[], T]) -> T:
        with self._guard:
            if key in self._values:
                return self._values[key]  # type: ignore[return-value]
            key_lock = self._key_locks.setdefault(key, threading.Lock())

        with key_lock:
            with self._guard:
                if key in self._values:
                    return self._values[key]  # type: ignore[return-value]
            result = fn()
            with self._guard:
                self._values[key] = result
                self._key_locks.pop(key, None)
            return result


CACHE = SingleflightCache()


def make_headers(url: str) -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT}
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token and url.startswith(("https://github.com/", "https://api.github.com/")):
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers=make_headers(url))
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return resp.read()


def fetch_text(url: str) -> str:
    return CACHE.get_or_compute(f"text:{url}", lambda: fetch_bytes(url).decode("utf-8"))


def fetch_json(url: str) -> object:
    return CACHE.get_or_compute(f"json:{url}", lambda: json.loads(fetch_bytes(url).decode("utf-8")))


def stream_sha256(url: str, logs: list[str] | None = None) -> str:
    def _compute() -> str:
        if logs is not None:
            logs.append(f"  Streaming {url} for sha256...")
        req = urllib.request.Request(url, headers=make_headers(url))
        hasher = hashlib.sha256()
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            while chunk := resp.read(CHUNK_SIZE):
                hasher.update(chunk)
        return hasher.hexdigest()

    return CACHE.get_or_compute(f"sha256:{url}", _compute)


def version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version))


# ---------------------------------------------------------------------------
# PKGBUILD Reader / Writer
# ---------------------------------------------------------------------------


class Pkgbuild:
    def __init__(self, pkg_name: str) -> None:
        self.path = PACKAGES_DIR / pkg_name / "PKGBUILD"
        self.text = self.path.read_text(encoding="utf-8") if self.path.is_file() else ""

    @property
    def exists(self) -> bool:
        return bool(self.text)

    def get(self, key: str) -> str | None:
        m = re.search(rf"^{re.escape(key)}=(.+)", self.text, re.MULTILINE)
        return m.group(1).strip().strip("'\"") if m else None

    def set_var(self, key: str, value: str) -> None:
        self.text = re.sub(rf"^{re.escape(key)}=.*", f"{key}={value}", self.text, flags=re.MULTILINE)

    def set_version(self, version: str, *, bump_pkgrel: bool = False) -> None:
        same_ver = self.get("pkgver") == version
        cur_rel = int(self.get("pkgrel") or "1")
        next_rel = cur_rel + 1 if bump_pkgrel else (cur_rel if same_ver else 1)
        self.set_var("pkgver", version)
        self.set_var("pkgrel", str(next_rel))

    def save(self) -> None:
        tmp = self.path.with_suffix(".PKGBUILD.tmp")
        tmp.write_text(self.text, encoding="utf-8")
        tmp.replace(self.path)


# ---------------------------------------------------------------------------
# Upstream Resolvers
# ---------------------------------------------------------------------------


def resolve_antigravity_release(logs: list[str]) -> tuple[str, str]:
    def _resolve() -> tuple[str, str]:
        feed_ver = feed_build = None
        manifest_ver = manifest_build = None

        try:
            feed = fetch_json(AGY_FEED_URL)
            entry = feed[0] if isinstance(feed, list) else feed["versions"][0]  # type: ignore[index]
            feed_ver = str(entry.get("version", "")).strip()
            feed_build = str(entry.get("execution_id", "")).strip()
            if not (feed_ver and feed_build):
                raise ValueError("version or execution_id missing")
        except Exception as exc:
            logs.append(f"  Warning: release feed unavailable: {exc}")

        try:
            manifest = fetch_text(AGY_MANIFEST_URL)
            ver_m = re.search(r"^version:\s*(.+)", manifest, re.MULTILINE)
            bld_m = AGY_MANIFEST_BUILD_RE.search(manifest)
            manifest_ver = ver_m.group(1).strip() if ver_m else ""
            manifest_build = bld_m.group(1) if bld_m else ""
            if not (manifest_ver and manifest_build):
                raise ValueError("version or build missing")
        except Exception as exc:
            logs.append(f"  Warning: manifest unavailable: {exc}")
            manifest_ver = manifest_build = None

        if feed_ver and feed_build and manifest_ver and manifest_build:
            if version_tuple(manifest_ver) > version_tuple(feed_ver):
                return manifest_ver, manifest_build
            return feed_ver, feed_build
        if feed_ver and feed_build:
            return feed_ver, feed_build
        if manifest_ver and manifest_build:
            return manifest_ver, manifest_build

        page = fetch_text(AGY_PAGE_URL)
        m = AGY_PAGE_ARTIFACT_RE.search(page)
        if not m:
            raise RuntimeError("unable to resolve Antigravity release from feed, manifest, or download page")
        return m.group(1), m.group(2)

    return CACHE.get_or_compute("antigravity_release", _resolve)


def resolve_overlay_commit(repo: str, path: str) -> str:
    def _resolve() -> str:
        commits = fetch_json(f"https://api.github.com/repos/{repo}/commits?path={path}&per_page=1")
        if isinstance(commits, list) and commits:
            return str(commits[0]["sha"])
        head = fetch_json(f"https://api.github.com/repos/{repo}/commits/main")
        return str(head["sha"])  # type: ignore[index]

    return CACHE.get_or_compute(f"overlay:{repo}:{path}", _resolve)


def resolve_github_sha256(upstream: str, tag: str, asset_name: str, download_url: str, logs: list[str]) -> str:
    sums_url = f"https://github.com/{upstream}/releases/download/{tag}/SHA256SUMS"
    try:
        for line in fetch_text(sums_url).splitlines():
            if asset_name in line:
                return line.split()[0]
    except Exception:
        pass
    return stream_sha256(download_url, logs)


# ---------------------------------------------------------------------------
# Per-Package Sync Logic
# ---------------------------------------------------------------------------


def sync_github_release(pkg: GithubRelease, pb: Pkgbuild, force: bool, logs: list[str]) -> bool:
    logs.append(f"Checking {pkg.name} (upstream: {pkg.upstream})...")
    data = fetch_json(f"https://api.github.com/repos/{pkg.upstream}/releases/latest")
    if not isinstance(data, dict) or not data.get("tag_name"):
        return False

    tag = str(data["tag_name"])
    version = tag.lstrip("v")
    if not force and pb.get("pkgver") == version:
        logs.append(f"  [OK] {pkg.name} PKGBUILD is up-to-date ({tag}).")
        return False

    asset = next((a for a in data.get("assets", []) if re.search(pkg.asset_pattern, a["name"])), None)
    if not asset:
        raise RuntimeError(f"no asset matching {pkg.asset_pattern} in {pkg.upstream} {tag}")

    logs.append(f"  [UPDATE] {pkg.name} -> {tag}")
    download_url = str(asset["browser_download_url"])
    sha256 = resolve_github_sha256(pkg.upstream, tag, str(asset["name"]), download_url, logs)

    pb.set_version(version)
    pb.text = re.sub(r"(sha256sums_x86_64=\(\s*')[^']+", rf"\g<1>{sha256}", pb.text, count=1)
    pb.text = re.sub(r'::https://[^"]+', f"::{download_url}", pb.text, count=1)
    pb.save()
    return True


def sync_antigravity_feed(pkg: AntigravityFeed, pb: Pkgbuild, force: bool, logs: list[str]) -> bool:
    logs.append(f"Checking {pkg.name} (Antigravity feed)...")
    version, build = resolve_antigravity_release(logs)

    cur_ver = pb.get("pkgver")
    if cur_ver and version_tuple(version) < version_tuple(cur_ver):
        logs.append(f"  [SKIP] upstream {version} is older than packaged {cur_ver}.")
        return False

    hans_commit = overlay_url = None
    if pkg.overlay_repo and pkg.overlay_path:
        hans_commit = resolve_overlay_commit(pkg.overlay_repo, pkg.overlay_path)
        overlay_url = f"https://raw.githubusercontent.com/{pkg.overlay_repo}/{hans_commit}/{pkg.overlay_path}"

    overlay_changed = bool(hans_commit and pb.get("_hans_commit") != hans_commit)
    rebuild = cur_ver == version and (pb.get("_build") != build or overlay_changed)
    if not force and cur_ver == version and not rebuild:
        logs.append(f"  [OK] {pkg.name} PKGBUILD is up-to-date ({version}, build {build}).")
        return False

    logs.append(f"  [UPDATE] {pkg.name} -> {version} (build {build})")
    download_url = AGY_DOWNLOAD_TPL.format(version=version, build=build)
    sha256 = stream_sha256(download_url, logs)
    overlay_sha256 = stream_sha256(overlay_url, logs) if overlay_url else None

    pb.set_version(version, bump_pkgrel=rebuild)
    pb.set_var("_build", build)
    pb.text = re.sub(r'::https://[^"]+', f"::{download_url}", pb.text, count=1)
    if hans_commit and overlay_sha256:
        pb.set_var("_hans_commit", hans_commit)
        pb.text = re.sub(
            r"sha256sums_x86_64=\(\s*'[^']+'\s*'[^']+'\s*\)",
            f"sha256sums_x86_64=('{sha256}'\n                   '{overlay_sha256}')",
            pb.text,
            count=1,
        )
    else:
        pb.text = re.sub(r"(sha256sums_x86_64=\(\s*')[^']+", rf"\g<1>{sha256}", pb.text, count=1)
    pb.save()
    return True


def sync_manifest_release(pkg: ManifestRelease, pb: Pkgbuild, force: bool, logs: list[str]) -> bool:
    logs.append(f"Checking {pkg.name} (manifest)...")
    manifest = fetch_text(pkg.manifest_url)
    m = re.search(r"^version:\s*(.+)", manifest, re.MULTILINE)
    if not m:
        raise RuntimeError(f"version missing in {pkg.manifest_url}")

    version = m.group(1).strip()
    if not force and pb.get("pkgver") == version:
        logs.append(f"  [OK] {pkg.name} PKGBUILD is up-to-date ({version}).")
        return False

    logs.append(f"  [UPDATE] {pkg.name} -> {version}")
    download_url = pkg.download_tpl.format(version=version)
    sha256 = stream_sha256(download_url, logs)

    pb.set_version(version)
    pb.text = re.sub(r"(sha256sums_x86_64=\(\s*')[^']+", rf"\g<1>{sha256}", pb.text, count=1)
    pb.text = re.sub(r'::https://[^"]+', f"::{download_url}", pb.text, count=1)
    pb.save()
    return True


def sync_leinux_source(pkg: LeinuxSource, pb: Pkgbuild, force: bool, logs: list[str]) -> bool:
    logs.append(f"Checking {pkg.name} (upstream: {pkg.upstream})...")
    with ThreadPoolExecutor(max_workers=3) as pool:
        commit_fut = pool.submit(fetch_json, f"https://api.github.com/repos/{pkg.upstream}/commits/main")
        bin_fut = pool.submit(fetch_bytes, pkg.binary_url)
        xdb_fut = pool.submit(stream_sha256, pkg.xdb_url)

        commit = str(commit_fut.result()["sha"])  # type: ignore[index]
        bin_bytes = bin_fut.result()
        xdb_sha256 = xdb_fut.result()

    bin_sha256 = hashlib.sha256(bin_bytes).hexdigest()
    ver_m = re.search(rb"ipPortProtocolReport\x00([0-9]+(?:\.[0-9]+)+)\x00", bin_bytes)
    if ver_m:
        version = ver_m.group(1).decode("ascii")
    else:
        ini = fetch_text(pkg.version_ini_url)
        ini_m = re.search(r"^(?:version|UPSTREAM_VERSION)=(.+)", ini, re.MULTILINE)
        if not ini_m:
            raise RuntimeError("version missing in acc_version.ini")
        version = ini_m.group(1).strip().strip("'\"")

    cur_ver = pb.get("pkgver")
    sums_changed = (bin_sha256 not in pb.text) or (xdb_sha256 not in pb.text)
    rebuild = cur_ver == version and (pb.get("_commit") != commit or sums_changed)
    if not force and cur_ver == version and not rebuild:
        logs.append(f"  [OK] {pkg.name} PKGBUILD is up-to-date ({version}, {commit[:7]}).")
        return False

    logs.append(f"  [UPDATE] {pkg.name} -> {version} ({commit[:7]})")
    archive_url = f"https://github.com/{pkg.upstream}/archive/{commit}.tar.gz"
    archive_sha256 = stream_sha256(archive_url, logs)

    pb.set_version(version, bump_pkgrel=rebuild)
    pb.set_var("_commit", commit)
    pb.text = re.sub(
        r"sha256sums_x86_64=\(\s*'[^']+'\s*'[^']+'\s*'[^']+'\s*\)",
        f"sha256sums_x86_64=(\n  '{archive_sha256}'\n  '{bin_sha256}'\n  '{xdb_sha256}'\n)",
        pb.text,
        count=1,
    )
    pb.save()
    return True


def sync_package(pkg: PackageSpec, force: bool) -> tuple[str, bool, list[str]]:
    logs: list[str] = []
    pb = Pkgbuild(pkg.name)
    if not pb.exists:
        logs.append(f"  Error: {pb.path} does not exist")
        return pkg.name, False, logs

    try:
        match pkg:
            case GithubRelease():
                updated = sync_github_release(pkg, pb, force, logs)
            case AntigravityFeed():
                updated = sync_antigravity_feed(pkg, pb, force, logs)
            case ManifestRelease():
                updated = sync_manifest_release(pkg, pb, force, logs)
            case LeinuxSource():
                updated = sync_leinux_source(pkg, pb, force, logs)
        return pkg.name, updated, logs
    except Exception as exc:
        logs.append(f"  Error syncing {pkg.name}: {exc}")
        return pkg.name, False, logs


# ---------------------------------------------------------------------------
# Release & Repository Drift Inspection
# ---------------------------------------------------------------------------


def get_release_assets(repo: str) -> set[str]:
    res = subprocess.run(
        ["gh", "release", "view", "repo", "--repo", repo, "--json", "assets", "-q", ".assets[].name"],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        print(f"  Warning: gh release view failed: {res.stderr.strip()}", file=sys.stderr)
        return set()
    return {line.strip() for line in res.stdout.splitlines() if line.strip()}


def get_push_modified_packages() -> set[str]:
    if os.environ.get("GITHUB_EVENT_NAME") != "push":
        return set()
    res = subprocess.run(["git", "diff", "--name-only", "HEAD~1", "HEAD"], capture_output=True, text=True)
    if res.returncode != 0:
        return set()
    touched: set[str] = set()
    for line in res.stdout.splitlines():
        parts = Path(line.strip()).parts
        if len(parts) >= 2 and parts[0] == "packages" and parts[1] != "repo":
            touched.add(parts[1])
    return touched


@dataclass(frozen=True, slots=True)
class RepoState:
    drift: bool
    build_pkgs: list[str]
    obsolete_assets: list[str]
    removed_pkgs: list[str]
    missing_db: bool


def inspect_repo_state(release_assets: set[str], updated_pkgs: set[str], force: bool) -> RepoState:
    missing_db = sorted(REQUIRED_DB_ASSETS - release_assets)
    if missing_db:
        print(f"  [SYNC] Missing repo database asset(s): {', '.join(missing_db)}")

    push_touched = get_push_modified_packages()
    build_pkgs: list[str] = []
    expected_patterns: list[re.Pattern[str]] = []
    active_pkgnames: set[str] = set()

    if PACKAGES_DIR.is_dir():
        for pkg_dir in sorted(PACKAGES_DIR.iterdir()):
            if pkg_dir.name == "repo" or not (pkg_dir / "PKGBUILD").is_file():
                continue
            pb = Pkgbuild(pkg_dir.name)
            pkgname = pb.get("pkgname") or pkg_dir.name
            pkgver = pb.get("pkgver")
            pkgrel = pb.get("pkgrel") or "1"
            active_pkgnames.add(pkgname)
            if not pkgver:
                continue

            pat = re.compile(rf"^{re.escape(pkgname)}-{re.escape(pkgver)}-{re.escape(pkgrel)}-[^-]+\.pkg\.tar\.zst$")
            expected_patterns.append(pat)
            in_release = any(pat.match(asset) for asset in release_assets)

            if force or pkg_dir.name in push_touched or not in_release:
                if not in_release and pkg_dir.name not in updated_pkgs:
                    print(f"  [SYNC] Missing release package: {pkgname}-{pkgver}-{pkgrel}")
                elif pkg_dir.name in push_touched and pkg_dir.name not in updated_pkgs:
                    print(f"  [SYNC] Package modified in push: {pkg_dir.name}")
                build_pkgs.append(pkg_dir.name)

    obsolete_assets: list[str] = []
    removed_pkgs: list[str] = []
    pkg_file_re = re.compile(r"^(.+)-[^-]+-[^-]+-[^-]+\.pkg\.tar\.zst$")

    for asset in sorted(release_assets):
        if asset.endswith(".pkg.tar.zst") and not any(p.match(asset) for p in expected_patterns):
            print(f"  [SYNC] Obsolete release package: {asset}")
            obsolete_assets.append(asset)
            m = pkg_file_re.match(asset)
            if m and m.group(1) not in active_pkgnames and m.group(1) not in removed_pkgs:
                removed_pkgs.append(m.group(1))

    drift = bool(missing_db or build_pkgs or obsolete_assets)
    return RepoState(
        drift=drift,
        build_pkgs=build_pkgs,
        obsolete_assets=obsolete_assets,
        removed_pkgs=removed_pkgs,
        missing_db=bool(missing_db),
    )


def write_github_outputs(state: RepoState, skip: bool) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as f:
        f.write(f"skip={'true' if skip else 'false'}\n")
        f.write(f"build_pkgs={' '.join(state.build_pkgs)}\n")
        f.write(f"obsolete_assets={' '.join(state.obsolete_assets)}\n")
        f.write(f"removed_pkgs={' '.join(state.removed_pkgs)}\n")
        f.write(f"missing_db={'true' if state.missing_db else 'false'}\n")


def main() -> None:
    repo = os.environ.get("GITHUB_REPOSITORY", "AsabaLazy/aur-ASLZ")
    force = os.environ.get("FORCE_BUILD", "false").lower() == "true"

    with ThreadPoolExecutor(max_workers=8) as pool:
        assets_fut = pool.submit(get_release_assets, repo)
        sync_futs = [pool.submit(sync_package, pkg, force) for pkg in PACKAGES]

        updated_pkgs: set[str] = set()
        for fut in sync_futs:
            name, updated, logs = fut.result()
            for line in logs:
                print(line)
            if updated:
                updated_pkgs.add(name)

        release_assets = assets_fut.result()

    state = inspect_repo_state(release_assets, updated_pkgs, force)
    skip = not updated_pkgs and not state.drift and not force
    print(
        f"\nSummary: {len(updated_pkgs)} package(s) updated, "
        f"{len(state.build_pkgs)} package(s) to build ({', '.join(state.build_pkgs) or 'none'}), "
        f"repo drift: {state.drift}. Skip build: {skip}"
    )
    write_github_outputs(state, skip)


if __name__ == "__main__":
    main()
