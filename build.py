#!/usr/bin/env python3
"""从上游 ruijzhan/chnroute 拉取最新列表，按 ips.txt 二次加工，
产出小写命名、直接写 list=cn 的 cn.rsc / gfwlist.rsc。

构建阶段统一做三件事：
  1. 网段去重合并（collapse）：上游段、ips.txt 的段互相包含的只留最大段；
  2. 内网段并进 cn，两者一起去重合并，同一个 cn 列表里不允许有互相包含的段；
  3. 网段按数值排序、域名去重后按纯字典序排序。
"""

import hashlib
import ipaddress
import os
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

UPSTREAM = "https://raw.githubusercontent.com/ruijzhan/chnroute/master"
# 先走快的镜像，raw 源只当备胎（实测正门经常超时，白等两分钟）
MIRRORS = [
    "https://cdn.jsdelivr.net/gh/ruijzhan/chnroute@master",
    "https://raw.githubusercontent.com/ruijzhan/chnroute/master",
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


def parse_block(text: str, var: str) -> tuple:
    """解析 `:local <var> { ... }`，返回 (行列表, 起始行, 结束行, 条目字符串列表)。
    条目可能是网段也可能是域名，调用方按需再转类型。"""
    lines = text.splitlines()
    try:
        start = next(
            i for i, l in enumerate(lines) if l.strip().startswith(f":local {var}")
        )
        end = next(i for i in range(start, len(lines)) if lines[i].strip() == "}")
    except StopIteration:
        raise SystemExit(f"上游结构变了：找不到 :local {var} 块")
    items = [m for l in lines[start:end] for m in re.findall(r'"([^"]+)"', l)]
    return lines, start, end, items


def to_nets(items: list) -> list:
    nets = []
    for raw in items:
        try:
            nets.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            print(f"[warn] 忽略无效的网段：{raw}", flush=True)
    return nets


def render(lines: list, start: int, end: int, items: list) -> str:
    """把 items 写回 :local 块内部，替换掉原来的内容。"""
    body = [f'    "{i}";' for i in items]
    return "\n".join(lines[: start + 1] + body + lines[end:]) + "\n"


def normalize(nets: list) -> list:
    """合并重叠/相邻网段并排序：结果里任意两段都不互相包含。"""
    merged = ipaddress.collapse_addresses(nets)
    return sorted(merged, key=lambda n: (int(n.network_address), n.prefixlen))


def build_cn(text: str, custom_ips: list, lan_nets: list) -> tuple:
    """上游 CN.rsc → cn.rsc：改小写、并入自定义段与内网段、去重合并排序。"""
    out = text.replace("list=CN", "list=cn")
    lines, start, end, items = parse_block(out, "ipList")
    nets = to_nets(items)
    raw_count = len(nets)

    for raw in custom_ips:
        try:
            nets.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            print(f"[warn] 忽略无效的自定义段：{raw}", flush=True)

    # 内网段直接并进 cn：路由器只需导入一个文件
    nets.extend(lan_nets)
    final = normalize(nets)

    return render(lines, start, end, [str(n) for n in final]), raw_count, len(final)


def read_prev_meta() -> dict:
    """读上一次构建留下的 VERSION.txt，用于比对上游指纹和条目数。"""
    p = ROOT / "VERSION.txt"
    if not p.exists():
        return {}
    meta = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta


def prev_int(meta: dict, key: str) -> int:
    m = re.search(r"\d+", meta.get(key, ""))
    return int(m.group()) if m else 0


def selfcheck(final: list, n_domain: int, prev: dict) -> None:
    """提交前的最后一道关：任何一项不对劲就直接终止，坏表绝不出仓库门。"""
    errs = []

    if len(final) < 5000:
        errs.append(f"cn 只有 {len(final)} 条（少于 5000），明显是残缺表")

    prev_cn = prev_int(prev, "cn_entries")
    if prev_cn and len(final) < prev_cn * 0.9:
        errs.append(
            f"cn 条目从 {prev_cn} 掉到 {len(final)}，跌幅超过一成，疑似上游给错数据"
        )

    if n_domain < 3000:
        errs.append(f"域名只有 {n_domain} 个（少于 3000），疑似残缺")

    # 再聚合一次，条数不变才说明没有互相包含 / 可合并的段
    if len(list(ipaddress.collapse_addresses(final))) != len(final):
        errs.append("cn 内部仍存在互相包含或可合并的段")

    must = ["10.0.0.0/8", "192.168.0.0/16", "172.16.0.0/12", "127.0.0.0/8"]
    miss = [x for x in must if not any(ipaddress.ip_network(x).subnet_of(n) for n in final)]
    if miss:
        errs.append(f"内网段丢失：{miss}")

    if errs:
        raise SystemExit("自检没通过，终止构建（不会提交）：\n- " + "\n- ".join(errs))
    print(f"自检通过：cn {len(final)} 条，域名 {n_domain} 个", flush=True)


def strip_covered(domains: list) -> tuple:
    """去掉被父域覆盖的子域：每条都带 match-subdomain=yes，
    表里已有 wordpress.com 时，xxx.wordpress.com 就是多余的。返回 (剩余列表, 剔除数)。"""
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
    """域名去重 + 排序：纯字典序，直接用 Python 内建 sorted，
    一路顺着字母往下走，按字母查某个域名最快。"""
    return sorted(set(domains))


def build_gfw(text: str, custom_domains: list) -> tuple:
    """上游 gfwlist_v7.rsc → gfwlist.rsc：并入自定义域名、去 gfw_list 标签、
    去重、剔除被父域覆盖的子域、排序。"""
    cleaned = re.sub(r"\saddress-list=gfw_list", "", text)
    lines, start, end, domains = parse_block(cleaned, "domainList")
    all_d = [d.strip().lower() for d in domains] + [d.strip().lower() for d in custom_domains]
    final, n_dropped = strip_covered(sort_domains(all_d))
    out = render(lines, start, end, final)
    out = re.sub(
        r'/log info "GFW domain list updated with \d+ domains"',
        f'/log info "GFW domain list updated with {len(final)} domains"',
        out,
    )
    return out, len(final), n_dropped


def main() -> None:
    custom_ips = load_list(ROOT / "ips.txt")

    cn_text = fetch("CN.rsc")
    lan_text = fetch("LAN.rsc")
    gfw_text = fetch("gfwlist_v7.rsc")

    # 上游指纹：三个源文件都没变就说明没更新，定时任务可以直接收工
    sig = hashlib.sha256((cn_text + lan_text + gfw_text).encode()).hexdigest()[:16]
    prev = read_prev_meta()
    if os.environ.get("SKIP_IF_UNCHANGED", "").lower() == "true" and prev.get("upstream_sig") == sig:
        print(f"上游无变化（指纹 {sig}），本次跳过构建", flush=True)
        return

    _, _, _, lan_items = parse_block(lan_text, "lanIps")
    lan_nets = to_nets(lan_items)

    cn_out, raw_ip, n_ip = build_cn(cn_text, custom_ips, lan_nets)
    custom_domains = load_list(ROOT / "domains.txt")
    gfw_out, n_domain, n_dropped = build_gfw(gfw_text, custom_domains)

    selfcheck(to_nets(re.findall(r'^\s*"([^"]+)"', cn_out, re.M)), n_domain, prev)

    (ROOT / "cn.rsc").write_text(cn_out, encoding="utf-8")
    (ROOT / "gfwlist.rsc").write_text(gfw_out, encoding="utf-8")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    meta = [
        f"build_time: {now}",
        f"upstream: {UPSTREAM}",
        f"upstream_sig: {sig}",
        f"cn_entries: {n_ip} (上游 {raw_ip} + 自定义 {len(custom_ips)} + 内网 {len(lan_nets)}，去重合并后)",
        f"gfw_domains: {n_domain} (上游 + 自定义 {len(custom_domains)}，去重并剔除被父域覆盖的 {n_dropped} 条后)",
    ]
    for name in ("cn.rsc", "gfwlist.rsc"):
        data = (ROOT / name).read_bytes()
        meta.append(f"{name}: {len(data)} bytes, sha1={hashlib.sha1(data).hexdigest()}")
    (ROOT / "VERSION.txt").write_text("\n".join(meta) + "\n", encoding="utf-8")
    print("\n".join(meta))


if __name__ == "__main__":
    main()
