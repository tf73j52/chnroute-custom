# chnroute-custom

每天北京时间 00:00（UTC 16:00）自动从上游 [ruijzhan/chnroute](https://github.com/ruijzhan/chnroute) 拉取最新列表，按本仓库的定制项二次加工，产出可直接被 RouterOS 拉取的 `cn.rsc` / `gfwlist.rsc`。

## 产物特性（定制已烧进文件，RouterOS 端无需再做转换）

| 产物 | 说明 |
|---|---|
| `cn.rsc` | 上游国内段 + `ips.txt` 里的自定义段 + 内网段，**列表名直接是小写 `cn`**，文件自带清空逻辑 |
| `gfwlist.rsc` | 上游 gfw 域名列表 + `domains.txt` 的自定义域名，被父域覆盖的多余子域自动剔除 |

内网段（原来的 `lan.rsc`）已合并进 `cn.rsc`，不再单独产出文件。

因为列表名已经是 `cn`，RouterOS 脚本里不再需要 `CN`→`cn`、`LAN`→`cn` 的改名循环。

## 构建时会做的整理

每次构建都在 Python 里把三张表重新算一遍，不靠人肉维护：

| 处理 | 说明 |
|---|---|
| **网段去重合并** | 上游段与自定义段互相包含的只留最大的那条。例如 `17.0.0.0/8`（Apple）会把上游零散的 `17.x` 小段整片吃掉，最终只剩 1 条 |
| **相邻段聚合** | 能合成一条的不拆开写，例如 `43.160.0.0/16` + `43.161.0.0/16` 合成 `43.160.0.0/15` |
| **内网段并进 cn** | 上游 `lanIps` 的内网段直接合并进 `cn.rsc`，去重后一起排序，路由器只导入一个文件就够了。注意相邻段会被聚合（例如 `224.0.0.0/4` + `240.0.0.0/4` 合成 `224.0.0.0/3`），所以进去多少条和最后留下多少条不一定相等，实际数量看 `VERSION.txt` |
| **网段排序** | 按网段起始地址的数值从小到大排 |
| **域名去重 + 剔除冗余子域** | 完全相同的只留一条；表里已有 `wordpress.com` 时，`xxx.wordpress.com` 这类子域会被自动剔除（每条都带 match-subdomain=yes，父域已经覆盖了子域） |
| **域名排序** | 去重后按纯字典序排 |

`VERSION.txt` 里的 `cn_entries` / `gfw_domains` 就是整理后的实际条目数。

## 定制项

| 文件 | 作用 |
|---|---|
| `ips.txt` | 要并入 `cn` 的 IP 段（每行一个，`#` 开头为注释），**直连**用 |
| `domains.txt` | 要追加进 gfwlist 的域名（每行一个，`#` 开头为注释），让这些域名**走代理 DNS** 解析。**只影响 DNS 解析**，目标 IP 最终走不走代理仍由 `cn` 列表决定（不在 `cn` 里就走代理） |

改完推送 Actions 会自动生效；日常增删不需要碰 `build.py`（真改了它也会触发一次构建）。

当前 `ips.txt` 里并入的是腾讯云海外段（`43.128~175`、`49.51`、`101.32/33`、`119.28`、`129.226`、`203.205`、`216.198`）加 Apple 全段（`17.0.0.0/8`）。增删直接改 `ips.txt`，**完完整整的清单以该文件为准**。

## 产物地址

```
https://cdn.jsdelivr.net/gh/tf73j52/chnroute-custom@main/cn.rsc
https://cdn.jsdelivr.net/gh/tf73j52/chnroute-custom@main/gfwlist.rsc
```

`VERSION.txt` 记录构建时间、上游地址、上游指纹 `upstream_sig`（「上游没变就收工」就是靠它比对）、整理后的条目数、各文件大小与 sha1，可用来确认拉到的是不是最新。

## 对应的 RouterOS 脚本

```routeros
:global dnsserver "proxy"
:local logOpen [/system logging find disabled=no]
/system logging disable [find]
:foreach f in={"cn.rsc";"gfwlist.rsc"} do={ /tool fetch url=("https://cdn.jsdelivr.net/gh/tf73j52/chnroute-custom@main/" . $f) dst-path=$f duration=10s check-certificate=no; /import $f; /file remove [find name=$f] }
:foreach lid in=$logOpen do={ /system logging enable $lid }
```

说明：

- `dnsserver` 必须在路由器上真实存在，二选一：① 直接写你的代理 DNS 的 IP（如 `:global dnsserver 192.168.0.2`）；② 保留 `"proxy"` 这种名字写法的话，要先建一个同名 forwarder——`/ip/dns/forwarders/add name=proxy dns-servers=192.168.0.2`。**用了名字却没建 forwarder，FWD 条目就转不出去，域名等于白加**（`forward-to` 接受 IP、DNS 名、forwarder 名三种写法）；
- `cn.rsc` 自身第一行就会清空旧的 cn 列表，所以脚本里不需要额外的 `remove [find list=cn]`；
- 内网段已经在 `cn.rsc` 里了，数组里**没有 `lan.rsc`**、仓库里也**没有这个文件了**；还在用旧脚本（数组里三个文件）的话必须先删掉 `"lan.rsc"`，否则会去下载不存在的文件，导致后面的 `gfwlist.rsc` 不再导入；
- 日志那两行是防止几千条 `add` 刷爆日志：先记下开着的条目再临时全关，跑完照原样恢复（所以这段要**整段**粘贴，拆开跑变量就丢了）。别图省事写成 `enable [find]`，那会把你主动关掉的日志也一并打开。

## 三道保险

| 保险 | 作用 | 触发时机 |
|---|---|---|
| **并发保护** | 同一时间只允许一个构建，后来的排队等着。避免两个任务抢着提交被拒、白跑一次 | 每次 |
| **上游没变就收工** | 先算上游三个文件的指纹，和上次一样就直接结束，不重算、不提交、不刷缓存 | 仅定时触发（手动触发一律重算） |
| **产物自检** | 提交前检查：条目数下限、比上次掉超过一成、域名数下限、是否仍存在互相包含的段、内网段是否丢失——**任一项不对就终止，坏表绝不出仓库门**（阈值硬编码在 `build.py` 的 `selfcheck()` 里，改那里才生效） | 每次 |

自检不通过时构建会**失败但不提交**，仓库里保留上一次的好表，路由器不受影响。

## 手动触发

仓库页面 → Actions → daily-build → Run workflow，不必等定时任务。

页面上有 `skip_if_unchanged` 选项（默认关闭）。勾上就模拟定时触发的行为：上游没变则跳过。正常情况不用勾。

## 注意

- GitHub 的定时任务不保证准点，高峰期可能晚几分钟到几十分钟，属正常现象；
- jsDelivr 对分支文件有缓存，workflow 里已加 purge 请求；若仍拿到旧文件，等十几分钟重试。
- 上游文件结构若变化（如 `:local ipList` 块改名），`build.py` 会直接报错退出，不会产生残缺产物。
