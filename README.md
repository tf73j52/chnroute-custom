# chnroute-custom

每天北京时间 00:00（UTC 16:00）自动从上游 [ruijzhan/chnroute](https://github.com/ruijzhan/chnroute) 拉取最新列表，并入本仓库定制项，产出可直接被 RouterOS 拉取的 `cn.rsc` / `gfwlist.rsc`。

## 产物

| 产物 | 说明 |
|---|---|
| `cn.rsc` | 上游国内段 + `ips.txt` 自定义段 + 内网段，列表名小写 `cn`，文件自带清空旧列表逻辑 |
| `gfwlist.rsc` | 上游 gfw 域名 + `domains.txt` 自定义域名 − `exclude.txt` 排除域名，被父域覆盖的子域自动剔除；FWD 记录自带 `address-list=gfwlist`，域名被解析时其 IP 自动写入防火墙 `gfwlist` 地址列表 |

内网段（原 `lan.rsc`）已并入 `cn.rsc`，路由器只需导入这两个文件。

### gfwlist 地址列表

`gfwlist.rsc` 的 FWD 记录自带 `address-list=gfwlist`：当 gfwlist 域名被解析时，解析出的 IP 会自动写入防火墙的 `gfwlist` 地址列表（TTL 到期自动清除，下次访问再重新填充）。可配合 mangle 做精确分流，例如强制这些 IP 走代理：

```routeros
/ip firewall mangle add chain=prerouting src-address-list=proxy dst-address-list=gfwlist action=mark-routing new-routing-mark=vpn passthrough=no
```

## 定制项

| 文件 | 作用 |
|---|---|
| `ips.txt` | 并入 `cn` 的 IP 段，**直连**用。当前用于 WeChat / Apple 应用直连 |
| `domains.txt` | 追加进 gfwlist 的域名，让其**走代理 DNS** 解析；解析出的 IP 自动写入 `gfwlist` 地址列表，可配合 mangle 强制走代理 |
| `exclude.txt` | 从 gfwlist 中**排除**的域名（含其子域），每行一个 |

改完推送即自动构建生效；日常增删不需要碰 `build.py`。

## 产物地址

```
https://cdn.jsdelivr.net/gh/tf73j52/chnroute-custom@main/cn.rsc
https://cdn.jsdelivr.net/gh/tf73j52/chnroute-custom@main/gfwlist.rsc
```

`VERSION.txt` 记录构建时间、上游指纹、条目数与 sha1，可用于确认拉到的版本。

## RouterOS 导入脚本

```routeros
:global dnsserver "proxy"
:local logOpen [/system logging find disabled=no]
/system logging disable [find]
:foreach f in={"cn.rsc";"gfwlist.rsc"} do={ /tool fetch url=("https://cdn.jsdelivr.net/gh/tf73j52/chnroute-custom@main/" . $f) dst-path=$f duration=10s check-certificate=no; /import $f; /file remove [find name=$f] }
:foreach lid in=$logOpen do={ /system logging enable $lid }
```

- `dnsserver` 必须真实存在：直接填代理 DNS 的 IP，或先建同名 forwarder（`/ip/dns/forwarders/add name=proxy dns-servers=<IP>`）。用了名字却没建，FWD 条目转不出去；
- 日志两行是防止几千条 add 刷爆日志，需整段粘贴。

## 构建机制

每次构建自动完成：网段去重合并（互相包含只留最大段）、相邻段聚合、数值排序、域名去重、排除 `exclude.txt` 域名并剔除冗余子域。三道保险：

1. **并发保护**：同一时间只跑一个构建；
2. **上游没变就收工**：比对上游指纹，仅定时触发时生效；
3. **产物自检**：条目数下限、跌幅超一成、域名数下限、残留包含段、内网段缺失，任一项不通过即终止且不提交。

## 手动触发

Actions → daily-build → Run workflow。`skip_if_unchanged` 选项模拟定时行为（上游没变则跳过），默认关闭。

## 注意

- GitHub 定时任务不保证准点，晚几分钟属正常；
- jsDelivr 有缓存，workflow 已加 purge；若拿到旧文件等十几分钟重试。
