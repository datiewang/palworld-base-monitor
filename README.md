# palmon — 帕鲁服务器据点面板

直接解析 Palworld 专用服务器的 `Level.sav`，把据点里的帕鲁、库存、设施、电力、
产出速率和制造配方摊在一个网页上。除了在线人数和服务器名之外，所有数据都来自
存档本身，不依赖任何 mod、插件或游戏内接口。

> **English**: palmon parses a Palworld dedicated server's `Level.sav` and
> serves a single-page dashboard of every base camp — pals and their work
> assignments, stock, facilities, power, measured production rates, and a
> crafting calculator matched against what you actually have. Only the
> player list and uptime come from the server's REST API; everything else is
> read out of the save. The UI is in Chinese; item and pal names follow the
> `[data] language` setting.

面板包含四个标签页：

| 标签页 | 内容 |
| --- | --- |
| ⚠ 警报 | 异常帕鲁、长期空闲统计（谁在闲着、哪座设施没人上工、是不是缺料） |
| 🏕 据点详情 | 每个据点一块面板：物资（原材料 / 一次加工材料）、产出速率、电力、设施、帕鲁与工作适应性矩阵 |
| 🔨 制造计算 | 1295 种可制作物的配方图鉴，自动匹配当前库存，算出能做几个、缺什么、缺的东西哪个商人有卖 |
| 📡 服务器监控 | 内存与在线人数趋势、玩家列表 |

## 需要什么

- Python **3.11+**（用到 `tomllib`）
- 一台能读到 `Level.sav` 的机器（通常就是跑服务器那台）
- `palworld-save-tools`
- 可选：服务器的 REST API（`PalWorldSettings.ini` 里 `RESTAPIEnabled=True`），
  用来取在线玩家、服务器名和运行时长

## 安装

```bash
git clone <this repo> ~/palworld-base-monitor
cd ~/palworld-base-monitor
pip install --user -r requirements.txt

# 1. 下载游戏数据（物品 / 建筑 / 帕鲁数据表、配方、商人）
./palmon.py fetch-data

# 2. 写配置：至少填 REST API 密码
mkdir -p ~/.config/palmon
cp config.example.toml ~/.config/palmon/config.toml
$EDITOR ~/.config/palmon/config.toml

# 3. 看看它找到了什么
./palmon.py config

# 4. 跑起来
./palmon.py serve        # http://<这台机器>:8088/
```

存档路径默认自动搜索（SteamCMD 的常见安装位置下最新的那个 `Level.sav`），
一般不用填。

### 作为服务常驻

```bash
mkdir -p ~/.config/systemd/user
cp systemd/palmon-*.service systemd/palmon-*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now palmon-web.service palmon-update.timer
```

`palmon-web` 是面板本身；`palmon-update.timer` 每 5 分钟跑一次解析，作用是
**没人开着页面的时候也在积累趋势数据**——面板打开时每次刷新都会自己重算一遍。

## 命令

```
./palmon.py config        # 配置从哪来、存档在哪、缺什么
./palmon.py update        # 解析一次存档，写出 status.json
./palmon.py serve         # 常驻，提供面板
./palmon.py fetch-data    # 下载 / 重建游戏数据（游戏更新后重跑）
```

所有命令都接受 `--config FILE`。

## 目录约定

代码和运行时数据是分开的，这样仓库可以随便 `git pull` 覆盖：

```
仓库/                        只读
  palmon/                    Python 包
  web/index.html             面板页面（单文件，无构建步骤）
  systemd/                   服务模板
  config.example.toml

~/.local/share/palmon/       运行时状态（值得备份的是这里）
  status.json                面板读的那份文档
  memory_history.json        内存 / 在线人数趋势
  resource_history.json      每个据点的库存快照，工作占比与产出速率由它算出
  food_history.json          饱食度趋势，据点头部的耗尽预估由它算出
  data/                      fetch-data 下载的游戏数据
```

## 配置

见 `config.example.toml`，每一项都有注释。最常改的三处：

- `[server] rest_api_password` — 没有它面板照常工作，只是玩家列表和运行时长为空
- `[data] language` — 物品 / 帕鲁 / 建筑名的语言，默认 `zh-Hans`
- `[bases] anchors` — 见下

任何一项也都能用环境变量覆盖：`PALMON_WEB_PORT=9000`、
`PALMON_SERVER_SAVE_PATH=...`。

### 关于 `[bases] anchors`

存档里 `BaseCampSaveData` 列出的是**世界上所有公会的据点**，所以必须先挑出
「你的」那一批。默认取据点最多的那个公会，按据点 GUID 编号——绝大多数服务器
这样就对了。

有历史数据之后 anchors 才重要：列出的每个 worker container id 依次占据
base1、base2……，让一个据点的趋势数据始终跟着同一个据点走。**据点拆掉重建会
换一个新的 container id**，那时需要更新对应的 anchor，否则那个据点的所有字段
会静默变空。

## 数据是怎么来的

| 数据 | 来源 |
| --- | --- |
| 帕鲁、库存、设施、工作分配、农田状态、电力 | `Level.sav`（GVAS），用 `palworld-save-tools` 解码，外加本项目对几个解码器的猴子补丁 |
| 在线玩家、服务器名、运行时长 | 服务器自己的 REST API |
| 物品 / 建筑 / 帕鲁数据表与本地化名 | [palworld-save-pal](https://github.com/oMaN-Rod/palworld-save-pal) 的 `data/json` |
| 制作配方 | [palworld.wiki.gg](https://palworld.wiki.gg) 的 `Module:DataManager/item_data.json`，按英文名连接回内部 item id |
| 商人商品与位置 | 同一个 wiki 的 `MerchantItem` Cargo 表 + 手工转录的商人位置表 |
| 工作占比、在岗率、产出速率、耗尽预估 | 本项目自己的历史快照，不是估算公式 |

游戏数据不在仓库里，由 `fetch-data` 下载：它们是别人对游戏数据表的挖掘结果，
体积大且每次游戏更新就过期。

配方为什么走 wiki 而不是直接读游戏文件：权威来源是服务器自带的
`Pal-LinuxServer.pak` 里的 `DT_ItemRecipeDataTable`。那个 pak 的索引没有加密、
能正常解析（本项目开发时确实读到了条目位置），但里面每个资源都是 Oodle 压缩，
而 Oodle 是专有格式——所以只能用别人已经挖好的数据。

## 一些实现上的取舍

- **工作占比不是瞬时值**。`resource_history.json` 里每份快照都记下当时每只帕鲁
  有没有岗位，占比是在这些快照上算的。快照带 schema 版本号，解析器修过 bug 之后
  旧快照会被跳过，而不是把错误数据平均进趋势里。
- **一个工作对象的多个工位共用一个 slot GUID**，只有 `location_index` 不同。按
  GUID 建字典会静默丢掉除最后一个之外的所有工人（本项目踩过：45 个工位只剩 27 个）。
- **产出速率是实测的**，取历史窗口内库存的净增长，不是把设施数量乘以理论速率。
- **面板是单个 HTML 文件**，没有构建步骤、没有外部 JS 依赖。要改样式直接改
  `web/index.html`。

## 致谢

- [palworld-save-tools](https://github.com/cheahjs/palworld-save-tools) — GVAS 解析
- [palworld-save-pal](https://github.com/oMaN-Rod/palworld-save-pal) — 游戏数据表与本地化
- [palworld.wiki.gg](https://palworld.wiki.gg) — 配方与商人数据

MIT 许可，见 `LICENSE`。Palworld 是 Pocketpair, Inc. 的商标，本项目与其无关。
