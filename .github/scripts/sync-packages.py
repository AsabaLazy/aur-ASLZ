#!/usr/bin/env python3
"""Auto-discover and synchronize Arch binary packages from self-describing PKGBUILDs."""

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
USER_AGENT = "aur-aslz-sync/3.0"
REQUIRED_DB_ASSETS = frozenset({"aslz.db", "aslz.db.tar.gz", "aslz.files", "aslz.files.tar.gz"})

GH_RELEASE_URL_RE = re.compile(r"^https://github\.com/([^/]+/[^/]+)/releases/download/[^/]+/(.+)$")
GH_ARCHIVE_URL_RE = re.compile(r"^https://github\.com/([^/]+/[^/]+)/archive/[0-9a-f]{40}\.tar\.gz$")
GH_RAW_COMMIT_URL_RE = re.compile(r"^https://raw\.githubusercontent\.com/([^/]+/[^/]+)/[0-9a-f]{40}/(.+)$")
ASSET_VER_TOKEN_RE = re.compile(
    r"v?\d+\.\d+[0-9A-Za-z._-]*?(?=[._-](?:linux|amd64|x86_64|x64)|\.(?:deb|tar\.|AppImage|zip))"
)
MANIFEST_BUILD_RE = re.compile(r"/[0-9.]+-([0-9]+)/")
YAML_VERSION_RE = re.compile(r"^version:\s*(.+)", re.MULTILINE)
INI_VERSION_RE = re.compile(r"^(?:version|UPSTREAM_VERSION)=(.+)", re.MULTILINE)
BINARY_VERSION_RE = re.compile(rb"ipPortProtocolReport\x00([0-9]+(?:\.[0-9]+)+)\x00")
PKG_ASSET_RE = re.compile(r"^(.+)-[^-]+-[^-]+-[^-]+\.pkg\.tar\.zst$")

T = TypeVar("T")
Status = Literal["OK", "UPDATE", "SKIP", "ERROR"]


@dataclass(frozen=True, slots=True)
class GithubReleaseSpec:
    name: str
    upstream: str
    asset_pattern: str


@dataclass(frozen=True, slots=True)
class FeedReleaseSpec:
    name: str
    feed_url: str
    manifest_url: str | None
    download_tpl: str
    overlay_repo: str | None = None
    overlay_path: str | None = None


@dataclass(frozen=True, slots=True)
class ManifestReleaseSpec:
    name: str
    manifest_url: str
    download_tpl: str


@dataclass(frozen=True, slots=True)
class CommitHybridSpec:
    name: str
    upstream: str
    companion_urls: tuple[str, ...]
    version_ini_path: str | None = None


type PackageSpec = GithubReleaseSpec | FeedReleaseSpec | ManifestReleaseSpec | CommitHybridSpec


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
        self.name = pkg_name
        self.dir = PACKAGES_DIR / pkg_name
        self.path = self.dir / "PKGBUILD"
        self.srcinfo_path = self.dir / ".SRCINFO"
        self.text = self.path.read_text(encoding="utf-8") if self.path.is_file() else ""

    @property
    def exists(self) -> bool:
        return bool(self.text)

    def get(self, key: str) -> str | None:
        m = re.search(rf"^{re.escape(key)}=(.+)", self.text, re.MULTILINE)
        return m.group(1).strip().strip("'\"") if m else None

    def source_urls(self) -> list[str]:
        entries: list[str] = []
        if self.srcinfo_path.is_file():
            for line in self.srcinfo_path.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("source_x86_64 ="):
                    entries.append(line.split("=", 1)[1].strip())
        else:
            m = re.search(r"source_x86_64=\(([^)]*)\)", self.text, re.DOTALL)
            if m:
                raw = m.group(1)
                for k in ("pkgname", "pkgver", "url", "_commit", "_hans_commit", "_build"):
                    if val := self.get(k):
                        raw = raw.replace(f"${{{k}}}", val).replace(f"${k}", val)
                entries.extend(tok.strip("'\"") for tok in re.findall(r"""['"][^'"]+['"]|\S+""", raw))

        urls: list[str] = []
        for entry in entries:
            target = entry.split("::", 1)[-1]
            if target.startswith(("https://", "http://")):
                urls.append(target)
        return urls

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


def derive_asset_pattern(filename: str) -> str:
    m = ASSET_VER_TOKEN_RE.search(filename)
    if not m:
        return f"^{re.escape(filename)}$"
    prefix = re.escape(filename[: m.start()])
    suffix = re.escape(filename[m.end() :])
    return f"^{prefix}.*{suffix}$"


def discover_package(pb: Pkgbuild) -> PackageSpec | None:
    if pb.get("_sync_skip") == "true":
        return None
    urls = pb.source_urls()
    if not urls:
        return None

    primary = urls[0]
    cur_ver = pb.get("pkgver") or ""

    if feed_url := pb.get("_feed_url"):
        cur_build = pb.get("_build") or ""
        tpl = primary.replace(f"{cur_ver}-{cur_build}", "{version}-{build}", 1)
        overlay_repo = overlay_path = None
        for u in urls[1:]:
            if raw_m := GH_RAW_COMMIT_URL_RE.match(u):
                overlay_repo, overlay_path = raw_m.group(1), raw_m.group(2)
                break
        return FeedReleaseSpec(pb.name, feed_url, pb.get("_manifest_url"), tpl, overlay_repo, overlay_path)

    if manifest_url := pb.get("_manifest_url"):
        tpl = primary.replace(cur_ver, "{version}", 1)
        return ManifestReleaseSpec(pb.name, manifest_url, tpl)

    if arch_m := GH_ARCHIVE_URL_RE.match(primary):
        return CommitHybridSpec(pb.name, arch_m.group(1), tuple(urls[1:]), pb.get("_version_ini"))

    if gh_m := GH_RELEASE_URL_RE.match(primary):
        pattern = pb.get("_asset_pattern") or derive_asset_pattern(gh_m.group(2))
        return GithubReleaseSpec(pb.name, gh_m.group(1), pattern)

    return None


def discover_packages() -> list[tuple[Pkgbuild, PackageSpec]]:
    if not PACKAGES_DIR.is_dir():
        return []
    discovered: list[tuple[Pkgbuild, PackageSpec]] = []
    for pkg_dir in sorted(PACKAGES_DIR.iterdir()):
        if pkg_dir.name == "repo" or not (pkg_dir / "PKGBUILD").is_file():
            continue
        pb = Pkgbuild(pkg_dir.name)
        if spec := discover_package(pb):
            discovered.append((pb, spec))
    return discovered


def resolve_feed_release(feed_url: str, manifest_url: str | None) -> tuple[str, str]:
    def _probe() -> tuple[str, str]:
        candidates: list[tuple[str, str]] = []

        try:
            feed = fetch_json(feed_url)
            entry = feed[0] if isinstance(feed, list) else feed["versions"][0]
            ver, bld = str(entry.get("version", "")).strip(), str(entry.get("execution_id", "")).strip()
            if ver and bld:
                candidates.append((ver, bld))
        except Exception:
            pass

        if manifest_url:
            try:
                manifest = fetch_text(manifest_url)
                ver_m, bld_m = YAML_VERSION_RE.search(manifest), MANIFEST_BUILD_RE.search(manifest)
                if ver_m and bld_m:
                    candidates.append((ver_m.group(1).strip(), bld_m.group(1)))
            except Exception:
                pass

        if not candidates:
            raise RuntimeError(f"unable to resolve release from {feed_url}")
        return max(candidates, key=lambda item: version_key(item[0]))

    return once(f"feed:{feed_url}:{manifest_url}", _probe)


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


def sync_github(pkg: GithubReleaseSpec, pb: Pkgbuild, force: bool) -> SyncResult:
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


def sync_feed(pkg: FeedReleaseSpec, pb: Pkgbuild, force: bool) -> SyncResult:
    version, build = resolve_feed_release(pkg.feed_url, pkg.manifest_url)
    cur_ver = pb.get("pkgver")
    if cur_ver and version_key(version) < version_key(cur_ver):
        return SyncResult(pkg.name, "SKIP", f"upstream {version} < {cur_ver}")

    hans_commit = resolve_overlay_commit(pkg.overlay_repo, pkg.overlay_path) if pkg.overlay_repo and pkg.overlay_path else None
    overlay_changed = bool(hans_commit and pb.get("_hans_commit") != hans_commit)
    rebuild = cur_ver == version and (pb.get("_build") != build or overlay_changed)

    if not force and cur_ver == version and not rebuild:
        return SyncResult(pkg.name, "OK", f"{version} ({build})")

    url = pkg.download_tpl.format(version=version, build=build)
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


def sync_manifest(pkg: ManifestReleaseSpec, pb: Pkgbuild, force: bool) -> SyncResult:
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


def sync_commit_hybrid(pkg: CommitHybridSpec, pb: Pkgbuild, force: bool) -> SyncResult:
    with ThreadPoolExecutor(max_workers=1 + len(pkg.companion_urls)) as pool:
        commit_fut = pool.submit(fetch_json, f"https://api.github.com/repos/{pkg.upstream}/commits/main")
        first_bin_fut = pool.submit(fetch_bytes, pkg.companion_urls[0]) if pkg.companion_urls else None
        extra_futs = [pool.submit(sha256_url, u) for u in pkg.companion_urls[1:]]

        commit = str(commit_fut.result()["sha"])
        first_bytes = first_bin_fut.result() if first_bin_fut else b""
        companion_sums = ([hashlib.sha256(first_bytes).hexdigest()] if first_bin_fut else []) + [
            f.result() for f in extra_futs
        ]

    ver_m = BINARY_VERSION_RE.search(first_bytes)
    if ver_m:
        version = ver_m.group(1).decode("ascii")
    elif pkg.version_ini_path:
        ini = fetch_text(f"https://raw.githubusercontent.com/{pkg.upstream}/{commit}/{pkg.version_ini_path}")
        ini_m = INI_VERSION_RE.search(ini)
        if not ini_m:
            raise RuntimeError(f"version missing in {pkg.version_ini_path}")
        version = ini_m.group(1).strip().strip("'\"")
    else:
        version = pb.get("pkgver") or "0.0.0"

    cur_ver = pb.get("pkgver")
    sums_changed = any(s not in pb.text for s in companion_sums)
    rebuild = cur_ver == version and (pb.get("_commit") != commit or sums_changed)

    if not force and cur_ver == version and not rebuild:
        return SyncResult(pkg.name, "OK", f"{version} ({commit[:7]})")

    archive_sum = sha256_url(f"https://github.com/{pkg.upstream}/archive/{commit}.tar.gz")
    pb.set_version(version, bump_pkgrel=rebuild)
    pb.set_var("_commit", commit)
    pb.set_all_sums(archive_sum, *companion_sums)
    pb.save()
    return SyncResult(pkg.name, "UPDATE", f"{version} ({commit[:7]})")


def sync_entry(entry: tuple[Pkgbuild, PackageSpec], force: bool) -> SyncResult:
    pb, spec = entry
    try:
        match spec:
            case GithubReleaseSpec():
                return sync_github(spec, pb, force)
            case FeedReleaseSpec():
                return sync_feed(spec, pb, force)
            case ManifestReleaseSpec():
                return sync_manifest(spec, pb, force)
            case CommitHybridSpec():
                return sync_commit_hybrid(spec, pb, force)
    except Exception as exc:
        return SyncResult(pb.name, "ERROR", str(exc))


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
    entries = discover_packages()

    with ThreadPoolExecutor(max_workers=8) as pool:
        assets_fut = pool.submit(get_release_assets, repo)
        results = list(pool.map(lambda item: sync_entry(item, force), entries))
        release_assets = assets_fut.result()

    for r in results:
        print(f"[{r.status:<6}] {r.name:<26} {r.detail}")

    updated_pkgs = {r.name for r in results if r.updated}
    state = inspect_repo_state(release_assets, updated_pkgs, force)
    skip = not updated_pkgs and not state.drift and not force

    print(
        f"\nSummary: {len(entries)} discovered, {len(updated_pkgs)} updated, "
        f"{len(state.build_pkgs)} to build ({', '.join(state.build_pkgs) or 'none'}), "
        f"obsolete: {len(state.obsolete_assets)}, skip: {skip}"
    )
    write_github_outputs(state, skip)


if __name__ == "__main__":
    main()
