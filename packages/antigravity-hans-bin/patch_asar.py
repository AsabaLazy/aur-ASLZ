#!/usr/bin/env python3
"""Inject Antigravity-Hans localization overlay into Electron app.asar without external Node tooling."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import sys

MARKER = "/* ANTIGRAVITY_HANS_OVERLAY_INJECTED */"
BLOCK_SIZE = 4 * 1024 * 1024  # 4 MiB Electron ASAR integrity block size
PRELOAD_TARGETS = ("dist/preload.js", "dist/ideInstall/wizardPreload.js")
MAIN_ANCHOR = "electron_1.app.on('web-contents-created', (_event, contents) => {"


def calc_integrity(data: bytes) -> dict[str, object]:
    blocks = [hashlib.sha256(data[i : i + BLOCK_SIZE]).hexdigest() for i in range(0, len(data), BLOCK_SIZE)] or [
        hashlib.sha256(b"").hexdigest()
    ]
    return {
        "algorithm": "SHA256",
        "hash": hashlib.sha256(data).hexdigest(),
        "blockSize": BLOCK_SIZE,
        "blocks": blocks,
    }


def get_node(tree: dict[str, object], rel_path: str) -> dict[str, object] | None:
    node: object = tree
    for part in rel_path.split("/"):
        if not isinstance(node, dict):
            return None
        files = node.get("files")
        node = files.get(part) if isinstance(files, dict) else None
    return node if isinstance(node, dict) else None


def read_asar(asar_path: Path) -> tuple[dict[str, object], bytearray]:
    with asar_path.open("rb") as f:
        prefix = f.read(16)
        if len(prefix) < 16:
            raise ValueError(f"{asar_path} is not a valid ASAR archive")
        u0, header_size, _, json_len = struct.unpack("<4I", prefix)
        if u0 != 4:
            raise ValueError(f"unexpected ASAR pickle header u0={u0}")
        tree = json.loads(f.read(json_len).decode("utf-8"))
        f.seek(8 + header_size)
        payload = bytearray(f.read())
    return tree, payload


def patch_asar(asar_path: Path, overlay_path: Path) -> None:
    overlay_js = overlay_path.read_text(encoding="utf-8").strip()
    if not overlay_js:
        raise ValueError(f"overlay script {overlay_path} is empty")

    tree, payload = read_asar(asar_path)

    def read_entry(node: dict[str, object]) -> str:
        off, size = int(str(node["offset"])), int(str(node["size"]))
        return bytes(payload[off : off + size]).decode("utf-8")

    def write_entry(node: dict[str, object], content: str) -> None:
        encoded = content.encode("utf-8")
        node["offset"] = str(len(payload))
        node["size"] = len(encoded)
        node["integrity"] = calc_integrity(encoded)
        payload.extend(encoded)

    patched_targets: set[str] = set()
    for rel_path in PRELOAD_TARGETS:
        node = get_node(tree, rel_path)
        if node is None or "files" in node:
            continue
        source = read_entry(node)
        if MARKER not in source:
            write_entry(node, f"{source.rstrip()}\n\n{MARKER}\n;{overlay_js}\n")
            print(f"  Injected localization overlay into {rel_path}")
        patched_targets.add(rel_path)

    if "dist/preload.js" not in patched_targets:
        raise RuntimeError("critical target dist/preload.js not found in app.asar")

    main_node = get_node(tree, "dist/main.js")
    if main_node is not None and "files" not in main_node:
        main_src = read_entry(main_node)
        if MARKER not in main_src and MAIN_ANCHOR in main_src:
            splash_hook = (
                f"{MAIN_ANCHOR}\n"
                f"    {MARKER}\n"
                f"    if (contents.getType() === 'window') {{\n"
                f"        contents.on('dom-ready', () => {{\n"
                f"            if (contents.getURL().startsWith('data:text/html')) {{\n"
                f"                void contents.executeJavaScript({json.dumps(overlay_js)}).catch(() => undefined);\n"
                f"            }}\n"
                f"        }});\n"
                f"    }}"
            )
            write_entry(main_node, main_src.replace(MAIN_ANCHOR, splash_hook, 1))
            print("  Injected splash window localization hook into dist/main.js")

    json_bytes = json.dumps(tree, separators=(",", ":")).encode("utf-8")
    aligned_len = (len(json_bytes) + 3) & ~3
    padding = b"\x00" * (aligned_len - len(json_bytes))
    header = struct.pack("<4I", 4, 8 + aligned_len, 4 + aligned_len, len(json_bytes))

    tmp = asar_path.with_suffix(".asar.tmp")
    with tmp.open("wb") as f:
        f.write(header)
        f.write(json_bytes)
        f.write(padding)
        f.write(payload)
    tmp.chmod(asar_path.stat().st_mode)
    tmp.replace(asar_path)
    print(f"  Repacked {asar_path}")


def main() -> None:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <path/to/app.asar> <path/to/overlay.js>", file=sys.stderr)
        sys.exit(1)
    patch_asar(Path(sys.argv[1]), Path(sys.argv[2]))


if __name__ == "__main__":
    main()
