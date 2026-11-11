# chnroute-custom

从上游 [ruijzhan/chnroute](https://github.com/ruijzhan/chnroute) 拉取国内 IP 段与 gfw 域名，合并本地定制（`ips.txt` / `domains.txt` / `exclude.txt`），产出 RouterOS 可直接导入的 `cn.rsc` / `gfwlist.rsc`。

## 环境要求

- Python 3.11+
- 可访问 GitHub

## 安装

```bash
git clone https://github.com/tf73j52/chnroute-custom.git
cd chnroute-custom
```

## 构建

```bash
python3 build.py
```

产物：`cn.rsc`（国内段直连白名单）、`gfwlist.rsc`（被墙域名走代理 DNS，解析 IP 自动写入防火墙 `gfwlist` 列表）、`VERSION.txt`（构建元数据）。

## RouterOS 导入

```routeros
:if ([ /ping  192.168.0.1 count=5]=0) do={   :local p [:rndnum from=49152 to=65535]   :do { /interface  wireguard set wireguard1 listen-port=$p /ip  firewall filter set (([ /ip  firewall filter find]->5)) dst-port=$p /file  set [ /file  find name="wgport.txt"] contents=[:tostr $p]     :log warning ("wireguard1 port changed to: " . $p)   } on-error={     :log error "wireguard1 update failed"   } }
```
