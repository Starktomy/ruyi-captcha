# 发版与内置内核 wheel

推送与 `pyproject.toml` 版本一致的 `v*` tag 时，`release.yml` 自动构建并发布到
GitHub Releases。也可以手动运行 Release wheels 工作流生成 artifact。

例如版本为 `0.1.0` 时，发布对应 `v0.1.0` tag。流水线先验证版本一致，再执行：

1. 构建轻量 `py3-none-any` wheel 和源码包，在没有内核的环境验证远程客户端。
2. 读取 `src/ruyi_captcha/runtime-lock.json`，从精确的 kernel commit 检出源码。
3. 使用内核仓库自己的脚本编译 Firefox donor、原生运行时与网络组件。
4. 分别构建 Windows x64、Linux x86_64 和 macOS arm64 kernel wheel。
5. 将每个平台的 kernel wheel 嵌入 ruyi-captcha 平台 wheel。
6. 安装应用 wheel，确认未安装外部 `ruyijs`，运行能力检查、API 启动和离线回归。
7. 将同一份 Linux 内置内核 wheel 安装到 Debian 12 和 Ubuntu 26.04，验证内核能力。
8. 所有平台与两项 Linux 安装测试成功后，生成 SHA256SUMS 与 release-manifest.json，并附加到 GitHub Release。

产物为一份源码包与四份 wheel：

| 产物 | 内容与要求 |
| --- | --- |
| `ruyi_captcha-V-py3-none-any.whl` | 轻量应用与远程客户端 |
| `ruyi_captcha-V-py3-none-win_amd64.whl` | Windows x64 内置内核 |
| `ruyi_captcha-V-py3-none-manylinux_2_36_x86_64.whl` | Linux x86_64，glibc 2.36+；Debian 12 / Ubuntu 26.04 |
| `ruyi_captcha-V-py3-none-macosx_26_0_arm64.whl` | macOS 26+ Apple Silicon |
| `ruyi_captcha-V.tar.gz` | 应用源码、构建工具和测试；默认构建轻量包 |

CI 只在版本 tag 和手动触发时编译全部原生内核；日常 push/PR 运行普通 Python CI。
发版不会自动跟随 kernel/main 漂移。升级内核时先更新 runtime-lock.json 中的提交和
能力要求，完成验收，再发布新应用版本。失败的任一平台会阻止发布残缺的 release。

Linux 的 Firefox donor、原生库、NSS/Neqo 和内置 Python 3.14 都在
`python:3.14-bookworm` 中编译，缓存键包含 bookworm/glibc236，避免恢复来自 Ubuntu
24.04 的旧二进制。打包器沿用内核 wheel 的平台标签；内核和应用 wheel 均检查所有
ELF 的 glibc 版本需求、依赖是否随包提供，以及动态库路径是否可重定位。只修改旧包
的文件名或标签不能得到 Debian 12 兼容性。`ci/check_linux_wheel.py` 与内核仓库的
`ci/linux/check_wheel_abi.py` 保持同一实现，让旧的锁定内核也能接受新 CI 的检查。

Debian 12 的 HarfBuzz 6 缺少内核字体实现需要的 synthetic-bold API，Linux 流水线
通过 `ci/build_linux_harfbuzz.sh` 编译固定 SHA-256 的 HarfBuzz 8.3.0 及 subset 库，
许可证沿用原内核打包器的收集路径。锁定的旧内核另通过 `CXXFLAGS` 显式设置自带
Brotli 头文件目录，避免内部引用落到系统旧版头文件；这些属于构建依赖和参数，
不改变 `runtime-lock.json` 锁定的内核源码提交。

两个干净容器使用各自发行版的 Python 安装同一个应用 wheel，发布任务依赖这两项
检查成功。此处描述的是新的 CI 配置；完整原生构建与双系统结果须由实际工作流确认。

2026-11-16 的本地验证包括全部 85 项应用测试、actionlint，以及 Debian 12 和
Ubuntu 26.04 容器各 6 项真实 ELF 夹具检查（包括 DT_RELR 对 glibc 2.36 的要求）；同一份 Debian 编译的 C 测试库在两个
容器均返回 42。夹具结果不代表完整内核 wheel 已在两端验收，本次未触发远端发布
工作流，内核锁定提交也未改变。

## 包内结构

平台 wheel 将 kernel Python 绑定安装到 `ruyi_captcha._vendor.ruyijs`，原生运行时保留
内核原有的 `_runtime/dist`、helper 和动态库相对路径。它不覆盖外部 `ruyijs` 包。
默认优先使用内置版本；轻量包回退到外部绑定。可用 `RUYI_CAPTCHA_KERNEL` 明确选择。

macOS 的 kernel helper 使用安装应用的 Python，保留内核 wheel 的 helper 模块及依赖。
Linux 和 Windows 的 helper Python 随原生运行时打包。内核原始许可证和通知一并保留；
kernel-build.json 记录源提交、输入 wheel SHA256、平台和打包后的 shell SHA256。
重定位动态库会改变 shell 二进制散列，因此源码构建散列与平台包散列分别记录。

## 本机重现

```sh
# /root/ff-ruyijs 必须位于 lock 中记录的提交，已编译当前原生运行时。
.venv/bin/python tools/build_bundled.py --kernel /root/ff-ruyijs

# 或使用同一次 CI 中已经编译的、匹配平台的 kernel wheel。
.venv/bin/python tools/build_bundled.py --kernel-wheel /path/to/ruyijs-*.whl

# 构建轻量包，不需要编译内核。
.venv/bin/python -m build --outdir dist/lightweight
```

本次本机 Linux 平台包约 55 MiB，内核能力、API 启动和 44 项回归均通过，
独立环境中未安装外部 ruyijs；还通过 CapSkip 真实 token 的后台验收。
本机 glibc 是 2.43，所生成 wheel 如实标记为 manylinux_2_43_x86_64。
三平台发版工作流已通过 actionlint 检查；Windows/macOS 的原生构建结果由实际 CI 运行确认。
