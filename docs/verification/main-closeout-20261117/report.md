# Main integration and accessibility closeout — 2026-11-17

## 交付结论

本轮补齐三模式 GUI 工程无障碍检查，根因修复后完成主分支集成。源码提交为 `1e4fc5766b9ef9f329364121927b6f39f1bc9c62`；该提交由已有开发分支快进合入 `main`，没有重写历史、创建公开 Release 或修改生产任务。最终 GitHub ref 读回记录保留在本地收尾证据中。

此前“未合入 main”只描述当时保留开发分支的状态；“完整无障碍未验证”表示还未执行本轮工程检查清单，并非三模式功能尚未实现。此前报告与失败证据保留，以下为追加验收，不回填历史成功。

## 根因修复

- 主视图页签采用 roving tabindex，支持方向键、Home/End 及程序导航焦点；频道为具名 group 中的原生筛选按钮，按实际筛选状态设置 aria-pressed，不再冒用嵌套 tab 语义。
- 成员列表保持 list/listitem 与原生 button 分层；成员、概况和协作图收到真实 SSE 后重绘，恢复同一逻辑控件的焦点。目标退役进入关闭 details 时，按原生 details/summary 语义转回对应页签，不自动展开、不依赖误导性的布局矩形。
- 窄屏检查器是有效的 DIV dialog：背景 inert、入口焦点、Tab 环、Escape 和返回入口。桌面使用 complementary 非模态区域，调整宽度时同步解除模态限制。标题可聚焦，控件被移除时焦点保持在检查器内。
- 导出菜单支持键盘打开、移动、关闭；实际选择下载后也返回触发按钮，不把关闭菜单误当成无须恢复焦点。
- 健康/SSE 状态采用 polite live region，内容不变时不反复替换播报。协作图仅降低形状/连线透明度，不降低文字对比度；reduced-motion 禁用装饰动画及平滑跳转，跳转后的消息可聚焦。
- 去掉浅色/深色选中控件上导致文字不足的父级/文字 opacity。正式历史任务额外暴露浅色已合入徽标 4.4866:1 对比度；共享浅色 cyan 前景由 #0e7490 调整为 #0c6b85，实际合成对比度提升到 5.0863:1，深色与布局不变。

复用现有渲染、事件与 CSS token；未新增运行依赖、插件工具或数据库迁移，没有隐式吞错或伪造成功路径。

## 自动化与独立审查

| 检查 | 实际结果 |
| --- | --- |
| 初始行为 RED、live focus RED、modal RED、closed-details RED | 真实失败均保留；先验证失败再修改。 |
| 独立规格/质量复核 | 最终关联 **107 passed**，语法与 diff 检查通过；保留此前真实 FAIL 和返工记录。 |
| 最终开发分支全量 | **480 passed in 73.47s**，-W error，仓库外 basetemp。 |
| 快进后 main 全量 | **480 passed in 73.03s**，-W error，仓库外 basetemp。 |
| 最终 CSS 真实浏览器 axe 4.14.0 | **48 场景、0 violations**；3 模式 × 4 主视图 × 2 主题 × 2 视口（1280×900、390×844）。逐项记录真实主题/宽度，无页面横向溢出。 |
| 最终 CSS 计算样式人工对比度检查 | **24 场景、3428 文本样本、0 失败**；包含 alpha 合成和 SVG 标签背景，不以规则 incomplete 充当通过。 |
| 正式历史任务窄屏模态复测 | 实际成员按钮打开 DIV/dialog，入口焦点正确，axe **0 violations**；原徽标失败另存。 |

可复现全量命令（临时目录必须在仓库外）：

```powershell
Set-Location 'C:/workspace/codex-orchestrator'
& .venv/Scripts/python.exe -m pytest -q -W error --basetemp (Join-Path $env:TEMP ('tri-main-'+(Get-Date -Format 'yyyyMMdd-HHmmss')))
node --check plugin/src/orchestrator/web/app.js
git diff --check
```

原始本地证据：`full-strict-feature-final4.log`、`full-strict-main.log`、`a11y-fix-review.md`、`production-cyan-contrast-red.log`/`green.log`、`axe-final-css-matrix.json`、`manual-contrast-final-css.json`、`production-gui-readback.json`。原始 JSON、数据库、截图、环境路径和运行任务正文不提交公开仓库。

axe incomplete 保留人工核对结论：导出按钮 aria-controls 目标真实存在、打开后可见且为 menu/两个 menuitem；实际 Markdown 下载及焦点返回通过。数字、装饰字符及截断/遮挡内容的自动色彩不确定项由计算样式与实际交互交叉检查。这里是已执行的工程清单，不是屏幕阅读器或所有辅助设备的正式 WCAG 认证。

## 实际键盘和实时刷新

11 项保存的交互记录覆盖页签、菜单、实际 23,445 字节 Markdown 下载、窄屏模态、桌面解除焦点环、概况导航、reduced-motion 及真实 SSE。真实 board_message 事件使图消息数 41→42、概况日志按钮 42→43，并保留逻辑焦点；真实 worker_updated 替换原 DOM 节点后仍保持同一成员焦点。退役成员进入关闭 details 时回到概况页签，details 仍关闭，保留了此前失败反例。

这几次测试写入只发生在一致性复制的隔离数据库。UI 专用成员没有原生 agent/workspace/业务步骤；测试 run 最后归档，未标业务 completed。隔离服务已停止并释放 19894；原始数据、备份和停止代理保留，不唤醒已停止任务。

## 旧聊天刷新：已完成的合同与边界

上一批已在原根聊天同 ID 的新一轮真实加载 **27 工具并执行 2 次原生只读调用**，不是另开聊天或用 SDK 替代。本轮又从正式安装包只读构造能力：工具数 27，普通与 modern 初始化均为 `tools.listChanged=false`。现有 shim 返回固定工具目录，未实现目录变更通知链。

因此本项目的已验收合同是**更新后同一旧聊天的下一轮加载**，不是强制替换一个正在执行中的模型轮次。官方 `config/mcpServer/reload` 排队刷新已加载线程，技能目录重新扫描也不等于活动 turn 的模型上下文已重建，见 [OpenAI App Server](https://learn.chatgpt.com/docs/app-server)。未启用实验全局开关、注入宿主或增加无法证明生效的“强刷”代码；这不是原计划遗留功能。对任意 in-flight 自动刷新不报 PASS，也不据此断言未来宿主永不支持。

## 正式安装、数据与集成

仅备份并逐文件临时写入/fsync/原子替换三个静态文件；最后 cyan 修正只更新 CSS。各文件原子，不声称三个文件整体事务。服务仍为原 PID/starttime、0.3.0/schema5；14 张表全量哈希和 8 个历史任务保持不变。没有后端重启、Python/角色/config 修改或生产测试写入。

33 个安装载荷与已测试候选的字节核对一致；快进后再次核对 main Git 规范化内容一致。Git checkout 将其中 19 个本机文本变为 CRLF，仅行尾差异单独记录，没有为迎合哈希重写安装版 Python。正式 HTTP 静态资源读回与安装文件一致。

本地主分支全量测试后采用普通非 force 推送；最终远端 main SHA 与本地 HEAD 核对。开发分支保留为历史，不清理用户工作区、不创建 PR/标签/Release。README、两份实施计划和外部计划同步本检查点；先前“未合 main / 未做 GUI”均为历史状态。

## 验收边界

三模式 GUI、本轮工程无障碍清单、旧聊天下一轮加载及主分支集成的工作已收尾。没有扩展新功能；实体手机触控、各屏幕阅读器组合和任意活动 turn 目录强制替换不纳入已完成声明。
