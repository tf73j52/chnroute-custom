# chnroute-custom

每天北京时间 00:00（UTC 16:00）自动从上游 [ruijzhan/chnroute](https://github.com/ruijzhan/chnroute) 拉取最新列表，按本仓库的定制项二次加工，产出可直接被 RouterOS 拉取的 `cn.rsc` / `gfwlist.rsc`。

## 产物特性（定制已烧进文件，RouterOS 端无需再做转换）

| 产物 | 说明 |
|---|---|
| `cn.rsc` | 上游国内段 + `custom_ips.txt` 里的 26 个段 + **14 条内网段**，**列表名直接是小写 `cn`**，文件自带清空逻辑 |
| `gfwlist.rsc` | 上游 gfw 域名列表 + `custom_domains.txt` 的自定义域名，**已去掉 `address-list=gfw_list` 标签**，被父域覆盖的多余子域自动剔除 |

内网段（原来的 `lan.rsc`）已合并进 `cn.rsc`，不再单独产出文件。

因为列表名已经是 `cn`，RouterOS 脚本里不再需要 `CN`→`cn`、`LAN`→`cn` 的改名循环。

## 构建时会做的整理

每次构建都在 Python 里把三张表重新算一遍，不靠人肉维护：

| 处理 | 说明 |
|---|---|
| **网段去重合并** | 上游段与自定义段互相包含的只留最大的那条。例如 `17.0.0.0/8`（Apple）会吃掉上游 190 条零散的 `17.x` 小段，最终只剩 1 条 |
| **相邻段聚合** | 能合成一条的不拆开写，例如 `43.160.0.0/16` + `43.161.0.0/16` 合成 `43.160.0.0/15` |
| **内网段并进 cn** | lan 的 14 条内网段直接合并进 `cn.rsc`，去重后一起排序，路由器只导入一个文件就够了 |
| **网段排序** | 按网段起始地址的数值从小到大排 |
| **域名去重 + 剔除冗余子域** | 完全相同的只留一条；表里已有 `wordpress.com` 时，`xxx.wordpress.com` 这类子域会被自动剔除（每条都带 match-subdomain=yes，父域已经覆盖了子域） |
| **域名排序** | 去重后按层级排，同一主域下的子域聚在一起（`1-apple.com.tw` 紧挨 `powerapple.com` 那组）；想改成纯字典序，把 `build.py` 里的 `DOMAIN_SORT` 改成 `"alpha"` |

`VERSION.txt` 里的 `cn_entries` / `lan_entries` / `gfw_domains` 就是整理后的实际条目数。

## 定制项

| 文件 | 作用 |
|---|---|
| `custom_ips.txt` | 要并入 `cn` 的 IP 段（每行一个，`#` 开头为注释），**直连**用 |
| `custom_exclude.txt` | 要从上面这些段里**剔除**的更精确网段（每行一个，`#` 开头为注释） |
| `custom_domains.txt` | 要追加进 gfwlist 的域名（每行一个，`#` 开头为注释），让这些域名**走代理 DNS** 解析 |

两个文件改完推送后 Actions 会自动生效，不需要动 `build.py`。

当前并入的 26 个段：腾讯云海外段 `43.128~175`、`49.51`、`101.32/33`、`119.28`、`129.226`、`203.205`、`216.198`，以及 Apple 全段 `17.0.0.0/8`。

### 自定义段误伤了别的网段怎么办

比如 `43.160.0.0/16` 里混进了不属于微信的服务，整段并进 `cn` 会把它也一起直连。处理方式：

1. 打开 `custom_exclude.txt`，把误伤的那个**更精确的前缀**写进去，例如 `43.160.99.0/24`；
2. 保存推送（网页上直接 Edit 就行，不用本地）；
3. Actions 下次构建时会在 Python 里做 CIDR 差集，`43.160.0.0/16` 被切成若干更小的段并**挖掉** `43.160.99.0/24`，被挖掉的部分不再进 `cn`，恢复走 VPN。

规则说明：

- 排除段必须是某个现有段的**子网**（`43.160.99.0/24` ⊂ `43.160.0.0/16`）才能精确相减；只是部分重叠的，构建时会保留原段并打印提示，不会出错；
- 排除一个 /24，原来的 /16 会变成 8~9 条更小的 CIDR，条目数增加但语义完全等价；
- 差集作用在**全体网段**（上游段 + 自定义段）上，所以也能从上游的国内段里挖洞——比如上游判成国内、但你实测走 VPN 更快的段；
- 这个文件**只管"挖洞"，不管"去冗余"**。互相包含的冗余由 `build.py` 在构建时自动合并，不需要在这里写。

## 产物地址

```
https://cdn.jsdelivr.net/gh/tf73j52/chnroute-custom@main/cn.rsc
https://cdn.jsdelivr.net/gh/tf73j52/chnroute-custom@main/gfwlist.rsc
```

`VERSION.txt` 记录构建时间、上游地址、各文件大小与 sha1，可用来确认拉到的是不是最新。

## 对应的 RouterOS 脚本

```routeros
:global dnsserver "proxy"
/system logging disable [find]
:foreach f in={"cn.rsc";"gfwlist.rsc"} do={ /tool fetch url=("https://cdn.jsdelivr.net/gh/tf73j52/chnroute-custom@main/" . $f) dst-path=$f duration=20s check-certificate=no; /import $f; /file remove [find name=$f] }
```

说明：

- `cn.rsc` 自身第一行就会清空旧的 cn 列表，所以脚本里不需要额外的 `remove [find list=cn]`；
- 内网段已经在 `cn.rsc` 里了，所以数组里**没有 `lan.rsc`**，仓库里也**没有这个文件了**；
- 如果还在用旧脚本（数组里三个文件），必须先把 `"lan.rsc"` 从数组里删掉，否则会去下载一个不存在的文件，导致后面的 `gfwlist.rsc` 不再导入。

还没清理历史 `gfw_list` 条目的话，末尾再加一行：

```routeros
/ip firewall address-list remove [find list=gfw_list]
```

确认列表已空后，把这行也删掉。

## 三道保险

| 保险 | 作用 | 触发时机 |
|---|---|---|
| **并发保护** | 同一时间只允许一个构建，后来的排队等着。避免两个任务抢着提交被拒、白跑一次 | 每次 |
| **上游没变就收工** | 先算上游三个文件的指纹，和上次一样就直接结束，不重算、不提交、不刷缓存 | 仅定时触发（手动触发一律重算） |
| **产物自检** | 提交前检查：条目少于 5000、比上次掉超过一成、域名少于 3000、仍有互相包含的段、内网段丢失——**任一项不对就终止，坏表绝不出仓库门** | 每次 |

自检不通过时构建会**失败但不提交**，仓库里保留上一次的好表，路由器不受影响。

## 手动触发

仓库页面 → Actions → daily-build → Run workflow，不必等定时任务。

页面上有 `skip_if_unchanged` 选项（默认关闭）。勾上就模拟定时触发的行为：上游没变则跳过。正常情况不用勾。

## 注意

- jsDelivr 对分支文件有缓存，workflow 里已加 purge 请求；若仍拿到旧文件，等十几分钟重试。
- 上游文件结构若变化（如 `:local ipList` 块改名），`build.py` 会直接报错退出，不会产生残缺产物。
