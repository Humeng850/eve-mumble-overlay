# EVE Online 语音浮层（Mumble 说话者显示）

[![build](https://github.com/Humeng850/eve-mumble-overlay/actions/workflows/build.yml/badge.svg)](https://github.com/Humeng850/eve-mumble-overlay/actions/workflows/build.yml)
[![release](https://img.shields.io/github/v/release/Humeng850/eve-mumble-overlay)](https://github.com/Humeng850/eve-mumble-overlay/releases/latest)

Mumble 自带的「游戏内显示 / overlay」在 EVE Online 里不生效。本项目绕开它失效的环节，
把 Mumble **已经渲染好的**说话者浮层贴到 EVE 窗口上方。

## 为什么原生 overlay 在 EVE 里不显示

实测证据（本机 Mumble 1.5.915 + EVE 64 位客户端 `bin64\exefile.exe`）：

| 环节 | 结果 |
| --- | --- |
| `mumble_ol_x64.dll` 是否注入 EVE 进程 | **已注入**，4 个客户端全都有 |
| 排除模式 / 白名单（`.mumblelay` 里的 `"paths": []`） | 与本次无关，注入根本没被拦 |
| Mumble 是否能渲染浮层 | **能**，接管道就能拿到带名字的位图 |
| DX11 呈现链挂载 | **失败**：Mumble 只 hook `IDXGISwapChain::Present`（vtable 槽 8）和 `ResizeBuffers`（槽 13），源码中无任何 `Present1` 处理。EVE 走 flip-model / `Present1` 路径，钩子不触发，DLL 在进程里但一帧都不画 |

所以原生 overlay 想修只有两条路：给 Mumble 提 PR 补 `Present1`，或者像本项目这样不依赖它在游戏内绘制。

## 为什么不做成 Mumble 插件

插件跑在 Mumble 进程内部，只能拿到「谁在说话」的状态，而缺的那一步是「把像素画到别人窗口的
画面上」—— 插件没有这个能力，那正是 Mumble 自己 hook 失败的部分。所以容器只能是独立进程，
做成托盘常驻是最省事的形式。

## 原理

Mumble 客户端在 `\\.\pipe\MumbleOverlayPipe` 上做服务端，把整个浮层渲染成 ARGB 预乘位图放进
共享内存（`Local\MumbleOverlayMemoryN`），再用消息告诉客户端贴哪块：

```
INIT(宽,高) -> SHMEM(共享内存名) -> BLIT(x,y,w,h) -> ACTIVE(x,y,w,h)
协议定义见 mumble 仓库 overlay/overlay.h，OVERLAY_MAGIC_NUMBER = 5
```

本项目扮演一个「虚拟 overlay 客户端」：收位图 → 贴到置顶、鼠标穿透的分层窗口 → 跟随 EVE 窗口。
不注入、不 hook DirectX，因此和 EVE 的渲染器、EVE-O-Preview 都不冲突。
说话判定、字体、颜色、位置全部沿用你 Mumble 里的设置（`.mumblelay`），改设置立即生效。

## 用法

**日常：下载 [Releases](https://github.com/Humeng850/eve-mumble-overlay/releases/latest) 里的 `EveMumbleOverlay.exe` 并双击** —— 单文件、免安装 Python、不弹控制台，右下角托盘出现绿色圆点图标。

> 首次运行如果弹「Windows 已保护你的电脑」（因为 exe 没有代码签名），点「更多信息 → 仍要运行」。
> 若杀软误报，把该 exe 加进白名单；它只做三件事：连 Mumble 的本地命名管道、贴一个透明窗口、写自己的日志。

托盘右键菜单：

| 菜单项 | 作用 |
| --- | --- |
| 屏蔽浮层 / 启用浮层 | 临时暂停显示（等同于热键 `Ctrl+Alt+M`） |
| 全部客户端同时显示 | 勾选后所有可见的 EVE 客户端各贴一份浮层；默认只跟随前台客户端。开关记在 `config.json` |
| 开机自动启动 | 勾选后写入 `HKCU\...\Run`，开机静默拉起（显示当前状态） |
| 打开日志文件 | 出问题时看这里 |
| 退出 | 关掉整个程序 |

- 双击托盘图标 = 显示/隐藏浮层
- 热键 `Ctrl+Alt+M`（被占用时自动退到 `Alt+F8`）
- **跟随 EVE-O-Preview 切换**：谁在前台就贴谁；非当前客户端被最小化时自动隐藏，切回来立刻恢复。
  目标切换做了 0.3 秒去抖，不会在几个客户端之间乱跳。
- **只允许一个实例**：重复启动会提示「已在运行」并退出
- **随 Mumble 自动接管**：托盘程序一直等在那儿，Mumble 起来就自动连上，Mumble 关掉就回到「等待 Mumble」
- Win11 新图标默认收进托盘溢出区（任务栏的 `^`），想常驻可见就把它从溢出区拖到任务栏上

想把浮层放到桌面/任务栏固定，直接给 exe 建一个快捷方式即可（exe 自带图标）。

排错时用 `run.bat`（跑源码版，带控制台输出）。

## 启动顺序

**无关。** 三种顺序都验证过（`tools/test_order.py`）：

| 顺序 | 行为 |
| --- | --- |
| 插件 → Mumble → EVE | 启动后先空跑等待；Mumble 起来 3 秒内连上管道；EVE 窗口出现 0.5 秒内锁定并显示 |
| 插件 → EVE → Mumble | 先锁定窗口、打一条"等待 Mumble 覆盖层管道"日志；Mumble 起来后自动连上 |
| 三者都在，随便换客户端 | 跟随前台切换，画布与管道全程复用 |

## 已知限制

- **必须窗口化 / 无边框窗口**。独占全屏（exclusive fullscreen）下任何置顶窗口都盖不上去，这是 Windows 的限制，不是本项目的问题。EVE 默认窗口化，用 EVE-O-Preview 的也必然是窗口化。
- 默认只在**一个**客户端上显示（跟随当前前台）；要 4 开同时显示，勾选托盘菜单的「全部客户端同时显示」。
  多客户端模式共用**一块画布**（Mumble 对同一画布尺寸渲染的内容完全一样），所以开几个客户端内存都不会翻倍。
- 画布在程序运行期间保持，不因客户端消失而重建 —— 切窗口/最小化再恢复都不会重新连管道。
  只有反复改动游戏分辨率、画布超过 3 块时才淘汰最久未用的那块。
- 需要 Mumble 客户端已连接服务器且「游戏内显示」处于启用状态（否则管道里没有内容）。
- exe 是 PyInstaller onefile 打包，启动时要把运行时解到临时目录，冷启动约 1~2 秒；这不影响浮层本身。
- 源码版 `EveMumbleOverlay.pyw` 仍然可用（需要本机 Python 3，零第三方包）。

## 文件

| 文件 | 作用 |
| --- | --- |
| `EveMumbleOverlay.exe` | **成品单文件**（约 10 MB，免 Python，无控制台，带图标）—— 不打进仓库，从 [Releases](https://github.com/Humeng850/eve-mumble-overlay/releases/latest) 下载 |
| `EveMumbleOverlay.pyw` | 源码（单文件，含托盘 / 窗口跟随 / 管道客户端，只能标准库） |
| `build.bat` | 改完源码后一键重新打包 exe |
| `overlay.ico` | 托盘 / exe 图标 |
| `run.bat` | 排错用：跑源码版，带控制台日志 |
| `NOTICE.md` | 许可与合规说明（分发前必读） |
| `licenses/` | 第三方许可原文：Mumble BSD-3、Python PSF-2.0、PyInstaller GPL2+Bootloader Exception |
| `tools/probe_pipe.py` | 诊断：连管道并把 Mumble 渲染的位图导出成 `overlay_dump.bmp` |
| `tools/window_report.py` | 诊断：列出所有 EVE 客户端窗口的位置/最小化/前台状态 |
| `tools/switch_test.py` | 回归测试：模拟 EVE-O-Preview 的窗口切换，验证浮层跟随与恢复 |
| `tools/test_multi.py` | 回归测试：多客户端模式产出 2 个浮层窗口、分别贴在各自客户区原点 |
| `tools/test_order.py` | 回归测试：Mumble 后启动 / EVE 后启动 / 两者都没启动，均能自动接管 |
| `tools/capture_screen.py` | 诊断：抓全屏成 BMP |
| `tools/crop_bmp.py` | 诊断：裁切 BMP 局部 |
| `tools/make_icon.py` | 重新生成 `overlay.ico` |

日志：`%LOCALAPPDATA%\EveMumbleOverlay\overlay.log`（超过 512KB 自动轮转成 `.old`）

## 排查顺序

0. 双击 exe 没反应 → 看日志文件最后几行；或双击 `run.bat` 跑源码版看控制台。`.bat` 是 CRLF + 纯 ASCII，别用编辑器改存成 LF/UTF-8。
1. 日志出现 `等待 Mumble` → Mumble 没开，或「游戏内显示」没启用。
2. 日志出现 `等待 EVE 窗口` → 客户端最小化，或被别的窗口挡住；也可用 `--pid` 指定。
3. 日志出现 `检测到说话者` 但屏幕没东西 → 看紧跟着有没有 `浮层显示`；再跑 `tools/probe_pipe.py` 看 `overlay_dump.bmp` 里有没有名字。
4. 名字位置跑到屏幕外 → 检查 Mumble 里 overlay 编辑器保存的 `x_position / y_position`。

## 命令行（高级）

```bat
python EveMumbleOverlay.pyw                  :: 有控制台时打印日志
python EveMumbleOverlay.pyw --autostart on   :: 直接写开机自启（off 取消）
python EveMumbleOverlay.pyw --pid 43252      :: 绑定指定 EVE 客户端
python EveMumbleOverlay.pyw --duration 20    :: 自测，20 秒后退出
python EveMumbleOverlay.pyw --multi          :: 所有可见客户端同时显示
python EveMumbleOverlay.pyw --single         :: 强制只跟随前台客户端
```

## 构建

**自动（推荐）**：`.github/workflows/build.yml` 在 `windows-latest` 上跑，流程是
装 Python 3.12 → 装 PyInstaller → 打包单文件 exe → 冒烟测试（`--autostart off` 必须返回 0 且体积 > 3 MB）→ 上传 artifact。

- 推 `main` / 提 PR：只产出 artifact，不碰 Release
- 推 `v*` tag：自动创建或更新对应 Release，并把 exe 作为附件上传（同名覆盖）
- 也能在 Actions 页面手动 `Run workflow`

所以发版只要：`git tag v1.0.1 && git push origin v1.0.1`，其余全自动。

**本地**：双击 `build.bat`（内部就是同一条 PyInstaller 命令，产物覆盖根目录的 exe）。

## 许可与合规

- 本项目**独立实现** Mumble 覆盖层的通信协议，只用了协议常量与字段宽度这类互操作必需的事实信息
  （出处 `mumble-voip/mumble` 的 `overlay/overlay.h`，BSD-3-Clause）；**没有复制、链接或分发 Mumble
  的任何源码或二进制**，因此不触发任何开源义务。
- 提及 "Mumble" 属于指明互操作对象的描述性使用，本项目**与 Mumble 官方无隶属或背书关系**。
- exe 内打包了 Python 运行时（PSF-2.0）与 PyInstaller bootloader（GPL-2.0+ 带 Bootloader Exception，
  明确允许无限制地嵌入与分发组合程序，不影响本项目许可）。
- 对外分发时必须同时带走 `NOTICE.md` 与 `licenses/`。完整分析见 [NOTICE.md](NOTICE.md)。
