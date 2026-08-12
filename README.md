# kodi-addons

自用的 Kodi 插件集合，带一个自建更新源——装一次仓库，之后所有插件由 Kodi 自动提示更新。

## 插件

| 插件 | 说明 |
|---|---|
| [plugin.video.uhd](plugin.video.uhd/) | UHD 媒体服务器客户端（Emby 兼容接口） |

## 安装

1. 下载 `repository.kodiaddons-*.zip`：[Releases](https://github.com/zzzwannasleep/kodi-addons/releases) 或 <https://zzzwannasleep.github.io/kodi-addons/>
2. Kodi → 插件 → 从 zip 文件安装 → 选中它
3. 之后在「从存储库安装 → Kodi Addons」里装需要的插件

> **必须走第 3 步。** Kodi 只自动更新它自己从仓库装过的插件；手动装 zip 或直接把目录拷进
> `addons/` 的，数据库里 `origin` 为空，永远收不到更新。插件设置存在 `addon_data`，重装不丢。

需要 Kodi 20 (Nexus) 及以上。

## 加一个新插件

新建 `<addon.id>/` 目录，放 `addon.xml` 和 `icon.png`，push。构建脚本扫描所有含 `addon.xml`
的顶层目录，不需要改任何清单。目录名必须和 `addon.xml` 里的 `id` 一致（CI 会检查）。

## 发布

改完代码，把该插件 `addon.xml` 里的 `version` 加一档，push 到 `main`：

1. `tools/check_static.py` 用 Python 3.8（Kodi 21 内置的版本）跑静态检查
2. `tools/make_repo.py` 生成各插件 zip、仓库插件 zip、`addons.xml` 与 md5
3. 推到 `gh-pages`，旧版本 zip 保留，可回滚
4. 每个还没打过 tag 的版本建一个 Release（tag 即 zip 名，如 `plugin.video.uhd-1.0.2`）

版本号没变就只刷索引，不重复发版。本地预览：`python tools/make_repo.py --out repo`。

`repository.kodiaddons` 的版本单独维护在 `tools/make_repo.py` 里，只有仓库地址变了才需要动
——跟着插件版本一起涨会让 Kodi 多花一个更新周期先升仓库、下一轮才轮到插件。

## 红线

服务器地址、账号、密码只存在于各插件的 Kodi 设置和本地被 gitignore 的 `1.env` 里，不进版本库。
access token 缓存在 Kodi 的 `addon_data`，同样不在仓库内。`tools/check_static.py` 里有一条
扫描，扫到硬编码地址或凭据直接让 CI 失败。
