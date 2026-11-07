# 2026-11-07：NopeCHA PerimeterX 示例实测

本轮尚未通过验证。已运行真实页面、定位挑战并发送可信按住事件；没有将 PerimeterX
注册为 SDK/API 支持的验证码类型。

`https://nopecha.com/captcha/perimeterx` 自身只是包装页，嵌入
`https://www.walmart.com/blocked`。后者使用 HUMAN/PerimeterX 的 Press & Hold 挑战，
appId 为 `PXu6b0qd2S`。它与 Turnstile 的 sitekey/token 回调流程不同。

## 实际执行

| 运行时与路径 | 可信输入 | 结果 |
| --- | --- | --- |
| 当前精简内核，NopeCHA iframe | 按住约 6 秒 | 显示 Please try again |
| 当前精简内核，校准事件与屏幕坐标 | 按住约 8 秒 | 显示 Please try again |
| 旧完整 fork，同一新 helper | 按住约 8 秒 | 显示 Please try again |
| 当前精简内核，直接打开 Walmart /blocked | 按住约 10 秒，保留目标对象并设置焦点 | 30 秒内未确认通过，仍在挑战页面 |

首个 6 秒尝试是初步输入序列；8 秒尝试按 Firefox 的 pointer/mouse 顺序、pointerId、
screen/client 坐标和按钮状态校准。按住期间持续运行事件循环，释放后继续观察页面。
直接页面尝试进一步保留同一个 DOM 目标对象，避免动态节点索引变化影响释放事件。

页面、挑战脚本、PerimeterX collector 多次返回 HTTP 200，过程中未捕获到 JSError。
`_pxvid`、`_pxde`、`_px3` 等 Cookie 名称可观察到，但生成 Cookie 或采集 POST 返回
200 都不是挑战被接受的证据。本轮没有确认保护解除或业务后台接受。

## 宿主检查的发现

实际控件位于 closed Shadow DOM 的内部 iframe。页面对部分 iframe 实例的公开属性
访问做了封装：直接读取 `localName`、`contentDocument` 时，宿主检查看不到实际控件。
通过最外层干净 realm 的原生 Element/HTMLIFrameElement getter 调用，可以读到真实
节点并定位 `aria-label="Press & Hold Human Challenge"` 的可见按钮。

因此，最初“没有找到按钮”的状态不是脚本没有加载。检查器修正后已能执行实际输入。
本轮两种内核都没有通过，不能将拒绝唯一归因到上游更新、精简补丁或某个指纹字段；
网络出口、环境一致性和事件行为仍是待独立排查因素。

## 复现

```sh
cd /root/ruyi-captcha
.venv/bin/python -B tools/probe_perimeterx.py \
  --hold 8 --timeout 25 --output artifacts/perimeterx/repeat.json

.venv/bin/python -B tools/probe_perimeterx.py \
  --url https://www.walmart.com/blocked --hold 10 --timeout 30 \
  --output artifacts/perimeterx/direct-repeat.json
```

工具是诊断探针，不返回可供调用方消费的 PerimeterX 解决结果。
报告记录 DOM 摘要、输入时间、脚本错误及请求状态，移除 URL 查询、Cookie 值和
挑战 iframe 的 token 属性值。详细本地证据在忽略目录 `artifacts/perimeterx/`。
当前内核为 `b9877c4`，旧 fork 二进制来自 `158eae7`；本轮未修改内核源码。
