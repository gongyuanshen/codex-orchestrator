# 三模式 GUI、旧聊天刷新及 GitHub 推送验收 — 2026-11-17

**GUI/修复/旧聊天本轮加载：PASS。** 修复代码871cfe181bfb60d8c5a6f54696dbff05f664b014已推送并由远端ref核对一致。正式0.3.0/schema5服务仍为PID25084；只更新两个静态文件，未重启daemon、修改Python/角色/config或写入生产任务。

目标分支：[feature/route-a-mode-enhancements](https://github.com/gongyuanshen/codex-orchestrator/tree/feature/route-a-mode-enhancements)。保留当前开发分支，不合入main，不创建PR、标签或GitHub Release；默认GitHub安装仍以远端main版本为准。本摘要及既往安装摘要随本轮文档提交保存在同一开发分支，最终推送回执为本地git-push-readback.json。

## 已确认问题与根因修复

1. **工作区Diff竞态：**同工作区两次加载的旧成功/失败可覆盖新状态，甚至误示工作树干净；不同工作区共用loading也会串状态。复用现有请求路径，加入每工作区最新请求身份及独立在途状态，仍检查contextVersion。当前请求失败仍展示明确错误；try仅覆盖API，真实render异常直接暴露，不伪装成功或当成过期响应。
2. **后台回放未停止：**graph→日志且自动滚动开启走scroll分支而跳过stop。现在所有离开graph的路径先停止真实定时器，再执行所选视图行为。
3. **协作图键盘/可访问性：**SVG role=img屏蔽内部节点，节点只有鼠标处理。改为分组及有名称的可聚焦toggle按钮；Tab可达，Enter/Space复用原鼠标选择逻辑，Space阻止滚页，aria-pressed随选择更新，复用2px可见焦点。播放/暂停的可访问名称也同步真实状态。

代码：G:/工具/codex-orchestrator/plugin/src/orchestrator/web/app.js:118,1381,2099,2523,2619,3408；styles.css:170。行为回归：tests/test_dashboard_behavior.py:494起。没有新依赖、重设计、MCP或数据库架构变更。

## 真实测试与独立审查

| 证据 | 实际结果 |
| --- | --- |
| gui-regressions-red.log | 13 failed / 3 passed：原实现未满足新增行为断言，保留真实失败。 |
| gui-render-exception-red.log | 2 failed：自审确认render异常会被宽catch吞掉，追加RED后收窄catch。 |
| gui-regressions-green.log | 67 passed，包含原行为测试与18个新增参数化case。 |
| gui-fix-review-tests.log、gui-fix-review.md | 独立规格/质量PASS，独立67 passed in2.29s；语法/diff/机械检测通过；后追加现场部署核对PASS。 |
| full-strict.log | **440 passed in69.39s，exit0**；-W error，仓库外basetemp，无失败/跳过/警告汇总。 |

全套实际命令：

```powershell
Set-Location 'G:/工具/codex-orchestrator'
& .venv/Scripts/python.exe -m pytest -q -W error --basetemp (Join-Path $env:TEMP ('tri-gui-final-'+(Get-Date -Format 'yyyyMMdd-HHmmss')))
```

浏览器是真实IAB，不是HTTP文本或Node模拟浏览器截图。Node harness用于可控重排真实模块的网络Promise；它不是实体浏览器或生产执行成功证据。机械检测空结果不等于完整WCAG认证。

## GUI 实际操作范围

- 正式18790：Boost历史任务、Teamwork无任务空态、Swarm历史任务；三个入口模式隔离、项目/任务切换、实际实时连接。更新后再打开三入口，正式协作图12个节点均具备新button/tabindex。
- 隔离19894：复用上一轮真实原生流程留下的workflow2数据，不伪造生产任务。Boost Solo完成态及耗尽失败态/返工3/3、Teamwork parse/sum两个完成里程碑、Swarm label/parity两个完成步骤均实际显示。
- 三模式分别检查1440×900桌面及390×844窄屏，documentWidth等于viewportWidth，无页面横向溢出；中文/空格路径可读，长目标/日志折叠。
- 日志频道#verify与关键词组合筛选，实际只显示一条初始FAIL；展开完整原始验收内容。成员任务包/所有权信息、检查器、已关闭任务请求暂停/结束按钮禁用均实际核对。
- 实际浅/深色切换；实际从Teamwork导出JSON并下载，包含workflow/inbox及原run数据。没有发送人工消息或点击合入/清理/暂停/结束等副作用操作。
- 修复后实际Tab从末个图控件进入coordinator按钮；Enter选中、Space取消，aria-pressed变化，焦点solid2px。实际播放显示“暂停回放”，切日志立即恢复“回放协作流”。

本地gui-observations-final.json共22条混合历史观察/RED与最终GREEN记录，**不是22项均PASS**。修复前回放观察曾读取静态aria-label，单独不足以证明timer；RED依据真实模块断言，修复后再通过真实浏览器名称/交互验证。主题控件初次按checkbox查找无匹配，随后按实际BUTTON定位；viewport调整初次截屏重绘异常经reload消失，均未用产品兼容代码掩盖测试载体问题。最终浏览器console error/warn为空。

原始截图/DOM/下载文件仅保留本地证据目录，不上传生产历史数据。截图包括boost-desktop.jpg、teamwork-desktop.jpg、swarm-dark-desktop.jpg、swarm-keyboard-fixed.jpg及solo/sum/parity-narrow-fixed.jpg。它们是浏览器仿真视口，不是实体手机或穷举无障碍/触控验收。临时viewport已reset；19894两个自有服务session均Ctrl+C结束并释放端口，原数据和停止代理保留。

## 原有旧聊天刷新：实际验收边界

同一原根聊天 **01a1e565-8127-7781-a2ba-bcd4079b6fd6**，不是新child或新聊天，在本轮取得27项实际原生工具；workflow_step_advance包含artifact_id/recipient_agent_id；开发者技能目录已指向安装0.3.0。实际由tools[原生工具名]调用run_list及inbox_wait，各isError=false；读回8历史任务，关闭任务wait(0)返回run_closed。old-root-native.json保留完整实际目录和两次响应，独立审查对照原根会话执行记录核对，不以SDK替代。

首次证据保存因Windows命令过长206失败；仍保存同一次实际调用record，分块后读回成功，没有重放生产RPC。旧0.2验收时根聊天19工具的记录仍保留；本次27是新增证据，不改写历史。既有同聊天新turn刷新已验证，**不承诺任意进行中的旧turn/旧shim即时刷新**，也不把新版技能目录可见当作三种技能整套流程本轮重新执行。未重启桌面或换聊天来冒充热刷新，无需新增插件强刷宿主代码。

## 正式静态更新与数据保存

installed-web-update.json保存两个静态文件更新前后SHA和备份。CSS先更新再JS，各文件临时写入/fsync/原子替换；GET正式资源字节=源码=安装文件。没有声称两文件构成整体事务。current-installed-inventory.json现场核对33载荷文件均匹配当前源码，只有app.js/styles.css区别于上一轮revision3。

正式PID25084、started_at、0.3.0/schema5在更新和检查前后相同。production-baseline.json、installed-web-update.json及独立只读重算证实14张表全部哈希相同、8历史任务原样、无生产测试写入。静态备份：G:/工具/.test-backups/gui-hot-refresh-20261117/installed-web。

上一轮ZIP/wheel/source-hashes/completion-readback及原始FAIL保持历史证据不改；本次以新静态清单和报告补充，不把旧33清单冒充新GUI源码哈希。持续更新的计划文档由本轮新检查点说明，原计划快照已备份。

## GitHub 与文档

源码推送前全量测试/独立审查已通过；首次普通Git连接超时，配置的本机代理不可用，另一次直连读取被重置。小范围只读差分找到了实际可用的每命令Schannel/HTTP1.1路径；随后正常非force push及ls-remote成功，远端修复commit与本地871cfe181bfb60d8c5a6f54696dbff05f664b014一致。不把网络间歇失败归因于单个TLS/HTTP参数，不修改全局Git、代理或系统配置。

同步README当前0.3/workflow2及真实验证边界；补入前轮两个原未跟踪安装摘要，修复其远端文档链接。主计划、canonical/staged台账追加本次GUI/旧聊天/推送检查点。原始日志、DB、备份、截图和会话不纳入Git；没有公开Release，因此不生成发布说明。

剩余边界：未穷举平台/负载/完整无障碍、真实手机触控或副作用GUI操作；这些不被实际GUI读取验收替代。已停止的/root/mode_native_author_a保持interrupted，不自动恢复。
