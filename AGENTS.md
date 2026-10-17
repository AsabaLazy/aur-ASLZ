# Agent Instructions

Personal Arch Linux repository (`[aslz]`). Prebuilt binaries only (`<name>-bin`).

## Rules for Adding Packages

1. **Prebuilt Linux Only**: Package official precompiled binaries only. Reject source-only or Windows-only upstreams. Set `options=('!debug' '!strip')`.
2. **Directory**: Place `PKGBUILD` and `.SRCINFO` in `packages/<name>-bin/`.
3. **Auto-Sync**: Register in `.github/scripts/sync-packages.py` (`name`, `upstream`, `asset_pattern`).
4. **README.md**: Add row to table; package name **must** link to upstream repo/URL.
5. **Git Cleanliness**: Never commit `.pkg.tar.*`, `.deb`, `src/`, or `pkg/` into git.
