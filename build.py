#!/usr/bin/env python3
"""从上游 ruijzhan/chnroute 拉取最新列表，按 custom_ips.txt / custom_domains.txt
二次加工后生成 CN.rsc / LAN.rsc / gfwlist_v7.rsc，供 RouterOS 直接拉取。"""

import hashlib
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

UPSTREAM = "https://raw.githubusercontent.com/ruijzhan/chnroute/master"
TIMEOUT = 60
ROOT = Path(__file__).resolve().parent


def fetch(name: str) -> str:
    req = urllib.request.Request(
        f"{UPSTREAM}/{name}", headers={"User-Agent": "chnroute-custom-build"}
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8")


def load_list(path: Path) -> list:
    out = []
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def build_cn(text: str, custom_ips: list) -> tuple:
    """把 custom_ips 并入 CN.rsc 的 :local ipList 块，已存在的不重复添加。"""
    lines = text.splitlines()
    try:
        start = next(
            i for i, l in enumerate(lines) if l.strip().startswith(":local ipList")
        )
        end = next(i for i in range(start, len(lines)) if lines[i].strip() == "}")
    except StopIteration:
        raise SystemExit("CN.rsc 结构变了：找不到 :local ipList 块，请检查上游文件")

    existing = set()
    for l in lines[start:end]:
        m = re.search(r'"([^"]+)"', l)
        if m:
            existing.add(m.group(1))

    indent = "    "
    added = [f'{indent}"{ip}";' for ip in custom_ips if ip not in existing]
    new_lines = lines[:end] + added + lines[end:]
    return "\n".join(new_lines) + "\n", len(added)


def build_lan(text: str) -> str:
    """LAN.rsc 不做改动，原样输出。"""
    return text if text.endswith("\n") else text + "\n"


def build_gfw(text: str, custom_domains: list) -> tuple:
    """1) 去掉 address-list=gfw_list 标签
       2) 在 /ip dns cache flush 之前追加自定义域名"""
    # 去掉标签（兼容参数顺序不同的写法）
    cleaned = re.sub(r"\saddress-list=gfw_list", "", text)
    removed_tag = "address-list=gfw_list" in text

    # 找出已有域名，避免重复
    existing = set(re.findall(r'"([a-z0-9][a-z0-9.\-]*\.[a-z]{2,})";', cleaned))

    adds = [
        f"/ip dns static add forward-to=$dnsserver type=FWD match-subdomain=yes name={d}"
        for d in custom_domains
        if d not in existing
    ]

    if adds:
        block = "\n# --- custom domains (appended by chnroute-custom) ---\n" + "\n".join(
            adds
        )
        if "/ip dns cache flush" in cleaned:
            cleaned = cleaned.replace(
                "/ip dns cache flush", block + "\n\n/ip dns cache flush", 1
            )
        else:
            cleaned = cleaned.rstrip() + "\n" + block + "\n"

    return cleaned, removed_tag, len(adds)


def main() -> None:
    custom_ips = load_list(ROOT / "custom_ips.txt")
    custom_domains = load_list(ROOT / "custom_domains.txt")

    cn_raw = fetch("CN.rsc")
    lan_raw = fetch("LAN.rsc")
    gfw_raw = fetch("gfwlist_v7.rsc")

    cn_out, n_ip = build_cn(cn_raw, custom_ips)
    lan_out = build_lan(lan_raw)
    gfw_out, had_tag, n_domain = build_gfw(gfw_raw, custom_domains)

    (ROOT / "CN.rsc").write_text(cn_out, encoding="utf-8")
    (ROOT / "LAN.rsc").write_text(lan_out, encoding="utf-8")
    (ROOT / "gfwlist_v7.rsc").write_text(gfw_out, encoding="utf-8")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    meta = [
        f"build_time: {now}",
        f"upstream: {UPSTREAM}",
        f"custom_ips_added: {n_ip}",
        f"custom_domains_added: {n_domain}",
        f"gfw_list_tag_removed: {had_tag}",
    ]
    for name in ("CN.rsc", "LAN.rsc", "gfwlist_v7.rsc"):
        data = (ROOT / name).read_bytes()
        meta.append(f"{name}: {len(data)} bytes, sha1={hashlib.sha1(data).hexdigest()}")
    (ROOT / "VERSION.txt").write_text("\n".join(meta) + "\n", encoding="utf-8")

    print("\n".join(meta))


if __name__ == "__main__":
    main()
