# chnroute-custom

每天自动从上游 [ruijzhan/chnroute](https://github.com/ruijzhan/chnroute) 拉取最新列表，按本仓库的定制项二次加工，产出可直接被 RouterOS 拉取的 `CN.rsc` / `LAN.rsc` / `gfwlist_v7.rsc`。

## 定制项（改这两个文件即可，Actions 会自动生效）

| 文件 | 作用 |
|---|---|
| `custom_ips.txt` | 这些 IP 段会被并入 `CN.rsc` 的 ipList（已存在的不重复添加） |
| `custom_domains.txt` | 这些域名会被追加进 `gfwlist_v7.rsc`，由 `$dnsserver` 解析 |

### 当前烧进产物里的定制

1. **26 个 IP 段并入 CN.rsc**：腾讯云海外段 `43.128~175`、`49.51`、`101.32/33`、`119.28`、`129.226`、`203.205`、`216.198`，以及 Apple 全段 `17.0.0.0/8`。用于让这些目标不走 VPN、直接出口。
2. **自定义域名并入 gfwlist_v7.rsc**：`javmost.ws`、`xslist.org`，追加在 `/ip dns cache flush` 之前。
3. **去掉 `address-list=gfw_list` 标签**：上游那句 add 里的 `address-list=gfw_list` 已被删除，因此不会再产生几千条无人使用的动态防火墙条目。

## 产物地址

```
https://cdn.jsdelivr.net/gh/<你的用户名>/<仓库名>@main/CN.rsc
https://cdn.jsdelivr.net/gh/<你的用户名>/<仓库名>@main/LAN.rsc
https://cdn.jsdelivr.net/gh/<你的用户名>/<仓库名>@main/gfwlist_v7.rsc
```

`VERSION.txt` 里有构建时间、上游地址、各文件大小与 sha1，可以用来确认拉到的是不是最新。

## 对应的 RouterOS 脚本（定制项已烧进产物，脚本本体因此缩短）

```routeros
:global dnsserver "proxy"
/system logging disable [find]
:foreach f in={"CN.rsc";"LAN.rsc";"gfwlist_v7.rsc"} do={
  /tool fetch url=("https://cdn.jsdelivr.net/gh/<你的用户名>/<仓库名>@main/" . $f) dst-path=$f duration=20s check-certificate=no
  /import $f
  /file remove [find name=$f]
}
/ip firewall address-list remove [find list=cn]
:foreach l in={"CN";"LAN"} do={ /ip firewall address-list set [find list=$l] list=cn }
/ip firewall address-list remove [find list=gfw_list]
```

与旧脚本相比：26 个 IP 段的循环被去掉（已并入 CN.rsc）、补加域名的循环被去掉（已并入 gfwlist）、"撕 gfw_list 标签"那行被去掉（产物里本来就没标签）。最后那行 `remove [find list=gfw_list]` 只用于清理切换到本方案之前遗留的历史条目，**确认列表已空之后可以删掉**。

## 手动触发

仓库页面 → Actions → daily-build → Run workflow，无需等待定时任务。

## 注意

- jsDelivr 对分支文件有缓存，workflow 里已加了 purge 请求；若仍拿到旧文件，等十几分钟或手动访问 purge 接口。
- 上游文件结构若变化（例如 `:local ipList` 块改名），`build.py` 会直接报错退出，不会产生残缺产物，此时需要同步调整 `build.py`。
