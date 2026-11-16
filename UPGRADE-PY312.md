# Python 3.12 与 LangChain 1.x 升级记录

升级日期为 2026-11-16（北京时间）。原目录为 `D:\Projects\pylangchain`，新版位于 `D:\Projects\pylangchain-py312`。以本地工作区为源，保留 `b0c22a6` 及此前的三个 Git 提交和独立的 Git 元数据。副本未复制 Visual Studio 缓存、字节码、虚拟环境和常见密钥文件。旧历史中已经跟踪的缓存文件在副本里显示为待删除；历史保持完整。未提交、未推送。

## 环境和依赖

| 组件 | 原环境 | 新环境 |
| --- | --- | --- |
| CPython（Windows x64） | 3.10.18，Miniconda base | 3.12.10，Python.org 当前用户安装 |
| Flask / Werkzeug | 3.1.1 / 3.1.3 | 3.1.3 / 3.1.9 |
| LangChain / Core | 0.3.27 / 0.3.78 | 1.5.0 / 1.8.1 |
| LangChain Community | 0.3.30 | 0.4.2 |
| LangChain DeepSeek / OpenAI | 0.1.3 / 0.3.27 | 1.2.0 / 1.8.0 |
| LangGraph | 原运行环境未安装 | 1.2.22 |
| OpenAI SDK | 1.95.1 | 3.51.0 |
| Requests / Pillow / Pydantic | 2.32.5 / 12.3.0 / 2.13.4 | 2.34.2 / 13.0.0 / 2.14.3 |

使用 Python.org 官方 Windows x64 安装包，安装前验证了 Python Software Foundation Authenticode 签名。解释器以当前用户身份安装在项目外。官方独立 Launcher MSI 验证签名后仅加入 Launcher 到用户 PATH。系统 PATH、原 Python 默认解析与两个 Conda 环境均保持原状。`D:\Projects\pylangchain-py312\.venv\Scripts\python.exe` 是新版解释器入口。3.12.10 是该系列最后一版附官方 Windows 安装包的版本，之后的安全补丁以源码形式分发。[Python 3.12.10](https://www.python.org/downloads/release/python-31210/) · [Python 3.12 安全版本说明](https://www.python.org/downloads/release/python-31215/)

| 当前用户环境验收 | 实测结果 |
| --- | --- |
| 独立 Python 3.12.10 解释器 | `C:\Users\willi\AppData\Local\Programs\Python\Python312\python.exe` |
| Windows Python Launcher | `C:\Users\willi\AppData\Local\Programs\Python\Launcher\py.exe`，通过当前用户 PATH 解析 |
| `py -3.12 --version` | 在 `D:\Projects`、用户临时目录和新版目录均返回 `Python 3.12.10` |
| `py -3.12 -c "import sys; print(sys.executable)"` | 上述三个目录均返回项目外的独立 Python 3.12.10 解释器 |
| `python --version` | 继续解析 `C:\miniconda3\python.exe`，返回 `Python 3.10.18` |
| Conda `llm_env` | `C:\miniconda3\envs\llm_env\python.exe`，返回 `Python 3.10.18` |
| 项目虚拟环境 | `D:\Projects\pylangchain-py312\.venv\Scripts\python.exe`，基解释器为上文的当前用户 3.12.10 |
| 依赖隔离 | Flask 等应用依赖从项目 `.venv\Lib\site-packages` 导入，项目外的 3.12 安装仍只含自带 pip |

已打开的终端若仍沿用启动时的环境变量，重开终端后即可使用 `py -3.12`。

原 `requirements.txt` 是整套开发环境快照，含本机 Conda 构建路径。新 `requirements.in` 从实际导入的依赖和离线测试依赖出发，`requirements.txt` 固定 Windows x64、Python 3.12 的 69 个包及哈希。Community 仍提供 FileManagementToolkit，其间接安装的 `langchain-classic` 和旧版 Agent 执行机制彼此独立。第三方 API 仍使用原供应商和模型配置。

## 代码兼容性

- `main.py` 使用 `create_agent` 和 DeepSeek 原生函数调用，替换 `initialize_agent`、文本式 ReAct 和 `.run()`。工具类型 `StructuredTool` 改由 `langchain_core.tools` 导入，以适配 LangChain 1.x 的导出接口。按消息状态取最终回复，同时支持删除申请工具的直接返回。每步只调度模型给出的第一个有效工具调用，再由模型读取结果并继续。Graph 递归有上限。代码将工具结果留在工具消息里，保持联网开关和前端删除确认。
- `deepseek_extensions.py` 在 Chat Completions 请求中保留 DeepSeek 的 `file_id` 内容块，并在工具多轮中回传上游提供的 `reasoning_content`。角色文件和本地记忆仍作为当前请求上下文，浏览器历史仍由原界面读写。[LangChain v1 迁移指南](https://docs.langchain.com/oss/python/migrate/langchain-v1) · [DeepSeek Tool Calls](https://api-docs.deepseek.com/zh-cn/guides/tool_calls/) · [DeepSeek 思考模式](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode/)
- `main.py` 对自定义脚本和音频路径解析真实目标后检查实际目录边界，继续使用原扩展名白名单和脚本超时。默认关闭 Flask 调试器与热重载，保留回环地址和启动时打开浏览器的行为。模型异常通过简化提示返回，避免展示上游请求详情。
- `requirements.in`、`requirements.txt`、`.gitignore` 和 `.vscode/settings.json` 分别维护依赖、忽略本地文件、提示编辑器选择项目解释器。`tests/test_migration.py` 增加可重复执行的离线验收用例。`README.md` 与本文更新部署和维护说明。
- `main.html`、三个前端 JS、CSS、Pollo 价格数据均与原目录逐字节一致。图片、视频、语音、DeepSeek REST 路由继续使用原表单字段和响应结构。应用的业务数据无需转换。

## 测试与待核对

- 依赖按哈希校验安装，`pip check` 通过，最终锁文件可从已核对的 wheel 重新解析安装。OSV 查询覆盖 69 个锁定包，未返回匹配的公开公告。
- Python 语法、模块导入和 JavaScript 语法检查通过。独立 Flask 进程在 `127.0.0.1:5011` 启动并返回首页及 CSS 的 HTTP 200。
- 12 项离线自动化测试通过。在真实 Agent 与 SDK 上用 Mock 传输验证多步工具顺序、工具调用上限、DeepSeek 附件与推理内容回传、本地路径和 Junction 越界拦截、记忆和角色、余额、云文件分页与删除确认、联网搜索与抓取、三家图片路径、两家视频路径、音色训练和 TTS。脚本执行使用 Mock，核对调用命令、超时与路径校验。
- Playwright CLI 使用真实 Chrome 测试，覆盖旧浏览器历史读取、设置保存、余额、上传图片并发送、删除确认与取消、视频估价切换，以及手机宽度下布局；未出现未捕获页面异常。
- 原仓库随附源码的隔离副本用 3.10 对照运行。21 项视频估价输入与设置保存、静态资源及缺少媒体路径的响应共 24 项一致。原项目 121 个文件（含 Git 元数据）的路径、大小、SHA-256 与最后写入时间均无变化，原 Git 工作区仍干净，HEAD 为 `b0c22a6`。
- 本轮未使用真实 API Key。仍待线上验证模型自然语言工具选择、真实生成、供应商在线响应与计费；麦克风识别和用户本机脚本的真实执行也未验证。无已知阻塞本地启动的升级问题。

启动和测试命令见 [README](README.md)。

## 数据、安全与回退

- 浏览器历史沿用 `lingsi_sessions_v2`。使用相同浏览器和相同地址时沿用原存储，更换端口会使用另一份存储。附件授权和待确认令牌随服务进程重建而失效。
- 记忆和媒体仍位于现有配置路径。两个版本配置同一外部目录时会共享数据。测试使用独立临时目录。回退时结束新版服务，从原目录以原有 3.10 环境启动即可，无需逆向转换数据。
- 本地脚本按 Flask 进程权限运行，程序仍采用原 PowerShell 调用方式。目录边界检查适用于脚本路径，脚本自身的读写权限由 Windows 管理。已有的普通表单回填密钥、媒体目录白名单、缺少账户认证等行为依然属于单机应用边界。当前保持仅本机监听。
- 后续升级依赖时可用 `uv pip compile --python-version 3.12 --python-platform x86_64-pc-windows-msvc --only-binary :all: --generate-hashes requirements.in -o requirements.txt` 生成候选锁文件，再核对包大小、变更和测试。若需要官方预编译且仍持续提供二进制安全更新的 Python，下一次升级可评估较新的 Python 主次版本。
