# 旧 fork 性能补丁与多环境生成器审计

审计基准：旧完整 fork `158eae7`（`backup/main-full-20261107`），当前精简
`main` 为 `b9877c4`，基于上游 0.3.5 `0ab7201`。下面的历史收益来自旧版本
实验，不是逐项恢复到 0.3.5 后的新结果，也不能相加。

## 哪些降低资源开销

| 旧提交 | 机制与主要收益 | 当前情况与迁移方式 |
| --- | --- | --- |
| `0f8872c` | Linux 字体按需初始化；同一 JS 线程的 realm 共用 FontCollection；HarfBuzz 读取 Skia 映射的字体数据，减少字体表复制。这是最直接的已测内存优化。 | 当前 `font_posix.cpp` 仍按实例初始化并复制字体表，旧共享／映射路径未恢复。优先迁回内核，保留原有 fallback。 |
| `d6b3c91` | Fontconfig family/style 匹配独立缓存，与字形 fallback 分开，缓存上限 256 项，减少重复匹配。 | 旧缓存未保留；放在当前 Linux `font_posix.cpp`，保持字体配置更新时失效。 |
| `e4702db` | Base64 使用连续字节／Latin1 路径，减少逐字符访问和 UTF-16 中间缓冲；同时改进派生 Skia 的 Fontconfig 匹配。 | 当前 Base64 仍有旧逐字符路径；Fontconfig 部分需随构建脚本恢复，不覆盖 Firefox donor 源码。 |
| `17ce940` | 短 Base64 解码使用栈缓冲和小字符串分配路径；大结果转移缓冲所有权。 | 与上一项配套迁回内核。 |
| `88c6389`、`6c84341` | 复用 selector dependencies、stylesheet rule fingerprint 和 media 摘要，在级联缓存命中时避开重复扫描。 | 当前仍有重复摘要／依赖构建；按失效语义融合，不直接复制整个样式子系统。 |
| `be13222`、`b9cd792` | 一次样式读取内记忆父／兄弟关系；复用 physical declarations，延迟建立局部样式上下文，减少查找和分配。 | 旧实现未恢复；保留上游 0.3.5 的 `child_inputs` 和 reset-property 跟踪。 |
| `bf41b40`、`259f2d0` | inline 编辑后复用未受影响的 shadow 样式；全部 cascade scope 未变化时复用 composed layout。 | 上游现有布局已有文本测量复用等优化，需逐项合并。整文件覆盖会丢失上游的新行为。 |

样式缓存主要降低 CPU 和临时分配；缓存自身也占内存，不能只凭“增加缓存”推断峰值内存下降。
上游的 `Engine` 多 Context 共享进程功能也有资源价值，但不是这批旧 fork 的独有补丁。

## 有哪些实测证据

旧字体共享／映射实验的峰值进程树 PSS：

| 并发 | 优化前 → 后 | 变化 |
| --- | --- | --- |
| 1 | 423 → 299 MiB | −29.4% |
| 2 | 677 → 517 MiB | −23.7% |
| 4 | 1255 → 972 MiB | −22.6% |

上述 2／4 并发数值是整批内存。CPU 收益较小且不一致：2 并发这批 CPU/token
反而增加 3.9%。360 个字体渲染用例的 TextMetrics、RGBA 和 PNG 内容保持一致，
比较时排除了随机 PNG 元数据。

后续 Base64＋Fontconfig 组合实验在 1／2／4 并发的 CPU/token 分别降低
6.6%／7.9%／4.4%，峰值 PSS 分别降低 1.5%／1.4%／3.8%；这些调用合计 CPU
降低 5.1%。另一个带 metadata 的控制负载出现回退，不能承诺所有场景加速。
独立编码微基准 CPU 0.52 → 0.14 秒，仅说明编码路径成本。

其他局部证据：Fontconfig sweep 3.034 → 0.523 秒，1461 行度量及采样像素散列一致；
关系记忆微基准 0.3535 → 0.2894 秒；规则展开 1.0850 → 0.7779 秒。
旧 shadow 样式／布局组合的小样本（各 3 次）显示 token 时主线程 CPU 中位数
降低 11.5%、token 延迟降低 6.0%。这些结果不能等同于长期验证码吞吐提升。

原始记录保留在旧内核的
[CLOUDFLARE.md](https://github.com/Starktomy/ff-ruyijs/blob/158eae70e9cc78b263474aca58bc42d10412c990/native_tests/CLOUDFLARE.md)。
本次已做过的[完整旧 fork 与当前 0.3.5 底座对比](runtime-comparison-20261107.md)
显示当前 CPU/token 高 15.7%～21.5%、PSS 高 29.9%～35.6%；使用相同 helper
仍存在差距，但尚未逐项归因。

建议按“字体共享／映射 → Fontconfig／Base64 → CSS 缓存 → shadow/layout”分组恢复。
每组先验证行为和缓存失效，再交替测真实负载的 CPU/token、整批 PSS 与后台接受结果。
保持与上游的差异集中在这些通用能力，避免将验证码站点状态机带回内核。

## 哪个是多环境生成器，放在哪里

旧提交 **`b014478`** 的 `python/ruyijs/profile_generator.py` 就是生成器。
它已迁到 **`src/ruyi_captcha/profiles/generator.py`**，Linux／Windows JSON 模板
与它一起随轻量 wheel 和内置内核 wheel 分发。

- 应用层负责模板、seed、语言／时区组合、批量导出以及以后按任务选择 profile。
- 内核保留 `Context.configure_fingerprint()`，负责属性、realm 继承及原生渲染行为。
- SDK、HTTP API 使用相同 profile 字典；HTTP 请求已有 `profile` 字段，无需单独的生成服务。

```python
from ruyi_captcha import generate_profile

profile = generate_profile("linux", seed=42, locale="en-US",
                           timezone="America/New_York", hardware_concurrency=8)
```

CLI 支持 `ruyi-captcha profile` 与 `python -m ruyi_captcha.profiles`。
`--count` 支持 1～10000；固定 seed 的批量输出依次使用 seed、seed+1 等。
生成器使用局部随机数，不改变调用方全局 random 状态，每次返回独立的嵌套字典。
相同参数和 seed 的复现以相同生成器版本为前提。

范围是 Firefox 159 的桌面属性组合：UA／platform／oscpu／WebGL 模板与 OS 配套，
变化项包括屏幕、DPR、逻辑 CPU 数及显式语言／时区。它不会创建完整的其他 OS、
字体集合或 GPU；Windows profile 在 Linux 上不能保证 Windows 字体／Canvas 输出。
`hardware_concurrency=8` 是浏览器报告值，不限制实际线程或 CPU；API 的并发限制由任务池控制。

本次测试还确认：精简内核会将已配置的 WebGL 等 owner group 在新 iframe 中丢失，
例如父页面为 Linux llvmpipe，子页面却回到 Windows ANGLE 默认值。旧 `bb58104`
修复已恢复到 `fix/perimeterx-browser-compat` 的 `9b46ad4`，覆盖初始／嵌套 iframe、跨域导航、
srcdoc、顶层文档替换和 GC。发版锁定的 `main` 内核尚未包含该分支；应用生成器
不会在 Python 层自行伪造子页面配置。升级发布 pin 前需要单独验收内核变更。
