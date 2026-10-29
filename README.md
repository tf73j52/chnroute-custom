# chnroute-custom

每天 UTC 03:00 自动从上游 [ruijzhan/chnroute](https://github.com/ruijzhan/chnroute) 拉取最新列表，按本仓库的定制项二次加工，产出可直接被 RouterOS 拉取的 `cn.rsc` / `lan.rsc` / `gfwlist.rsc`。

## 产物特性（定制已烧进文件，RouterOS 端无需再做转换）

| 产物 | 说明 |
|---|---|
| `cn.rsc` | 上游国内段 + `custom_ips.txt` 里的 26 个段，**列表名直接是小写 `cn`**，文件自带清空逻辑 |
| `lan.rsc` | 内网段，**同样直接写入 `cn`**，不再生成 LAN 列表 |
| `gfwlist.rsc` | 上游 gfw 域名列表，**已去掉 `address-list=gfw_list` 标签**，不追加任何自定义域名 |

因为列表名已经是 `cn`，RouterOS 脚本里不再需要 `CN`→`cn`、`LAN`→`cn` 的改名循环。

## 构建时会做的整理

每次构建都在 Python 里把三张表重新算一遍，不靠人肉维护：

| 处理 | 说明 |
|---|---|
| **网段去重合并** | 上游段与自定义段互相包含的只留最大的那条。例如 `17.0.0.0/8`（Apple）会吃掉上游 190 条零散的 `17.x` 小段，最终只剩 1 条 |
| **相邻段聚合** | 能合成一条的不拆开写，例如 `43.160.0.0/16` + `43.161.0.0/16` 合成 `43.160.0.0/15` |
| **cn 与 lan 互斥** | 两个文件都写进同一个 `cn` 列表，构建时先做差集，保证两边没有任何重叠 |
| **网段排序** | 按网段起始地址的数值从小到大排 |
| **域名排序** | 去重后按层级排，同一主域下的子域聚在一起（`1-apple.com.tw` 紧挨 `powerapple.com` 那组）；想改成纯字典序，把 `build.py` 里的 `DOMAIN_SORT` 改成 `"alpha"` |

`VERSION.txt` 里的 `cn_entries` / `lan_entries` / `gfw_domains` 就是整理后的实际条目数。

## 定制项

| 文件 | 作用 |
|---|---|
| `custom_ips.txt` | 要并入 `cn` 的 IP 段（每行一个，`#` 开头为注释） |
| `custom_exclude.txt` | 要从上面这些段里**剔除**的更精确网段（每行一个，`#` 开头为注释） |

两个文件改完推送后 Actions 会自动生效，不需要动 `build.py`。

当前并入的 26 个段：腾讯云海外段 `43.128~175`、`49.51`、`101.32/33`、`119.28`、`129.226`、`203.205`、`216.198`，以及 Apple 全段 `17.0.0.0/8`。

### 自定义段误伤了别的网段怎么办

比如 `43.160.0.0/16` 里混进了不属于微信的服务，整段并进 `cn` 会把它也一起直连。处理方式：

1. 打开 `custom_exclude.txt`，把误伤的那个**更精确的前缀**写进去，例如 `43.160.99.0/24`；
2. 保存推送（网页上直接 Edit 就行，不用本地）；
3. Actions 下次构建时会在 Python 里做 CIDR 差集，`43.160.0.0/16` 被切成若干更小的段并**挖掉** `43.160.99.0/24`，被挖掉的部分不再进 `cn`，恢复走 VPN。

规则说明：

- 排除段必须是自定义段的**子网**（`43.160.99.0/24` ⊂ `43.160.0.0/16`）才能精确相减；只是部分重叠的，构建时会保留原段并打印提示，不会出错；
- 排除一个 /24，原来的 /16 会变成 8~9 条更小的 CIDR，条目数增加但语义完全等价；
- 剔除只作用于 `custom_ips.txt` 的段，**不会动上游原本的国内段**。

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
