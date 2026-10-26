import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


PYTHON_MIN = (3, 11)
NODE_MIN = (20, 19)

EXIT_OK = 0
EXIT_ARG = 1
EXIT_PYTHON = 2
EXIT_NODE = 3
EXIT_NPM = 4
EXIT_CHROME = 5
EXIT_VENV = 6
EXIT_SERVER = 7
EXIT_HTTP = 8
EXIT_FRONTEND = 9
EXIT_RENDER = 10

CHROME_ENV = "CHROME_PATH"


def log(message):
    print(message, flush=True)


def fail(message, code):
    print(f"失败: {message}", file=sys.stderr, flush=True)
    raise SystemExit(code)


def parse_version(text):
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    if not match:
        return None
    return tuple(int(group) for group in match.groups() if group is not None)


def run_capture(command, cwd=None, env=None):
    completed = subprocess.run(
        command, cwd=str(cwd) if cwd else None, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    return completed.returncode, (completed.stdout or "").strip()


def check_python(python_path=None):
    if python_path:
        exe = Path(python_path)
        if not exe.is_file():
            fail(f"指定的 Python 文件不存在: {exe}", EXIT_PYTHON)
    else:
        found = shutil.which("python") or shutil.which("python3")
        if not found:
            fail("未在 PATH 中找到 Python，请安装 Python 3.11+", EXIT_PYTHON)
        exe = Path(found)
    code, output = run_capture([str(exe), "--version"])
    if code != 0:
        fail(f"无法运行 Python {exe}: {output}", EXIT_PYTHON)
    version = parse_version(output)
    if version is None or version < PYTHON_MIN:
        fail(f"Python 需要 3.11+，当前 {output}", EXIT_PYTHON)
    log(f"[通过] Python {'.'.join(map(str, version[:2]))} -> {exe}")
    return exe


def check_node():
    found = shutil.which("node")
    if not found:
        fail("未在 PATH 中找到 Node.js，请安装 Node.js 20.19+", EXIT_NODE)
    exe = Path(found)
    code, output = run_capture([str(exe), "--version"])
    if code != 0:
        fail(f"无法运行 Node.js {exe}: {output}", EXIT_NODE)
    version = parse_version(output)
    if version is None or version[:2] < NODE_MIN:
        fail(f"Node.js 需要 20.19+，当前 {output}", EXIT_NODE)
    log(f"[通过] Node.js {'.'.join(map(str, version))} -> {exe}")
    return exe


def check_npm(node_exe):
    npm_cmd = node_exe.parent / "npm.cmd"
    if not npm_cmd.is_file():
        on_path = shutil.which("npm.cmd") or shutil.which("npm")
        if not on_path:
            fail(f"未找到 npm.cmd（期望随 Node.js 位于 {npm_cmd}）", EXIT_NPM)
        npm_cmd = Path(on_path)
    code, output = run_capture([str(npm_cmd), "--version"])
    if code != 0:
        fail(f"npm.cmd 无法运行: {output}", EXIT_NPM)
    log(f"[通过] npm {output} -> {npm_cmd}")
    return npm_cmd


def chrome_candidates():
    env_path = os.environ.get(CHROME_ENV)
    if env_path:
        candidate = Path(env_path)
        if not candidate.is_file():
            fail(f"{CHROME_ENV} 指向的浏览器不存在: {candidate}", EXIT_CHROME)
        return [candidate]
    candidates = []
    program_files = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("LocalAppData", ""),
    ]
    for base in program_files:
        if not base:
            continue
        candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    seen = set()
    result = []
    for candidate in candidates:
        key = str(candidate).lower()
        if key not in seen:
            seen.add(key)
            result.append(candidate)
    return result


def find_chrome():
    for candidate in chrome_candidates():
        if candidate.is_file():
            return candidate
    return None


def check_chrome():
    chrome = find_chrome()
    if chrome is None:
        env_hint = os.environ.get(CHROME_ENV)
        if env_hint:
            fail(f"{CHROME_ENV} 指向的浏览器不存在: {env_hint}", EXIT_CHROME)
        fail(
            "未找到 Chrome/Chromium。请安装 Google Chrome，或设置环境变量 "
            f"{CHROME_ENV} 指向 chrome.exe",
            EXIT_CHROME,
        )
    if os.environ.get(CHROME_ENV):
        log(f"[通过] 浏览器 ({CHROME_ENV}) -> {chrome}")
    else:
        log(f"[通过] 浏览器 -> {chrome}")
    return chrome


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def http_get(url, timeout=5):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, method="GET")
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status, body
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        return error.code, body


def wait_for_server(port, server_log, process, timeout=60):
    url = f"http://127.0.0.1:{port}/api/studio/health"
    deadline = time.time() + timeout
    last_error = "服务尚未开始监听"
    while time.time() < deadline:
        if process.poll() is not None:
            fail(f"健康检查服务已退出，退出码 {process.returncode}，日志: {server_log}", EXIT_SERVER)
        try:
            status, body = http_get(url, timeout=2)
            if status == 200:
                return body
            last_error = f"HTTP {status}"
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = str(error)
        time.sleep(0.5)
    tail = server_log.read_text(encoding="utf-8", errors="replace")[-2000:]
    fail(f"健康服务在 {timeout} 秒内未就绪: {last_error}\n服务日志:\n{tail}", EXIT_SERVER)


def verify_render(root, frontend_dir, chrome, log_dir):
    node_exe = shutil.which("node")
    if not node_exe:
        fail("健康检查需要 Node.js", EXIT_RENDER)
    worker = frontend_dir / "scripts" / "render_worker.mjs"
    if not worker.is_file():
        fail(f"渲染工作脚本缺失: {worker}", EXIT_RENDER)
    render_log = log_dir / "render_worker.log"
    env = os.environ.copy()
    env["CHROME_PATH"] = str(chrome)
    process = subprocess.Popen(
        [node_exe, str(worker)], cwd=str(root), env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    stdout = stderr = ""
    try:
        stdout, stderr = process.communicate(input="", timeout=60)
        render_log.write_text(stdout + stderr, encoding="utf-8")
        if process.returncode != 0:
            fail(f"浏览器渲染检查失败，退出码 {process.returncode}，日志: {render_log}", EXIT_RENDER)
        payload = json.loads(stdout.strip())
        if payload.get("ready") is not True:
            fail("渲染工作进程未报告 ready", EXIT_RENDER)
        log("[通过] 真实 Chrome 可启动并加载渲染页面")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            stdout, stderr = process.communicate()
            render_log.write_text(stdout + stderr, encoding="utf-8")


def health_check(root, venv_python, log_dir):
    if not venv_python or not Path(venv_python).is_file():
        fail("健康检查需要有效的虚拟环境 Python，请先完成安装", EXIT_VENV)
    venv_python = Path(venv_python)
    version_code, version_out = run_capture([str(venv_python), "-c",
        "import json,sys; print(json.dumps({'version':list(sys.version_info[:2]),'venv':sys.prefix!=sys.base_prefix}))"])
    if version_code != 0:
        fail(f"虚拟环境 Python 无效: {version_out}", EXIT_VENV)
    python_info = json.loads(version_out)
    if not python_info["venv"] or tuple(python_info["version"]) < PYTHON_MIN:
        fail(f"需要 Python 3.11+ 的有效虚拟环境: {venv_python}", EXIT_VENV)

    frontend_dist = root / "frontend-dist"
    if not (frontend_dist / "index.html").is_file():
        fail(f"前端尚未构建，缺少 {frontend_dist / 'index.html'}", EXIT_FRONTEND)
    if not (frontend_dist / "render.html").is_file():
        fail(f"前端缺少渲染页面 {frontend_dist / 'render.html'}", EXIT_FRONTEND)
    frontend_dir = root / "frontend-app"
    if not (frontend_dir / "node_modules" / "playwright").is_dir():
        fail("frontend-app 缺少 Playwright，请先运行 npm ci", EXIT_RENDER)

    chrome = check_chrome()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_dir = Path(tempfile.mkdtemp(prefix="healthcheck-", dir=log_dir))
    database = log_dir / "healthcheck.sqlite3"
    key_file = log_dir / "healthcheck.key"
    server_log = log_dir / "healthcheck_server.log"
    port = free_port()

    env = os.environ.copy()
    env["DRAW_AGENT_DB"] = str(database)
    env["DRAW_AGENT_SETTINGS_KEY_FILE"] = str(key_file)
    env["PYTHONIOENCODING"] = "utf-8"
    env["LITELLM_MODE"] = "PRODUCTION"

    command = [
        str(venv_python), "-m", "uvicorn", "server.app:create_app",
        "--factory", "--host", "127.0.0.1", "--port", str(port),
    ]
    log(f"启动健康检查服务: 127.0.0.1:{port}")
    log_handle = open(server_log, "w", encoding="utf-8")
    process = subprocess.Popen(
        command, cwd=str(root), env=env,
        stdout=log_handle, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        if process.poll() is not None:
            log_handle.close()
            tail = ""
            try:
                tail = server_log.read_text(encoding="utf-8", errors="replace")[-2000:]
            except OSError:
                pass
            fail(f"健康服务立即退出，码 {process.returncode}\n{tail}", EXIT_SERVER)

        body = wait_for_server(port, server_log, process)
        try:
            health = json.loads(body)
        except json.JSONDecodeError:
            fail(f"/api/studio/health 返回非 JSON: {body[:200]}", EXIT_HTTP)
        if health.get("status") != "ok" or "api_revision" not in health:
            fail(f"/api/studio/health 返回字段异常: {health}", EXIT_HTTP)
        log(f"[通过] /api/studio/health -> status={health.get('status')} "
            f"revision={health.get('api_revision')}")

        for path in ("/", "/settings"):
            status, page = http_get(f"http://127.0.0.1:{port}{path}", timeout=5)
            if status != 200:
                fail(f"{path} 返回 HTTP {status}", EXIT_FRONTEND)
            if "<html" not in page.lower():
                fail(f"{path} 未返回 HTML 页面", EXIT_FRONTEND)
            log(f"[通过] {path} -> HTTP 200")

        verify_render(root, frontend_dir, chrome, log_dir)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        log_handle.close()

    log("健康检查全部通过")
    log(f"临时数据库: {database}")
    log(f"服务日志: {server_log}")


def main():
    parser = argparse.ArgumentParser(description="Draw Agent Studio Windows 环境检查")
    parser.add_argument("--root", type=Path,
                        default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--python", type=str, default=None,
                        help="显式指定用于检查/创建虚拟环境的 Python 文件")
    parser.add_argument("--check-only", action="store_true",
                        help="只检查基础依赖，不安装、不启动服务")
    parser.add_argument("--health-check", action="store_true",
                        help="使用项目虚拟环境启动临时服务并执行完整健康检查")
    parser.add_argument("--venv-python", type=str, default=None,
                        help="健康检查使用的虚拟环境 Python 文件")
    parser.add_argument("--log-dir", type=Path, default=None,
                        help="健康检查临时文件与日志目录")
    args = parser.parse_args()

    root = args.root.resolve()
    if not (root / "run.py").is_file() or not (root / "requirements.txt").is_file():
        fail(f"项目根目录无效: {root}", EXIT_ARG)

    if args.health_check:
        venv_python = args.venv_python
        if not venv_python:
            candidate = root / ".venv" / "Scripts" / "python.exe"
            venv_python = str(candidate)
        log_dir = args.log_dir
        if log_dir is None:
            log_dir = root / "runs" / "windows-install" / "healthcheck"
        health_check(root, venv_python, log_dir.resolve())
        return

    log(f"项目根目录: {root}")
    check_python(args.python)
    node_exe = check_node()
    check_npm(node_exe)
    check_chrome()
    log("基础环境检查全部通过；检查模式未执行任何安装。")


if __name__ == "__main__":
    main()
