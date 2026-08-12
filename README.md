# kodi-addons

自用的 Kodi 插件，带一个自建更新源——装一次仓库，之后所有插件由 Kodi 自动提示更新。
需要 Kodi 20 (Nexus) 及以上。

## 插件

| 插件 | 说明 |
|---|---|
| [plugin.video.uhd](plugin.video.uhd/) | UHD 媒体服务器客户端（Emby 兼容接口） |

## 安装

先开一次 Kodi 的**设置 → 系统 → 附加组件 → 未知来源**，否则装不了非官方插件。

1. 下载 `repository.kodiaddons-*.zip`：[Releases](https://github.com/zzzwannasleep/kodi-addons/releases)
   或 <https://zzzwannasleep.github.io/kodi-addons/>
2. Kodi → 插件 → 从 zip 文件安装 → 选中它
3. 插件 → **从存储库安装 → Kodi Addons** → 装需要的插件

> **第 3 步别跳过。** Kodi 只自动更新它自己从仓库装过的插件；手动装 zip 或直接把目录拷进
> `addons/` 的，数据库里 `origin` 为空，永远收不到更新。插件设置存在 `addon_data`，重装不丢。

## 在 Kodi 里直接添加源（可选）

不想先下载 zip 的话，把更新源加成 Kodi 的文件源，上面第 1、2 步就都在 Kodi 里完成：

1. 设置 → 文件管理器 → 添加源 → `<无>`，路径填
   `https://zzzwannasleep.github.io/kodi-addons/`，起个名字（如 `kodi-addons`）
2. 插件 → 从 zip 文件安装 → 选这个源 → `repository.kodiaddons/` → 选里面的 zip
3. 之后同上，从存储库安装

装完仓库之后这个源就没用了，可以删掉——更新走的是仓库自己的地址，不是这个源。
