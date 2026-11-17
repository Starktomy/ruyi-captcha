# 应用内核升级到 0.4.1（2026-11-17）

应用的 `runtime-lock.json` 从 0.3.5 / Firefox 159 升级到 fork 0.4.1 / Firefox 160。
锁定 `1463bc5eaecc0224f0ed2098fca4534b7f13d0dd`，对应实际通过
[内核 CI](https://github.com/Starktomy/ff-ruyijs/actions/runs/43922863710) 的源码提交；
后续 `6f61cd6` 只修改文档。上游底座更新为
`31cea48145d54ab4d2b9b1522e6288ff81c35f6f`。

Trusted Types、closed Shadow DOM 宿主访问和 iframe 可信输入能力要求继续保留。
新内置包的验收会同时检查实际运行版本为 0.4.1、能力探针以及打包后的内核散列。
应用现有的兼容 profile 和 TLS 配置继续独立于内核版本管理。

| 已验证内核输入 | SHA-256 |
| --- | --- |
| `ruyijs-0.4.1-py3-none-manylinux_2_36_x86_64.whl` | `eed0783398473ea82e7ee10e4ee09bf73a9f4b96c90465cfa577e73dbc23891e` |
| 其中的原生 `dist/js` | `2f82fb94971d373947817292f24472139bc24b32c7891a20a836fb5b29c35a58` |

Linux 打包流水线新增 `kernel_repository`，可从锁定的内核仓库复用上述产物。
来源为应用仓库时核对构建提交中的 runtime lock；来源为内核仓库时直接核对
仓库名和构建提交。这保留了精确源码锁定，并省去重复编译已验收的内核。

本地已完成源码应用回归（87 项通过）及新内置 wheel 的 101 个 ELF 检查，最高
glibc 需求为 2.36。新内置包安装到独立环境后，实际运行版本为 0.4.1，原生散列与
上表一致；能力探针、Python Context、API 健康及 runtime 接口检查通过，全部
87 项应用测试通过、无跳过。

[应用远端 CI](https://github.com/Starktomy/ruyi-captcha/actions/runs/44032135725)
已通过应用打包、101 个 ELF 检查、87 项应用测试（无跳过），并在 Debian 12 /
Python 3.11 和 Ubuntu 26.04 / Python 3.14 安装同一份内置包后，验证实际版本为
0.4.1、原生散列及能力匹配，Python Context 和 API 健康/runtime 检查均通过。
[应用 Python CI](https://github.com/Starktomy/ruyi-captcha/actions/runs/44032114245)
也已通过。
远端应用 wheel `ruyi_captcha-0.1.0-py3-none-manylinux_2_36_x86_64.whl` 的 SHA-256：
`db30cc7339a4fa8834111c71428e57d0174a54a95e3e831efc481e8bc432ad69`。

本轮没有新增线上验证码后台接受结果，也未创建发布 tag。
