# Agent Instructions

Personal Arch Linux repository (`[aslz]`). Prebuilt binaries only (`<name>-bin`).

## Rules for Managing Packages

1. **Prebuilt Linux Only**: Package official precompiled binaries only. Reject source-only or Windows-only upstreams. Always set `options=('!debug' '!strip')`.
2. **Directory & Metadata**: Place `PKGBUILD` and synced `.SRCINFO` (`makepkg --printsrcinfo > .SRCINFO`) in `packages/<name>-bin/`. When renaming a package, set `provides`/`conflicts`/`replaces` and remove the old directory.
3. **Auto-Sync**: `.github/scripts/sync-packages.py` auto-discovers `packages/*/PKGBUILD` from `source_x86_64` (GitHub Releases need zero config; custom upstreams declare `_manifest_url`, `_feed_url`, or `_asset_pattern` inside `PKGBUILD`).
4. **Bilingual Docs**: `sync-packages.py` auto-syncs package tables in `README.md` and `README_zh.md` from `packages/*/PKGBUILD`; ensure `url` in `PKGBUILD` points to the upstream repo/website.
5. **Git Cleanliness**: Run local `makepkg` tests with `/tmp` isolation (`SRCDEST=/tmp/... PKGDEST=/tmp/... BUILDDIR=/tmp/...`). Stage explicit files (`PKGBUILD`, `.SRCINFO`, `.install`, `.desktop`, icons, patch scripts) — never `git add .` or commit downloaded sources, `.pkg.tar.*`, `src/`, or `pkg/`.
