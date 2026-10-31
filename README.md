# chnroute-custom

从上游 [ruijzhan/chnroute](https://github.com/ruijzhan/chnroute) 拉取国内 IP 段与 gfw 域名，合并本地定制（`ips.txt` / `domains.txt` / `exclude.txt`），产出 RouterOS 可直接导入的 `cn.rsc` / `gfwlist.rsc`。

## 环境要求

- Python 3.11+
- 可访问 GitHub 与 jsDelivr

## 安装步骤

```bash
git clone https://github.com/tf73j52/chnroute-custom.git
cd chnroute-custom
```

## 核心调用示例

```bash
python3 build.py
```

产物：`cn.rsc`（国内段直连白名单）、`gfwlist.rsc`（被墙域名走代理 DNS，FWD 记录自带 `address-list=gfwlist`，解析 IP 自动写入防火墙 `gfwlist` 列表）、`VERSION.txt`（构建元数据）。

RouterOS 导入：

```routeros
:global dnsserver "proxy"
:local logOpen [/system logging find disabled=no]
/system logging disable [find]
:foreach f in={"cn.rsc";"gfwlist.rsc"} do={ /tool fetch url=("https://cdn.jsdelivr.net/gh/tf73j52/chnroute-custom@main/" . $f) dst-path=$f duration=10s check-certificate=no; /import $f; /file remove [find name=$f] }
:foreach lid in=$logOpen do={ /system logging enable $lid }
```

> `dnsserver` 需指向真实存在的代理 DNS（IP 或同名 forwarder）；日志三行需整段粘贴，避免导入上万条记录时刷屏。
