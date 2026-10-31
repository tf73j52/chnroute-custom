#!/usr/bin/env python3

import hashlib
import ipaddress
import os
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

UPSTREAM = "https://raw.githubusercontent.com/ruijzhan/chnroute/master"
MIRRORS = [
    "https://cdn.jsdelivr.net/gh/ruijzhan/chnroute@master",
    "https://raw.githubusercontent.com/ruijzhan/chnroute/master",
]
TIMEOUT = 120
ROOT = Path(__file__).resolve().parent


def fetch(name: str) -> str:
    last = None
    for base in MIRRORS:
        try:
            req = urllib.request.Request(f"{base}/{name}", headers={"User-Agent": "chnroute-custom-build"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read().decode("utf-8")
        except Exception as e:
            last = e
            print(f"[warn] {base}/{name}：{e}", flush=True)
    raise SystemExit(f"拉取 {name} 失败：{last}")


def load_list(path: Path) -> list:
    if not path.exists():
        return []
    return [s for l in path.read_text(encoding="utf-8").splitlines() if (s := l.strip()) and not s.startswith("#")]


def parse_block(text: str, var: str) -> tuple:
    lines = text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip().startswith(f":local {var}"))
        end = next(i for i in range(start, len(lines)) if lines[i].strip() == "}")
    except StopIteration:
        raise SystemExit(f"上游结构变化：缺少 :local {var}")
    items = [m for l in lines[start:end] for m in re.findall(r'"([^"]+)"', l)]
    return lines, start, end, items


def to_nets(items: list) -> list:
    nets = []
    for raw in items:
        try:
            nets.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            print(f"[warn] 忽略无效网段：{raw}", flush=True)
    return nets


def render(lines: list, start: int, end: int, items: list) -> str:
    body = [f'    "{i}";' for i in items]
    return "\n".join(lines[: start + 1] + body + lines[end:]) + "\n"


def normalize(nets: list) -> list:
    return sorted(ipaddress.collapse_addresses(nets), key=lambda n: (int(n.network_address), n.prefixlen))


def build_cn(text: str, custom_ips: list, lan_nets: list) -> tuple:
    out = text.replace("list=CN", "list=cn")
    lines, start, end, items = parse_block(out, "ipList")
    nets = to_nets(items)
    raw_count = len(nets)
    for raw in custom_ips:
        try:
            nets.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            print(f"[warn] 忽略无效自定义段：{raw}", flush=True)
    nets.extend(lan_nets)
    final = normalize(nets)
    return render(lines, start, end, [str(n) for n in final]), raw_count, len(final)


def read_prev_meta() -> dict:
    p = ROOT / "VERSION.txt"
    if not p.exists():
        return {}
    return {
        k.strip(): v.strip()
        for line in p.read_text(encoding="utf-8").splitlines()
        if ":" in line
        for k, v in [line.split(":", 1)]
    }


def prev_int(meta: dict, key: str) -> int:
    m = re.search(r"\d+", meta.get(key, ""))
    return int(m.group()) if m else 0


def selfcheck(final: list, n_domain: int, prev: dict) -> None:
    errs = []
    if len(final) < 5000:
        errs.append(f"cn 仅 {len(final)} 条（<5000）")
    prev_cn = prev_int(prev, "cn_entries")
    if prev_cn and len(final) < prev_cn * 0.9:
        errs.append(f"cn 从 {prev_cn} 跌至 {len(final)}（跌幅>10%）")
    if n_domain < 3000:
        errs.append(f"域名仅 {n_domain} 个（<3000）")
    if len(list(ipaddress.collapse_addresses(final))) != len(final):
        errs.append("cn 仍存在可合并网段")
    must = ["10.0.0.0/8", "192.168.0.0/16", "172.16.0.0/12", "127.0.0.0/8"]
    miss = [x for x in must if not any(ipaddress.ip_network(x).subnet_of(n) for n in final)]
    if miss:
        errs.append(f"内网段丢失：{miss}")
    if errs:
        raise SystemExit("自检未通过：\n- " + "\n- ".join(errs))
    print(f"自检通过：cn {len(final)} 条，域名 {n_domain} 个", flush=True)


def strip_covered(domains: list) -> tuple:
    domset = set(domains)
    kept, dropped = [], 0
    for d in domains:
        parts = d.split(".")
        if any(".".join(parts[i:]) in domset for i in range(1, len(parts) - 1)):
            dropped += 1
        else:
            kept.append(d)
    return kept, dropped


def sort_domains(domains: list) -> list:
    return sorted(set(domains))


def is_blocked(domain: str, excl: set) -> bool:
    parts = domain.split(".")
    return any(".".join(parts[i:]) in excl for i in range(len(parts) - 1))


def build_gfw(text: str, custom_domains: list, exclude_domains: list) -> tuple:
    cleaned = re.sub(r"\saddress-list=gfw_list", "", text)
    lines, start, end, domains = parse_block(cleaned, "domainList")
    all_d = [d.strip().lower() for d in domains + custom_domains]
    excl = set(exclude_domains)
    kept = [d for d in all_d if not is_blocked(d, excl)]
    n_excluded = len(all_d) - len(kept)
    final, n_dropped = strip_covered(sort_domains(kept))
    out = render(lines, start, end, final)
    out = re.sub(
        r'/log info "GFW domain list updated with \d+ domains"',
        f'/log info "GFW domain list updated with {len(final)} domains"',
        out,
    )
    return out, len(final), n_dropped, n_excluded


def main() -> None:
    custom_ips = load_list(ROOT / "ips.txt")
    cn_text = fetch("CN.rsc")
    lan_text = fetch("LAN.rsc")
    gfw_text = fetch("gfwlist_v7.rsc")

    sig = hashlib.sha256((cn_text + lan_text + gfw_text).encode()).hexdigest()[:16]
    prev = read_prev_meta()
    if os.environ.get("SKIP_IF_UNCHANGED", "").lower() == "true" and prev.get("upstream_sig") == sig:
        print(f"上游无变化（{sig}），跳过构建", flush=True)
        return

    _, _, _, lan_items = parse_block(lan_text, "lanIps")
    lan_nets = to_nets(lan_items)

    cn_out, raw_ip, n_ip = build_cn(cn_text, custom_ips, lan_nets)
    custom_domains = load_list(ROOT / "domains.txt")
    exclude_domains = load_list(ROOT / "exclude.txt")
    gfw_out, n_domain, n_dropped, n_excluded = build_gfw(gfw_text, custom_domains, exclude_domains)

    selfcheck(to_nets(re.findall(r'^\s*"([^"]+)"', cn_out, re.M)), n_domain, prev)

    (ROOT / "cn.rsc").write_text(cn_out, encoding="utf-8")
    (ROOT / "gfwlist.rsc").write_text(gfw_out, encoding="utf-8")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    meta = [
        f"build_time: {now}",
        f"upstream: {UPSTREAM}",
        f"upstream_sig: {sig}",
        f"cn_entries: {n_ip}（上游 {raw_ip} + 自定义 {len(custom_ips)} + 内网 {len(lan_nets)}）",
        f"gfw_domains: {n_domain}（上游+自定义 {len(custom_domains)}，排除 {n_excluded}，剔除子域 {n_dropped}）",
    ]
    for name in ("cn.rsc", "gfwlist.rsc"):
        data = (ROOT / name).read_bytes()
        meta.append(f"{name}: {len(data)} bytes, sha1={hashlib.sha1(data).hexdigest()}")
    (ROOT / "VERSION.txt").write_text("\n".join(meta) + "\n", encoding="utf-8")
    print("\n".join(meta))


if __name__ == "__main__":
    main()
