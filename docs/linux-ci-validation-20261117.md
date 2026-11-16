# Linux 构建与安装验证（2026-11-17）

Linux 构建改用 `python:3.14-bookworm`，包括原生内核、NSS/Neqo 及内置 Python。
目标 wheel 为 `manylinux_2_36_x86_64`，不再依赖 Ubuntu 24.04 的 glibc 2.39。
应用内核仍锁定 `3999528b6e441337f9566926f87227ae823ed62b`（0.3.5 / Firefox 159）。

| 验证 | 实际结果 |
| --- | --- |
| [应用 Python CI](https://github.com/Starktomy/ruyi-captcha/actions/runs/43929112060) | 通过 |
| [完整 wheel 流水线](https://github.com/Starktomy/ruyi-captcha/actions/runs/43922867741)中的轻量包 | 通过，已生成 wheel 和源码包 |
| 同一流水线中的 Linux 内核 wheel | 通过：101 个 ELF 的最高 glibc 需求为 2.36，安装、helper、网络和 Firefox 159 ClientHello 检查通过 |
| [独立 Linux 应用 CI](https://github.com/Starktomy/ruyi-captcha/actions/runs/43929140807) | 通过：打包、101 个 ELF 检查、87 项应用测试（无跳过）及两个发行版安装/API 验收 |
| 本地用该 CI 内核组装的应用内置包 | 通过：101 个 ELF 的最高 glibc 需求为 2.36 |
| 同一应用包安装到干净 Debian 12 / Python 3.11 容器 | 通过 Python Context、能力探针及 API 健康/运行时检查 |
| 同一应用包安装到干净 Ubuntu 26.04 / Python 3.14 容器 | 同上，均不依赖外部 `ruyijs` 包 |

两端能力探针的 Trusted Types、closed-shadow host、trusted input 和 frame input
均为 true。两端各执行 87 项应用测试：80 项通过，7 项因未安装 C/Rust 编译工具及
readelf 而跳过；实际 wheel 的 101 个 ELF 已单独完成 ABI 检查。

本地组装的应用 wheel SHA-256：
`40e6eaca75f061e918f768a64d324012ebabacb7fe6368aa1623a7d00b705094`。
其远端内核输入 `ruyijs-0.3.5-py3-none-manylinux_2_36_x86_64.whl` 的 SHA-256：
`b145e8ff67c4d353d4656993182cd00024aa16c7e72ffa646ca2209161ee2f3d`。

构建器修复了容器 Git 信任路径、LLVM 仓库安装前置依赖、Brotli 头文件目录、
HarfBuzz 8.3.0 源码编译、Cargo 1.100 产物定位及 NSS 构建期间的库搜索路径。
Linux 构建工具补丁及依赖参数见 [发布说明](release.md)，原生运行时源码锁定未改变。

同步后的 0.4.1 / Firefox 160 内核另已通过
[独立远端 CI](https://github.com/Starktomy/ff-ruyijs/actions/runs/43922863710)，
同一内核 wheel 在 Debian 12 和 Ubuntu 26.04 的安装及运行检查均通过。
该内核升级没有自动带入应用的锁定版本。

原完整流水线的三个平台内核和应用包均已构建、测试成功，但其最后的 Linux
安装任务缺少可选的 FastAPI/httpx 依赖而失败。当前 `linux-application.yml` 改为
安装 `[api,test]`，并将 Linux 打包和双系统验收拆为独立任务，已在上述新运行通过。
复用内核 artifact 时还会核对所在提交与当前源码的内核锁定一致。

远端验收通过的应用内置 wheel SHA-256：
`f410d4dfe508f2e975e437b8b0c226e5407b22c1ce227c8a04d3bf68a95d495e`。
轻量 wheel、Linux/Windows/macOS 内置 wheel 和源码包共五个文件已通过
`ci/release_metadata.py` 实际清单检查，并生成 SHA256SUMS。本次未创建 tag 或发布 release。
