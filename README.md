# AI Assistant — 本地多模态智能助手

基于 Python Flask 和 LangChain 开发的本地 AI 助手系统，通过外部 AI API 实现多模态交互及本地工具调用。

## 主要功能

- DeepSeek 智能问答
- 秘塔联网搜索与网页读取
- 本地记忆与角色设定
- LangChain 本地文件管理及脚本执行
- 浏览器语音识别
- 豆包音色训练与 TTS 语音合成
- 火山方舟图片生成
- Pollo 视频生成
- 浏览器聊天记录管理

## 技术栈

- 后端：Python、Flask
- AI 框架：LangChain
- 前端：HTML、CSS、JavaScript、Jinja2
- 模型服务：DeepSeek、火山方舟、Pollo 等

## 安装依赖

```bash
python -m pip install -r requirements.txt
```

## 启动项目

```bash
python app.py
```

默认访问：

http://127.0.0.1:5000

注意：如果实际入口不是 `app.py`，请替换为对应文件名。

## 使用说明

根据使用的功能配置对应 API Key。

部分功能依赖互联网连接及第三方 API 服务，API 调用可能产生费用。

本地文件操作功能具有较高系统权限风险，请仅在可信环境中使用，并限制允许操作的目录。

## 项目说明

本项目在本地计算机上运行 Flask 服务，但 DeepSeek 问答、联网检索及部分多模态生成依赖外部云端 API，并非完全离线推理系统。

本项目主要用于学习、研究与个人实验。