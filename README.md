# Draw Agent Studio

**用对话画图，也能接着改。**

描述你的想法，看着画面逐笔出现；接着说“移到右边”“改成蓝色”，在同一张画布上继续完成作品。

An open-source AI drawing app. Draw with words, refine through conversation, and export editable artwork.

[![纯语言绘制科研流程图：多模态 RAG 科研工作流](docs/assets/research-preview.gif)](docs/assets/research-demo.mp4?raw=true)

[科研流程图 · 2K 原片](docs/assets/research-demo.mp4?raw=true) · [纯语言绘画 · 2K 原片](docs/assets/language-demo.mp4?raw=true) · [手动完善草图 · 2K 原片](docs/assets/drawing-demo.mp4?raw=true) · [快速启动](#快速启动)

用文字生成分组流程，补充并行分支、质量判断和反馈循环，整理成完整的科研流程示意图，再下载高清图片。[查看流程图](docs/assets/research-flowchart.png) · [下载 SVG 矢量图](docs/assets/research-flowchart.svg)

<details>
<summary>观看“海边灯塔”：全程通过文字完成绘画</summary>

[![只用文字画出月光下的海边灯塔](docs/assets/language-preview.gif)](docs/assets/language-demo.mp4?raw=true)

描述海边灯塔的场景，接着用文字完善灯塔、帆船和星星，调整海面配色，在同一张画布上完成整幅插画，再下载高清 PNG。

[下载绘画视频 · 2K](docs/assets/language-demo.mp4?raw=true) · [下载灯塔图片](docs/assets/lighthouse.png)

</details>

<details>
<summary>观看“向日葵窗台”：从草图到完整绘画</summary>

[![从草图到完整绘画：向日葵窗台](docs/assets/drawing-preview.gif)](docs/assets/drawing-demo.mp4?raw=true)

让 AI 生成基础轮廓，再在同一张画布上手动补画花瓣、上色、添加窗台和蝴蝶，最后下载高清 PNG。

[下载绘画过程 · 2K](docs/assets/drawing-demo.mp4?raw=true) · [下载向日葵图片](docs/assets/sunflower.png)

</details>

动态预览展示精选片段，点击可下载 2K 完整视频。三段原片均为 2560 × 1440，已剪去等待时间并加速部分绘制过程。

## 可以做什么

- **描述画面**：通过自然语言创建草图、插画和流程图，观看绘制过程。
- **接着修改**：移动、改色、删除、撤销和重做，连续调整同一幅作品。
- **直接动手**：随时使用 Excalidraw 的选择、拖动、画笔和文字工具编辑。
- **保存与导出**：自动保存多张画布，支持 PNG、JPG、WebP、SVG 和可继续编辑的 Excalidraw 文件。

## 快速启动

准备 **Python 3.11+** 和 **Node.js 20.19+**。Windows 还需要安装 Chrome。

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

打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)，点击右上角 **设置**：

1. 选择模型厂商，填写模型名称、API 地址和 API Key。
2. 使用支持 **图片输入和工具调用** 的模型，点击 **测试连接**。
3. 测试通过后 **保存配置**，返回画布开始绘图。

可选择 Ark、OpenAI、Claude、Gemini 等厂商，也可接入本地服务或自定义兼容接口。各模型的可用能力以连接测试为准，模型服务按所选厂商计费。[查看模型配置说明](docs/model-settings.md)

以后在项目目录运行 `python run.py` 即可启动。

## 试着这样说

```text
画一张多模态 RAG 科研流程图，按数据准备、检索与生成、评估与产出分组
在评估部分加入质量判断、误差分析和返回查询改写的反馈循环
画一幅海边灯塔插画：深蓝夜空、青蓝海面、白色灯塔和淡黄色月亮
给灯塔加上暖黄色灯室，在海面添加一艘小帆船，天空点缀星星
把海面改成更深的蓝绿色，让月光和灯光更加醒目
```

也可以输入“用画笔画一张笑脸，展示作画过程”，或者描述一幅你想画的场景。完成后点击 **导出作品**，保存图片或继续编辑的文件。

## 反馈与参与

欢迎在 [Issues](https://github.com/bugkeep/ai-draw/issues) 分享作品、提出想法或反馈问题。觉得有用的话，欢迎点亮 Star，让更多人看到这个项目。

[部署说明](docs/deployment.md) · [参与贡献](CONTRIBUTING.md) · [开发与验证](docs/development.md) · [MIT License](LICENSE)
