<h1 align="center" style="color: #fb8aaa">ViewIndexTTS</h1>

<p align="center">
  IndexTTS-2 模型的可视化桌面客户端<br>
  由 <b><a src="https://github.com/index-tts/index-tts">IndexTTS-2</a></b> 提供中英文高质量语音合成，支持情感控制与自定义音色
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/GUI-Flet_0.85-6366f1?logo=flet&logoColor=white" alt="Flet">
  <img src="https://img.shields.io/badge/License-MIT-6366f1" alt="MIT">
</p>

---

## 简介

ViewIndexTTS 是 [IndexTTS-2](https://github.com/IndexTeam/IndexTTS) 模型的可视化桌面客户端，通过 [AstraFlow](https://astraflow.ucloud.cn/) 官方托管 API 提供语音合成服务。无需配置本地模型环境，输入文本即可生成自然流畅的语音。

支持 **9 种内置音色**、**5 种情感控制模式**（8 维情感向量 / 文本描述 / 音频参考 / 随机化 / 🤖 AI 智能分析）、**自定义音色克隆**，以及语速、音量、采样率等精细调节。

---

## 功能

### 核心合成
- 🎙️ **9 种内置音色** — Jack Cheng、Crystla Liu、Stephen Chow、小岳岳、小说朗读等
- 📝 **长文本合成** — 支持多行文本输入，自动分句处理
- 🎛️ **语速控制** — 0.25× ~ 4.0× 可调
- 🔊 **音量增益** — 0 ~ 10× 增益系数
- 🎵 **采样率选择** — 16kHz / 22.05kHz / 24kHz / 44.1kHz

### 情感控制
- 🤖 **AI 智能分析** — 调用 LLM 自动分析合成文本的情感，一键生成情感向量与强度
- 😊 **情感文本** — 用自然语言描述情感（如「愉快」「悲伤」「激动」）
- 📐 **8 维情感向量** — 独立调节快乐、愤怒、悲伤、恐惧、厌恶、抑郁、惊讶、平静
- 🎧 **情感音频参考** — 上传音频文件作为情感模板
- 🎲 **随机情感** — 为合成引入自然的情绪变化

### 音色管理
- 📤 **自定义音色上传** — 上传 5–30 秒参考音频即可克隆音色
- 📋 **音色列表** — 查看、刷新所有已上传的自定义音色
- 🗑️ **音色删除** — 支持删除不再需要的音色

### 其他
- 🌓 **深色/浅色模式** — Material 3 主题切换
- 📂 **本地输出管理** — 合成结果保存至本地，文件列表一键播放
- 🔑 **API 配置** — 统一管理 TTS 与 LLM 的 API Key、模型和服务商地址
- 🧠 **多服务商 LLM 支持** — 支持 OpenAI、AstraFlow、DeepSeek 等任意 OpenAI 兼容接口

---

## 快速开始

### 环境要求
- Python **3.12+**
- Windows / macOS / Linux

### 安装

```powershell
# 1. 克隆仓库
git clone https://github.com/Q-wind520/ViewIndexTTS.git
cd ViewIndexTTS

# 2. 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate

# 3. 安装依赖
pip install flet httpx playsound3 python-dotenv
```

### 配置 API Key

1. 前往 [AstraFlow 官网](https://astraflow.ucloud.cn/) 注册并获取 API Key
2. 在项目根目录创建 `.env` 文件：

```env
MODELVERSE_API_KEY=your_api_key_here
```

或在应用内通过「设置 → API 配置」对话框直接输入。

### 运行

```powershell
python main.py
```

---

## 开源协议

[MIT License](https://opensource.org/licenses/MIT)

Copyright © 2026 ViewIndexTTS Contributors

特此授予任何人免费获取本软件及相关文档文件副本的权利，不受限制地处理本软件，包括但不限于使用、复制、修改、合并、发布、分发、再许可和/或出售本软件的副本，并允许获得本软件的人这样做，但须满足以下条件：

上述版权声明和本许可声明应包含在本软件的所有副本或实质部分中。

本软件按「原样」提供，不提供任何形式的明示或暗示担保，包括但不限于适销性、特定用途适用性和非侵权的担保。
