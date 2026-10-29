# chnroute-custom

每天 UTC 03:00 自动从上游 [ruijzhan/chnroute](https://github.com/ruijzhan/chnroute) 拉取最新列表，按本仓库的定制项二次加工，产出可直接被 RouterOS 拉取的 `cn.rsc` / `lan.rsc` / `gfwlist.rsc`。

## 产物特性（定制已烧进文件，RouterOS 端无需再做转换）

| 产物 | 说明 |
|---|---|
| `cn.rsc` | 上游国内段 + `custom_ips.txt` 里的 26 个段，**列表名直接是小写 `cn`**，文件自带清空逻辑 |
| `lan.rsc` | 内网段，**同样直接写入 `cn`**，不再生成 LAN 列表 |
| `gfwlist.rsc` | 上游 gfw 域名列表，**已去掉 `address-list=gfw_list` 标签**，不追加任何自定义域名 |

因为列表名已经是 `cn`，RouterOS 脚本里不再需要 `CN`→`cn`、`LAN`→`cn` 的改名循环。

## 定制项

只需改 `custom_ips.txt`（每行一个 IP 段，`#` 开头为注释），推送后 Actions 会自动生效。

当前并入的 26 个段：腾讯云海外段 `43.128~175`、`49.51`、`101.32/33`、`119.28`、`129.226`、`203.205`、`216.198`，以及 Apple 全段 `17.0.0.0/8`。

## 产物地址

```
https://cdn.jsdelivr.net/gh/tf73j52/chnroute-custom@main/cn.rsc
https://cdn.jsdelivr.net/gh/tf73j52/chnroute-custom@main/lan.rsc
https://cdn.jsdelivr.net/gh/tf73j52/chnroute-custom@main/gfwlist.rsc
```

`VERSION.txt` 记录构建时间、上游地址、各文件大小与 sha1，可用来确认拉到的是不是最新。

## 对应的 RouterOS 脚本

```routeros
:global dnsserver "proxy"
/system logging disable [find]
:foreach f in={"cn.rsc";"lan.rsc";"gfwlist.rsc"} do={ /tool fetch url=("https://cdn.jsdelivr.net/gh/tf73j52/chnroute-custom@main/" . $f) dst-path=$f duration=20s check-certificate=no; /import $f; /file remove [find name=$f] }
/ip firewall address-list remove [find list=gfw_list]
```

说明：

- `cn.rsc` 自身第一行就会清空旧的 cn 列表，所以脚本里不需要额外的 `remove [find list=cn]`；
- 导入顺序必须是 cn → lan（数组顺序即导入顺序），lan 会把内网段追加进同一个 cn 列表；
- 最后那行只用于清理切换方案前遗留的历史 gfw_list 条目，**确认列表已空后可删掉**，那时脚本只剩三行。

## 手动触发

仓库页面 → Actions → daily-build → Run workflow，不必等定时任务。

## 注意

- jsDelivr 对分支文件有缓存，workflow 里已加 purge 请求；若仍拿到旧文件，等十几分钟重试。
- 上游文件结构若变化（如 `:local ipList` 块改名），`build.py` 会直接报错退出，不会产生残缺产物。
