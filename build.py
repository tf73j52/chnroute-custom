#!/usr/bin/env python3
"""从上游 ruijzhan/chnroute 拉取最新列表，按 custom_ips.txt 二次加工，
产出小写命名、直接写 list=cn 的 cn.rsc / lan.rsc / gfwlist.rsc。"""

import hashlib
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

UPSTREAM = "https://raw.githubusercontent.com/ruijzhan/chnroute/master"
MIRRORS = [
    "https://raw.githubusercontent.com/ruijzhan/chnroute/master",
    "https://cdn.jsdelivr.net/gh/ruijzhan/chnroute@master",
]
TIMEOUT = 120
ROOT = Path(__file__).resolve().parent


def fetch(name: str) -> str:
    """依次尝试主源与镜像源，任一成功即可。"""
    last = None
    for base in MIRRORS:
        try:
            req = urllib.request.Request(
                f"{base}/{name}", headers={"User-Agent": "chnroute-custom-build"}
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read().decode("utf-8")
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"[warn] {base}/{name} 失败：{e}", flush=True)
    raise SystemExit(f"所有源都拉不到 {name}：{last}")


def load_list(path: Path) -> list:
    if not path.exists():
        return []
    return [
        l.strip()
        for l in path.read_text(encoding="utf-8").splitlines()
        if l.strip() and not l.strip().startswith("#")
    ]


def build_cn(text: str, custom_ips: list) -> tuple:
    """上游 CN.rsc → cn.rsc：list=CN 改小写，并并入 custom_ips。"""
    out = text.replace("list=CN", "list=cn")
    lines = out.splitlines()
    try:
        start = next(
            i for i, l in enumerate(lines) if l.strip().startswith(":local ipList")
        )
        end = next(i for i in range(start, len(lines)) if lines[i].strip() == "}")
    except StopIteration:
        raise SystemExit("上游 CN.rsc 结构变了：找不到 :local ipList 块")

    existing = {m.group(1) for l in lines[start:end] for m in [re.search(r'"([^"]+)"', l)] if m}
    added = [f'    "{ip}";' for ip in custom_ips if ip not in existing]
    return "\n".join(lines[:end] + added + lines[end:]) + "\n", len(added)


def build_lan(text: str) -> str:
    """上游 LAN.rsc → lan.rsc：直接写进 cn 列表，无需 RouterOS 再改名。"""
    out = text.replace("list=LAN", "list=cn")
    return out if out.endswith("\n") else out + "\n"


def build_gfw(text: str) -> tuple:
    """上游 gfwlist_v7.rsc → gfwlist.rsc：去掉 gfw_list 标签，不加任何自定义域名。"""
    cleaned = re.sub(r"\saddress-list=gfw_list", "", text)
    return cleaned, "address-list=gfw_list" in text


def main() -> None:
    custom_ips = load_list(ROOT / "custom_ips.txt")

    cn_out, n_ip = build_cn(fetch("CN.rsc"), custom_ips)
    lan_out = build_lan(fetch("LAN.rsc"))
    gfw_out, had_tag = build_gfw(fetch("gfwlist_v7.rsc"))

    (ROOT / "cn.rsc").write_text(cn_out, encoding="utf-8")
    (ROOT / "lan.rsc").write_text(lan_out, encoding="utf-8")
    (ROOT / "gfwlist.rsc").write_text(gfw_out, encoding="utf-8")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    meta = [
        f"build_time: {now}",
        f"upstream: {UPSTREAM}",
        f"custom_ips_added: {n_ip}",
        f"gfw_list_tag_removed: {had_tag}",
    ]
    for name in ("cn.rsc", "lan.rsc", "gfwlist.rsc"):
        data = (ROOT / name).read_bytes()
        meta.append(f"{name}: {len(data)} bytes, sha1={hashlib.sha1(data).hexdigest()}")
    (ROOT / "VERSION.txt").write_text("\n".join(meta) + "\n", encoding="utf-8")
    print("\n".join(meta))


if __name__ == "__main__":
    main()
