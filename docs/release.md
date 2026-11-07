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
7. 所有平台成功后，生成 SHA256SUMS 与 release-manifest.json，并附加到 GitHub Release。

产物为一份源码包与四份 wheel：

| 产物 | 内容与要求 |
| --- | --- |
| `ruyi_captcha-V-py3-none-any.whl` | 轻量应用与远程客户端 |
| `ruyi_captcha-V-py3-none-win_amd64.whl` | Windows x64 内置内核 |
| `ruyi_captcha-V-py3-none-manylinux_2_39_x86_64.whl` | Linux x86_64，glibc 2.39+ |
| `ruyi_captcha-V-py3-none-macosx_26_0_arm64.whl` | macOS 26+ Apple Silicon |
| `ruyi_captcha-V.tar.gz` | 应用源码、构建工具和测试；默认构建轻量包 |

CI 只在版本 tag 和手动触发时编译全部原生内核；日常 push/PR 运行普通 Python CI。
发版不会自动跟随 kernel/main 漂移。升级内核时先更新 runtime-lock.json 中的提交和
能力要求，完成验收，再发布新应用版本。失败的任一平台会阻止发布残缺的 release。

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
