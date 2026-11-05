#!/usr/bin/env python3
import hashlib, ipaddress, os, re, urllib.request
from datetime import datetime, timezone
from pathlib import Path

RAW = "https://raw.githubusercontent.com/ruijzhan/chnroute/master"
ROOT = Path(__file__).resolve().parent
MIN_CN, MIN_DOMAIN, MAX_DROP = 5000, 3000, 0.9
PRIVATE = tuple(ipaddress.ip_network(x) for x in ("10.0.0.0/8", "192.168.0.0/16", "172.16.0.0/12", "127.0.0.0/8"))
IPNet = ipaddress.IPv4Network | ipaddress.IPv6Network


def fetch(name: str) -> str:
    req = urllib.request.Request(f"{RAW}/{name}", headers={"User-Agent": "chnroute-custom-build"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read().decode()


def load(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [s for l in path.read_text(encoding="utf-8").splitlines() if (s := l.split("#", 1)[0].strip())]


def block(text: str, var: str) -> tuple[list[str], int, int]:
    lines = text.splitlines()
    s = next(i for i, l in enumerate(lines) if l.strip().startswith(f":local {var}"))
    e = next(i for i in range(s, len(lines)) if lines[i].strip() == "}")
    return lines, s, e


def items(text: str, var: str) -> list[str]:
    lines, s, e = block(text, var)
    return [m for l in lines[s:e] for m in re.findall(r'"([^"]+)"', l)]


def render(text: str, var: str, vals: list[str]) -> str:
    lines, s, e = block(text, var)
    return "\n".join(lines[: s + 1] + [f'    "{v}";' for v in vals] + lines[e:]) + "\n"


def nets(vals: list[str], label: str = "网段") -> list[IPNet]:
    out: list[IPNet] = []
    for raw in vals:
        try:
            out.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            print(f"[warn] 忽略无效{label}：{raw}", flush=True)
    return out


def normalize(ns: list[IPNet]) -> list[IPNet]:
    return sorted(ipaddress.collapse_addresses(ns), key=lambda n: (int(n.network_address), n.prefixlen))


def build_cn(text: str, custom: list[str], lan: list[IPNet]) -> tuple[str, int, list[IPNet]]:
    out = text.replace("list=CN", "list=cn")
    ns = nets(items(out, "ipList"))
    raw = len(ns)
    final = normalize(ns + nets(custom, "自定义段") + lan)
    return render(out, "ipList", [str(n) for n in final]), raw, final


def prev() -> dict[str, str]:
    p = ROOT / "VERSION.txt"
    if not p.exists():
        return {}
    return {k.strip(): v.strip() for l in p.read_text(encoding="utf-8").splitlines() if ":" in l for k, _, v in [l.partition(":")]}


def check(ns: list[IPNet], n_dom: int, prev_m: dict[str, str]) -> None:
    errs: list[str] = []
    if len(ns) < MIN_CN:
        errs.append(f"cn 仅 {len(ns)} 条（<{MIN_CN}）")
    m = re.search(r"\d+", prev_m.get("cn_entries", ""))
    if m and len(ns) < int(m.group()) * MAX_DROP:
        errs.append(f"cn 从 {m.group()} 跌至 {len(ns)}（跌幅>{(1 - MAX_DROP):.0%}）")
    if n_dom < MIN_DOMAIN:
        errs.append(f"域名仅 {n_dom} 个（<{MIN_DOMAIN}）")
    if missing := [str(x) for x in PRIVATE if not any(x.subnet_of(n) for n in ns)]:
        errs.append(f"内网段丢失：{missing}")
    if errs:
        raise SystemExit("自检未通过：\n- " + "\n- ".join(errs))
    print(f"自检通过：cn {len(ns)} 条，域名 {n_dom} 个", flush=True)


def is_listed(dom: str, names: set[str], *, self_: bool) -> bool:
    parts = dom.split(".")
    start = 0 if self_ else 1
    return any(".".join(parts[i:]) in names for i in range(start, len(parts) - 1))


def build_gfw(text: str, custom: list[str], exclude: list[str]) -> tuple[str, int, int, int]:
    out = text.replace("address-list=gfw_list", "address-list=gfwlist")
    all_d = [d.strip().lower() for d in items(out, "domainList") + custom]
    ex = {d.lower() for d in exclude}
    kept = [d for d in all_d if not is_listed(d, ex, self_=True)]
    n_ex = len(all_d) - len(kept)
    dset = set(kept)
    final = [d for d in sorted(dset) if not is_listed(d, dset, self_=False)]
    n_drop = len(dset) - len(final)
    out = render(out, "domainList", final)
    out = re.sub(r'/log info "GFW domain list updated with \d+ domains"', f'/log info "GFW domain list updated with {len(final)} domains"', out)
    return out, len(final), n_drop, n_ex


def main() -> None:
    custom_ips = load(ROOT / "ips.txt")
    cn, lan, gfw = fetch("CN.rsc"), fetch("LAN.rsc"), fetch("gfwlist_v7.rsc")
    sig = hashlib.sha256((cn + lan + gfw).encode()).hexdigest()[:16]
    prev_m = prev()
    if os.environ.get("SKIP_IF_UNCHANGED", "").lower() == "true" and prev_m.get("upstream_sig") == sig:
        print(f"上游无变化（{sig}），跳过构建", flush=True)
        return
    lan_nets = nets(items(lan, "lanIps"))
    cn_out, raw_ip, cn_nets = build_cn(cn, custom_ips, lan_nets)
    custom_dom = load(ROOT / "domains.txt")
    ex_dom = load(ROOT / "exclude.txt")
    gfw_out, n_dom, n_drop, n_ex = build_gfw(gfw, custom_dom, ex_dom)
    check(cn_nets, n_dom, prev_m)
    (ROOT / "cn.rsc").write_text(cn_out, encoding="utf-8")
    (ROOT / "gfwlist.rsc").write_text(gfw_out, encoding="utf-8")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    meta = "\n".join([
        f"build_time: {now}",
        f"upstream: {RAW}",
        f"upstream_sig: {sig}",
        f"cn_entries: {len(cn_nets)}（上游 {raw_ip} + 自定义 {len(custom_ips)} + 内网 {len(lan_nets)}）",
        f"gfw_domains: {n_dom}（上游+自定义 {len(custom_dom)}，排除 {n_ex}，剔除子域 {n_drop}）",
        *[f"{name}: {len(data := (ROOT / name).read_bytes())} bytes, sha1={hashlib.sha1(data).hexdigest()}" for name in ("cn.rsc", "gfwlist.rsc")],
    ]) + "\n"
    (ROOT / "VERSION.txt").write_text(meta, encoding="utf-8")
    print(meta, end="")


if __name__ == "__main__":
    main()
