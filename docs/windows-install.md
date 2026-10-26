# Windows 安装与启动

准备好 Python、Node.js 和 Chrome 后，双击项目目录中的 `install.bat` 完成安装，再双击 `start.bat` 启动。安装完成后，在浏览器打开 <http://127.0.0.1:8000>。

## 环境要求

| 软件 | 要求 |
| --- | --- |
| Python | 3.11 或更高版本，安装时加入 PATH |
| Node.js | 20.19 或更高版本，安装包包含 npm |
| Google Chrome | 用于画布渲染和图片导出 |
| PowerShell | Windows 自带的 Windows PowerShell 5.1 或更高版本 |

安装依赖需要能够访问 Python 和 npm 包源。项目目录可以包含中文和空格。

## 安装

取得完整源码后，双击 **`install.bat`**。安装程序会检查环境，在项目目录创建或复用 `.venv`，安装 Python 和前端依赖，构建前端，并验证本地服务和浏览器渲染。

安装期间会显示执行进度。出现完成提示后，双击 **`start.bat`**。后续启动直接使用 `start.bat` 即可。

首次打开网页后，进入右上角的 **设置**，填写支持图片输入和工具调用的模型连接，点击 **测试连接**，通过后保存。模型连接的配置方法见 [模型配置说明](model-settings.md)。

## 通过命令行安装

在项目目录打开 PowerShell，运行：

```powershell
.\install.bat
```

系统中有多个 Python 时，可以指定创建虚拟环境所用的 Python：

```powershell
.\install.bat -PythonPath "C:\Python313\python.exe"
```

只检查基础环境：

```powershell
.\install.bat -CheckOnly
```

也可以组合参数：

```powershell
.\install.bat -CheckOnly -PythonPath "C:\Python313\python.exe"
```

基础检查会确认 Python、Node.js、npm 和浏览器文件是否可用。完整安装最后还会实际启动服务和 Chrome，检查健康接口、主页、设置页和画布渲染页面。

## 指定浏览器位置

Chrome 安装在标准位置时，程序会自动找到它。使用其他安装位置的 Chrome 或兼容的 Chromium 浏览器时，在 PowerShell 中设置 `CHROME_PATH`，并从同一个窗口运行安装和启动入口：

```powershell
$env:CHROME_PATH = "D:\Applications\Chrome\chrome.exe"
.\install.bat
.\start.bat
```

如果希望以后通过双击启动，可以在 Windows 的用户环境变量中保存 `CHROME_PATH`。

## 日志与数据

安装日志保存在 `runs/windows-install/`。每次健康检查都会创建独立目录，保留自己的数据库、服务日志和浏览器渲染日志。检查使用独立端口，结束后关闭此次检查启动的服务。

| 路径 | 内容 |
| --- | --- |
| `.venv/` | 当前项目的 Python 环境 |
| `frontend-dist/` | 构建后的网页和渲染页面 |
| `runs/windows-install/install_*.log` | 安装过程和原生命令输出 |
| `runs/windows-install/healthcheck/healthcheck-*/` | 每次健康检查的数据库与日志 |
| `runs/studio.sqlite3` | 正常启动后的画布、对话及设置 |

保留源码目录和 `.venv` 的相对位置。需要迁移项目时，在新目录重新创建虚拟环境。

## 常见问题

- **Python 或 Node.js 检查未通过**：确认版本符合要求，重新打开 PowerShell 后再次运行。也可通过 `-PythonPath` 指定 Python。
- **已有虚拟环境无效**：根据提示修复或自行更换 `.venv`。安装程序会停止，让你处理当前环境。
- **找不到 Chrome**：安装 Chrome，或设置有效的 `CHROME_PATH`。
- **依赖下载或构建失败**：查看安装日志中的 pip 或 npm 输出，检查网络和包源。
- **健康检查失败**：查看此次检查目录中的 `healthcheck_server.log` 和 `render_worker.log`。修正问题后重新运行安装入口。
- **启动时提示端口 8000 已使用**：确认是否已经启动了本项目，或检查占用该端口的程序。

安装或检查失败时，入口会返回非零退出码，并保留日志。启动后按 `Ctrl+C` 可停止服务。
