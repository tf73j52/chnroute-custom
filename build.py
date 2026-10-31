#!/usr/bin/env python3
"""从上游 chnroute 拉取并合并本地定制项，产出 RouterOS 分流脚本。"""

import hashlib
import ipaddress
import os
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

UPSTREAM_RAW = "https://raw.githubusercontent.com/ruijzhan/chnroute/master"
MIRRORS = [
    "https://cdn.jsdelivr.net/gh/ruijzhan/chnroute@master",
    UPSTREAM_RAW,
]
TIMEOUT = 120
ROOT = Path(__file__).resolve().parent

# 自检阈值
MIN_CN_ENTRIES = 5000
MIN_DOMAIN_ENTRIES = 3000
MAX_CN_DROP_RATIO = 0.9  # 相对上一版最大跌幅，超过视为异常
PRIVATE_NETS = ("10.0.0.0/8", "192.168.0.0/16", "172.16.0.0/12", "127.0.0.0/8")

IPNet = ipaddress.IPv4Network | ipaddress.IPv6Network


def fetch(name: str) -> str:
    """依次尝试镜像拉取上游文件，全部失败才终止。"""
    last_error: Exception | None = None
    for base in MIRRORS:
        url = f"{base}/{name}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "chnroute-custom-build"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read().decode("utf-8")
        except Exception as exc:  # 网络异常降级到下一个镜像
            last_error = exc
            print(f"[warn] {url}：{exc}", flush=True)
    raise SystemExit(f"拉取 {name} 失败：{last_error}")


def load_list(path: Path) -> list[str]:
    """读取定制文件：每行一条，忽略空行与 # 注释。"""
    if not path.exists():
        return []
    return [
        stripped
        for line in path.read_text(encoding="utf-8").splitlines()
        if (stripped := line.strip()) and not stripped.startswith("#")
    ]


def _locate_block(lines: list[str], var: str) -> tuple[int, int]:
    """定位 :local {var} {...} 块，返回 (起始行, 结束行)。"""
    try:
        start = next(i for i, line in enumerate(lines) if line.strip().startswith(f":local {var}"))
        end = next(i for i in range(start, len(lines)) if lines[i].strip() == "}")
    except StopIteration:
        raise SystemExit(f"上游结构变化：缺少 :local {var}") from None
    return start, end


def extract_items(text: str, var: str) -> list[str]:
    """提取 :local {var} 块中的字符串元素。"""
    lines = text.splitlines()
    start, end = _locate_block(lines, var)
    return [m for line in lines[start:end] for m in re.findall(r'"([^"]+)"', line)]


def render_block(text: str, var: str, items: list[str]) -> str:
    """用新元素列表重写 :local {var} 块，保持其余内容不变。"""
    lines = text.splitlines()
    start, end = _locate_block(lines, var)
    body = [f'    "{item}";' for item in items]
    return "\n".join(lines[: start + 1] + body + lines[end:]) + "\n"


def to_nets(items: list[str], label: str = "网段") -> list[IPNet]:
    """把字符串解析为网段对象，无效项告警并跳过。"""
    nets: list[IPNet] = []
    for raw in items:
        try:
            nets.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            print(f"[warn] 忽略无效{label}：{raw}", flush=True)
    return nets


def normalize(nets: list[IPNet]) -> list[IPNet]:
    """合并互相包含/相邻的网段并数值排序。"""
    return sorted(ipaddress.collapse_addresses(nets), key=lambda n: (int(n.network_address), n.prefixlen))


def build_cn(text: str, custom_ips: list[str], lan_nets: list[IPNet]) -> tuple[str, int, list[IPNet]]:
    out = text.replace("list=CN", "list=cn")
    nets = to_nets(extract_items(out, "ipList"))
    raw_count = len(nets)
    nets += to_nets(custom_ips, label="自定义段")
    nets += lan_nets
    final = normalize(nets)
    return render_block(out, "ipList", [str(n) for n in final]), raw_count, final


def read_prev_meta() -> dict[str, str]:
    path = ROOT / "VERSION.txt"
    if not path.exists():
        return {}
    meta: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta


def prev_int(meta: dict[str, str], key: str) -> int:
    match = re.search(r"\d+", meta.get(key, ""))
    return int(match.group()) if match else 0


def selfcheck(cn_nets: list[IPNet], n_domain: int, prev: dict[str, str]) -> None:
    errors: list[str] = []
    if len(cn_nets) < MIN_CN_ENTRIES:
        errors.append(f"cn 仅 {len(cn_nets)} 条（<{MIN_CN_ENTRIES}）")
    prev_cn = prev_int(prev, "cn_entries")
    if prev_cn and len(cn_nets) < prev_cn * MAX_CN_DROP_RATIO:
        errors.append(f"cn 从 {prev_cn} 跌至 {len(cn_nets)}（跌幅>{(1 - MAX_CN_DROP_RATIO):.0%}）")
    if n_domain < MIN_DOMAIN_ENTRIES:
        errors.append(f"域名仅 {n_domain} 个（<{MIN_DOMAIN_ENTRIES}）")
    private = [ipaddress.ip_network(x) for x in PRIVATE_NETS]
    missing = [str(x) for x in private if not any(x.subnet_of(n) for n in cn_nets)]
    if missing:
        errors.append(f"内网段丢失：{missing}")
    if errors:
        raise SystemExit("自检未通过：\n- " + "\n- ".join(errors))
    print(f"自检通过：cn {len(cn_nets)} 条，域名 {n_domain} 个", flush=True)


def is_listed(domain: str, names: set[str], *, include_self: bool) -> bool:
    """判断 domain 自身（可选）或其任一父域名是否在 names 中。"""
    parts = domain.split(".")
    start = 0 if include_self else 1
    return any(".".join(parts[i:]) in names for i in range(start, len(parts) - 1))


def strip_covered(domains: list[str]) -> tuple[list[str], int]:
    """剔除已被父域名覆盖的子域，返回 (保留项, 剔除数)。"""
    domset = set(domains)
    kept = [d for d in domains if not is_listed(d, domset, include_self=False)]
    return kept, len(domains) - len(kept)


def build_gfw(text: str, custom_domains: list[str], exclude_domains: list[str]) -> tuple[str, int, int, int]:
    cleaned = text.replace("address-list=gfw_list", "address-list=gfwlist")
    domains = extract_items(cleaned, "domainList")
    all_domains = [d.strip().lower() for d in domains + custom_domains]
    exclude = set(exclude_domains)
    kept = [d for d in all_domains if not is_listed(d, exclude, include_self=True)]
    n_excluded = len(all_domains) - len(kept)
    final, n_dropped = strip_covered(sorted(set(kept)))
    out = render_block(cleaned, "domainList", final)
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

    lan_nets = to_nets(extract_items(lan_text, "lanIps"))
    cn_out, raw_ip, cn_nets = build_cn(cn_text, custom_ips, lan_nets)
    custom_domains = load_list(ROOT / "domains.txt")
    exclude_domains = load_list(ROOT / "exclude.txt")
    gfw_out, n_domain, n_dropped, n_excluded = build_gfw(gfw_text, custom_domains, exclude_domains)

    selfcheck(cn_nets, n_domain, prev)

    (ROOT / "cn.rsc").write_text(cn_out, encoding="utf-8")
    (ROOT / "gfwlist.rsc").write_text(gfw_out, encoding="utf-8")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    meta = [
        f"build_time: {now}",
        f"upstream: {UPSTREAM_RAW}",
        f"upstream_sig: {sig}",
        f"cn_entries: {len(cn_nets)}（上游 {raw_ip} + 自定义 {len(custom_ips)} + 内网 {len(lan_nets)}）",
        f"gfw_domains: {n_domain}（上游+自定义 {len(custom_domains)}，排除 {n_excluded}，剔除子域 {n_dropped}）",
    ]
    for name in ("cn.rsc", "gfwlist.rsc"):
        data = (ROOT / name).read_bytes()
        meta.append(f"{name}: {len(data)} bytes, sha1={hashlib.sha1(data).hexdigest()}")
    (ROOT / "VERSION.txt").write_text("\n".join(meta) + "\n", encoding="utf-8")
    print("\n".join(meta))


if __name__ == "__main__":
    main()
