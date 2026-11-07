# 2026-11-07：在上游 0.3.5 恢复旧 fork 性能优化

字体共享、Fontconfig、Base64、样式与 Shadow DOM 布局缓存已恢复，保留上游新的子节点输入、reset 属性继承及文本测量复用。内核 main 已更新，ruyi-captcha 的发版 pin 同步锁定 `9991779`。

## 同底座实测

优化前是 `9b46ad4`：上游 0.3.5 加当前已验证的通用兼容修复。字体／Base64 阶段为 `8d406f5`；全部优化的代码提交为 `0ee5069`。后续文档提交不改变原生二进制。

| 并发 | CPU 秒/token，前 → 后 | CPU 变化 | 整批峰值 PSS MiB，前 → 后 | PSS 变化 | Token 延迟中位数，前 → 后 |
| --- | --- | --- | --- | --- | --- |
| 1 | 5.129 → 4.401 | -14.2% | 476.7 → 375.2 | -21.3% | 4.606 → 3.642 秒 |
| 2 | 5.067 → 4.144 | -18.2% | 804.6 → 573.5 | -28.7% | 4.563 → 3.641 秒 |
| 4 | 5.125 → 4.111 | -19.8% | 1447.2 → 1035.3 | -28.5% | 5.777 → 4.390 秒 |

1／2／4 并发的 token 延迟中位数分别降低 20.9%／20.2%／24.0%。2／4 并发内存是整批内存，不能当作单任务占用。

每个版本各三档并发、各三批，共 21/21 次测量调用取得 token，加上各一次预热，合计 66/66。全部测量 cgroup 在清理后均无残留进程。资源批次没有逐个向业务后台提交 token；另一次优化后 SDK 调用在 3.228 秒取得非 dummy token，并被 CapSkip 后台接受。

完整原始 CapSkip 页面另做了一次新内核验收：10.784 秒看到 token，11.768 秒获页面后台接受，只有一次验证 POST、没有 surfaced JSError。此前精简底座原始页面的三次记录约 108～110 秒，见内核历史记录；这不是本轮交替资源实验中的成对重测，不能推导固定倍数或长期成功率。

## 分阶段结果与边界

| 并发 | 字体／Base64：CPU 秒/token | 全部优化：CPU 秒/token | 字体／Base64：PSS MiB | 全部优化：PSS MiB |
| --- | --- | --- | --- | --- |
| 1 | 4.526 | 4.401 | 332.3 | 375.2 |
| 2 | 4.504 | 4.144 | 557.0 | 573.5 |
| 4 | 4.589 | 4.111 | 1028.6 | 1035.3 |

字体共享承担主要内存收益，后续缓存进一步降低 CPU。本轮串行样本中，全部优化的 PSS 高于仅字体／Base64 阶段；缓存会保留数据，不能宣称每组补丁都降低内存。该实验只能比较补丁组，不能精确归因单项修改。2 并发字体阶段有一次网络延迟较长，统计仍保留该样本。

使用同一套 Python 绑定、应用 provider、Linux Firefox 159 profile、Necko helper、真实 CapSkip sitekey、system clock 和直连；每次新建 Context，timeout=30、attempts=1。预热后轮换、交替三个版本，测量期间没有并发编译或其他测试。

CPU 读取独立 cgroup 的 cpu.stat，包含调用方、原生进程、Worker、helper、启动和清理，排除采样器。PSS/RSS 每 50 ms 同时采样存活进程树；峰值可能漏采。三批峰值取中位数，CPU 为累计 CPU 除以成功取得的 token 数，token 延迟取所有调用中位数。主机 EPYC 7643、可用 5 个逻辑 CPU。小样本仍受网络、挑战内容和采样竞争影响。

## 行为与打包验收

- 35 项 Linux 原生回归和 smoke 通过；应用 54 项回归通过。
- 新 Linux 内置内核 wheel 在无外部 ruyijs 的隔离环境通过能力检查、API 启动和全部 54 项应用回归；包内核取得的 token 另获 CapSkip 后台接受，取得 token 用时 5.225 秒。轻量 wheel 的无内核安装和生成器也单独验收。
- 字体阶段对照优化前二进制，360 组、12 个 TextMetrics 字段、RGBA 散列及 PNG SHA256 一致；PNG 排除随机 deBG 元数据。
- 样式回归覆盖 CSSOM 编辑、替换／adopted sheet、GC、嵌套 closed scope、隐藏 scope 展开、自定义属性、reset 属性继承及 iframe 尺寸改变后的媒体查询。
- 修复了上游既有的一处缓存失效遗漏：父 frame 改宽度不会改变子文档 DOM revision，computed-block 缓存现在也按媒体环境失效。
- Linux 本地结果不等同于 Windows/macOS 原生验收；平台构建结果由 CI 单独确认。

逐批数据、散列和后台验收摘要见 [JSON](runtime-restoration-20261107.json)。旧 fork 补丁来源与生成器迁移见 [审计说明](fork-performance-and-profiles.zh-CN.md)。

## 复现

`tools/benchmark_runtime.py` 支持 `--variants-json`，输入为包含 `name`、`shell`、`helper` 的数组。各 shell 应为已保留的独立二进制；对照应使用相同 helper。需要 Linux cgroup v2 子组创建权限。

```sh
.venv/bin/python -B tools/benchmark_runtime.py \
  --variants-json /path/to/variants.json \
  --output artifacts/runtime-restoration.json
```
