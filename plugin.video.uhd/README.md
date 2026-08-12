# plugin.video.uhd

Kodi 客户端，对接 UHD 媒体服务器（服务端跑的是 Emby 兼容接口，`UHD Media Server 4.9.x`）。

安装见[仓库根目录](../README.md)。装完打开插件设置填**服务器地址 / 用户名 / 密码**，
仓库里不含任何地址与账号。

## 功能

- 资源库浏览（电影 / 剧集，分页）；剧集 → 季 → 集，单季剧自动跳过「季」这一层
- 继续观看、接着看（Next Up）、最近添加、我的收藏、搜索
- 库内排序：名称 / 入库时间 / 首播日期 / 评分 / 时长 / 最近播放 / 播放次数 / 随机，可切升降序，每个库各自记住
- 播放进度双向同步：Kodi 里看到哪，服务器和网页端就跟到哪
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

## 服务端与标准 Emby 的差异

实测出来的，`api.py` 顶部注释里也有一份，改代码前先看：

| 行为 | 实测结果 |
|---|---|
| `Genres` / `GenreIds` / `Years` / `NameStartsWith` 过滤 | **被静默忽略**，返回全量且 `TotalRecordCount` 不变 |
| `Filters=` | 仅在**不带** `ParentId` 时生效 |
| `/Years`、`/Tags` | 404（`/Genres`、`/Studios`、`/Persons` 正常） |
| `/Users/{id}/Items/Latest` | 返回裸数组，不是 `Items` 信封 |
| 转码 | 全部条目 `SupportsTranscoding=false`，只能直连播放 |
| 播放地址 | 302 跳转到 CDN 主机 |
| User-Agent | 前置 Cloudflare **403** 掉默认的 `Python-urllib`，所有请求必须带 UA |
| 排序字段 | `DateLastMediaAdded` / `CriticRating` / `OfficialRating` 被忽略；`DateLastContentAdded` 是 `DateCreated` 的别名，所以「更新时间」和「入库时间」无法区分 |

判断一个 SortBy 是否真生效要**比较升序与降序**——被忽略的字段两个方向返回同一结果，只看「和默认
顺序不同」会把忽略当成生效。排序在服务端做（列表 3000+ 条，客户端排序没意义）。

因为过滤参数不可用，插件不做类型/年份筛选菜单：服务端做不了的事，客户端拉全量再过滤只会更慢。
哪天服务端支持了，`selftest.py` 里那条回归断言会失败提醒。

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

两个测试都要读仓库根目录的 `1.env`（三行：地址 / 用户名 / 密码，已 gitignore）：

```bash
python selftest.py    # 打真实服务器，验证 api.py 的接口契约
python smoketest.py   # 桩掉 xbmc* 模块，用真实数据跑一遍路由与渲染
```

`smoketest.py` 证明的是路由和映射逻辑不会在真实数据上炸，**不能**证明 Kodi API 名字写对了——
那两个真机 bug 就是这么漏过去的。不碰服务器的检查在 `tools/check_static.py`，CI 跑的是它。
