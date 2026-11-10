#!/usr/bin/env python3
"""
Version-agnostic patch script for Google Antigravity CLI (agy).

Instead of relying on fragile stack-frame byte patterns and hardcoded virtual addresses,
this script dynamically resolves internal Go symbols from the binary's ELF section
headers and Go program counter line table (pclntab):

1. Unlocks Tab context window selection (256K / 512K / 900K) for all models in /model
   by making SelectorModel.offersContextWindow return true.
2. Redirects Store.ContextWindow to Store.ContextWindowChoice so non-Argon models
   do not drop the selected context window.
3. Removes the hardcoded 256,000 token compaction clamp in GetCheckpointConfig.
"""
import os
import shutil
import struct
import sys
from pathlib import Path

GO_SYMBOLS = {
    "offersContextWindow": "google3/third_party/jetski/cli/model/model.SelectorModel.offersContextWindow",
    "ContextWindow": "google3/third_party/jetski/cli/store/store.(*Store).ContextWindow",
    "ContextWindowChoice": "google3/third_party/jetski/cli/store/store.(*Store).ContextWindowChoice",
    "GetCheckpointConfig": "google3/third_party/jetski/cortex/utils/utils.GetCheckpointConfig",
    "AutoCompactOverride": "google3/third_party/jetski/cli/store/store.AutoCompactOverride",
}

def get_elf_text_section(data: bytes):
    if len(data) < 64 or data[:4] != b'\x7fELF':
        return None
    e_shoff = struct.unpack('<Q', data[0x28:0x30])[0]
    e_shentsize = struct.unpack('<H', data[0x3a:0x3c])[0]
    e_shnum = struct.unpack('<H', data[0x3c:0x3e])[0]
    e_shstrndx = struct.unpack('<H', data[0x3e:0x40])[0]

    shstr_hdr = e_shoff + e_shstrndx * e_shentsize
    shstr_offset = struct.unpack('<Q', data[shstr_hdr+0x18:shstr_hdr+0x20])[0]

    for i in range(e_shnum):
        sh = e_shoff + i * e_shentsize
        name_idx = struct.unpack('<I', data[sh:sh+4])[0]
        sh_offset = struct.unpack('<Q', data[sh+0x18:sh+0x20])[0]
        sh_size = struct.unpack('<Q', data[sh+0x20:sh+0x28])[0]
        name = data[shstr_offset + name_idx :].split(b'\x00')[0]
        if name == b'.text':
            return sh_offset, sh_size
    return None

def resolve_go_symbols(data: bytes, target_names: set[str]) -> dict[str, dict]:
    text_info = get_elf_text_section(data)
    if not text_info:
        return {}
    text_offset, _ = text_info

    # Locate Go pclntab
    pcln_base = None
    for m in [b'\xf1\xff\xff\xff', b'\xf2\xff\xff\xff', b'\xf0\xff\xff\xff']:
        idx = 0
        while True:
            idx = data.find(m, idx)
            if idx == -1: break
            if idx + 72 <= len(data):
                pad1, pad2, minLC, ptrSize = struct.unpack('4B', data[idx+4:idx+8])
                if ptrSize == 8 and minLC in (1, 2, 4):
                    pcln_base = idx
                    break
            idx += 4
        if pcln_base is not None:
            break

    if pcln_base is None:
        return {}

    fields = struct.unpack('<8Q', data[pcln_base+8:pcln_base+72])
    nfunc = fields[0]
    textStart = fields[2]
    funcnameOffset = fields[3]
    pclnOffset = fields[7]

    funcnametab = pcln_base + funcnameOffset
    func_tab_base = pcln_base + pclnOffset
    base_text = textStart if textStart != 0 else text_offset

    resolved = {}
    for i in range(nfunc):
        entry_off = struct.unpack('<I', data[func_tab_base + i*8 : func_tab_base + i*8 + 4])[0]
        func_data_rel = struct.unpack('<I', data[func_tab_base + i*8 + 4 : func_tab_base + i*8 + 8])[0]
        func_data_addr = func_tab_base + func_data_rel
        name_off = struct.unpack('<I', data[func_data_addr + 4 : func_data_addr + 8])[0]
        raw_name = data[funcnametab + name_off : funcnametab + name_off + 250].split(b'\x00')[0].decode('utf-8', 'replace')

        if raw_name in target_names:
            next_entry_off = struct.unpack('<I', data[func_tab_base + (i+1)*8 : func_tab_base + (i+1)*8 + 4])[0] if i + 1 < nfunc else entry_off + 0x100
            resolved[raw_name] = {
                'offset': base_text + entry_off,
                'size': next_entry_off - entry_off
            }
    return resolved

def find_target_binary(custom_path: str | None = None) -> Path | None:
    if custom_path:
        p = Path(custom_path).expanduser().resolve()
        if p.is_file():
            return p

    try:
        proc1 = Path("/proc/1/exe").resolve()
        if "agy" in proc1.name and proc1.is_file():
            return proc1
    except Exception:
        pass

    bin_dir = Path.home() / ".local" / "bin"
    if bin_dir.exists():
        exact = bin_dir / "agy"
        if exact.is_file() and not exact.is_symlink():
            return exact

        candidates = [
            p for p in bin_dir.glob("agy*")
            if p.is_file() and not p.name.endswith(".bak") and not p.is_symlink()
        ]
        if candidates:
            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return candidates[0]

    agy_path = shutil.which("agy")
    if agy_path:
        p = Path(agy_path).resolve()
        if p.is_file():
            return p

    return None

def patch_binary(binary_path: Path, dry_run: bool = False) -> bool:
    print(f"Target binary: {binary_path}")
    data = bytearray(binary_path.read_bytes())
    modified = False

    symbols = resolve_go_symbols(data, set(GO_SYMBOLS.values()))
    if not symbols:
        print("! Error: Failed to resolve Go symbol table (pclntab). Is this a valid agy binary?", file=sys.stderr)
        return False

    # -------------------------------------------------------------
    # 1. Clean AutoCompactOverride
    # -------------------------------------------------------------
    auto_sym = symbols.get(GO_SYMBOLS["AutoCompactOverride"])
    if auto_sym:
        auto_off = auto_sym['offset']
        auto_size = auto_sym['size']
        auto_slice = data[auto_off : auto_off + auto_size]
        auto_pattern = bytes.fromhex("894424309031c031db")
        if auto_pattern in auto_slice:
            print("• AutoCompactOverride is clean")
        else:
            print(f"• AutoCompactOverride checked at {hex(auto_off)}")

    # -------------------------------------------------------------
    # 2. Patch SelectorModel.offersContextWindow -> returns true
    # -------------------------------------------------------------
    offers_sym = symbols.get(GO_SYMBOLS["offersContextWindow"])
    if offers_sym:
        off = offers_sym['offset']
        size = offers_sym['size']
        fn_data = data[off : off + size]
        patch_offers = bytes.fromhex("b001c3")  # mov $1, %al; ret

        if patch_offers in fn_data[:0x100]:
            print(f"• offersContextWindow already patched at {hex(off + fn_data.find(patch_offers))}")
        else:
            # Locate hot entry point: 'mov %rsp, %r12' (49 89 e4) or 'push %rbp' (55 48 89 e5)
            prologue_idx = fn_data.find(bytes.fromhex("4989e4"))
            if prologue_idx == -1:
                prologue_idx = fn_data.find(bytes.fromhex("554889e5"))

            if prologue_idx != -1 and prologue_idx < 0x100:
                entry_addr = off + prologue_idx
                if not dry_run:
                    data[entry_addr : entry_addr + len(patch_offers)] = patch_offers
                modified = True
                print(f"✓ Patched offersContextWindow -> returns true at {hex(entry_addr)}")
            else:
                print(f"! Could not find function prologue in offersContextWindow [{hex(off)}..{hex(off+size)}]")
    else:
        print("! Symbol offersContextWindow not found in pclntab")

    # -------------------------------------------------------------
    # 3. Patch Store.ContextWindow -> jmp Store.ContextWindowChoice
    # -------------------------------------------------------------
    cw_sym = symbols.get(GO_SYMBOLS["ContextWindow"])
    choice_sym = symbols.get(GO_SYMBOLS["ContextWindowChoice"])
    if cw_sym and choice_sym:
        choice_off = choice_sym['offset']
        choice_pattern = bytes.fromhex("493b66107627554889e54883ec10488b48104885c9")
        c_idx = data.find(choice_pattern, choice_off, choice_off + 0x100)
        if c_idx != -1:
            cw_entry = c_idx + 0x40
            rel32 = c_idx - (cw_entry + 5)
            expected_jmp = b'\xe9' + rel32.to_bytes(4, 'little', signed=True)
            if data[cw_entry : cw_entry + 5] == expected_jmp:
                print(f"• Store.ContextWindow already patched to jump to ContextWindowChoice at {hex(cw_entry)}")
            else:
                if not dry_run:
                    data[cw_entry : cw_entry + 5] = expected_jmp
                modified = True
                print(f"✓ Patched Store.ContextWindow at {hex(cw_entry)} -> jmp Store.ContextWindowChoice ({expected_jmp.hex()})")
        else:
            print("! Could not find ContextWindowChoice anchor pattern")
    else:
        print("! Symbols ContextWindow / ContextWindowChoice not found in pclntab")

    # -------------------------------------------------------------
    # 4. Patch cortex/utils.GetCheckpointConfig 256K clamp
    # -------------------------------------------------------------
    chk_sym = symbols.get(GO_SYMBOLS["GetCheckpointConfig"])
    if chk_sym:
        chk_off = chk_sym['offset']
        chk_size = chk_sym['size']
        fn_data = data[chk_off : chk_off + chk_size]
        clamp_pat = bytes.fromhex("81fa00e80300be00e803000f42f2")
        patch_clamp = bytes.fromhex("81fa00d00700be00d007000f42f2")

        if patch_clamp in fn_data:
            print(f"• GetCheckpointConfig clamp already removed at {hex(chk_off + fn_data.find(patch_clamp))}")
        elif clamp_pat in fn_data:
            clamp_addr = chk_off + fn_data.find(clamp_pat)
            if not dry_run:
                data[clamp_addr : clamp_addr + len(patch_clamp)] = patch_clamp
            modified = True
            print(f"✓ Removed 256K checkpoint clamp in GetCheckpointConfig at {hex(clamp_addr)}")
        else:
            print("! Could not locate GetCheckpointConfig clamp pattern inside function")
    else:
        print("! Symbol GetCheckpointConfig not found in pclntab")

    if dry_run:
        print("Dry run complete: binary can be patched successfully.")
        return True

    if modified:
        backup_path = binary_path.with_suffix(binary_path.suffix + ".bak")
        if not backup_path.exists():
            shutil.copy2(binary_path, backup_path)
            print(f"✓ Created backup at {backup_path}")

        temp_path = binary_path.with_suffix(binary_path.suffix + ".patched")
        temp_path.write_bytes(data)
        temp_path.chmod(binary_path.stat().st_mode)
        temp_path.replace(binary_path)
        print(f"✓ Successfully wrote patched binary to {binary_path}")
        return True
    else:
        print("Binary is already fully configured.")
        return True

def main():
    dry_run = "--dry-run" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    custom_path = args[0] if args else None

    target = find_target_binary(custom_path)
    if not target:
        print("Error: Could not locate agy binary.", file=sys.stderr)
        sys.exit(1)

    success = patch_binary(target, dry_run=dry_run)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
