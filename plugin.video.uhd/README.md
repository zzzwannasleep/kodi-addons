# plugin.video.uhd

Kodi 客户端，对接 Emby 及 Emby 兼容服务器。已在 `Emby 4.9.5` 与 `UHD Media Server 4.9.3`
两台真实服务器上验证。

安装见[仓库根目录](../README.md)。装完打开插件设置填**服务器地址 / 用户名 / 密码**，
仓库里不含任何地址与账号。

## 功能

- 资源库浏览（电影 / 剧集，分页）；剧集 → 季 → 集，单季剧自动跳过「季」这一层
- 继续观看、接着看（Next Up）、最近添加、我的收藏、搜索
- 库内排序：名称 / 入库时间 / 首播日期 / 评分 / 时长 / 最近播放 / 播放次数 / 随机，可切升降序，每个库各自记住
- **多版本选择**：一个条目有多个版本时，播放前列出版本名、容器、体积、分辨率让你选
  （测试库里最多的一个条目有 31 个版本）。设置里可关，关掉就直接播第一个
- **类型浏览**：仅在服务器真的支持 `Genres=` 过滤时出现，探测结果按服务器缓存
- 播放进度双向同步：Kodi 里看到哪，服务器和网页端就跟到哪
- 外挂文本字幕自动挂载；图形字幕（PGS/VOBSUB）交给播放器从容器里读
- 网关抖动自动重试
- 长按菜单：收藏、标记已看 / 未看

## 结构

```
main.py                路由与目录渲染
service.py             后台服务：回报播放进度、容器加载后设视图
resources/settings.xml 设置项（账号、分页、排序、视图、超时、TLS 校验）
resources/lib/api.py   服务端 HTTP 客户端，无 Kodi 依赖，可独立测试
resources/lib/session.py  token 磁盘缓存 + 每库排序持久化
resources/lib/listing.py  Emby item → Kodi ListItem 映射
```

## 两台服务器的差异

同一套 API，两家实现对不上的地方。全部实测，`api.py` 顶部注释里也有一份，改代码前先看：

| 行为 | UHD 4.9.3 | Emby 4.9.5 |
|---|---|---|
| `Genres=` / `Years=` 过滤 | **被静默忽略**，返回全量 | 生效 |
| `NameStartsWith` / `HasSubtitles` | 忽略 | 忽略 |
| `Filters=` 带 `ParentId` | 忽略 | 生效 |
| 列表查询里的 `MediaSources` | 有 | **没有**，必须取详情 |
| 上报的 `PlaySessionId` | 可省 | **必须有**，否则 400 |
| 未声明的图片类型 | 返回 Primary 的字节 | 404 |
| `/Years` | 404 | 500 |
| `/Tags` | 404 | 正常 |
| `/Playlists` | — | 404 |
| `/Search/Hints` | — | 返回空，搜索得用 `Items?SearchTerm=` |
| 转码 | `SupportsTranscoding=false` | `SupportsTranscoding=false`，账号策略也禁了 |

两家一致的地方：`/Users/{id}/Items/Latest` 返回裸数组不是信封；`Chapters` 只在详情端点返回，
列表查询里给的是空数组；前置网关会 403 掉默认的 `Python-urllib`，所有请求必须带 UA。

**判断一个过滤参数是否生效，必须用取反对照。** 只测正向不能证伪——一个全库都有字幕的库，
`HasSubtitles=true` 返回全量恰恰说明它生效了。同理判断 SortBy 要比较升序与降序，被忽略的字段
两个方向返回同一结果。排序在服务端做（列表 20000+ 条，客户端排序没意义）。

类型菜单不是写死的：`api.genre_filter_works()` 拿一个真实类型名比对总数，答案按服务器缓存在
`addon_data/caps.json`。UHD 上探测为 false，菜单就不出现——否则会得到一堆点进去都是全库的类型。

## 续播是怎么工作的

这一条决定了 `service.py` 的形状，改之前务必读：

- `/Sessions/Playing/Progress` **在 Emby 上不写入续播点**（等到 50 秒回读仍是 0），UHD 上写入。
- `/Sessions/Playing/Stopped` 两家都写入。**续播完全依赖这一次上报。**
- 所以 Kodi 被强杀 / 断电时，这次观看的进度会丢。`service.py` 在 `waitForAbort` 返回后补发一次
  Stopped，正常退出不受影响。
- 位置太靠前会被服务器丢弃（不记续播点），约 90% 之后则直接标记为已看。
- 服务器写用户数据有延迟，**写完立刻回读拿到的是旧值**。测试里必须轮询等待，否则会把
  「还没落盘」误判成「服务器拒绝了」——这个坑让我误判过一次服务器有 bug。

## 实现要点（都是真机上踩出来的）

- **settings.xml 必须用 v1 schema**：`default` 写成属性、或空字符串默认值缺
  `<allowempty>true</allowempty>`，任一条都会让 Kodi **丢弃整个文件的所有设置**，
  运行时 `getSettingInt` 抛 `Invalid setting type`。桩测试看不见这层，由静态检查兜底。
- **开播有竞态**：`setResolvedUrl` 之后 Kodi 还要几秒才真开流，此时 `isPlayingVideo()` 为 False。
  服务必须区分「还没开始」和「已经结束」并留 60 秒宽限，否则整场播放无人跟踪、resume 永远是 0。
- **排序只从 `addon_data/sort.json` 读，绝不进 URL**。做成 `<库URL>&sort=X` 会让资源库有两个
  地址：返回退回排序页，Kodi 又从磁盘缓存取那个「干净」地址，顺序还是旧的。同理
  `endOfDirectory` 一律 `cacheToDisc=False`。
- **视图不能由插件设**：`Container.SetViewMode` 从插件进程发出后会被 Kodi 的「记住的视图」覆盖，
  改由 `service.py` 在容器建好后设。`Container.Viewmode` 为空表示容器还没建好，此时发指令会被
  静默丢弃。视图编号是否可用取决于**内容类型**：`52 图标墙` 只在内容类型为空或 files 等时可用，
  `54 信息墙` / `500 墙` / `51 海报` 反过来需要内容类型。
- **主界面要 `setContent("videos")`**：不设内容类型时 Estuary 只给图标墙，图片槽是 160×130 的
  方框（给工作室 logo 用的），16:9 的图只能渲染成 160×90；设了之后格子是 300×301 且图片铺满。
- **Estuary 网格印的是 InfoTag 的 Title，不是 ListItem 的 label**，两者必须一致，否则界面上显示的
  仍是服务器原始名。分集标题：混合列表里是 `剧名 S01E08`，剧集内部是 `S01E08 分集名`（分集名若
  只是「第 8 集」就省掉）。
- 续播点用 `InfoTagVideo.setResumePoint()`，Kodi 21 已弃用 `ResumeTime` / `TotalTime` 属性。
- token 乐观使用，只在真的 401 时重登重试一次。早先每次导航都打 `/Views` 验 token，等于每翻一页
  多一个来回，网络一抖整页就空。

Kodi 自身若显示乱码，是 `locale.charset` 被设成了 `BIG5` 之类；配 `DEFAULT` +
`resource.language.zh_cn` + `lookandfeel.font=Arial`（Kodi 自带的 arial.ttf 实为 DejaVu Sans，
含 16502 个中日韩字形）。这属于 Kodi 设置，不是插件能改的。

## 测试

两个测试都读仓库根目录的 env 文件（三行：地址 / 用户名 / 密码，已 gitignore），
默认 `1.env`，加参数可指定另一台：

```bash
python tests/plugin.video.uhd/selftest.py           # api.py 的接口契约，打真实服务器
python tests/plugin.video.uhd/selftest.py 2.env     # 换一台服务器
python tests/plugin.video.uhd/smoketest.py          # 桩掉 xbmc*，用真实数据跑路由与渲染
python tests/plugin.video.uhd/smoketest.py 2.env
```

**改完要两台都跑。** 两家实现的差异就是靠这个兜住的：只跑一台的话，凡是把某一台的行为
当成通用行为写死的代码都会一路绿灯——这份文件上一版就是那么写的。

`selftest.py` 里只有「插件依赖它才能工作」的才写成断言，服务器允许不一致的地方一律打印成
实测值（比如 progress 落不落盘），因为那些是插件运行时探测的对象，不是回归目标。

`smoketest.py` 证明的是路由和映射逻辑不会在真实数据上炸，**不能**证明 Kodi API 名字写对了——
那两个真机 bug 就是这么漏过去的。不碰服务器的检查在 `tools/check_static.py`，CI 跑的是它。
