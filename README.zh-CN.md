<div align="center">
  <h1>Draw Agent Studio</h1>
  <p>用对话画图，也能接着改。</p>
  <p><strong>简体中文</strong> · <a href="README.md">English</a></p>
</div>

## 演示

**通过对话画出海边灯塔**

https://github.com/user-attachments/assets/7714f650-9fae-4703-bc8d-e0e7c9602668

**通过对话绘制科研流程图**

https://github.com/user-attachments/assets/d940e09e-315d-4c90-b445-78f5c335a306

<details>
<summary>AI 生成草图，手动完成向日葵窗台</summary>

https://github.com/user-attachments/assets/a49c6592-7d0c-42b2-9ead-c513bdab30bf

</details>

## 可以做什么

- **描述画面**：用文字创建草图、插画和流程图。
- **接着修改**：通过对话移动对象、调整颜色、删除、撤销和重做。
- **直接动手**：在画布上选择、拖动、使用画笔或添加文字。
- **保存作品**：自动保存画布，支持 PNG、JPG、WebP、SVG 和可继续编辑的 Excalidraw 文件。

## 快速启动

需要 **Python 3.11+** 和 **Node.js 20.19+**。Windows 还需要安装 Chrome。

### 1. 安装并启动

```bash
git clone https://github.com/bugkeep/ai-draw.git
cd ai-draw
python -m pip install -r requirements.txt
cd frontend-app
npm ci
npm run build
npx playwright install --with-deps chromium
cd ..
python run.py
```

以后在项目目录运行 `python run.py` 即可启动。

### 2. 连接模型

打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)，点击右上角 **设置**。

1. 选择模型厂商，填写模型名称、API 地址和 API Key。
2. 使用支持 **图片输入和工具调用** 的模型，点击 **测试连接**。
3. 测试通过后 **保存配置**，返回画布开始绘图。

可选择 OpenAI、Claude、Gemini、Ark 等厂商，也可接入本地服务或自定义兼容接口。模型服务按所选厂商计费。[模型配置说明](docs/model-settings.md)

## 试着这样说

- “画一张多模态 RAG 科研流程图，按数据准备、检索与生成、评估与产出分组。”
- “在评估部分加入质量判断和反馈循环。”
- “画一幅月光下的海边灯塔，加上小帆船和星星。”
- “把海面改成更深的蓝绿色，让灯光更亮一些。”

## 反馈与参与

欢迎在 [Issues](https://github.com/bugkeep/ai-draw/issues) 分享作品、提出想法或反馈问题。

[部署说明](docs/deployment.md) · [贡献指南](CONTRIBUTING.zh-CN.md) · [开发文档](docs/development.md) · [MIT License](LICENSE)
