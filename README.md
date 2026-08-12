# plugin.video.uhd

Kodi 客户端，对接 UHD 媒体服务器（服务端跑的是 Emby 兼容接口，`UHD Media Server 4.9.x`）。

## 安装

**自动更新（推荐）**：装一次 `repository.uhd-*.zip`，之后 Kodi 会自己提示新版本。
从 [Releases](https://github.com/zzzwannasleep/plugin.video.uhd/releases) 或
<https://zzzwannasleep.github.io/plugin.video.uhd/> 下载 →
Kodi → 插件 → 从 zip 文件安装 → 之后在「从存储库安装」里找到 UHD。

**手动**：直接装 `plugin.video.uhd-*.zip`，或本地 `python package.py` 自己打包。以后升级要重装。

装完打开插件设置，填**服务器地址 / 用户名 / 密码**。仓库里不含任何地址与账号，必须自己填。

要求 Kodi 20 (Nexus) 及以上。

## 发布流程

改完代码，把 `plugin.video.uhd/addon.xml` 里的 `version` 加一档，push 到 `main`，剩下的 CI 做完：

1. 用 Python 3.8（Kodi 21 内置的版本）跑 `tools/check_static.py`
2. `tools/make_repo.py` 生成插件 zip、repository 插件 zip、`addons.xml` 与 md5
3. 推到 `gh-pages`（保留旧版本 zip，便于回滚）
4. 若该版本还没有 tag，建一个 Release 并附上两个 zip

版本号不变就只刷新索引、不重复发版。

## 功能

- 资源库浏览（电影 / 剧集，分页，可设排序方式）
- 继续观看、接着看（Next Up）、最近添加、我的收藏
- 搜索
- 资源库内可切换排序（名称 / 入库时间 / 首播日期 / 评分 / 时长 / 最近播放 / 播放次数 / 随机，可切升降序），每个库各自记住选择
- 剧集 → 季 → 集；单季剧集自动跳过"季"这一层
- 播放进度双向同步：Kodi 里看到哪，服务器和网页端就跟到哪
- 长按菜单：收藏 / 取消收藏、标记已看 / 未看

## 结构

```
plugin.video.uhd/
  addon.xml              插件与后台服务两个扩展点
  main.py                路由与目录渲染
  service.py             后台服务，回报播放进度（插件进程播放后即退出，管不了播放器）
  resources/settings.xml 设置项（账号、分页、排序、超时、TLS 校验）
  resources/lib/api.py   服务端 HTTP 客户端，无任何 Kodi 依赖，可独立测试
  resources/lib/session.py  token 磁盘缓存 + 客户端构造
  resources/lib/listing.py  Emby item → Kodi ListItem 映射
```

## 服务端与标准 Emby 的差异

摸接口时实测出来的，都写在 `api.py` 顶部注释里，改代码前先看：

| 行为 | 实测结果 |
|---|---|
| `Genres` / `GenreIds` / `Years` / `NameStartsWith` 过滤 | **被静默忽略**，返回全量且 `TotalRecordCount` 不变 |
| `Filters=` | 仅在**不带** `ParentId` 时生效 |
| `/Years`、`/Tags` | 404（`/Genres`、`/Studios`、`/Persons` 正常） |
| `/Users/{id}/Items/Latest` | 返回裸数组，不是 `Items` 信封 |
| 转码 | 全部条目 `SupportsTranscoding=false`，只能直连播放 |
| 播放地址 | 302 跳转到 CDN 主机 |
| User-Agent | 前置 Cloudflare 会 **403** 掉默认的 `Python-urllib`，所有请求必须带 UA |
| 排序字段 | `DateLastMediaAdded` / `CriticRating` / `OfficialRating` 被忽略；`DateLastContentAdded` 是 `DateCreated` 的别名（该字段本身返回空），所以「更新时间」和「入库时间」无法区分 |

排序用弹窗选择，选完**替换当前容器**回到资源库的同一个 URL——排序只从 `addon_data/sort.json` 读，绝不进 URL。早先的做法是导航到 `<库URL>&sort=X`，结果资源库有了两个地址：返回会退回排序页，而 Kodi 又从磁盘缓存里取那个「干净」地址，显示的仍是旧顺序。同理 `endOfDirectory` 一律带 `cacheToDisc=False`，否则返回看到的永远是上一次的列表。

排序是服务端做的（列表分页到 3000+ 条，客户端排序没有意义）。判断一个 SortBy 是否真生效的方法是**比较升序与降序**——被忽略的字段两个方向返回同一结果，而单看"和默认顺序不同"会把忽略当成生效。

因为过滤参数不可用，插件没做类型/年份筛选菜单——服务端做不了的事，客户端拉全量再过滤只会更慢。若哪天服务端支持了，`selftest.py` 里那条回归断言会失败提醒。

## 测试

两个测试都要读根目录的 `1.env`（三行：地址 / 用户名 / 密码，已被 gitignore）：

```bash
python selftest.py    # 打真实服务器，验证 api.py 的接口契约
python smoketest.py   # 桩掉 xbmc* 模块，用真实数据跑一遍路由与渲染
```

`smoketest.py` 验证的是本插件自己的路由和映射逻辑不会在真实数据上炸；它**不能**证明 Kodi API 名字写对了。

## 真机验证结论（Kodi 21.3 Omega / Windows）

已装进 Kodi 21.3 实测，经 JSON-RPC 驱动全部路径：根菜单 17 项、电影/剧集列表、剧集下钻、继续观看、接着看、最近添加、收藏、搜索全部正常渲染；4K HEVC 直连播放正常；停止播放后服务端 resume 位置正确回写（停在 27s，服务端收到 26.4s）。日志零 Python 异常。

真机跑出来两个桩测试抓不到的 bug，都已修复并补了回归断言：

1. **settings.xml 用了旧 schema** — `default="..."` 写成属性、且空字符串默认值缺 `<constraints><allowempty>true</allowempty></constraints>`。任一条都会让 Kodi **丢弃整个文件的所有设置**，运行时 `getSettingInt` 抛 `Invalid setting type`。桩测试直接从 dict 返回设置值，看不见这层。现由 `smoketest.py` 的 `_check_settings_schema()` 静态校验。
2. **后台服务的开播竞态** — 插件 `setResolvedUrl` 后立刻交出条目 id，但 Kodi 还要几秒才真正打开流。服务此时查 `isPlayingVideo()` 得到 False，误判为"播放结束"，上报位置 0 并清空状态，整场播放此后无人跟踪、resume 永远为 0。修法是区分"还没开始"与"已经结束"，并给开流留 60 秒宽限。桩里的假播放器原先从第 0 帧就返回 True，正好掩盖了它；现在它模拟 5 秒开流延迟。

### 视图与显示（第二轮真机排查）

- **条目标题**：混合列表（继续观看/接着看/最近添加/我的收藏/搜索）里的分集标成 `剧名 S01E08`；剧集内部的分集列表标成 `S01E08 分集名`，分集名若只是「第 8 集」就省掉不重复。注意 Estuary 的网格视图印的是 **InfoTag 的 Title 而不是 ListItem 的 label**，所以两者必须一致，否则界面上仍显示服务器原始名。
- **主界面**：条目图是 16:9、名字已印在图里（服务器的资源库封面本来就这样做，插件自带的 5 个入口图同样处理，见 `tools/make_menu_tiles.py`）。主界面必须调 `setContent("videos")`——不设内容类型时 Estuary 只提供图标墙，而图标墙的图片槽是 **160×130** 的方框（给工作室 logo 用的），16:9 的图在里面只能渲染成 160×90。设了 videos 之后墙式视图的格子是 300×301 且图片铺满整格，面积约为原来的 3.5 倍。
- **海报墙**：插件不能自己设视图——`Container.SetViewMode` 从插件进程发出后，Kodi 才恢复它为该路径记住的视图，会把插件的设置覆盖掉，首次进入任何路径都只能看到列表。改由 `service.py` 在容器加载完成后设置。另有两个坑：`Container.Viewmode` 为空表示容器还没建好，此时发指令会被静默丢弃，必须等下一拍；以及视图编号是否可用取决于**内容类型**：`52 图标墙` 的可见条件明确排除 movies/tvshows/episodes，只在内容类型为空或 files/genres 等时可用；反过来 `54 信息墙`/`500 墙`/`51 海报` 需要内容类型。内容页默认 **54 信息墙**，主界面默认 **54 + videos 内容**，设置里只列实测可用的编号。
- **Kodi 显示中文**：本机 Kodi 的 `locale.charset` 被设成了 `BIG5`，插件返回的 UTF-8 按 BIG5 解就是满屏乱码；同时 `locale.language` 是英文、`lookandfeel.font` 是 Estuary 自带字体（不含中日韩字形）。三项分别改为 `DEFAULT` / `resource.language.zh_cn` / `Arial`（Kodi 自带的 arial.ttf 实为 DejaVu Sans，含 16502 个中日韩字形），字幕字体一并设为 `arial.ttf`。这些是 Kodi 自身设置，不属于插件。
- 续播点改用 `InfoTagVideo.setResumePoint()`：Kodi 21 已弃用 `ResumeTime`/`TotalTime` 这两个 ListItem 属性。

另外去掉了每次导航都打一次 `/Views` 验 token 的设计：那是每翻一页多一个来回，网络一抖整页就空。改为乐观使用缓存 token，只在真的 401 时重登并重试一次（已验证服务端对失效 token 确实回 401 而非空列表，所以不会静默空库）。

## 红线

服务器地址、账号、密码只存在于 Kodi 插件设置和本地 `1.env` 中，不进版本库。access token 缓存在 Kodi 的 `addon_data` profile 目录，也不在仓库内。
