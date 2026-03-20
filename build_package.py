#!/usr/bin/env python3
"""
Build script: PyArmor encrypt + assemble pip package.

Usage:
    python build_package.py          # Encrypt + build wheel
    python build_package.py --skip-encrypt  # Only build wheel (use existing encrypted files)

Output:
    dist/box2driver-0.4.4-py3-none-any.whl
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
PKG_SRC = ROOT / "box2driver"
OBF_OUT = ROOT / "box2driver_obf"
DIST_PKG = ROOT / "dist_pkg" / "box2driver"

# Files to encrypt (core, closed source)
ENCRYPT_FILES = [
    "datastore.py",
    "serial_io.py",
    "ws_server.py",
    "sts_server.py",
    "vcom_bridge.py",
    "gateway.py",
    "cli.py",
    "client.py",
    "com_tcp_bridge.py",
    "start_com_bridge.py",
]

# Files to keep as-is (open source)
OPEN_FILES = [
    "__init__.py",
]

# Static assets (open source)
STATIC_FILES = [
    "static/dashboard.html",
]


def step_encrypt():
    """Encrypt core files with PyArmor."""
    print("\n=== Step 1: PyArmor Encrypt ===")
    if OBF_OUT.exists():
        shutil.rmtree(OBF_OUT)

    src_files = [str(PKG_SRC / f) for f in ENCRYPT_FILES]
    cmd = ["pyarmor", "gen", "-O", str(OBF_OUT), "-i"] + src_files
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
        sys.exit(1)
    print(f"Encrypted {len(ENCRYPT_FILES)} files OK")


def step_assemble():
    """Assemble final package directory."""
    print("\n=== Step 2: Assemble Package ===")
    if DIST_PKG.exists():
        shutil.rmtree(DIST_PKG)
    DIST_PKG.mkdir(parents=True)

    # Copy encrypted .py files and fix pyarmor_runtime import path
    runtime_module = None
    for d in OBF_OUT.iterdir():
        if d.is_dir() and d.name.startswith("pyarmor_runtime"):
            runtime_module = d.name
            break

    for f in ENCRYPT_FILES:
        src = OBF_OUT / f
        dst = DIST_PKG / f
        if not src.exists():
            print(f"  [MISSING] {f}")
            sys.exit(1)
        # Fix import: only replace the exact import line in text portion
        # PyArmor encrypted files: shebang \n comment \n import \n __pyarmor__(binary blob)
        with open(src, "rb") as fp:
            raw = fp.read()
        if runtime_module:
            # Find the import line and replace only that exact occurrence
            old_line = f"from {runtime_module} import __pyarmor__".encode()
            new_line = f"from box2driver.{runtime_module} import __pyarmor__".encode()
            # Only replace first occurrence (the import statement, not inside binary data)
            raw = raw.replace(old_line, new_line, 1)
        dst.write_bytes(raw)
        print(f"  [encrypted] {f}")

    # Copy pyarmor_runtime
    runtime_dir = None
    for d in OBF_OUT.iterdir():
        if d.is_dir() and d.name.startswith("pyarmor_runtime"):
            runtime_dir = d
            break
    if runtime_dir:
        dst_runtime = DIST_PKG / runtime_dir.name
        shutil.copytree(runtime_dir, dst_runtime)
        print(f"  [runtime] {runtime_dir.name}/")
    else:
        print("  [ERROR] pyarmor_runtime not found!")
        sys.exit(1)

    # Copy open source files
    for f in OPEN_FILES:
        shutil.copy2(PKG_SRC / f, DIST_PKG / f)
        print(f"  [open] {f}")

    # Copy static assets
    static_dst = DIST_PKG / "static"
    static_dst.mkdir(exist_ok=True)
    for f in STATIC_FILES:
        src = PKG_SRC / f
        dst = DIST_PKG / f
        if src.exists():
            shutil.copy2(src, dst)
            print(f"  [static] {f}")

    print(f"\nPackage assembled at: {DIST_PKG}")


def step_build_wheel():
    """Build wheel from assembled package."""
    print("\n=== Step 3: Build Wheel ===")
    dist_root = ROOT / "dist_pkg"

    # Copy pyproject.toml and README
    shutil.copy2(ROOT / "pyproject.toml", dist_root / "pyproject.toml")
    if (ROOT / "README.md").exists():
        shutil.copy2(ROOT / "README.md", dist_root / "README.md")

    # Build
    result = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", str(dist_root)],
        capture_output=True, text=True
    )
    print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
    if result.returncode != 0:
        print(result.stderr[-500:] if len(result.stderr) > 500 else result.stderr)
        sys.exit(1)

    # Copy wheel to project dist/
    dist_dir = ROOT / "dist"
    dist_dir.mkdir(exist_ok=True)
    for whl in (dist_root / "dist").glob("*.whl"):
        dst = dist_dir / whl.name
        shutil.copy2(whl, dst)
        print(f"\n  Wheel: {dst}")
        print(f"  Install: pip install {dst}")


def main():
    skip_encrypt = "--skip-encrypt" in sys.argv

    if not skip_encrypt:
        step_encrypt()
    else:
        print("Skipping encryption (using existing files)")

    step_assemble()
    step_build_wheel()

    print("\n=== Done! ===")
    print("Test install:")
    print(f"  pip install dist/box2driver-*.whl")
    print("  box2driver --help")


if __name__ == "__main__":
    main()
