# 2026-11-07：PerimeterX 排查与真实验证

**ff-ruyijs 尚未通过这个挑战。** 已修复四个可独立复现的浏览器兼容问题，完善诊断工具，
并使用用户提供的代理做了真实对照；PerimeterX 仍未注册为 SDK/API 支持类型。

NopeCHA 的页面 `https://nopecha.com/captcha/perimeterx` 嵌入
`https://www.walmart.com/blocked`，使用 HUMAN/PerimeterX Press & Hold，appId 为
`PXu6b0qd2S`。它不是 Turnstile 的 sitekey/token 回调流程。

## 已确认与已修复

内核修复在 ff-ruyijs 的 `fix/perimeterx-browser-compat` 分支，提交
[`1a5475a`](https://github.com/Starktomy/ff-ruyijs/commit/1a5475a)。

1. 从其他同源 iframe 借用 `Document.prototype` getter，会错误地拒绝有效的 Document。
   现在根据接收对象所属文档读取 body、defaultView、URL 等属性。
2. MouseEvent、PointerEvent 和 WheelEvent 的 `which` 原来始终是 0。
   现在遵循 Firefox 的 `button + 1`；左键为 1，pointermove 的 button=-1 对应 0。
3. iframe 的 `mozInnerScreenX/Y` 原来忽略 iframe 位置，返回顶层窗口原点。
   现在通过布局所有者投影 iframe 内容框，计算嵌套位置、边框、内边距、滚动和变换，
   隐藏祖先下的 frame 返回 0。可信输入使用同一个屏幕原点。

4. 新 iframe 没有继承配置中的 WebGL 等 owner group：Linux 父页面显示 llvmpipe，
   子页面却恢复 Windows ANGLE 默认值。恢复旧 `bb58104` 的通用继承逻辑，
   在目标 realm 包装配置对象，继续让原生实例和文档状态相互独立。新提交
   [`9b46ad4`](https://github.com/Starktomy/ff-ruyijs/commit/9b46ad4) 覆盖嵌套 frame、
   跨域导航、srcdoc、顶层文档替换及 GC。

四个问题均先在不访问网络的最小用例中复现，再修复。最新 15 项原生测试和 smoke
检查通过；应用 54 项测试通过。新生成的美国语言／时区 profile 在最新内核上取得
真实 Turnstile token，约 4.5 秒，并被 CapSkip 后端接受。这个回归不代表 PerimeterX 通过。

## 实测结果

| 路径 | 输入与观察 | 结果 |
| --- | --- | --- |
| 直连 NopeCHA，固定按住 6/8 秒；旧完整 fork 按住 8 秒 | 最早一轮对照 | Please try again |
| 直连，按页面完成提示释放 | 按住约 6.1 秒；18.4 秒结束 | 明确重试 |
| 直连，无障碍模式 | 点击 Accessible challenge，等 Press again，再点击 | 明确重试；22.9 秒 |
| 代理第 1 条，原始 SOCKS5 | 按住约 5.1 秒，进度完成后释放 | 明确重试；28.2 秒 |
| 代理第 1 条，修复屏幕坐标后 | 按住约 3.4 秒，进度完成后释放 | 明确重试；26.8 秒 |
| 代理第 1 条，直接 Walmart，默认 profile | 按住约 11.8 秒，按完成提示释放 | 明确重试；31.5 秒 |
| 代理第 2 条 | 挑战脚本请求超时，没有向按钮发送输入 | 未验证，不能算挑战拒绝或通过 |
| 代理第 3 条，直接 Walmart，最终内核 | 延迟点击，按住约 6.4 秒，完成后释放 | 明确重试；40.1 秒 |
| 代理第 3 条，校准全部输入标志后 | 按住约 2.9 秒，按完成提示释放 | 明确重试；33.3 秒；主页仍可返回 200 |
| 代理第 3 条，继承修复＋生成的美国 profile | 正常完成，按住约 6.5 秒 | 明确重试；25.8 秒 |
| 相同生成 profile，仅将时区改为上海 | 正常完成，按住约 11.1 秒 | 明确重试；37.4 秒 |
| 继承修复＋美国 profile，增加连续鼠标移动 | 正常完成，按住约 4.2 秒 | 明确重试；27.2 秒 |
| 继承修复＋美国 profile，无障碍两次点击 | 完成页面规定的两次点击 | 明确重试；28.4 秒 |

美国／上海对照是同一条原始 SOCKS 会话凭据，各新建 Context；没有记录公开 IP。
单次样本不能证明出口在整个期间没有变化，也不足以比较时区对成功率的影响。
另一次连续移动运行在控件加载较晚时耗尽预算，只有 down、没有完成释放；该次保持
`unconfirmed`，没有计成明确拒绝。

额外插桩运行现在读到真实 collector 命令 `oIIIoooI` 携带数值状态 **`-1`**。
公开脚本将其送入 `PX764`，解析为状态后进入失败路径；其成功路径要求状态 `0`。
这确认了该次是服务端拒绝，不能仅解释为扫描器没看到成功 UI。没有保存命令中的
Cookie 或 token 参数，也没有改写服务端结果。新工具选项 `--observe-server-result`
可只包装并转发该回调，记录有上限的数值状态；报告会标记 `instrumented: true`，
即使观察到 `0` 也不会自动宣称干净环境验收成功。保留的新观察器在 live 运行中
同样得到 `server_results: [{"status": -1}]`，30.4 秒后显示重试，没有脚本异常。

还从公开采集脚本提取了它检查的 318 个 Window／Document／Navigator／Location
属性，在同为安全上下文的 ff-ruyijs 与完整 Firefox 159 中比较存在性、类型及真假值。
只有两处差异：窗口 `mozInnerScreenX` 的零／非零（窗口位置不同），以及
`selectedStyleSheetSet` 的 null／空字符串。这不是完整的浏览器一致性测试，页面
样式表也未完全统一，后者尚未确认是本挑战的原因；不能由此证明指纹已通过。

原始 SOCKS5 连通性检查中，同一会话的两个独立连接得到相同出口；首次 HTTP CONNECT
检查曾观察到不同出口，所以随后对照使用原始 SOCKS5。测试用本地 CONNECT 适配器
转发 TCP 字节，目标站 TLS 仍由 Necko/NSS 或参考 Firefox 处理。

另做了 Firefox 159 对照：包括浏览器测试协议，以及关闭自动化接口、通过隔离 X11 显示
发送系统鼠标输入。直连出现重试；代理下能观察到进度完成、重新显示控件和访问 Walmart
首页。但后续 ff-ruyijs 测试发现，**没有发送挑战输入，或挑战明确拒绝后，首页也能返回
HTTP 200 和正常标题**。因此参考浏览器打开首页、页面重载均未计为挑战通过。

目前没有足够证据把失败唯一归因于 IP、上游更新、某个指纹字段或事件时长。

## 诊断扫描会改变结果的地方

实际按钮位于 `#px-captcha` 的 closed Shadow DOM 内部 iframe。页面会给隐藏诱饵
iframe 的属性安装访问监测，也会包装真实 frame 的 DOM 查询、markup、style、class
和 aria 属性读取。早期扫描会触发这些监测，不能据此判断脚本加载失败。

检查器现在调用外层干净 realm 的原生 getter、查询和属性方法，跳过隐藏 iframe，
用真实节点对象保持输入目标。按住期间只读取该目标的连接状态及 aria 提示；收到完成
提示后释放，然后继续等待明确的重试提示。无障碍模式完成页面规定的两次点击。
离线回归证明扫描不会调用页面包装的查询和属性方法，也不会遍历隐藏诱饵文档。
输入回归另外核对真实 iframe 收到的可信事件序列：pointerdown/up 的 detail 为 0，
mousedown/up 和 click 为 1；pointerenter/mouseenter 不冒泡且不可取消。早期手动
派发的探针在这些字段上有偏差，后续已按 Firefox 行为校准。

额外诊断运行曾给公开脚本加只读状态观察，记录真假按钮、完成标记读取、未知脚本、
CSS 检查和 collector 命令名称；这些运行单独标记为 instrumented，不能作为成功证据。
其中两次隐藏 frame 属性读取的调用栈来自 PerimeterX 自己的采集脚本；CSS 标记对应
Walmart 真实设置的 margin，与完整 Firefox 的计算值一致，没有把这两项改成固定值。

Cookie `_pxvid`、`_px3`、`_pxde` 等的存在，collector HTTP 200、进度条完成以及主页
HTTP 200，都不是这个挑战被服务端接受的充分证据。工具保持 `verified: false`，并用
`outcome` 区分 `rejected`、`client_completed`、`unconfirmed` 和 `error`。

## 复现

使用已编译的对应内核：

```sh
cd /root/ruyi-captcha
.venv/bin/python -B tools/probe_perimeterx.py \
  --hold 30 --until-complete --wait 5 --timeout 60 \
  --output artifacts/perimeterx/repeat.json

.venv/bin/python -B tools/probe_perimeterx.py \
  --accessible --wait 5 --timeout 60 \
  --output artifacts/perimeterx/accessible-repeat.json
```

HTTP 代理可从环境变量读取，或使用一行一个 URL 的配置文件。该测试网关已确认同时支持
HTTP CONNECT，以下命令显式切换协议；不要对仅支持 SOCKS 的代理使用这个选项：

```sh
.venv/bin/python -B tools/probe_perimeterx.py \
  --proxy-file /root/ff-ruyijs/proxy.test --proxy-index 3 --proxy-as-http \
  --url https://www.walmart.com/blocked --hold 30 --until-complete \
  --wait 12 --timeout 75 --check-walmart \
  --output artifacts/perimeterx/proxy-repeat.json
```

`--check-walmart` 只记录同会话请求的状态、标题和是否返回 blocked；它不提升 verified。
生成器对照可先运行以下命令，再把 `--profile-file` 传给上述探针；增加
`--observe-server-result` 时会将结果标记为插桩诊断：

```sh
.venv/bin/ruyi-captcha profile --os linux --seed 42 --locale en-US \
  --timezone America/New_York --cpu 8 --output artifacts/perimeterx/generated-us.json
```

本轮脱敏汇总与原生二进制散列见
[profile follow-up](perimeterx-profile-followup-20261107.json)。
代理凭据不进入报告；URL 查询与用户信息、Cookie 值和 iframe token 属性均被移除。
详细本地证据在忽略目录 `artifacts/perimeterx/`，其中 `final-press.json`、
`final-accessible.json`、`socks-01.json`、`socks-03-direct.json` 和参考浏览器截图可复核
上述结果。运行时内核的发布锁仍单独维护，不因诊断修复自动宣称新 provider 可用。
