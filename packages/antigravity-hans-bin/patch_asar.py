#!/usr/bin/env python3
"""
Injects Antigravity-Hans Chinese localization overlay into Electron's app.asar
without external Node/asar dependencies.

Patches:
- dist/preload.js (all main Antigravity Hub BrowserWindows)
- dist/ideInstall/wizardPreload.js (IDE install wizard BrowserWindow)
- dist/main.js (data:text/html splash windows without a preload script)
"""
import hashlib
import json
import struct
import sys
from pathlib import Path

MARKER = "/* ANTIGRAVITY_HANS_OVERLAY_INJECTED */"
BLOCK_SIZE = 4 * 1024 * 1024  # 4 MiB Electron ASAR integrity block size


def calc_integrity(data: bytes) -> dict:
    blocks = [
        hashlib.sha256(data[i : i + BLOCK_SIZE]).hexdigest()
        for i in range(0, len(data), BLOCK_SIZE)
    ] or [hashlib.sha256(b"").hexdigest()]
    return {
        "algorithm": "SHA256",
        "hash": hashlib.sha256(data).hexdigest(),
        "blockSize": BLOCK_SIZE,
        "blocks": blocks,
    }


def get_node(tree: dict, rel_path: str) -> dict | None:
    node = tree
    for part in rel_path.split("/"):
        node = node.get("files", {}).get(part)
        if node is None:
            return None
    return node


def patch_asar(asar_path: Path, overlay_path: Path) -> None:
    overlay_js = overlay_path.read_text(encoding="utf-8")
    if not overlay_js.strip():
        raise ValueError(f"Overlay script {overlay_path} is empty")

    raw_asar = asar_path.read_bytes()
    if len(raw_asar) < 16:
        raise ValueError(f"{asar_path} is not a valid ASAR archive")

    u0, header_size, u2, json_len = struct.unpack("<IIII", raw_asar[:16])
    if u0 != 4:
        raise ValueError(f"Unexpected ASAR pickle header u0={u0}")

    payload_offset = 8 + header_size
    header_json = raw_asar[16 : 16 + json_len].decode("utf-8")
    tree = json.loads(header_json)
    payload = bytearray(raw_asar[payload_offset:])

    def read_asar_file(node: dict) -> bytes:
        off = int(node["offset"])
        size = int(node["size"])
        return bytes(payload[off : off + size])

    def update_asar_file(node: dict, new_bytes: bytes) -> None:
        new_off = len(payload)
        payload.extend(new_bytes)
        node["offset"] = str(new_off)
        node["size"] = len(new_bytes)
        node["integrity"] = calc_integrity(new_bytes)

    patched_targets = []

    # 1. Inject overlay into preload scripts (runs before DOMContentLoaded in renderer)
    for preload_rel in ("dist/preload.js", "dist/ideInstall/wizardPreload.js"):
        node = get_node(tree, preload_rel)
        if node is None or "files" in node:
            continue
        source = read_asar_file(node).decode("utf-8")
        if MARKER in source:
            print(f"• {preload_rel} already contains localization overlay")
            patched_targets.append(preload_rel)
            continue
        patched = f"{source.rstrip()}\n\n{MARKER}\n;{overlay_js.strip()}\n"
        update_asar_file(node, patched.encode("utf-8"))
        patched_targets.append(preload_rel)
        print(f"✓ Injected localization overlay into {preload_rel}")

    if "dist/preload.js" not in patched_targets:
        raise RuntimeError("Critical target dist/preload.js not found in app.asar")

    # 2. Inject fallback into dist/main.js for data:text/html splash windows without preload
    main_node = get_node(tree, "dist/main.js")
    if main_node is not None and "files" not in main_node:
        main_src = read_asar_file(main_node).decode("utf-8")
        anchor = "electron_1.app.on('web-contents-created', (_event, contents) => {"
        if MARKER not in main_src and anchor in main_src:
            splash_hook = (
                f"{anchor}\n"
                f"    {MARKER}\n"
                f"    if (contents.getType() === 'window') {{\n"
                f"        contents.on('dom-ready', () => {{\n"
                f"            if (contents.getURL().startsWith('data:text/html')) {{\n"
                f"                void contents.executeJavaScript({json.dumps(overlay_js)}).catch(() => undefined);\n"
                f"            }}\n"
                f"        }});\n"
                f"    }}"
            )
            main_src = main_src.replace(anchor, splash_hook, 1)
            update_asar_file(main_node, main_src.encode("utf-8"))
            print("✓ Injected splash window localization hook into dist/main.js")

    new_json_bytes = json.dumps(tree, separators=(",", ":")).encode("utf-8")
    new_json_len = len(new_json_bytes)
    aligned_json_len = (new_json_len + 3) & ~3
    padding = b"\x00" * (aligned_json_len - new_json_len)
    new_u2 = 4 + aligned_json_len
    new_header_size = 4 + new_u2
    new_header = struct.pack("<IIII", 4, new_header_size, new_u2, new_json_len)

    tmp_path = asar_path.with_suffix(".asar.tmp")
    with open(tmp_path, "wb") as f:
        f.write(new_header)
        f.write(new_json_bytes)
        f.write(padding)
        f.write(payload)
    tmp_path.chmod(asar_path.stat().st_mode)
    tmp_path.replace(asar_path)
    print(f"✓ Successfully repacked {asar_path}")


def main() -> None:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <path/to/app.asar> <path/to/antigravity-hans-overlay.js>", file=sys.stderr)
        sys.exit(1)
    patch_asar(Path(sys.argv[1]), Path(sys.argv[2]))


if __name__ == "__main__":
    main()
