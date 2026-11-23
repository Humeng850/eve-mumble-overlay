# 许可与合规说明（NOTICE）

本文件记录 `EveMumbleOverlay` 与本仓库里其他文件涉及的许可证关系，以及对外分发时的义务。
结论先给：**本项目没有违反 Mumble 的开源许可**，原因见下。

---

## 1. 与 Mumble 的关系：只用了协议，没有用它的代码

| 项目 | 结论 |
| --- | --- |
| 是否复制 / 修改 Mumble 源码 | **没有**。仓库内没有任何来自 `mumble-voip/mumble` 的源文件，也没有逐字照搬的函数、类名、结构名（如 `OverlayMsg`、`uiMagic`、`qlsSocket` 等均未出现） |
| 是否链接 Mumble 的库 / 分发其二进制 | **没有**。程序运行时装的是你本机已安装的 Mumble 客户端，本项目不打包、不修改它 |
| 用了什么 | 只有 **协议事实**：命名管道路径 `\\.\pipe\MumbleOverlayPipe`、魔数 `0x00000005`、7 种消息类型的编号与字段宽度（`INIT` 宽高、`SHMEM` 名字、`BLIT`/`ACTIVE` 矩形、`PID`、`FPS`、`INTERACTIVE`），以及共享内存名 `Local\MumbleOverlayMemoryN` |
| 这些事实的出处 | 阅读 `mumble-voip/mumble` 的 `overlay/overlay.h`（tag `v1.5.915`）确认，代码为本项目独立用 Python 重写 |

为什么这不算侵权：

1. **接口 / 协议常量不受版权保护**。消息类型编号、结构体字段宽度、管道名属于"为实现互操作所必需的接口信息"，各国司法实践普遍不给予版权保护（相当于 API / 文件格式）。
2. **即使按最保守的假设**——把 `overlay/overlay.h` 整体视为受版权保护的代码——Mumble 用的是 **BSD-3-Clause**（见 `licenses/mumble-BSD-3-Clause.txt`），它明确允许"以源码或二进制形式再分发和使用，无论是否修改"，只要求保留版权声明与免责声明、且不得用 The Mumble Developers 的名义为衍生产品背书。本项目连这段声明都没用到（未复制该文件），仍然在第 3 节给出了完整署名，属于超额合规。
3. **BSD-3 不是传染性许可**。它不会要求本项目开源、也不会要求本项目采用同样许可。对比一下：如果 Mumble 是 GPL，那么"复制其代码"才会触发开源义务；而本项目没有复制。

## 2. 运行时行为不构成"绕过技术保护"

程序做三件事：连本地命名管道读位图、创建自己的置顶透明窗口、读取 EVE 窗口的客户区坐标。
没有读取游戏进程内存、没有注入 DLL、没有 hook 图形 API、没有修改 Mumble 的任何文件或配置、没有绕过任何加密或校验机制。
管道本身是 Mumble 以 `WorldAccessOption` 开放的（同一个用户态即可连接），不存在"破解"。

## 3. 署名与许可原文

按 BSD-3-Clause 第 2 条的要求，本仓库以文件形式完整附带了下列许可原文（`licenses/` 目录）：

| 文件 | 覆盖对象 | 许可 |
| --- | --- | --- |
| `licenses/mumble-BSD-3-Clause.txt` | Mumble 覆盖层协议定义（`overlay/overlay.h`），版权归 The Mumble Developers | BSD-3-Clause |
| `licenses/python-PSF-2.0.txt` | 打包进 exe 的 Python 运行时与标准库 | PSF License 2.0 |
| `licenses/pyinstaller-GPL2-with-bootloader-exception.txt` | 打包工具 PyInstaller 的 bootloader | GPL-2.0+，附 Bootloader Exception |

关于 PyInstaller：它本身是 GPL-2.0+，但带有 **Bootloader Exception**——原文明确授予"将编译后的 bootloader 及相关文件链接或嵌入其他程序，并无限制地分发这些组合"的许可。因此用 PyInstaller 打包本项目的 exe **不会**让本项目变成 GPL，也不需要开源本项目。

## 4. 商标与"官方关系"声明

- "Mumble" 是 Mumble 项目的名称。本项目在文档中提及它属于**指明互操作对象的描述性使用**（nominative use）。
- 本项目**与 Mumble 官方（The Mumble Developers / mumble.info）无任何隶属或背书关系**，不是官方插件、不是官方发行版。
- 依据 BSD-3-Clause 第 3 条，本项目**不使用** The Mumble Developers 或贡献者的名义进行推广。
- "EVE Online"、"EVE" 是 CCP hf. 的商标，本项目同样与其无隶属关系。

## 5. 关于 EVE Online 的用户协议

本项目**不读写游戏进程、不自动化游戏操作、不提供游戏内信息优势**，只把语音软件的说话者列表画在自己创建的窗口上（与 Discord / TS 的浮层同类）。
但仍需提醒：第三方覆盖工具在 CCP 的 EULA 下属于灰色地带，风险与责任由使用者自行承担；如官方明确禁止，应停止使用。

## 6. 分发清单（如果你要把 exe 发给别人）

必须一起带走：

```
EveMumbleOverlay.exe
NOTICE.md
licenses/            （三个许可原文）
```

源码文件 `EveMumbleOverlay.pyw`、`build.bat`、`tools/` 可选。
不要删除 `NOTICE.md` 与 `licenses/` —— 那是 BSD-3 与 PSF 许可对"二进制再分发"的硬性要求（保留版权声明与免责声明）。

## 7. 如果将来修改了规则

- 若你**直接复制**了 Mumble 的 C++ 代码（而不是重写），必须在源码与二进制分发物中都保留 `Copyright (C) The Mumble Developers` 与 BSD-3 全文，并在改动文件中加注说明。
- 若你把本项目改用 GPL/AGPL 等许可，也不影响上述第三方许可的独立性。
- 协议魔数若随 Mumble 版本变化（当前 `OVERLAY_MAGIC_NUMBER = 5`，v1.5.915 与 master 一致），需要重新对照上游头文件，而不是猜测。
