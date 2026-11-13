"""
Build script to package the LAN Whiteboard Server and Client using PyInstaller.
Generates standalone executables in the dist/ directory.
"""

import argparse
import os
import shutil
import subprocess
import sys


def check_pyinstaller() -> bool:
    try:
        import PyInstaller
        return True
    except ImportError:
        return False


def build_target(spec_file: str, name: str) -> bool:
    print(f"\n==================================================")
    print(f" Packaging {name} using PyInstaller...")
    print(f" Spec file: {spec_file}")
    print(f"==================================================")

    cmd = [sys.executable, "-m", "PyInstaller", "--clean", "-y", spec_file]
    result = subprocess.run(cmd)
    if result.returncode == 0:
        print(f"[SUCCESS] {name} successfully packaged into dist/{name}")
        return True
    else:
        print(f"[ERROR] Failed to package {name}. Exit code: {result.returncode}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Package Whiteboard application with PyInstaller")
    parser.add_argument(
        "--target",
        choices=["server", "client", "all"],
        default="all",
        help="Target executable to build (default: all)",
    )
    args = parser.parse_args()

    if not check_pyinstaller():
        print("[ERROR] PyInstaller is not installed in the current Python environment.")
        print("Please install PyInstaller by running:")
        print("    pip install pyinstaller")
        sys.exit(1)

    success = True
    if args.target in ("server", "all"):
        if not build_target("WhiteboardServer.spec", "WhiteboardServer"):
            success = False

    if args.target in ("client", "all"):
        if not build_target("WhiteboardClient.spec", "WhiteboardClient"):
            success = False

    if success:
        print("\n[ALL BUILDS COMPLETE] Executables are located in the 'dist/' directory:")
        dist_dir = os.path.join(os.path.dirname(__file__), "dist")
        if os.path.exists(dist_dir):
            for item in os.listdir(dist_dir):
                print(f"  -> dist/{item}")


if __name__ == "__main__":
    main()
