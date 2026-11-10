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
from typing import Any, Callable, Literal, TypeVar
import urllib.request

PACKAGES_DIR = Path("packages")
HTTP_TIMEOUT = 30
CHUNK_SIZE = 65536
USER_AGENT = "aur-aslz-sync/2.1"
REQUIRED_DB_ASSETS = frozenset({"aslz.db", "aslz.db.tar.gz", "aslz.files", "aslz.files.tar.gz"})

AGY_FEED_URL = "https://antigravity-hub-auto-updater-974169037036.us-central1.run.app/releases"
AGY_MANIFEST_URL = (
    "https://antigravity-hub-auto-updater-974169037036.us-central1.run.app/manifest/latest-x64-linux.yml"
)
AGY_PAGE_URL = "https://antigravity.google/download"
AGY_DOWNLOAD_TPL = (
    "https://storage.googleapis.com/antigravity-public/antigravity-hub/{version}-{build}/linux-x64/Antigravity.tar.gz"
)

AGY_MANIFEST_BUILD_RE = re.compile(r"antigravity-hub/[0-9.]+-([0-9]+)/")
AGY_PAGE_ARTIFACT_RE = re.compile(r"antigravity-hub/([0-9.]+)-([0-9]+)/linux-x64/Antigravity\.tar\.gz")
YAML_VERSION_RE = re.compile(r"^version:\s*(.+)", re.MULTILINE)
INI_VERSION_RE = re.compile(r"^(?:version|UPSTREAM_VERSION)=(.+)", re.MULTILINE)
BINARY_VERSION_RE = re.compile(rb"ipPortProtocolReport\x00([0-9]+(?:\.[0-9]+)+)\x00")
PKG_ASSET_RE = re.compile(r"^(.+)-[^-]+-[^-]+-[^-]+\.pkg\.tar\.zst$")

T = TypeVar("T")
Status = Literal["OK", "UPDATE", "SKIP", "ERROR"]


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


@dataclass(frozen=True, slots=True)
class SyncResult:
    name: str
    status: Status
    detail: str

    @property
    def updated(self) -> bool:
        return self.status == "UPDATE"


@dataclass(frozen=True, slots=True)
class RepoState:
    drift: bool
    build_pkgs: list[str]
    obsolete_assets: list[str]
    removed_pkgs: list[str]
    missing_db: bool


_cache_lock = threading.Lock()
_cache_values: dict[str, Any] = {}
_cache_inflight: dict[str, threading.Lock] = {}


def once(key: str, compute: Callable[[], T]) -> T:
    """Execute `compute` at most once per `key`, coalescing concurrent callers."""
    with _cache_lock:
        if key in _cache_values:
            return _cache_values[key]
        key_lock = _cache_inflight.setdefault(key, threading.Lock())

    with key_lock:
        with _cache_lock:
            if key in _cache_values:
                return _cache_values[key]
        value = compute()
        with _cache_lock:
            _cache_values[key] = value
            _cache_inflight.pop(key, None)
        return value


def request_headers(url: str) -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT}
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token and url.startswith(("https://github.com/", "https://api.github.com/")):
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers=request_headers(url))
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return resp.read()


def fetch_text(url: str) -> str:
    return once(f"text:{url}", lambda: fetch_bytes(url).decode("utf-8"))


def fetch_json(url: str) -> Any:
    return once(f"json:{url}", lambda: json.loads(fetch_bytes(url).decode("utf-8")))


def sha256_url(url: str) -> str:
    def _stream() -> str:
        req = urllib.request.Request(url, headers=request_headers(url))
        digest = hashlib.sha256()
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            while chunk := resp.read(CHUNK_SIZE):
                digest.update(chunk)
        return digest.hexdigest()

    return once(f"sha256:{url}", _stream)


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", version))


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
        self.text = re.sub(rf"^{re.escape(key)}=.*", f"{key}={value}", self.text, count=1, flags=re.MULTILINE)

    def set_version(self, version: str, *, bump_pkgrel: bool = False) -> None:
        same_ver = self.get("pkgver") == version
        cur_rel = int(self.get("pkgrel") or "1")
        next_rel = cur_rel + 1 if bump_pkgrel else (cur_rel if same_ver else 1)
        self.set_var("pkgver", version)
        self.set_var("pkgrel", str(next_rel))

    def set_url(self, url: str) -> None:
        self.text = re.sub(r'::https://[^"]+', f"::{url}", self.text, count=1)

    def set_primary_sum(self, sha256: str) -> None:
        self.text = re.sub(r"(sha256sums_x86_64=\(\s*')[^']+", rf"\g<1>{sha256}", self.text, count=1)

    def set_all_sums(self, *sums: str) -> None:
        joined = "\n  ".join(f"'{s}'" for s in sums)
        self.text = re.sub(r"sha256sums_x86_64=\([^)]*\)", f"sha256sums_x86_64=(\n  {joined}\n)", self.text, count=1)

    def save(self) -> None:
        tmp = self.path.with_suffix(".PKGBUILD.tmp")
        tmp.write_text(self.text, encoding="utf-8")
        tmp.replace(self.path)


def resolve_antigravity_release() -> tuple[str, str]:
    def _probe() -> tuple[str, str]:
        candidates: list[tuple[str, str]] = []

        try:
            feed = fetch_json(AGY_FEED_URL)
            entry = feed[0] if isinstance(feed, list) else feed["versions"][0]
            ver, bld = str(entry.get("version", "")).strip(), str(entry.get("execution_id", "")).strip()
            if ver and bld:
                candidates.append((ver, bld))
        except Exception:
            pass

        try:
            manifest = fetch_text(AGY_MANIFEST_URL)
            ver_m, bld_m = YAML_VERSION_RE.search(manifest), AGY_MANIFEST_BUILD_RE.search(manifest)
            if ver_m and bld_m:
                candidates.append((ver_m.group(1).strip(), bld_m.group(1)))
        except Exception:
            pass

        if candidates:
            return max(candidates, key=lambda item: version_key(item[0]))

        m = AGY_PAGE_ARTIFACT_RE.search(fetch_text(AGY_PAGE_URL))
        if not m:
            raise RuntimeError("unable to resolve Antigravity release")
        return m.group(1), m.group(2)

    return once("antigravity_release", _probe)


def resolve_overlay_commit(repo: str, path: str) -> str:
    def _probe() -> str:
        commits = fetch_json(f"https://api.github.com/repos/{repo}/commits?path={path}&per_page=1")
        if isinstance(commits, list) and commits:
            return str(commits[0]["sha"])
        return str(fetch_json(f"https://api.github.com/repos/{repo}/commits/main")["sha"])

    return once(f"overlay:{repo}:{path}", _probe)


def resolve_github_sha256(upstream: str, tag: str, asset_name: str, download_url: str) -> str:
    try:
        sums = fetch_text(f"https://github.com/{upstream}/releases/download/{tag}/SHA256SUMS")
        for line in sums.splitlines():
            if asset_name in line:
                return line.split()[0]
    except Exception:
        pass
    return sha256_url(download_url)


def parse_ini_version(text: str) -> str:
    m = INI_VERSION_RE.search(text)
    if not m:
        raise RuntimeError("version missing in acc_version.ini")
    return m.group(1).strip().strip("'\"")


def sync_github(pkg: GithubRelease, pb: Pkgbuild, force: bool) -> SyncResult:
    data = fetch_json(f"https://api.github.com/repos/{pkg.upstream}/releases/latest")
    tag = str(data.get("tag_name", "")) if isinstance(data, dict) else ""
    if not tag:
        return SyncResult(pkg.name, "SKIP", "no upstream release tag")

    version = tag.lstrip("v")
    cur_ver = pb.get("pkgver")
    if not force and cur_ver == version:
        return SyncResult(pkg.name, "OK", tag)

    asset = next((a for a in data.get("assets", []) if re.search(pkg.asset_pattern, a["name"])), None)
    if not asset:
        raise RuntimeError(f"no asset matching {pkg.asset_pattern} in {tag}")

    url = str(asset["browser_download_url"])
    pb.set_version(version)
    pb.set_primary_sum(resolve_github_sha256(pkg.upstream, tag, str(asset["name"]), url))
    pb.set_url(url)
    pb.save()
    return SyncResult(pkg.name, "UPDATE", f"{cur_ver} -> {tag}")


def sync_antigravity(pkg: AntigravityFeed, pb: Pkgbuild, force: bool) -> SyncResult:
    version, build = resolve_antigravity_release()
    cur_ver = pb.get("pkgver")
    if cur_ver and version_key(version) < version_key(cur_ver):
        return SyncResult(pkg.name, "SKIP", f"upstream {version} < {cur_ver}")

    hans_commit = resolve_overlay_commit(pkg.overlay_repo, pkg.overlay_path) if pkg.overlay_repo and pkg.overlay_path else None
    overlay_changed = bool(hans_commit and pb.get("_hans_commit") != hans_commit)
    rebuild = cur_ver == version and (pb.get("_build") != build or overlay_changed)

    if not force and cur_ver == version and not rebuild:
        return SyncResult(pkg.name, "OK", f"{version} ({build})")

    url = AGY_DOWNLOAD_TPL.format(version=version, build=build)
    main_sum = sha256_url(url)

    pb.set_version(version, bump_pkgrel=rebuild)
    pb.set_var("_build", build)
    pb.set_url(url)
    if hans_commit and pkg.overlay_repo and pkg.overlay_path:
        overlay_url = f"https://raw.githubusercontent.com/{pkg.overlay_repo}/{hans_commit}/{pkg.overlay_path}"
        pb.set_var("_hans_commit", hans_commit)
        pb.set_all_sums(main_sum, sha256_url(overlay_url))
    else:
        pb.set_primary_sum(main_sum)
    pb.save()
    return SyncResult(pkg.name, "UPDATE", f"{cur_ver} -> {version} ({build})")


def sync_manifest(pkg: ManifestRelease, pb: Pkgbuild, force: bool) -> SyncResult:
    m = YAML_VERSION_RE.search(fetch_text(pkg.manifest_url))
    if not m:
        raise RuntimeError(f"version missing in {pkg.manifest_url}")

    version = m.group(1).strip()
    cur_ver = pb.get("pkgver")
    if not force and cur_ver == version:
        return SyncResult(pkg.name, "OK", version)

    url = pkg.download_tpl.format(version=version)
    pb.set_version(version)
    pb.set_primary_sum(sha256_url(url))
    pb.set_url(url)
    pb.save()
    return SyncResult(pkg.name, "UPDATE", f"{cur_ver} -> {version}")


def sync_leinux(pkg: LeinuxSource, pb: Pkgbuild, force: bool) -> SyncResult:
    with ThreadPoolExecutor(max_workers=3) as pool:
        commit_fut = pool.submit(fetch_json, f"https://api.github.com/repos/{pkg.upstream}/commits/main")
        bin_fut = pool.submit(fetch_bytes, pkg.binary_url)
        xdb_fut = pool.submit(sha256_url, pkg.xdb_url)
        commit, bin_bytes, xdb_sum = str(commit_fut.result()["sha"]), bin_fut.result(), xdb_fut.result()

    bin_sum = hashlib.sha256(bin_bytes).hexdigest()
    ver_m = BINARY_VERSION_RE.search(bin_bytes)
    version = ver_m.group(1).decode("ascii") if ver_m else parse_ini_version(fetch_text(pkg.version_ini_url))

    cur_ver = pb.get("pkgver")
    sums_changed = (bin_sum not in pb.text) or (xdb_sum not in pb.text)
    rebuild = cur_ver == version and (pb.get("_commit") != commit or sums_changed)

    if not force and cur_ver == version and not rebuild:
        return SyncResult(pkg.name, "OK", f"{version} ({commit[:7]})")

    archive_sum = sha256_url(f"https://github.com/{pkg.upstream}/archive/{commit}.tar.gz")
    pb.set_version(version, bump_pkgrel=rebuild)
    pb.set_var("_commit", commit)
    pb.set_all_sums(archive_sum, bin_sum, xdb_sum)
    pb.save()
    return SyncResult(pkg.name, "UPDATE", f"{version} ({commit[:7]})")


def sync_package(pkg: PackageSpec, force: bool) -> SyncResult:
    pb = Pkgbuild(pkg.name)
    if not pb.exists:
        return SyncResult(pkg.name, "ERROR", f"{pb.path} not found")
    try:
        match pkg:
            case GithubRelease():
                return sync_github(pkg, pb, force)
            case AntigravityFeed():
                return sync_antigravity(pkg, pb, force)
            case ManifestRelease():
                return sync_manifest(pkg, pb, force)
            case LeinuxSource():
                return sync_leinux(pkg, pb, force)
    except Exception as exc:
        return SyncResult(pkg.name, "ERROR", str(exc))


def get_release_assets(repo: str) -> set[str]:
    res = subprocess.run(
        ["gh", "release", "view", "repo", "--repo", repo, "--json", "assets", "-q", ".assets[].name"],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        print(f"Warning: gh release view failed: {res.stderr.strip()}", file=sys.stderr)
        return set()
    return {line.strip() for line in res.stdout.splitlines() if line.strip()}


def get_push_modified_packages() -> set[str]:
    if os.environ.get("GITHUB_EVENT_NAME") != "push":
        return set()
    res = subprocess.run(["git", "diff", "--name-only", "HEAD~1", "HEAD"], capture_output=True, text=True)
    if res.returncode != 0:
        return set()
    return {
        parts[1]
        for line in res.stdout.splitlines()
        if len(parts := Path(line.strip()).parts) >= 2 and parts[0] == "packages" and parts[1] != "repo"
    }


def inspect_repo_state(release_assets: set[str], updated_pkgs: set[str], force: bool) -> RepoState:
    missing_db = sorted(REQUIRED_DB_ASSETS - release_assets)
    push_touched = get_push_modified_packages()
    build_pkgs: list[str] = []
    expected: list[re.Pattern[str]] = []
    active_names: set[str] = set()

    if PACKAGES_DIR.is_dir():
        for pkg_dir in sorted(PACKAGES_DIR.iterdir()):
            if pkg_dir.name == "repo" or not (pkg_dir / "PKGBUILD").is_file():
                continue
            pb = Pkgbuild(pkg_dir.name)
            pkgname = pb.get("pkgname") or pkg_dir.name
            pkgver = pb.get("pkgver")
            pkgrel = pb.get("pkgrel") or "1"
            active_names.add(pkgname)
            if not pkgver:
                continue

            pat = re.compile(rf"^{re.escape(pkgname)}-{re.escape(pkgver)}-{re.escape(pkgrel)}-[^-]+\.pkg\.tar\.zst$")
            expected.append(pat)
            in_release = any(pat.match(a) for a in release_assets)
            if force or pkg_dir.name in updated_pkgs or pkg_dir.name in push_touched or not in_release:
                build_pkgs.append(pkg_dir.name)

    obsolete: list[str] = []
    removed: list[str] = []
    for asset in sorted(release_assets):
        if asset.endswith(".pkg.tar.zst") and not any(p.match(asset) for p in expected):
            obsolete.append(asset)
            m = PKG_ASSET_RE.match(asset)
            if m and m.group(1) not in active_names and m.group(1) not in removed:
                removed.append(m.group(1))

    drift = bool(missing_db or build_pkgs or obsolete)
    return RepoState(drift, build_pkgs, obsolete, removed, bool(missing_db))


def write_github_outputs(state: RepoState, skip: bool) -> None:
    if output_path := os.environ.get("GITHUB_OUTPUT"):
        with open(output_path, "a", encoding="utf-8") as f:
            f.write(
                f"skip={'true' if skip else 'false'}\n"
                f"build_pkgs={' '.join(state.build_pkgs)}\n"
                f"obsolete_assets={' '.join(state.obsolete_assets)}\n"
                f"removed_pkgs={' '.join(state.removed_pkgs)}\n"
                f"missing_db={'true' if state.missing_db else 'false'}\n"
            )


def main() -> None:
    repo = os.environ.get("GITHUB_REPOSITORY", "AsabaLazy/aur-ASLZ")
    force = os.environ.get("FORCE_BUILD", "false").lower() == "true"

    with ThreadPoolExecutor(max_workers=8) as pool:
        assets_fut = pool.submit(get_release_assets, repo)
        results = list(pool.map(lambda pkg: sync_package(pkg, force), PACKAGES))
        release_assets = assets_fut.result()

    for r in results:
        print(f"[{r.status:<6}] {r.name:<26} {r.detail}")

    updated_pkgs = {r.name for r in results if r.updated}
    state = inspect_repo_state(release_assets, updated_pkgs, force)
    skip = not updated_pkgs and not state.drift and not force

    print(
        f"\nSummary: {len(updated_pkgs)} updated, "
        f"{len(state.build_pkgs)} to build ({', '.join(state.build_pkgs) or 'none'}), "
        f"obsolete: {len(state.obsolete_assets)}, skip: {skip}"
    )
    write_github_outputs(state, skip)


if __name__ == "__main__":
    main()
