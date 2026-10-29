#!/usr/bin/env python3
"""从上游 ruijzhan/chnroute 拉取最新列表，按 custom_ips.txt 二次加工，
产出小写命名、直接写 list=cn 的 cn.rsc / lan.rsc / gfwlist.rsc。

构建阶段统一做三件事：
  1. 网段去重合并（collapse）：上游段、custom_ips.txt 的段互相包含的只留最大段；
  2. cn 与 lan 之间做差集，两个文件写入同一个 cn 列表，不允许有重叠；
  3. 网段按数值排序、域名按层级排序（同一主域的子域聚在一起）。
"""

import hashlib
import ipaddress
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
# 内网段已并入 cn.rsc，lan.rsc 只留一条日志占位，保证旧脚本导入时不报错、不中断
LAN_PLACEHOLDER = (
    '/log info "lan.rsc 已并入 cn.rsc，此文件为空，仅为兼容旧脚本，可安全忽略"\n'
)
# 域名排序方式：hierarchy（按层级，同主域聚一起） / alpha（纯字典序）
DOMAIN_SORT = "hierarchy"
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


def subtract_all(nets: list, excludes: list) -> list:
    """从 nets 中减去所有 exclude。exclude 必须是某段的子网才能精确相减，
    部分重叠时保留原段（CIDR 无法表示，强拆会误伤）。"""
    out = []
    for net in nets:
        cur = [net]
        for ex in excludes:
            if not any(c.overlaps(ex) for c in cur):
                continue
            nxt = []
            for c in cur:
                if not c.overlaps(ex):
                    nxt.append(c)
                    continue
                try:
                    nxt.extend(c.address_exclude(ex))
                except ValueError:
                    nxt.append(c)
            cur = nxt
        out.extend(cur)
    return out


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

    # 挖洞：custom_exclude.txt 里声明的更精确前缀
    excluded = []
    for raw in load_list(ROOT / "custom_exclude.txt"):
        try:
            excluded.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            print(f"[warn] 忽略无效的排除段：{raw}", flush=True)
    if excluded:
        nets = subtract_all(nets, excluded)

    # 内网段直接并进 cn：路由器只需导入一个文件
    nets.extend(lan_nets)
    final = normalize(nets)

    return render(lines, start, end, [str(n) for n in final]), raw_count, len(final)


def sort_domains(domains: list) -> list:
    """域名去重 + 排序。
    hierarchy（默认）：按域名层级排，同一主域下的子域聚在一起，同一后缀归为一组。
    alpha：纯字典序，按字母查某个域名最快。"""
    uniq = sorted(set(domains))
    if DOMAIN_SORT == "alpha":
        return uniq
    return sorted(uniq, key=lambda d: (tuple(reversed(d.split("."))), d))


def build_gfw(text: str) -> tuple:
    """上游 gfwlist_v7.rsc → gfwlist.rsc：去 gfw_list 标签，域名排序。"""
    cleaned = re.sub(r"\saddress-list=gfw_list", "", text)
    lines, start, end, domains = parse_block(cleaned, "domainList")
    final = sort_domains(d.strip().lower() for d in domains)
    out = render(lines, start, end, final)
    out = re.sub(
        r'/log info "GFW domain list updated with \d+ domains"',
        f'/log info "GFW domain list updated with {len(final)} domains"',
        out,
    )
    return out, "address-list=gfw_list" in text, len(final)


def main() -> None:
    custom_ips = load_list(ROOT / "custom_ips.txt")

    lan_text = fetch("LAN.rsc")
    _, _, _, lan_items = parse_block(lan_text, "lanIps")
    lan_nets = to_nets(lan_items)

    cn_out, raw_ip, n_ip = build_cn(fetch("CN.rsc"), custom_ips, lan_nets)
    gfw_out, had_tag, n_domain = build_gfw(fetch("gfwlist_v7.rsc"))

    (ROOT / "cn.rsc").write_text(cn_out, encoding="utf-8")
    (ROOT / "lan.rsc").write_text(LAN_PLACEHOLDER, encoding="utf-8")
    (ROOT / "gfwlist.rsc").write_text(gfw_out, encoding="utf-8")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    meta = [
        f"build_time: {now}",
        f"upstream: {UPSTREAM}",
        f"cn_entries: {n_ip} (上游 {raw_ip} + 自定义 {len(custom_ips)} + 内网 {len(lan_nets)}，去重合并后)",
        f"lan_entries: 0 (内网段已并入 cn.rsc，lan.rsc 为空占位)",
        f"gfw_domains: {n_domain}",
        f"excluded_ranges: {len(load_list(ROOT / 'custom_exclude.txt'))}",
        f"gfw_list_tag_removed: {had_tag}",
    ]
    for name in ("cn.rsc", "lan.rsc", "gfwlist.rsc"):
        data = (ROOT / name).read_bytes()
        meta.append(f"{name}: {len(data)} bytes, sha1={hashlib.sha1(data).hexdigest()}")
    (ROOT / "VERSION.txt").write_text("\n".join(meta) + "\n", encoding="utf-8")
    print("\n".join(meta))


if __name__ == "__main__":
    main()
