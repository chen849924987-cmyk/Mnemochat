"""构建/更新 Windows 可执行版，且**不破坏用户数据**。

  python scripts/build_exe.py                 # 构建并同步到 dist/Mnemochat
  python scripts/build_exe.py -o 别的目录      # 同步到其他位置
  python scripts/build_exe.py --skip-build    # 跳过构建，只把 staging 同步过去

为什么需要它：PyInstaller 每次构建都会清空自己的输出目录——直接对
dist/Mnemochat 跑，config.yaml/.env/personas/data 会被一起删掉。
本脚本改为两步：
  1. 构建到临时目录 dist_new/Mnemochat；
  2. 只把程序文件（Mnemochat.exe、_internal/）同步进目标目录，
     用户文件（config.yaml/.env/personas/data 等）原样保留；
     personas 仅"增"不"覆盖"——新版本的新卡片会出现，改过的卡不会被覆盖。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NL = "\n"

ADD_DATA = [
    ("memory/schema.sql", "memory"),
    ("memory/schema_stickers.sql", "memory"),
    ("gui/page.html", "gui"),
    ("editor/page.html", "editor"),
    ("personas", "personas"),
    ("config.example.yaml", "."),
    (".env.example", "."),
    ("docs", "docs"),
]

# 用户文件：同步时绝不覆盖（只允许"缺失时播种"）
USER_FILES = ("personas", "docs", "config.example.yaml", ".env.example")
PROGRAM_FILES = ("Mnemochat.exe", "_internal")


def build(staging_dist: Path) -> None:
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           "--name", "Mnemochat", "--console",
           "--distpath", str(staging_dist),
           "--workpath", str(ROOT / "build"),
           "--specpath", str(ROOT),
           "--collect-data", "jieba",
           "--hidden-import", "distro"]
    for src, dst in ADD_DATA:
        cmd += ["--add-data", f"{src};{dst}"]
    cmd.append(str(ROOT / "main.py"))
    print("[build]", " ".join(cmd[2:]))
    subprocess.run(cmd, check=True, cwd=ROOT)


def sync(staged_app: Path, out: Path) -> None:
    if not (staged_app / "Mnemochat.exe").exists():
        raise SystemExit(f"staging 里没有 Mnemochat.exe：{staged_app}")
    out.mkdir(parents=True, exist_ok=True)

    # 程序文件：整体替换（_internal 是纯程序文件，不含任何用户数据）
    try:
        shutil.copy2(staged_app / "Mnemochat.exe", out / "Mnemochat.exe")
        internal = out / "_internal"
        if internal.exists():
            shutil.rmtree(internal)
        shutil.copytree(staged_app / "_internal", internal)
    except PermissionError as e:
        msg = (
            f"程序文件被占用，无法更新：{e}" + NL +
            "请先关闭正在运行的 Mnemochat（与 NapCat 无关），再重新执行本脚本。")
        raise SystemExit(msg)
    print(f"[sync] 程序文件已更新：{out / 'Mnemochat.exe'}")

    # 用户文件：缺失时才播种（已存在的一律不动）
    seeded = []
    for name in USER_FILES:
        src = staged_app / name
        dst = out / name
        if not src.exists():
            continue
        if src.is_dir():
            for f in src.rglob("*"):
                if not f.is_file():
                    continue
                target = dst / f.relative_to(src)
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, target)
                    seeded.append(str(target))
        elif not dst.exists():
            shutil.copy2(src, dst)
            seeded.append(str(dst))
    if seeded:
        print("[sync] 播种缺失的用户文件：")
        for p in seeded:
            print("   +", p)

    preserved = [n for n in ("config.yaml", ".env", "data") if (out / n).exists()]
    if preserved:
        print("[sync] 用户数据原样保留：" + "、".join(preserved))


def main() -> None:
    ap = argparse.ArgumentParser(description="构建/更新 Mnemochat 可执行版（保护用户数据）")
    ap.add_argument("-o", "--out", default=str(ROOT / "dist" / "Mnemochat"),
                    help="目标应用目录（默认 dist/Mnemochat）")
    ap.add_argument("--skip-build", action="store_true",
                    help="跳过 PyInstaller 构建，只同步 dist_new 里的 staging")
    args = ap.parse_args()

    out = Path(args.out).resolve()
    staging_dist = ROOT / "dist_new"
    if not args.skip_build:
        if staging_dist.exists():
            shutil.rmtree(staging_dist)   # staging 是纯构建产物，随便清
        build(staging_dist)
    sync(staging_dist / "Mnemochat", out)
    print(f"[done] 应用目录：{out}")


if __name__ == "__main__":
    main()
