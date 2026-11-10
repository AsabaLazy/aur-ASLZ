#!/usr/bin/env python3
"""Patch Google Antigravity CLI (agy) to unlock context window selection and adjust compaction clamp."""

from __future__ import annotations

import argparse
from pathlib import Path
import struct

GO_SYMBOLS = {
    "offersContextWindow": "google3/third_party/jetski/cli/model/model.SelectorModel.offersContextWindow",
    "ContextWindow": "google3/third_party/jetski/cli/store/store.(*Store).ContextWindow",
    "ContextWindowChoice": "google3/third_party/jetski/cli/store/store.(*Store).ContextWindowChoice",
    "GetCheckpointConfig": "google3/third_party/jetski/cortex/utils/utils.GetCheckpointConfig",
}

OFFERS_TRUE_PATCH = bytes.fromhex("b001c3")  # mov $1, %al; ret
PROLOGUES = (bytes.fromhex("4989e4"), bytes.fromhex("554889e5"))
CHOICE_ANCHOR = bytes.fromhex("493b66107627554889e54883ec10488b48104885c9")
CLAMP_256K_PAT = bytes.fromhex("81fa00e80300be00e803000f42f2")
CLAMP_PATCHES = {
    "none": bytes.fromhex("89d6909090909090909090909090"),  # mov %edx, %esi; nop... (unclamped 900K)
    "512k": bytes.fromhex("81fa00d00700be00d007000f42f2"),  # raise 256,000 -> 512,000 ceiling
}


def find_elf_text_offset(data: bytes) -> int:
    if len(data) < 64 or data[:4] != b"\x7fELF":
        raise RuntimeError("not a valid ELF64 binary")
    e_shoff = struct.unpack_from("<Q", data, 0x28)[0]
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<3H", data, 0x3A)
    shstr_off = struct.unpack_from("<Q", data, e_shoff + e_shstrndx * e_shentsize + 0x18)[0]

    for i in range(e_shnum):
        sh = e_shoff + i * e_shentsize
        name_idx = struct.unpack_from("<I", data, sh)[0]
        sh_offset = struct.unpack_from("<Q", data, sh + 0x18)[0]
        if data[shstr_off + name_idx :].split(b"\x00", 1)[0] == b".text":
            return sh_offset
    raise RuntimeError(".text section not found in ELF headers")


def resolve_go_symbols(data: bytes, targets: set[str]) -> dict[str, tuple[int, int]]:
    text_offset = find_elf_text_offset(data)
    pcln_base: int | None = None

    for magic in (b"\xf1\xff\xff\xff", b"\xf2\xff\xff\xff", b"\xf0\xff\xff\xff"):
        idx = 0
        while (idx := data.find(magic, idx)) != -1:
            if idx + 72 <= len(data):
                _, _, min_lc, ptr_size = struct.unpack_from("4B", data, idx + 4)
                if ptr_size == 8 and min_lc in (1, 2, 4):
                    pcln_base = idx
                    break
            idx += 4
        if pcln_base is not None:
            break

    if pcln_base is None:
        raise RuntimeError("Go pclntab not found in binary")

    fields = struct.unpack_from("<8Q", data, pcln_base + 8)
    nfunc, _, text_start, funcname_off, _, _, _, pcln_off = fields
    funcnametab = pcln_base + funcname_off
    functab = pcln_base + pcln_off
    base_text = text_start or text_offset

    resolved: dict[str, tuple[int, int]] = {}
    for i in range(nfunc):
        entry_off, func_data_rel = struct.unpack_from("<2I", data, functab + i * 8)
        name_off = struct.unpack_from("<I", data, functab + func_data_rel + 4)[0]
        raw_name = data[funcnametab + name_off : funcnametab + name_off + 256].split(b"\x00", 1)[0].decode(
            "utf-8", "replace"
        )
        if raw_name in targets:
            next_off = struct.unpack_from("<I", data, functab + (i + 1) * 8)[0] if i + 1 < nfunc else entry_off + 0x100
            resolved[raw_name] = (base_text + entry_off, next_off - entry_off)
            if len(resolved) == len(targets):
                break
    return resolved


def patch_binary(binary_path: Path, *, clamp: str, dry_run: bool = False) -> None:
    print(f"Target binary: {binary_path} (clamp={clamp})")
    data = bytearray(binary_path.read_bytes())
    symbols = resolve_go_symbols(data, set(GO_SYMBOLS.values()))
    if missing := [k for k, sym in GO_SYMBOLS.items() if sym not in symbols]:
        raise RuntimeError(f"missing required Go symbols: {', '.join(missing)}")

    modified = False

    # 1. SelectorModel.offersContextWindow -> return true
    off, size = symbols[GO_SYMBOLS["offersContextWindow"]]
    fn_head = data[off : off + min(size, 0x100)]
    if OFFERS_TRUE_PATCH not in fn_head:
        prologue_idx = next((fn_head.find(p) for p in PROLOGUES if p in fn_head), -1)
        if prologue_idx == -1:
            raise RuntimeError("prologue not found in SelectorModel.offersContextWindow")
        data[off + prologue_idx : off + prologue_idx + len(OFFERS_TRUE_PATCH)] = OFFERS_TRUE_PATCH
        modified = True
        print(f"  Patched offersContextWindow at {hex(off + prologue_idx)}")

    # 2. Store.ContextWindow -> jmp Store.ContextWindowChoice
    choice_off, _ = symbols[GO_SYMBOLS["ContextWindowChoice"]]
    anchor_idx = data.find(CHOICE_ANCHOR, choice_off, choice_off + 0x100)
    if anchor_idx == -1:
        raise RuntimeError("anchor pattern not found in Store.ContextWindowChoice")
    cw_entry = anchor_idx + 0x40
    rel32 = anchor_idx - (cw_entry + 5)
    jmp_insn = b"\xe9" + rel32.to_bytes(4, "little", signed=True)
    if data[cw_entry : cw_entry + 5] != jmp_insn:
        data[cw_entry : cw_entry + 5] = jmp_insn
        modified = True
        print(f"  Patched Store.ContextWindow -> ContextWindowChoice at {hex(cw_entry)}")

    # 3. Adjust 256K checkpoint compaction clamp in GetCheckpointConfig
    target_clamp = CLAMP_PATCHES[clamp]
    chk_off, chk_size = symbols[GO_SYMBOLS["GetCheckpointConfig"]]
    chk_slice = data[chk_off : chk_off + chk_size]
    if target_clamp not in chk_slice:
        rel_idx = chk_slice.find(CLAMP_256K_PAT)
        if rel_idx == -1:
            raise RuntimeError("256K clamp pattern not found in GetCheckpointConfig")
        clamp_addr = chk_off + rel_idx
        data[clamp_addr : clamp_addr + len(target_clamp)] = target_clamp
        modified = True
        print(f"  Updated compaction clamp ({clamp}) at {hex(clamp_addr)}")

    if dry_run or not modified:
        print("  Dry run complete." if dry_run else "  Binary already patched.")
        return

    tmp = binary_path.with_suffix(binary_path.suffix + ".patched")
    tmp.write_bytes(data)
    tmp.chmod(binary_path.stat().st_mode)
    tmp.replace(binary_path)
    print(f"  Wrote patched binary to {binary_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path, help="Path to agy ELF binary")
    parser.add_argument("--clamp", choices=tuple(CLAMP_PATCHES), default="none", help="Compaction ceiling mode")
    parser.add_argument("--dry-run", action="store_true", help="Verify symbols and patterns without writing")
    args = parser.parse_args()
    patch_binary(args.binary, clamp=args.clamp, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
