# Agent Instructions

Personal Arch Linux repository (`[aslz]`). Prebuilt binaries only (`<name>-bin`).

## Rules for Managing Packages

1. **Prebuilt Linux Only**: Package official precompiled binaries only. Reject source-only or Windows-only upstreams. Always set `options=('!debug' '!strip')`.
2. **Directory & Metadata**: Place `PKGBUILD` and synced `.SRCINFO` (`makepkg --printsrcinfo > .SRCINFO`) in `packages/<name>-bin/`. When renaming a package, set `provides`/`conflicts`/`replaces` and remove the old directory.
3. **Auto-Sync**: Register or remove the entry in `PACKAGES` inside `.github/scripts/sync-packages.py` (supports GitHub Releases `upstream` + `asset_pattern`, `manifest_url`, `release_feed`, or custom commit/binary URLs).
4. **Bilingual Docs**: Update package tables in **both** `README.md` and `README_zh.md`; package names (`[`name`](url)`) must link to the upstream repo/website.
5. **Git Cleanliness**: Run local `makepkg` tests with `/tmp` isolation (`SRCDEST=/tmp/... PKGDEST=/tmp/... BUILDDIR=/tmp/...`). Stage explicit files (`PKGBUILD`, `.SRCINFO`, `.install`, `.desktop`, icons, patch scripts) — never `git add .` or commit downloaded sources, `.pkg.tar.*`, `src/`, or `pkg/`.
