# FreeCAD 聊天工具调用诊断与验证

验证时间：2026-09-28。环境：Windows、FreeCAD 1.2.0、本仓库工作区。

结论：用户给出的四步 Smoke Box / Smoke Cylinder 指令可以执行。失败涉及 SSH、当前 Gateway 的工具调用链路以及本地 Agent 的实现问题，不能靠改写用户 prompt 解决。修复本地代码并通过 SSH 直连同一台服务器上的 Qwen 模型接口后，原始中文指令已经在真实 FreeCAD GUI 进程中完成。

## 分层测试结果

| 层次 | 实测结果 |
| --- | --- |
| SSH | 当前使用 Windows OpenSSH 的 BatchMode 免密连接成功。日志中的早期 Permission denied 发生在模型请求之前。 |
| 当前 Gateway | OpenClaw 2026.9.6，`openclaw/default`。提供一个无副作用的客户端探测函数，`tool_choice: auto` 返回文字但没有工具调用；指定函数调用则返回 SSE `api_error`，说明未产生所要求的函数调用。 |
| 底层模型 | 同一部署的 Ollama / Qwen3.6-35B-A3B，直接调用 `/api/chat` 和 `/v1/chat/completions` 均产生正确的函数调用。项目自己的 SSH + 流式 HTTP 客户端也通过独立测试。 |
| 本地 Agent | 在真实 GUI 中复现了工具执行期间 Qt 事件重入导致续轮遗漏的问题；修复后强制该事件顺序的回归测试通过。 |
| 对象识别 | 真实模型曾传入标签 `Smoke Box`，旧代码仅识别内部名称 `AgentBox`，移动失败。现在接受唯一、精确匹配的标签；重名时返回可用内部名称并拒绝模糊移动。 |
| 装配、运动仿真 | 两套曲柄滑块尺寸及运动参数的原生测试通过。它们尚未注册到聊天面板的工具列表。 |

这里的 Gateway 结论只针对当前部署和模型路由。官方 [Chat tool contract](https://github.com/openclaw/openclaw/blob/main/docs/gateway/openai-http-api.md#chat-tool-contract) 定义了客户端函数工具及结果续轮协议；本次没有将当前故障推广为所有 OpenClaw 版本都不支持该协议，也没有修改服务器配置或源码。

## 原始四步指令的真实模型测试

使用用户提供的完整中文 prompt，通过真实 SSH、Qwen 模型和本地 FreeCAD AgentDockWidget 执行。该项没有使用模拟模型响应。

| 步骤 | 实测结果 |
| --- | --- |
| 创建 Smoke Box | 长 20 mm、宽 10 mm、高 5 mm，体积 1000 mm³。 |
| 创建 Smoke Cylinder | 半径 4 mm、高 12 mm，底面圆心在 (30, 0, 0) mm，体积校验通过。 |
| 移动和旋转 | 长方体 Placement.Base = (2, 3, 4) mm；Rotation 等价于绕 Z 轴 30°。 |
| 视图和回复 | `fit_view` 执行成功，调用轴测视图和 fitAll；模型接收全部四个成功结果后给出逐项中文回复，面板恢复可操作。 |

测试使用独立 FreeCAD 进程和新文档。用户原有的 FreeCAD 进程保留。测试运行记录的模型/操作耗时为 19.276 秒。

- [真实模型调用及完整结果](../outputs/agent_diagnosis/live_agent_report.json)
- [生成的 FreeCAD 模型](../outputs/agent_diagnosis/live_smoke.FCStd)
- [实际 FreeCAD 视口截图](../outputs/agent_diagnosis/live_smoke.png)
- [标签兼容修复前的失败记录](../outputs/agent_diagnosis/live_agent_before_label_fix.json)
- [Gateway 探测结果](../outputs/agent_diagnosis/gateway_probe.jsonl)
- [底层模型探测结果](../outputs/agent_diagnosis/provider_probe.jsonl)

“圆柱中心”存在坐标语义上的小歧义：当前工具使用底面圆心，而不是实体的几何中心。若需要几何中心在 (30, 0, 0)，高度 12 mm 且沿 +Z 的圆柱应将底面圆心设为 (30, 0, -6)。这不是此次没有调用工具的原因。

## 本地修复与回归

- `panel.py`：等待回复处理和网络线程结束两者均完成，再发送工具结果续轮；CAD 操作期间保持 Send/Clear 禁用。显示每个工具结果，文字回复明确标识未执行 FreeCAD 操作。
- `tools.py`：位置工具接受内部名称或唯一标签；明确圆柱坐标、旋转轴和轴测视图工具的描述。
- `session.py`：说明使用已提供工具及创建结果中的内部名称，要求修正可恢复的工具错误，避免虚报不支持的操作。
- `test_agent_gui.py`：加入真实 GUI、多轮 SSE、事件重入、纯文字、流式错误、唯一/歧义标签的回归覆盖。

最终 `pixi run --locked test-native`：**14 项通过，0 失败，0 错误，0 跳过**。包含曲柄滑块零件生成、装配、重开文档及两组原生运动仿真。

[最终原生测试报告](../.pixi/native-test-runs/1f5408660d4c4921beb9d2a58f96b93a/result.json)

普通 Python 测试有 9 项通过，5 项因需要 FreeCAD GUI 而跳过；这些 GUI 测试均已在上面的原生测试中实际执行。`git diff --check` 通过。系统 Python 未安装 Ruff，因此未执行 Ruff 检查。

## 现在可用的面板配置

重启 FreeCAD 以加载本地修复，然后在 KineSketch 聊天面板填写：

| 字段 | 值 |
| --- | --- |
| Endpoint | `http://127.0.0.1:11434/v1` |
| Agent | `modelscope.cn/unsloth/Qwen3.6-35B-A3B-GGUF:Q4_K_M` |
| Access token | 本次验证的远程 Ollama 服务不要求 token，可留空。 |
| SSH key tunnel | 保持开启，使用已有服务器、端口和用户名。 |

此处的 `127.0.0.1` 通过 SSH 指向服务器本机。默认 Gateway 端口 18789 与这里的模型端口 11434 是不同服务。原有面板配置仍指向 Gateway，测试使用了隔离的设置文件。

基础建模已实测可用。装配和运动仿真目前通过已有 Python 接口或 `pixi run demo-native` 运行；要让聊天调用它们，还需单独接入工具定义、输入清单和结果展示。目前支持的机构是符合接口清单的曲柄滑块，不是任意长方体与圆柱的自动装配或任意动力学仿真。

## 2026-09-29 GUI 复测

用户报告手工测试失败，但当时的面板错误文本不可得。本次使用原始中文提示词在新的 FreeCAD 1.2.0 GUI 进程中重新执行，并让自动诊断保留窗口。第一次重跑时，诊断脚本的 Qt 定时器未被窗口持有，收不到最终状态；这属于测试脚本的生命周期问题。将定时器和回调固定在 FreeCAD 主窗口后，GUI 内四个工具调用全部返回 `ok: true`，两个实体的尺寸、体积、位置和旋转断言通过，模型回复逐项给出结果。通过桌面应用列表确认了标题为 `* visible_gui_demo - FreeCAD 1.2.0dev` 的窗口。窗口内容抓取所需的 Computer Use 应用授权超时，因此窗口内文本以 FreeCAD 面板自身写出的报告为准，视图以 `activeView.saveImage` 导出的实际截图为准。不能据此断言用户原先失败的具体原因。

- [本次 GUI 面板报告](../outputs/agent_diagnosis/visible_gui_demo_report.json)
- [本次 GUI 模型](../outputs/agent_diagnosis/visible_gui_demo.FCStd)
- [本次 GUI 视口截图](../outputs/agent_diagnosis/visible_gui_demo.png)

同时重新运行 `pixi run --locked demo-native`。原生 Assembly 求解返回码为 0；运动生成返回码为 0，得到 401 个采样点和 402 个原生帧；FreeCAD GUI 视口采集 120 帧，编码的 H.264 MP4 报告 `status: success`。已目视比较两张不同时间的实际 FreeCAD 视口帧，滑块与曲柄位置不同。这条 demo 仍是通过 FreeCAD GUI Python 进程运行的脚本，和聊天面板可用的四个基础建模工具是两个入口。

- [完整装配与运动 demo 报告](../outputs/demo_native_7d2412884e0f4fb88a960ed8fa795d43/demo_result.json)
- [装配模型](../outputs/demo_native_7d2412884e0f4fb88a960ed8fa795d43/assembly/assembly.FCStd)
- [运动视频](../outputs/demo_native_7d2412884e0f4fb88a960ed8fa795d43/motion/freecad_native_3d.mp4)
