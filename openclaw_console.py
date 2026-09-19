# -*- coding: utf-8 -*-
"""
OpenClaw \u63a7\u5236\u53f0 v3.3
管理本地模型服务(llama-server) + OpenClaw Gateway + 控制台入口
v2.3: 修网关启动走 --task-supervisor 重启循环；修 RAMCleanup 参数 400；生图模型列表异步加载不再卡启动；
      单实例锁；开机自启 mmproj 覆盖参数；清理显存链式挂 RAMCleanup；路径跟随配置
v2.4: 一键恢复不再覆盖用户自定义路径（comfy_root 迁到 L:/ComfyUI 等）；修复"生图连接的不是选择的模型"——
      控制台与 MCP 统一按 paths.json 实时读取目录，改路径后无需重启网关即可生效
v2.5: 生图链路完全修复脚本（apply_openclaw_patches.py）：OpenClaw 升级覆盖 dist 补丁后一键重打；
      路径保存后自动重打媒体白名单（跟随新 comfy_root），解决"生图成功但前端不显示图片"
v2.6: 日志窗口新增「网关」页签（gateway.log 实时尾随）；主窗口顶部新增全局硬件状态栏
      （CPU/内存/GPU/显存/温度，切任意页签可见）
v2.7: ComfyUI 版本可选与路径跟随
v2.8: 模型页大模型选择与一键恢复入口
v2.9: 生图链路输出目录统一；自定义 ComfyUI 路径应用
v3.1: 数字人启动进度条 + WSL 一键修复批处理（每 15s 轮询，不再需要重启控制台看灯）
v3.2: 移除数字人模块（页签/日志/启停/WSL 管理全部删除），不再依赖 WSL 与 AI数字人 目录
"""
import os, sys, json, time, glob, io, subprocess, threading, webbrowser, tkinter as tk
from tkinter import ttk, messagebox
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote

# ============ 常量 ============
# 路径配置（paths.json 可自定义，重装系统后一键复原的依据）
# PyInstaller onefile 下 __file__ 指向临时解压目录，写入会随进程退出丢失，
# 故固定状态目录用真实路径（paths.json 所在目录）；源码运行则用脚本目录。
_BASE_DIR = r'L:\OpenClaw\OpenClawData\console'
if not os.path.isdir(_BASE_DIR):
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_PATHS_CFG = os.path.join(_BASE_DIR, 'paths.json')
_UI_STATE_CFG = os.path.join(_BASE_DIR, 'ui_state.json')   # 窗口大小/位置记忆
_DEFAULT_PATHS = {
    'llama_dir':      r'L:\OpenClaw\llama',
    'comfy_root':     r'L:\OpenClaw\ComfyUI',
    'openclaw_data':  r'L:\OpenClaw\OpenClawData',
    'openclaw_npm':   r'L:\OpenClaw\npm',
    'node_exe':       r'C:\Program Files\nodejs\node.exe',
    'tailscale_exe':  r'C:\Program Files\Tailscale\tailscale.exe',
    'textgen_dir':    r'L:\text-generation-webui',
}

def _load_paths():
    p = dict(_DEFAULT_PATHS)
    try:
        cfg = _PATHS_CFG
        if not os.path.isfile(cfg):
            # onefile 打包时 __file__ 指向临时解压目录，内置有 paths.json 快照可回退
            alt = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'paths.json')
            if os.path.isfile(alt):
                cfg = alt
        if os.path.isfile(cfg):
            with io.open(cfg, 'r', encoding='utf-8') as f:
                p.update(json.load(f))
    except Exception:
        pass
    return p

def _save_paths(p):
    try:
        with io.open(_PATHS_CFG, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(p, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False

def _apply_paths(p):
    """保存后即时更新全局路径常量（无需重启控制台）"""
    global LLAMA_DIR, MODELS_DIR, LLAMA_SERVER, NODE_EXE, _PORTABLE_NPM, OPENCLAW_MJS
    global CONFIG_PATH, TAILSCALE_EXE, COMFY_ROOT, COMFY_BAT, COMFY_PY, COMFY_DIR
    global TEXTGEN_DIR, TEXTGEN_PY
    LLAMA_DIR     = p['llama_dir']
    MODELS_DIR    = os.path.join(LLAMA_DIR, 'models')
    LLAMA_SERVER  = os.path.join(LLAMA_DIR, 'llama-server.exe')
    NODE_EXE      = p['node_exe']
    if not os.path.isfile(NODE_EXE):
        NODE_EXE = os.path.join(r'L:\OpenClaw\node', 'node.exe')
    _PORTABLE_NPM = p['openclaw_npm']
    OPENCLAW_MJS  = os.path.join(_PORTABLE_NPM, r'node_modules\openclaw\openclaw.mjs')
    if not os.path.isfile(OPENCLAW_MJS):
        OPENCLAW_MJS = os.path.join(os.environ['APPDATA'], r'npm\node_modules\openclaw\openclaw.mjs')
    CONFIG_PATH   = os.path.join(p['openclaw_data'], 'openclaw.json')
    TAILSCALE_EXE = p['tailscale_exe']
    COMFY_ROOT    = p['comfy_root']
    COMFY_BAT     = os.path.join(COMFY_ROOT, 'start_comfy.bat')
    COMFY_PY      = os.path.join(COMFY_ROOT, 'python_embeded', 'python.exe')
    COMFY_DIR     = os.path.join(COMFY_ROOT, 'ComfyUI')
    TEXTGEN_DIR   = p.get('textgen_dir', r'L:\text-generation-webui')
    TEXTGEN_PY    = os.path.join(TEXTGEN_DIR, 'installer_files', 'env', 'python.exe')

_PATHS = _load_paths()
LLAMA_DIR     = _PATHS['llama_dir']
MODELS_DIR    = os.path.join(LLAMA_DIR, 'models')
LLAMA_SERVER  = os.path.join(LLAMA_DIR, 'llama-server.exe')
NODE_EXE      = _PATHS['node_exe']
if not os.path.isfile(NODE_EXE):
    NODE_EXE = os.path.join(r'L:\OpenClaw\node', 'node.exe')
_PORTABLE_NPM = _PATHS['openclaw_npm']
OPENCLAW_MJS  = os.path.join(_PORTABLE_NPM, r'node_modules\openclaw\openclaw.mjs')
if not os.path.isfile(OPENCLAW_MJS):
    OPENCLAW_MJS = os.path.join(os.environ['APPDATA'], r'npm\node_modules\openclaw\openclaw.mjs')
CONFIG_PATH   = os.path.join(_PATHS['openclaw_data'], 'openclaw.json')
TAILSCALE_EXE = _PATHS['tailscale_exe']
TEXTGEN_DIR   = _PATHS.get('textgen_dir', r'L:\text-generation-webui')
TEXTGEN_PY    = os.path.join(TEXTGEN_DIR, 'installer_files', 'env', 'python.exe')
PORT_LLM      = 8080
PORT_GW       = 18789
TEXTGEN_PORT  = 7861
ST_DIR        = _PATHS.get('sillytavern_dir', r'L:\SillyTavern-1.11.5整合包\SillyTavern-1.11.5')
ST_PORT       = 8000
ST_LOG        = os.path.join(_BASE_DIR, 'logs', 'sillytavern.log')
TEXTGEN_LOG   = os.path.join(_BASE_DIR, 'logs', 'textgen.log')
DASH_URL      = f'http://127.0.0.1:{PORT_GW}/'
STARTUP_DIR   = os.path.join(os.environ['APPDATA'], r'Microsoft\Windows\Start Menu\Programs\Startup')
AUTOSTART_VBS = os.path.join(STARTUP_DIR, 'OpenClawModel.vbs')
COMFY_AUTOSTART_VBS = os.path.join(STARTUP_DIR, 'OpenClawComfy.vbs')

# ============ 推理模式预设（4B CPU 常驻不杀 / 4B GPU / 9B GPU / 自定义） ============
LLM_4B_PATH = r'L:\ComfyUI\ComfyUI\models\LLM\Qwen3.5-4B-Uncensored-HauhauCS-Aggressive-Q6_K.gguf'
LLM_9B_PATH = r'L:\ComfyUI\ComfyUI\models\LLM\Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf'
LLM_MODES = [
    '4B CPU（推荐，常驻不杀）',
    '4B GPU',
    '9B GPU',
    '自定义（跟随上方模型）',
]
LLM_MODE_DEFAULT = LLM_MODES[0]

llm_proc = None          # llama-server 子进程
freelmapi_proc = None       # FreeLLMAPI 子进程
llm_pid  = None          # 记录 pid（含外部已启动的）
log_lock = threading.Lock()

# ============ 工具函数 ============
def http_ok(url, timeout=3):
    try:
        r = urllib.request.urlopen(url, timeout=timeout)
        return r.status == 200
    except Exception:
        return False

# ============ 工作流编辑器 HTTP 服务 ============
import re as _re
def _wf_proxy(self, sub, body=None, timeout=30):
    """把编辑器请求转发到 ComfyUI（解决浏览器跨域）"""
    try:
        url = 'http://127.0.0.1:%d%s' % (COMFY_PORT, sub)
        if body is not None:
            req = urllib.request.Request(url, data=body.encode('utf-8'),
                                         headers={'Content-Type': 'application/json'})
        else:
            req = urllib.request.Request(url)
        r = urllib.request.urlopen(req, timeout=timeout)
        data = r.read()
        self.send_response(r.status)
        self.send_header('Content-Type', r.headers.get('Content-Type', 'application/json'))
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    except Exception as e:
        body = json.dumps({'error': str(e)}).encode('utf-8')
        self.send_response(502)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

def _wf_object_info(self):
    """object_info 带缓存：ComfyUI 在线拉取并存盘；离线用缓存兜底"""
    cache_path = os.path.join(WF_DIR, 'object_info_cache.json')
    try:
        url = 'http://127.0.0.1:%d/object_info' % COMFY_PORT
        r = urllib.request.urlopen(urllib.request.Request(url), timeout=20)
        data = r.read()
        try:
            with io.open(cache_path, 'wb') as f:
                f.write(data)
        except Exception:
            pass
        self.send_response(r.status)
        self.send_header('Content-Type', r.headers.get('Content-Type', 'application/json'))
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)
    except Exception:
        if os.path.isfile(cache_path):
            with io.open(cache_path, 'rb') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Object-Info-Source', 'cache')
            self.end_headers()
            self.wfile.write(data)
        else:
            body = json.dumps({'error': 'ComfyUI 离线且无缓存'}).encode('utf-8')
            self.send_response(502)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

class WFHandler(BaseHTTPRequestHandler):
    """工作流编辑器后端：服务 HTML / 读写 workflows / 代理 ComfyUI"""
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype='application/json'):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype + '; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_GET(self):
        path = urlparse(self.path).path
        qs = parse_qs(urlparse(self.path).query)
        if path == '/':
            try:
                with io.open(WF_HTML, 'r', encoding='utf-8') as f:
                    self._send(200, f.read(), 'text/html')
            except Exception as e:
                self._send(500, json.dumps({'error': str(e)}))
        elif path == '/api/workflows':
            files = []
            if os.path.isdir(WF_DIR):
                for dp, dns, fns in os.walk(WF_DIR):
                    for fn in sorted(fns):
                        if fn.endswith('.json') and not fn.endswith('.ui.json'):
                            rel = os.path.relpath(os.path.join(dp, fn), WF_DIR).replace('\\', '/')
                            files.append(rel)
            files.sort()
            self._send(200, json.dumps({'workflows': files}))
        elif path == '/api/workflow':
            name = (qs.get('name') or [''])[0]
            fp = os.path.abspath(os.path.join(WF_DIR, name)) if name else ''
            wf_abs = os.path.abspath(WF_DIR)
            if not name or os.path.commonpath([wf_abs, fp]) != wf_abs or not os.path.isfile(fp):
                self._send(404, json.dumps({'error': '工作流不存在'}))
                return
            try:
                with io.open(fp, 'r', encoding='utf-8') as f:
                    wf = json.load(f)
                ui = {}
                ufp = os.path.join(os.path.dirname(fp), os.path.splitext(os.path.basename(fp))[0] + '.ui.json')
                if os.path.isfile(ufp):
                    with io.open(ufp, 'r', encoding='utf-8') as f:
                        ui = json.load(f)
                self._send(200, json.dumps({'workflow': wf, 'ui': ui}))
            except Exception as e:
                self._send(500, json.dumps({'error': str(e)}))
        elif path == '/api/comfy/object_info':
            _wf_object_info(self)
        elif path == '/api/comfy/history':
            pid = (qs.get('prompt_id') or [''])[0]
            _wf_proxy(self, '/history/' + quote(pid))
        elif path == '/api/comfy/view':
            fn = quote((qs.get('filename') or [''])[0])
            sub = quote((qs.get('subfolder') or [''])[0])
            typ = quote((qs.get('type') or ['output'])[0])
            _wf_proxy(self, '/view?filename=%s&subfolder=%s&type=%s' % (fn, sub, typ))
        elif path == '/api/status':
            self._send(200, json.dumps({'comfy': comfy_alive(), 'port': COMFY_PORT}))
        else:
            self._send(404, json.dumps({'error': '404'}))

    def do_POST(self):
        path = urlparse(self.path).path
        if path == '/api/workflow':
            try:
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)).decode('utf-8'))
                name = body.get('name', '')
                if not name or not _re.match(r'^[\w\-/]+\.json$', name):
                    self._send(400, json.dumps({'error': '名称只能含字母数字_-/并以 .json 结尾'}))
                    return
                fp = os.path.abspath(os.path.join(WF_DIR, name))
                wf_abs = os.path.abspath(WF_DIR)
                if os.path.commonpath([wf_abs, fp]) != wf_abs:
                    self._send(400, json.dumps({'error': '非法路径'}))
                    return
                os.makedirs(os.path.dirname(fp), exist_ok=True)
                with io.open(fp, 'w', encoding='utf-8', newline='\n') as f:
                    json.dump(body.get('workflow', {}), f, ensure_ascii=False, indent=1)
                ufp = os.path.join(os.path.dirname(fp), os.path.splitext(os.path.basename(fp))[0] + '.ui.json')
                with io.open(ufp, 'w', encoding='utf-8', newline='\n') as f:
                    json.dump(body.get('ui', {}), f, ensure_ascii=False, indent=1)
                self._send(200, json.dumps({'ok': True}))
            except Exception as e:
                self._send(500, json.dumps({'error': str(e)}))
        elif path == '/api/comfy/prompt':
            body = self.rfile.read(int(self.headers.get('Content-Length') or 0)).decode('utf-8')
            _wf_proxy(self, '/prompt', body=body, timeout=60)
        else:
            self._send(404, json.dumps({'error': '404'}))

_wf_httpd = None

def start_wf_server():
    """启动工作流编辑器 HTTP 服务（127.0.0.1:8756）"""
    global _wf_httpd
    if _wf_httpd:
        return True
    try:
        os.makedirs(WF_DIR, exist_ok=True)
        _wf_httpd = ThreadingHTTPServer(('127.0.0.1', WF_PORT), WFHandler)
        threading.Thread(target=_wf_httpd.serve_forever, daemon=True).start()
        return True
    except Exception as e:
        _wf_httpd = None
        return False

def open_workflow_editor():
    if not start_wf_server():
        return '工作流服务启动失败'
    webbrowser.open('http://127.0.0.1:%d/' % WF_PORT)
    return '已打开工作流编辑器'

def llama_running():
    # 双通道判活：HTTP 通算活；HTTP 忙（大 prompt 处理中）但端口在监听也算活
    if http_ok(f'http://127.0.0.1:{PORT_LLM}/v1/models', timeout=5):
        return True
    return get_llm_pid() is not None

def gw_running():
    return http_ok(f'http://127.0.0.1:{PORT_GW}/')

COMFY_PORT = 8188
COMFY_ROOT = _PATHS['comfy_root']
COMFY_BAT  = os.path.join(COMFY_ROOT, 'start_comfy.bat')
COMFY_PY   = os.path.join(COMFY_ROOT, 'python_embeded', 'python.exe')
COMFY_DIR  = os.path.join(COMFY_ROOT, 'ComfyUI')
LOG_DIR    = r'L:\OpenClaw\OpenClawData\console\logs'
LLAMA_LOG  = os.path.join(LOG_DIR, 'llama.log')
COMFY_LOG  = os.path.join(LOG_DIR, 'comfyui.log')
GATEWAY_LOG = os.path.join(LOG_DIR, 'gateway.log')
WF_PORT    = 8756
WF_HTML    = os.path.join(_BASE_DIR, 'workflow_editor.html')
WF_DIR     = os.path.join(_BASE_DIR, 'workflows')

HW_PROFILE = os.path.join(_BASE_DIR, 'hw_profile.json')

def detect_hardware():
    """检测 GPU 显存 / 物理内存 / CPU 核数"""
    hw = {'gpu': '未知', 'vram': 0, 'ram': 0, 'cpu': os.cpu_count() or 0}
    try:
        import ctypes
        class MS(ctypes.Structure):
            _fields_ = [('dwLength', ctypes.c_uint32), ('dwMemoryLoad', ctypes.c_uint32),
                        ('ullTotalPhys', ctypes.c_uint64), ('ullAvailPhys', ctypes.c_uint64),
                        ('ullTotalPageFile', ctypes.c_uint64), ('ullAvailPageFile', ctypes.c_uint64),
                        ('ullTotalVirtual', ctypes.c_uint64), ('ullAvailVirtual', ctypes.c_uint64),
                        ('ullAvailExtendedVirtual', ctypes.c_uint64)]
        ms = MS(); ms.dwLength = ctypes.sizeof(MS)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
            hw['ram'] = round(ms.ullTotalPhys / 1024**3)
    except Exception:
        pass
    try:
        r = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total',
                            '--format=csv,noheader,nounits'],
                           capture_output=True, text=True, timeout=10,
                           encoding='utf-8', errors='replace')
        if r.returncode == 0:
            line = (r.stdout or '').strip().splitlines()
            if line:
                parts = line[0].split(',')
                hw['gpu'] = parts[0].strip()
                try:
                    hw['vram'] = round(int(parts[1].strip()) / 1024, 1)
                except Exception:
                    pass
    except Exception:
        pass
    return hw

def recommend_profile(hw):
    """按硬件档位生成推荐配置"""
    vram, ram = hw['vram'], hw['ram']
    if vram >= 16:
        tier, ngl, ctx, model = '大显存', 999, 65536, '12B 级模型可全载 GPU'
    elif vram >= 10:
        tier, ngl, ctx, model = '中高显存', 35, 32768, '12B 级模型部分层 GPU + CPU 分担'
    elif vram >= 6:
        tier, ngl, ctx, model = '中显存', 24, 16384, '建议 9B 级模型，少开并发'
    else:
        tier, ngl, ctx, model = '小显存/核显', 16, 8192, '建议 3B 级模型，关闭视觉'
    kv = 'on' if vram < 10 else 'q8_0'
    if ram >= 32:
        model += '；内存充裕可加大上下文'
    return {'tier': tier, 'gpu': hw['gpu'], 'vram': vram, 'ram': ram, 'cpu': hw['cpu'],
            'ngl': ngl, 'ctx': ctx, 'kv': kv, 'model': model,
            'label': '%s · %s · %sGB显存/%sGB内存' % (tier, hw['gpu'], vram, ram)}

def save_profile(rec):
    with open(HW_PROFILE, 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)

def load_profile():
    try:
        with open(HW_PROFILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None

def comfy_alive():
    # 双通道判活：HTTP 通算活；HTTP 忙（加载模型/生图）但端口在监听也算活
    if http_ok(f'http://127.0.0.1:{COMFY_PORT}/system_stats', timeout=5):
        return True
    return get_comfy_pid() is not None

def get_port_pid(port):
    # 通用：查某端口监听 PID
    try:
        out = subprocess.run(['powershell', '-NoProfile', '-Command',
            f'(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess)'],
            capture_output=True, text=True, timeout=15, encoding='utf-8', errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW).stdout.strip()
        return int(out) if out.isdigit() else None
    except Exception:
        return None

def textgen_alive():
    # 傻酒馆 text-generation-webui 判活
    if http_ok(f'http://127.0.0.1:{TEXTGEN_PORT}/', timeout=5):
        return True
    return get_port_pid(TEXTGEN_PORT) is not None

def st_alive():
    try:
        if http_ok(f'http://127.0.0.1:{ST_PORT}/', timeout=5):
            return True
    except Exception:
        pass
    return get_port_pid(ST_PORT) is not None

import ctypes as _ct

class _FILETIME(_ct.Structure):
    _fields_ = [("dwLowDateTime", _ct.c_uint32), ("dwHighDateTime", _ct.c_uint32)]

class _MEMORYSTATUSEX(_ct.Structure):
    _fields_ = [("dwLength", _ct.c_ulong), ("dwMemoryLoad", _ct.c_ulong),
                ("ullTotalPhys", _ct.c_ulonglong), ("ullAvailPhys", _ct.c_ulonglong),
                ("ullTotalPageFile", _ct.c_ulonglong), ("ullAvailPageFile", _ct.c_ulonglong),
                ("ullTotalVirtual", _ct.c_ulonglong), ("ullAvailVirtual", _ct.c_ulonglong),
                ("ullAvailExtendedVirtual", _ct.c_ulonglong)]

_cpu_prev = None

def _sys_cpu():
    global _cpu_prev
    try:
        idle, kernel, user = _FILETIME(), _FILETIME(), _FILETIME()
        _ct.windll.kernel32.GetSystemTimes(_ct.byref(idle), _ct.byref(kernel), _ct.byref(user))
        def ti(ft):
            return (ft.dwHighDateTime << 32) | ft.dwLowDateTime
        now = (ti(idle), ti(kernel) + ti(user))
        if _cpu_prev:
            didle = now[0] - _cpu_prev[0]
            dtotal = now[1] - _cpu_prev[1]
            if dtotal > 0:
                pct = 100.0 * (1.0 - didle / dtotal)
                _cpu_prev = now
                return max(0.0, min(100.0, pct))
        _cpu_prev = now
        return None
    except Exception:
        return None

def _sys_mem():
    try:
        m = _MEMORYSTATUSEX()
        m.dwLength = _ct.sizeof(m)
        _ct.windll.kernel32.GlobalMemoryStatusEx(_ct.byref(m))
        return m.dwMemoryLoad, m.ullTotalPhys / 1073741824.0
    except Exception:
        return None

def gpu_stats():
    try:
        r = subprocess.run(['nvidia-smi', '--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu',
                            '--format=csv,noheader,nounits'],
                           capture_output=True, text=True, timeout=5,
                           creationflags=0x08000000)
        parts = [p.strip() for p in r.stdout.strip().split(',')]
        if len(parts) >= 4:
            return {'util': float(parts[0]), 'used_mb': float(parts[1]),
                    'total_mb': float(parts[2]), 'temp': float(parts[3])}
    except Exception:
        pass
    return None

def sys_stats():
    cpu = _sys_cpu()
    mem = _sys_mem()
    return {'cpu': cpu,
            'mem_pct': mem[0] if mem else None,
            'mem_gb': mem[1] if mem else None,
            'gpu': gpu_stats()}

def get_comfy_pid():
    """查 ComfyUI 端口（COMFY_PORT）的 PID"""
    try:
        out = subprocess.run(['powershell', '-NoProfile', '-Command',
            f'(Get-NetTCPConnection -LocalPort {COMFY_PORT} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess)'],
            capture_output=True, text=True, timeout=15, encoding='utf-8', errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW).stdout.strip()
        return int(out) if out.isdigit() else None
    except Exception:
        return None

def read_config():
    try:
        with open(CONFIG_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def gw_token():
    try:
        d = read_config()
        return d.get('gateway', {}).get('auth', {}).get('token', '')
    except Exception:
        return ''

def _strip_quant(name):
    """去掉 GGUF 文件名的量化后缀段（-Q4_K_M / -IQ2_M 等）"""
    import re
    return re.sub(r'[-_](IQ|Q)\d[_A-Za-z0-9]*$', '', name)

def _match_mmproj(main_path):
    """按主模型匹配配套视觉投影：优先同目录，其次内置目录规则"""
    name = os.path.basename(main_path)
    base = _strip_quant(name)
    parts = base.split('-')
    key2 = '-'.join(parts[:2]) if len(parts) >= 2 else base
    d = os.path.dirname(main_path)
    # 1) 同目录：mmproj-<核心段> 开头
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.startswith('mmproj-') and f.endswith('.gguf') and key2 in f:
                return os.path.join(d, f)
    # 2) 内置目录规则
    key = name.lower()
    if 'gemma' in key:
        for cand in ('mmproj-gemma-4-12B-it-Q8_0.gguf', 'mmproj-F16.gguf'):
            p = os.path.join(MODELS_DIR, cand)
            if os.path.isfile(p):
                return p
    if 'qwen' in key:
        for f in sorted(os.listdir(MODELS_DIR)):
            if f.startswith('mmproj-Qwen3.6-35B') and f.endswith('.gguf'):
                return os.path.join(MODELS_DIR, f)
    return None

CUSTOM_FILE = r'L:\OpenClaw\OpenClawData\console\custom_models.json'
DIRS_FILE   = r'L:\OpenClaw\OpenClawData\console\model_dirs.json'

def load_extra_dirs():
    """额外模型扫描目录列表"""
    try:
        with open(DIRS_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []

def save_extra_dirs(dirs):
    try:
        os.makedirs(os.path.dirname(DIRS_FILE), exist_ok=True)
        with open(DIRS_FILE, 'w', encoding='utf-8') as f:
            json.dump(dirs, f, ensure_ascii=False, indent=1)
    except Exception:
        pass

def load_customs():
    """读取自定义模型列表 [{path, name}]"""
    try:
        with open(CUSTOM_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []

def save_customs(items):
    try:
        os.makedirs(os.path.dirname(CUSTOM_FILE), exist_ok=True)
        with open(CUSTOM_FILE, 'w', encoding='utf-8') as f:
            json.dump(items, f, ensure_ascii=False, indent=1)
    except Exception:
        pass

def list_models():
    """内置目录 + 额外扫描目录 + 自定义单文件 合并"""
    out = []
    seen = set()
    for d in [MODELS_DIR] + load_extra_dirs():
        if not os.path.isdir(d):
            continue
        for p in sorted(glob.glob(os.path.join(d, '*.gguf'))):
            name = os.path.basename(p)
            if name.startswith('mmproj') or p.lower() in seen:
                continue
            sz = os.path.getsize(p) // (1024*1024)
            mmproj = _match_mmproj(p)
            cust = os.path.normcase(os.path.dirname(p)) != os.path.normcase(MODELS_DIR)
            out.append([name, p, sz, mmproj, cust])
            seen.add(p.lower())
    for item in load_customs():
        p = item.get('path', '')
        if not p or not os.path.isfile(p) or p.lower() in seen:
            continue
        name = os.path.basename(p)
        if name.lower().startswith('mmproj'):
            continue
        sz = os.path.getsize(p) // (1024*1024)
        mmproj = _match_mmproj(p)
        out.append([name, p, sz, mmproj, True])
    return out

def run_cli(args, timeout=120):
    """调用 openclaw CLI（用系统 Node 24），显式带上 OPENCLAW_STATE_DIR 环境"""
    try:
        env = dict(os.environ)
        env.setdefault('OPENCLAW_STATE_DIR', os.path.dirname(CONFIG_PATH))
        r = subprocess.run([NODE_EXE, OPENCLAW_MJS] + args,
                           capture_output=True, text=True, timeout=timeout,
                           encoding='utf-8', errors='replace',
                           creationflags=subprocess.CREATE_NO_WINDOW, env=env)
        return r.returncode, (r.stdout or '') + (r.stderr or '')
    except Exception as e:
        return -1, str(e)

def get_llm_pid():
    """查 8080 端口的 llama-server PID"""
    try:
        out = subprocess.run(['powershell', '-NoProfile', '-Command',
            f'(Get-NetTCPConnection -LocalPort {PORT_LLM} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess)'],
            capture_output=True, text=True, timeout=15, encoding='utf-8', errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW).stdout.strip()
        return int(out) if out.isdigit() else None
    except Exception:
        return None

def stop_pid(pid):
    try:
        subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                       capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        return True
    except Exception:
        return False

# ============ 主窗口 ============
class App:
    def __init__(self, root):
        self.root = root
        root.title('OpenClaw 控制台 v3.1')
        root.geometry('980x540')
        root.minsize(760, 440)
        root.configure(bg='#2b2b2b')

        start_wf_server()  # 工作流编辑器 HTTP 服务

        self.models = list_models()
        self.model_proc = None   # 本程序启动的 llama-server 进程
        self.log_win = None
        self._log_win_text = None
        self._log_buffer = []
        self._log_buffers = {'console': [], 'llm': [], 'comfy': [], 'gw': [], 'textgen': []}
        self._log_texts = {}
        self._tail_pos = {}
        self._tails_on = True
        self._tail_started = set()
        self._mag_last = None
        self._mag_t = 0

        self._build_style()
        self._build_ui()
        self._refresh_models_combo()

        # 状态轮询 + 日志尾随（程序启动即开始，日志常驻缓冲）
        self._polling = True
        threading.Thread(target=self._poll_loop, daemon=True).start()
        self._start_tails()

    # ---------- 样式 ----------
    def _build_style(self):
        self.colors = {
            'bg':      '#2b2b2b',
            'panel':   '#383838',
            'fg':      '#e8e8e8',
            'dim':     '#9a9a9a',
            'green':   '#4caf50',
            'red':     '#e53935',
            'yellow':  '#fdd835',
            'accent':  '#e05a43',
            'border':  '#4a4a4a',
        }
        c = self.colors
        style = ttk.Style()
        try:
            style.theme_use('clam')
        except Exception:
            pass
        style.configure('TFrame', background=c['bg'])
        style.configure('Panel.TFrame', background=c['panel'])
        style.configure('TLabel', background=c['bg'], foreground=c['fg'], font=('Microsoft YaHei UI', 10))
        style.configure('Panel.TLabel', background=c['panel'], foreground=c['fg'], font=('Microsoft YaHei UI', 10))
        style.configure('Dim.TLabel', background=c['panel'], foreground=c['dim'], font=('Microsoft YaHei UI', 9))
        style.configure('TRadiobutton', background=c['panel'], foreground=c['fg'], font=('Microsoft YaHei UI', 10))
        style.configure('TButton', font=('Microsoft YaHei UI', 10), padding=(10, 4))
        # 顶栏小按钮（日志/启动所有/关闭所有）
        style.configure('Top.TButton', font=('Microsoft YaHei UI', 9), padding=(6, 1))
        style.configure('TopAccent.TButton', background='#1f883d', foreground='white',
                        font=('Microsoft YaHei UI', 9, 'bold'), padding=(8, 1))
        style.configure('TopStop.TButton', background='#7a3a35', foreground='white',
                        font=('Microsoft YaHei UI', 9), padding=(6, 1))
        style.map('Top.TButton', background=[('active', '#4f4f4f')])
        style.map('TopAccent.TButton', background=[('active', '#2ea043')])
        style.map('TopStop.TButton', background=[('active', '#93433d')])
        style.configure('Accent.TButton', background='#1f883d', foreground='white', font=('Microsoft YaHei UI', 10, 'bold'), padding=(12, 5))
        style.configure('Stop.TButton', background='#7a3a35', foreground='white', font=('Microsoft YaHei UI', 10), padding=(10, 4))
        style.configure('TCombobox', fieldbackground='#3a3a3a', background='#3a3a3a', foreground='#e8e8e8',
                        arrowcolor='#e8e8e8', font=('Microsoft YaHei UI', 10))
        style.configure('Dark.TCombobox',
                        fieldbackground='#f0f0f0', background='#f0f0f0',
                        foreground='#1a1a1a', arrowcolor='#1a1a1a',
                        selectbackground='#d0d0d0', selectforeground='#000000',
                        font=('Microsoft YaHei UI', 10))
        style.map('Dark.TCombobox',
                  fieldbackground=[('readonly', '#f0f0f0'), ('disabled', '#d8d8d8'), ('!disabled', '#f0f0f0')],
                  foreground=[('readonly', '#1a1a1a'), ('disabled', '#888888')],
                  selectbackground=[('readonly', '#d0d0d0')],
                  selectforeground=[('readonly', '#000000')])
        style.map('TButton', background=[('active', '#4f4f4f')])
        style.map('Accent.TButton', background=[('active', '#2ea043')])
        style.map('Stop.TButton', background=[('active', '#93433d')])
        style.configure('TCheckbutton', background=c['panel'], foreground=c['fg'], font=('Microsoft YaHei UI', 10))

    # ---------- UI ----------
    def _build_ui(self):
        c = self.colors
        root = self.root

        # ===== 顶栏：标题 + 状态灯 =====
        top = tk.Frame(root, bg=c['bg'])
        top.pack(fill='x', padx=12, pady=(12, 6))
        tk.Label(top, text='OpenClaw 控制台', bg=c['bg'], fg=c['fg'],
                 font=('Microsoft YaHei UI', 14, 'bold')).pack(side='left')
        # 最小化图标按钮：放标题右侧（不挤顶栏按钮区）
        self.btn_min = tk.Button(top, text='—', command=self.root.iconify,
                                 bg=c['bg'], fg=c['dim'], relief='flat', bd=0,
                                 font=('Segoe UI', 12), cursor='hand2',
                                 activebackground=c['panel'], activeforeground=c['fg'])
        self.btn_min.pack(side='left', padx=(10, 0))

        # 状态灯区域（模型/网关/生图 三灯并排）
        lamp = tk.Frame(top, bg=c['bg'])
        lamp.pack(side='right')
        self.lamp_llm = tk.Canvas(lamp, width=16, height=16, bg=c['bg'], highlightthickness=0)
        self.lamp_llm.pack(side='left', padx=(0, 4))
        tk.Label(lamp, text='模型', bg=c['bg'], fg=c['dim'], font=('Microsoft YaHei UI', 9)).pack(side='left', padx=(0, 10))
        self.lamp_gw = tk.Canvas(lamp, width=16, height=16, bg=c['bg'], highlightthickness=0)
        self.lamp_gw.pack(side='left', padx=(0, 4))
        tk.Label(lamp, text='网关', bg=c['bg'], fg=c['dim'], font=('Microsoft YaHei UI', 9)).pack(side='left', padx=(0, 10))
        self.lamp_comfy = tk.Canvas(lamp, width=16, height=16, bg=c['bg'], highlightthickness=0)
        self.lamp_comfy.pack(side='left', padx=(0, 4))
        tk.Label(lamp, text='生图', bg=c['bg'], fg=c['dim'], font=('Microsoft YaHei UI', 9)).pack(side='left')
        self.btn_log = ttk.Button(top, text='日志', style='Top.TButton', command=self.open_log_window)
        self.btn_log.pack(side='right', padx=(0, 8))
        # 启动在前、停止在后（side=right 先 pack 靠右，故先放停止）
        self.btn_all_stop = ttk.Button(top, text='⏹ 关闭所有', style='TopStop.TButton', command=self.stop_all)
        self.btn_all_stop.pack(side='right', padx=(0, 6))
        self.btn_all_start = ttk.Button(top, text='🚀 启动所有', style='TopAccent.TButton', command=self.start_all)
        self.btn_all_start.pack(side='right', padx=(0, 6))

        # ===== 全局硬件状态栏（所有页签可见）=====
        hwbar = tk.Frame(root, bg=c['panel'])
        hwbar.pack(fill='x', padx=10, pady=(0, 2))
        self.lbl_hw = tk.Label(hwbar, text='系统 --', bg=c['panel'], fg=c['dim'],
                               font=('Microsoft YaHei UI', 9), anchor='w')
        self.lbl_hw.pack(side='left', padx=8, pady=3)

        # ===== 主 Notebook（翻页式布局，适配小屏）=====
        nb = ttk.Notebook(root)
        nb.pack(fill='both', expand=True, padx=10, pady=(4, 10))
        st = ttk.Style()
        st.configure('TNotebook', background=c['bg'], borderwidth=0, tabmargins=(4, 4, 4, 0))
        st.configure('TNotebook.Tab', background=c['panel'], foreground=c['fg'],
                     padding=(18, 7), font=('Microsoft YaHei UI', 10))
        st.map('TNotebook.Tab', background=[('selected', c['accent'])],
               foreground=[('selected', 'white')])

        # ----- Tab 1：模型 -----
        tab_model = ttk.Frame(nb, style='Panel.TFrame')
        nb.add(tab_model, text=' 模型 ')
        ttk.Label(tab_model, text='本地模型服务 (llama-server)', style='Panel.TLabel',
                  font=('Microsoft YaHei UI', 11, 'bold')).grid(row=0, column=0, columnspan=4, sticky='w', padx=10, pady=(10, 6))

        ttk.Label(tab_model, text='推理模型', style='Panel.TLabel').grid(row=1, column=0, sticky='w', padx=10, pady=4)
        self.model_var = tk.StringVar()
        self.model_combo = ttk.Combobox(tab_model, textvariable=self.model_var, state='readonly', width=42,
                                      style='Dark.TCombobox')
        self.model_combo.grid(row=1, column=1, columnspan=2, sticky='we', padx=10, pady=4)
        self.btn_model_dirs = ttk.Button(tab_model, text='目录', width=4, command=self.manage_model_dirs)
        self.btn_model_dirs.grid(row=1, column=3, sticky='w', padx=(0, 10), pady=4)
        # ＋－ 已合并进「模型扫描目录」弹窗（自定义模型区），模型行只留「目录」

        # 运行模式下拉（4B CPU 常驻 / 4B GPU / 9B GPU / 自定义）——切换即保存，重启模型生效
        ttk.Label(tab_model, text='运行模式', style='Panel.TLabel').grid(row=2, column=0, sticky='w', padx=10, pady=(0, 4))
        self.llm_mode_var = tk.StringVar(value=LLM_MODE_DEFAULT)
        self.llm_mode_combo = ttk.Combobox(tab_model, textvariable=self.llm_mode_var,
                                            values=LLM_MODES, state='readonly', width=42, style='Dark.TCombobox')
        self.llm_mode_combo.grid(row=2, column=1, columnspan=2, sticky='we', padx=10, pady=(0, 4))
        self.llm_mode_combo.bind('<<ComboboxSelected>>', self._on_llm_mode_change)
        # 初始化时从 hw_profile.json 读取已保存的模式
        try:
            _prof = load_profile() or {}
            if _prof.get('llm_mode') in LLM_MODES:
                self.llm_mode_var.set(_prof['llm_mode'])
        except Exception:
            pass

        # 启动/停止/硬件配置/一键恢复：LoRA 下方一排紧贴（宽度统一、间隔统一）
        row6 = tk.Frame(tab_model, bg=c['panel'])
        row6.grid(row=7, column=0, columnspan=6, sticky='w', padx=10, pady=(4, 8))
        self.btn_llm_start = ttk.Button(row6, text='▶ 启动模型', width=11, style='Accent.TButton', command=self.start_llm)
        self.btn_llm_start.pack(side='left')
        self.btn_llm_stop = ttk.Button(row6, text='■ 停止模型', width=11, style='Stop.TButton', command=self.stop_llm)
        self.btn_llm_stop.pack(side='left', padx=(8, 0))
        self.btn_hw = ttk.Button(row6, text='硬件配置', width=11, command=self.show_hardware)
        self.btn_hw.pack(side='left', padx=(8, 0))
        self.btn_restore = ttk.Button(row6, text='一键恢复', width=11, command=self._restore_all)
        self.btn_restore.pack(side='left', padx=(8, 0))
        # （API 相关按钮已移到"远程API"标签页）

        # 生图默认模型（选即保存，MCP 生图时读取；与推理模型同一列对齐）
        self.gen_model_var = tk.StringVar()
        self.gen_model_combo = ttk.Combobox(tab_model, textvariable=self.gen_model_var, width=52,
                                            style='Dark.TCombobox')
        ttk.Label(tab_model, text='生图默认模型', style='Panel.TLabel').grid(row=3, column=0, sticky='w', padx=(10, 8), pady=(0, 4))
        self.gen_model_combo.grid(row=3, column=1, columnspan=2, sticky='we', padx=(6, 8), pady=(0, 4))
        self.gen_model_combo.bind('<<ComboboxSelected>>', self._save_gen_model)
        self.btn_gen_refresh = ttk.Button(tab_model, text='刷新', width=4, command=self._refresh_gen_models)
        self.btn_gen_refresh.grid(row=3, column=3, sticky='w')
        self._refresh_gen_models(initial=True)

        # 生图默认 LoRA（两排：Krea2 / Z-Image，输入框与上面同列对齐）
        self.lora_krea2_var = tk.StringVar()
        self.lora_krea2_combo = ttk.Combobox(tab_model, textvariable=self.lora_krea2_var, width=34,
                                             style='Dark.TCombobox')
        ttk.Label(tab_model, text='Krea2', style='Panel.TLabel').grid(row=4, column=0, sticky='w', padx=(10, 8), pady=(0, 4))
        self.lora_krea2_combo.grid(row=4, column=1, columnspan=2, sticky='we', padx=(6, 8), pady=(0, 4))
        self.lora_krea2_combo.bind('<<ComboboxSelected>>', lambda e: self._save_lora('krea2'))
        self.btn_lora_refresh = ttk.Button(tab_model, text='刷新', width=4, command=self._refresh_loras)
        self.btn_lora_refresh.grid(row=4, column=3, sticky='w')

        self.lora_zimg_var = tk.StringVar()
        self.lora_zimg_combo = ttk.Combobox(tab_model, textvariable=self.lora_zimg_var, width=34,
                                            style='Dark.TCombobox')
        ttk.Label(tab_model, text='Z-Image', style='Panel.TLabel').grid(row=5, column=0, sticky='w', padx=(10, 8), pady=(0, 8))
        self.lora_zimg_combo.grid(row=5, column=1, columnspan=2, sticky='we', padx=(6, 8), pady=(0, 8))
        self.lora_zimg_combo.bind('<<ComboboxSelected>>', lambda e: self._save_lora('zimg'))
        self._refresh_loras(initial=True)

        # 生图工作流（选即保存，MCP 生图时读取；列出 workflows 目录全部模板含自建）
        self.gen_wf_var = tk.StringVar()
        self.gen_wf_combo = ttk.Combobox(tab_model, textvariable=self.gen_wf_var, width=34,
                                         style='Dark.TCombobox')
        ttk.Label(tab_model, text='生图工作流', style='Panel.TLabel').grid(row=6, column=0, sticky='w', padx=(10, 8), pady=(0, 8))
        self.gen_wf_combo.grid(row=6, column=1, columnspan=2, sticky='we', padx=(6, 8), pady=(0, 8))
        self.gen_wf_combo.bind('<<ComboboxSelected>>', self._save_gen_workflow)
        self.btn_wf_refresh = ttk.Button(tab_model, text='刷新', width=4, command=self._refresh_gen_workflows)
        self.btn_wf_refresh.grid(row=6, column=3, sticky='w')
        self.btn_wf_open = ttk.Button(tab_model, text='打开', width=4, command=self.open_wf_dir)
        self.btn_wf_open.grid(row=6, column=4, sticky='w', padx=(4, 0))
        self._refresh_gen_workflows(initial=True)

        tab_model.columnconfigure(1, weight=1)

        # ----- Tab 2：ComfyUI（页签名：生图）-----
        tab_comfy = ttk.Frame(nb, style='Panel.TFrame')
        ttk.Label(tab_comfy, text='生图服务（ComfyUI）', style='Panel.TLabel',
                  font=('Microsoft YaHei UI', 11, 'bold')).grid(row=0, column=0, columnspan=4, sticky='w', padx=10, pady=(14, 6))

        comfy_row = tk.Frame(tab_comfy, bg='#383838')
        comfy_row.grid(row=1, column=0, columnspan=6, sticky='we', padx=10, pady=(0, 10))
        # 启动类（重启）在前、停止在后；按钮统一宽度与间隔
        self.btn_comfy_restart = ttk.Button(comfy_row, text='▶ 重启', width=10, style='Accent.TButton', command=self.restart_comfy)
        self.btn_comfy_restart.pack(side='left')
        self.btn_comfy_stop = ttk.Button(comfy_row, text='■ 停止', width=10, style='Stop.TButton', command=self.stop_comfy)
        self.btn_comfy_stop.pack(side='left', padx=(8, 0))
        ttk.Button(comfy_row, text='🛠 工作流', width=10, command=open_workflow_editor).pack(side='left', padx=(8, 0))
        # 远程地址做成可点击超链接
        self.comfy_url_lbl = tk.Label(comfy_row, text='远程: https://game.tail8c09f5.ts.net:8443',
                                      bg='#383838', fg='#6cb4ee', font=('Microsoft YaHei UI', 9),
                                      cursor='hand2')
        self.comfy_url_lbl.pack(side='right', padx=10)
        self.comfy_url_lbl.bind('<Button-1>', lambda e: self._open_comfy_remote())

        # ComfyUI 工具行（清理 + 文件夹）
        comfy_tools_row = tk.Frame(tab_comfy, bg='#383838')
        comfy_tools_row.grid(row=2, column=0, columnspan=6, sticky='we', padx=10, pady=(0, 6))
        self.btn_comfy_ram = ttk.Button(comfy_tools_row, text='清理内存', width=10, command=self.cleanup_ram)
        self.btn_comfy_ram.pack(side='left')
        self.btn_comfy_vram = ttk.Button(comfy_tools_row, text='清理显存', width=10, command=self.cleanup_vram)
        self.btn_comfy_vram.pack(side='left', padx=(8, 0))
        self.btn_comfy_upload = ttk.Button(comfy_tools_row, text='上传文件夹', width=10, command=self.open_upload_dir)
        self.btn_comfy_upload.pack(side='left', padx=(8, 0))
        self.btn_comfy_output = ttk.Button(comfy_tools_row, text='生成文件夹', width=10, command=self.open_output_dir)
        self.btn_comfy_output.pack(side='left', padx=(8, 0))
        tab_comfy.columnconfigure(1, weight=1)

        # 生图方式（固定 ComfyUI / 自动选择）——禁内置 image_generate 防弹工具选择框
        ttk.Separator(tab_comfy, orient='horizontal').grid(row=3, column=0, columnspan=6, sticky='we', padx=10, pady=(8, 6))
        ttk.Label(tab_comfy, text='生图方式', style='Panel.TLabel',
                  font=('Microsoft YaHei UI', 11, 'bold')).grid(row=4, column=0, columnspan=6, sticky='w', padx=10, pady=(2, 4))
        self.gen_mode_var = tk.StringVar(value=self._gen_mode_current())
        gf = ttk.Frame(tab_comfy, style='Panel.TFrame')
        gf.grid(row=5, column=0, columnspan=6, sticky='w', padx=10, pady=2)
        ttk.Radiobutton(gf, text='固定 ComfyUI（推荐）', value='comfy', variable=self.gen_mode_var,
                        style='TRadiobutton').pack(side='left', padx=(0, 16))
        ttk.Radiobutton(gf, text='自动选择（允许内置 image_generate）', value='auto', variable=self.gen_mode_var,
                        style='TRadiobutton').pack(side='left')
        ttk.Label(tab_comfy, text='固定 ComfyUI：禁用内置生图工具选择器，微信/各通道生图直接走本地 ComfyUI（comfyui__generate_image）',
                  style='Dim.TLabel').grid(row=6, column=0, columnspan=6, sticky='w', padx=10, pady=2)
        ttk.Button(tab_comfy, text='保存生图方式', style='Accent.TButton',
                   command=self.save_gen_mode).grid(row=7, column=0, sticky='we', padx=10, pady=(6, 10))

        # ----- Tab 3：网关 & 广域网 -----
        tab_gw = ttk.Frame(nb, style='Panel.TFrame')
        nb.add(tab_gw, text=' 网关 ')
        ttk.Label(tab_gw, text='OpenClaw 网关', style='Panel.TLabel',
                  font=('Microsoft YaHei UI', 11, 'bold')).grid(row=0, column=0, columnspan=4, sticky='w', padx=10, pady=(10, 6))

        self.gw_info = tk.StringVar(value='—')
        ttk.Label(tab_gw, textvariable=self.gw_info, style='Dim.TLabel').grid(row=1, column=0, columnspan=4, sticky='w', padx=10, pady=4)

        self.btn_gw_start = ttk.Button(tab_gw, text='▶  启动网关', style='Accent.TButton', command=self.start_gw)
        self.btn_gw_start.grid(row=2, column=0, sticky='we', padx=10, pady=(4, 10))
        self.btn_gw_stop = ttk.Button(tab_gw, text='■  停止网关', style='Stop.TButton', command=self.stop_gw)
        self.btn_gw_stop.grid(row=2, column=1, sticky='we', padx=(0, 10), pady=(4, 10))
        self.btn_dash = ttk.Button(tab_gw, text='打开控制台', command=self.open_dashboard)
        self.btn_dash.grid(row=2, column=2, sticky='we', padx=(0, 6), pady=(4, 10))
        self.btn_copy_key = ttk.Button(tab_gw, text='复制密钥', command=self.copy_gw_key)
        self.btn_copy_key.grid(row=2, column=3, sticky='we', padx=(0, 10), pady=(4, 10))
        tab_gw.columnconfigure(2, weight=1)
        nb.add(tab_comfy, text=' 生图 ')

        # ----- Tab：傻酒馆 text-generation-webui -----
        tab_textgen = ttk.Frame(nb, style='Panel.TFrame')
        nb.add(tab_textgen, text=' 傻酒馆 ')
        ttk.Label(tab_textgen, text='SillyTavern 酒馆前端', style='Panel.TLabel',
                  font=('Microsoft YaHei UI', 11, 'bold')).grid(row=0, column=0, columnspan=4, sticky='w', padx=10, pady=(14, 6))

        # 状态行
        st_row = tk.Frame(tab_textgen, bg='#383838')
        st_row.grid(row=1, column=0, columnspan=4, sticky='we', padx=10, pady=(0, 8))
        self.lamp_textgen = tk.Canvas(st_row, width=16, height=16, bg='#383838', highlightthickness=0)
        self.lamp_textgen.pack(side='left', padx=(2, 6))
        self.textgen_info = tk.StringVar(value='未运行')
        ttk.Label(st_row, textvariable=self.textgen_info, style='Dim.TLabel').pack(side='left')

        # 路径行（隐藏，不用textgen后端）
        self._tg_dir_lbl = ttk.Label(tab_textgen, text='安装目录', style='Panel.TLabel')
        self._tg_dir_lbl.grid(row=2, column=0, sticky='w', padx=(10, 4), pady=4)
        self.textgen_dir_var = tk.StringVar(value=TEXTGEN_DIR)
        self._tg_dir_entry = ttk.Entry(tab_textgen, textvariable=self.textgen_dir_var, width=46)
        self._tg_dir_entry.grid(row=2, column=1, columnspan=2, sticky='we', padx=4, pady=4)
        self._tg_dir_btn = ttk.Button(tab_textgen, text='浏览…', command=lambda: self._browse_textgen_dir())
        self._tg_dir_btn.grid(row=2, column=3, sticky='we', padx=(0, 10), pady=4)

        # 按钮行（隐藏）
        tg_row = tk.Frame(tab_textgen, bg='#383838')
        tg_row.grid(row=3, column=0, columnspan=4, sticky='we', padx=10, pady=(4, 6))
        self._tg_row = tg_row
        self.btn_textgen_start = ttk.Button(tg_row, text='\u25b6 启动', width=10, style='Accent.TButton', command=self.start_textgen)
        self.btn_textgen_start.pack(side='left')
        self.btn_textgen_stop = ttk.Button(tg_row, text='\u25a0 停止', width=10, style='Stop.TButton', command=self.stop_textgen)
        self.btn_textgen_stop.pack(side='left', padx=(8, 0))
        ttk.Button(tg_row, text='\U0001f595 打开界面', width=10, command=self.open_textgen).pack(side='left', padx=(8, 0))
        ttk.Button(tg_row, text='\U0001f4c1 文件夹', width=10, command=self.open_textgen_dir).pack(side='left', padx=(8, 0))

        # 模式切换行
        mode_row = tk.Frame(tab_textgen, bg='#383838')
        mode_row.grid(row=4, column=0, columnspan=4, sticky='we', padx=10, pady=(4, 2))
        self._mode_row = mode_row
        ttk.Label(mode_row, text='模式：', style='Panel.TLabel').pack(side='left')
        self.tg_mode_var = tk.StringVar(value='remote')
        ttk.Radiobutton(mode_row, text='复用 llama-server', value='remote', variable=self.tg_mode_var,
                        command=self._tg_mode_changed).pack(side='left', padx=(4, 12))
        ttk.Radiobutton(mode_row, text='本地加载模型', value='local', variable=self.tg_mode_var,
                        command=self._tg_mode_changed).pack(side='left', padx=(0, 12))
        ttk.Radiobutton(mode_row, text='在线 API', value='online', variable=self.tg_mode_var,
                        command=self._tg_mode_changed).pack(side='left')

        # SillyTavern 目录行
        st_dir_row = tk.Frame(tab_textgen, bg='#383838')
        st_dir_row.grid(row=2, column=0, columnspan=4, sticky='we', padx=10, pady=(4, 0))
        ttk.Label(st_dir_row, text='酒馆目录', style='Panel.TLabel').pack(side='left')
        self.st_dir_var = tk.StringVar(value=ST_DIR)
        ttk.Entry(st_dir_row, textvariable=self.st_dir_var, width=50).pack(side='left', padx=(6, 4))
        ttk.Button(st_dir_row, text='浏览…', width=6, command=self._browse_st_dir).pack(side='left')

        # SillyTavern 按钮行
        st_row = tk.Frame(tab_textgen, bg='#383838')
        st_row.grid(row=3, column=0, columnspan=4, sticky='we', padx=10, pady=(4, 6))
        ttk.Button(st_row, text='\U0001f3e8 启动酒馆', width=10, style='Accent.TButton', command=self.start_sillytavern).pack(side='left')
        ttk.Button(st_row, text='\U0001f5d4 停止', width=10, command=self.stop_sillytavern).pack(side='left', padx=(8, 0))
        ttk.Button(st_row, text='\U0001f310 打开页面', width=10, command=self.open_sillytavern).pack(side='left', padx=(8, 0))
        self.st_info = tk.StringVar(value='未运行')
        ttk.Label(st_row, textvariable=self.st_info, style='Dim.TLabel').pack(side='left', padx=(12, 0))

        # 后端模式切换
        backend_row = tk.Frame(tab_textgen, bg='#383838')
        backend_row.grid(row=4, column=0, columnspan=4, sticky='we', padx=10, pady=(4, 6))
        ttk.Label(backend_row, text='后端模式：', style='Panel.TLabel').pack(side='left')
        self.st_backend_var = tk.StringVar(value='local')
        ttk.Radiobutton(backend_row, text='本地 llama-server (8080)', value='local', variable=self.st_backend_var).pack(side='left', padx=(4, 12))
        ttk.Radiobutton(backend_row, text='在线 API', value='online', variable=self.st_backend_var).pack(side='left', padx=(0, 12))
        ttk.Label(backend_row, text='API 地址:', style='Panel.TLabel').pack(side='left')
        self.st_api_url_var = tk.StringVar(value=_PATHS.get('st_online_api_url', 'https://api.openai.com/v1'))
        ttk.Entry(backend_row, textvariable=self.st_api_url_var, width=28).pack(side='left', padx=(4, 4))
        ttk.Label(backend_row, text='Key:', style='Panel.TLabel').pack(side='left')
        self.st_api_key_var = tk.StringVar(value=_PATHS.get('st_online_api_key', ''))
        ttk.Entry(backend_row, textvariable=self.st_api_key_var, width=20, show='*').pack(side='left', padx=(4, 4))
        ttk.Button(backend_row, text='保存', width=6, command=self._save_st_backend).pack(side='left', padx=(4, 0))
        # 隐藏 textgen 后端 UI（不用了，只留酒馆前端）
        try:
            self._tg_dir_lbl.grid_remove()
            self._tg_dir_entry.grid_remove()
            self._tg_dir_btn.grid_remove()
            self._tg_row.grid_remove()
            self._mode_row.grid_remove()
            self.tg_model_row.grid_remove()
            if hasattr(self, 'tg_api_row'):
                self.tg_api_row.grid_remove()
            if hasattr(self, '_tg_table_row'):
                self._tg_table_row.grid_remove()
            if hasattr(self, '_tg_btn_row'):
                self._tg_btn_row.grid_remove()
        except Exception as e:
            self.log('隐藏textgen UI: ' + str(e))

        # 本地模型选择行（仅本地模式可见）
        self.tg_model_row = tk.Frame(tab_textgen, bg='#383838')
        self.tg_model_row.grid(row=7, column=0, columnspan=4, sticky='we', padx=10, pady=(2, 0))
        ttk.Label(self.tg_model_row, text='模型', style='Panel.TLabel').pack(side='left')
        self.tg_model_combo = ttk.Combobox(self.tg_model_row, width=40, state='readonly')
        self.tg_model_combo.pack(side='left', padx=(6, 6))
        ttk.Button(self.tg_model_row, text='刷新', width=4, command=self._tg_refresh_models).pack(side='left')

        # 在线 API 配置行
        self.tg_api_row = tk.Frame(tab_textgen, bg='#383838')
        self.tg_api_row.grid(row=5, column=0, columnspan=4, sticky='we', padx=10, pady=(2, 0))
        ttk.Label(self.tg_api_row, text='API 地址', style='Panel.TLabel').pack(side='left')
        self.tg_api_url_var = tk.StringVar(value=_PATHS.get('textgen_api_url', 'https://api.openai.com/v1'))
        ttk.Entry(self.tg_api_row, textvariable=self.tg_api_url_var, width=32).pack(side='left', padx=(6, 8))
        ttk.Label(self.tg_api_row, text='Key', style='Panel.TLabel').pack(side='left')
        self.tg_api_key_var = tk.StringVar(value=_PATHS.get('textgen_api_key', ''))
        ttk.Entry(self.tg_api_row, textvariable=self.tg_api_key_var, width=24, show='*').pack(side='left', padx=(6, 8))
        ttk.Label(self.tg_api_row, text='模型', style='Panel.TLabel').pack(side='left')
        self.tg_api_model_var = tk.StringVar(value=_PATHS.get('textgen_api_model', 'gpt-4o-mini'))
        ttk.Entry(self.tg_api_row, textvariable=self.tg_api_model_var, width=18).pack(side='left', padx=(6, 0))

        # 已保存配置表格
        tree_row = tk.Frame(tab_textgen, bg='#383838')
        tree_row.grid(row=6, column=0, columnspan=4, sticky='we', padx=10, pady=(4, 0))
        cols = ('name', 'url', 'model')
        self.tg_profiles_tree = ttk.Treeview(tree_row, columns=cols, show='headings', height=4)
        self.tg_profiles_tree.heading('name', text='名称')
        self.tg_profiles_tree.heading('url', text='API 地址')
        self.tg_profiles_tree.heading('model', text='模型')
        self.tg_profiles_tree.column('name', width=180, anchor='w')
        self.tg_profiles_tree.column('url', width=280, anchor='w')
        self.tg_profiles_tree.column('model', width=140, anchor='w')
        self.tg_profiles_tree.pack(side='left', fill='x', expand=True)
        self.tg_profiles_scroll = ttk.Scrollbar(tree_row, orient='vertical', command=self.tg_profiles_tree.yview)
        self.tg_profiles_tree.configure(yscrollcommand=self.tg_profiles_scroll.set)
        self.tg_profiles_scroll.pack(side='left', fill='y')
        self.tg_profiles_tree.bind('<Double-1>', lambda e: self._tg_load_profile())

        # 按钮行
        btn_row = tk.Frame(tab_textgen, bg='#383838')
        btn_row.grid(row=7, column=0, columnspan=4, sticky='we', padx=10, pady=(4, 0))
        ttk.Button(btn_row, text='测试连接', width=10, command=self._tg_api_test).pack(side='left', padx=(0, 6))
        ttk.Button(btn_row, text='保存到表格', width=10, command=self._tg_api_save).pack(side='left', padx=(0, 6))
        ttk.Button(btn_row, text='删除选中', width=10, command=self._tg_profile_delete).pack(side='left', padx=(0, 20))
        ttk.Label(btn_row, text='测试状态:', style='Dim.TLabel').pack(side='left')
        self._tg_lamp = tk.Canvas(btn_row, width=16, height=16, bg='#383838', highlightthickness=0)
        self._tg_lamp.pack(side='left', padx=(4, 0))
        self._tg_set_lamp('gray', '未测试')

        self.tg_hint_var = tk.StringVar(value='')
        self.tg_hint_lbl = ttk.Label(tab_textgen, textvariable=self.tg_hint_var, style='Dim.TLabel')
        self.tg_hint_lbl.grid(row=8, column=0, columnspan=4, sticky='w', padx=10, pady=(6, 4))
        tab_textgen.columnconfigure(1, weight=1)
        self._tg_mode_changed()


        # 广域网（Tailscale 远程访问 / 一键复原）
        ttk.Separator(tab_gw, orient='horizontal').grid(row=3, column=0, columnspan=4, sticky='we', padx=10, pady=(0, 6))
        ttk.Label(tab_gw, text='广域网远程访问（Tailscale）', style='Panel.TLabel',
                  font=('Microsoft YaHei UI', 11, 'bold')).grid(row=4, column=0, columnspan=4, sticky='w', padx=10, pady=(6, 4))
        self.wan_info = tk.StringVar(value='未检测 · 点「一键复原广域网」自动配置')
        ttk.Label(tab_gw, textvariable=self.wan_info, style='Dim.TLabel').grid(row=5, column=0, columnspan=4, sticky='w', padx=10, pady=4)
        self.btn_wan_restore = ttk.Button(tab_gw, text='🔧 一键复原广域网', style='Accent.TButton', command=self.wan_restore)
        self.btn_wan_restore.grid(row=6, column=0, sticky='we', padx=10, pady=(4, 10))
        self.btn_wan_copy = ttk.Button(tab_gw, text='复制远程地址', command=self.wan_copy_url)
        self.btn_wan_copy.grid(row=6, column=1, sticky='we', padx=(0, 6), pady=(4, 10))
        self.btn_wan_open = ttk.Button(tab_gw, text='打开远程地址', command=self.wan_open_url)
        self.btn_wan_open.grid(row=6, column=2, sticky='we', padx=(0, 10), pady=(4, 10))

        # ----- Tab 4：设置（路径 + 自启） -----
        tab_set = ttk.Frame(nb, style='Panel.TFrame')
        nb.add(tab_set, text=' 设置 ')
        ttk.Label(tab_set, text='安装路径设置（重装系统后在此改路径，保存即复原）', style='Panel.TLabel',
                  font=('Microsoft YaHei UI', 11, 'bold')).grid(row=0, column=0, columnspan=5, sticky='w', padx=10, pady=(10, 4))
        self.path_vars = {}
        _row = 1
        for key, label in [('llama_dir', 'LLM 模型目录'), ('comfy_root', 'ComfyUI 根目录'), ('openclaw_data', 'OpenClaw 数据目录'), ('textgen_dir', '傻酒馆目录')]:
            ttk.Label(tab_set, text=label, style='Panel.TLabel').grid(row=_row, column=0, sticky='w', padx=(10, 4), pady=3)
            v = tk.StringVar(value=_PATHS.get(key, ''))
            self.path_vars[key] = v
            ttk.Entry(tab_set, textvariable=v, width=46).grid(row=_row, column=1, columnspan=3, sticky='we', padx=4, pady=3)
            ttk.Button(tab_set, text='浏览…', width=6,
                       command=lambda k=key, vv=v: self._browse_path(k, vv)).grid(row=_row, column=4, sticky='we', padx=(0, 10), pady=3)
            _row += 1
        ttk.Button(tab_set, text='保存路径并应用', style='Accent.TButton',
                   command=self.save_paths_ui).grid(row=_row, column=1, columnspan=3, sticky='we', padx=4, pady=(6, 10))

        ttk.Separator(tab_set, orient='horizontal').grid(row=_row + 1, column=0, columnspan=5, sticky='we', padx=10, pady=(6, 6))
        ttk.Label(tab_set, text='开机自启', style='Panel.TLabel',
                  font=('Microsoft YaHei UI', 11, 'bold')).grid(row=_row + 2, column=0, columnspan=5, sticky='w', padx=10, pady=(6, 2))
        # 开机自启：勾选框 + 中间状态灯 + 文字（灯：勾=绿 未勾=红）
        def _mk_autostart(row, col, colspan, text, var, cmd, lamp_attr):
            f = ttk.Frame(tab_set, style='Panel.TFrame')
            f.grid(row=row, column=col, columnspan=colspan, sticky='w', padx=10, pady=2)
            ttk.Checkbutton(f, text='', variable=var, command=cmd,
                            style='TCheckbutton').pack(side='left')
            cv = tk.Canvas(f, width=12, height=12, bg=c['panel'], highlightthickness=0)
            cv.pack(side='left', padx=(4, 6))
            ttk.Label(f, text=text, style='Panel.TLabel').pack(side='left')
            f.winfo_children()[-1].bind('<Button-1>',
                                        lambda e, vv=var: vv.set(not vv.get()))
            setattr(self, lamp_attr, cv)
            return cv

        self.autostart_var = tk.BooleanVar(value=self._autostart_exists())
        self.lamp_auto_model = _mk_autostart(_row + 3, 0, 2,
            '开机自动启动本地模型服务（跟随当前选择的模型）',
            self.autostart_var, self.toggle_autostart, 'lamp_auto_model')
        self.gw_autostart_var = tk.BooleanVar(value=self._gw_autostart_exists())
        self.lamp_auto_gw = _mk_autostart(_row + 3, 3, 2,
            '开机自动启动 OpenClaw 网关',
            self.gw_autostart_var, self.toggle_gw_autostart, 'lamp_auto_gw')
        self.comfy_autostart_var = tk.BooleanVar(value=self._comfy_autostart_exists())
        self.lamp_auto_comfy = _mk_autostart(_row + 4, 0, 5,
            '开机自动启动 ComfyUI 生图服务',
            self.comfy_autostart_var, self.toggle_comfy_autostart, 'lamp_auto_comfy')

        # 自启状态灯更新：勾选=绿，未勾=红
        def _update_auto_lamps(*_a):
            for cv, var in ((self.lamp_auto_model, self.autostart_var),
                            (self.lamp_auto_gw, self.gw_autostart_var),
                            (self.lamp_auto_comfy, self.comfy_autostart_var)):
                cv.delete('all')
                cv.create_oval(2, 2, 10, 10,
                               fill='#52C41A' if var.get() else '#EA6668', outline='')
        self.autostart_var.trace_add('write', _update_auto_lamps)
        self.gw_autostart_var.trace_add('write', _update_auto_lamps)
        self.comfy_autostart_var.trace_add('write', _update_auto_lamps)
        _update_auto_lamps()
        tab_set.columnconfigure(3, weight=1)

        # ----- Tab 6：远程 API（FreeLLMAPI 聚合免费模型） -----
        tab_rapi = ttk.Frame(nb, style='Panel.TFrame')
        nb.add(tab_rapi, text=' 远程API ')
        ttk.Label(tab_rapi, text='远程 API 网关（FreeLLMAPI 聚合免费模型）', style='Panel.TLabel',
                  font=('Microsoft YaHei UI', 12, 'bold')).grid(row=0, column=0, columnspan=3, sticky='w', padx=10, pady=(12, 6))
        ttk.Label(tab_rapi, text='本地网关地址：http://127.0.0.1:3001/v1', style='Dim.TLabel').grid(row=1, column=0, columnspan=3, sticky='w', padx=10, pady=2)
        ttk.Label(tab_rapi, text='状态：', style='Dim.TLabel').grid(row=2, column=0, sticky='w', padx=10, pady=2)
        self.rapi_status = tk.Canvas(tab_rapi, width=12, height=12, bg=c['panel'], highlightthickness=0)
        self.rapi_status.grid(row=2, column=1, sticky='w')
        self.rapi_lamp = self.rapi_status.create_oval(2, 2, 12, 12, fill='#555', outline='')
        ttk.Label(tab_rapi, text='灰=未运行  绿=运行中', style='Dim.TLabel').grid(row=2, column=2, sticky='w', padx=(4, 0))
        row_rapi = tk.Frame(tab_rapi, bg=c['panel'])
        row_rapi.grid(row=3, column=0, columnspan=3, sticky='w', padx=10, pady=10)
        ttk.Button(row_rapi, text='打开后台', width=12, command=lambda: webbrowser.open('http://127.0.0.1:3001/')).pack(side='left')
        ttk.Button(row_rapi, text='免费API', width=12, command=self.start_freelmapi).pack(side='left', padx=(8, 0))
        ttk.Button(row_rapi, text='关API', width=12, command=self.stop_freelmapi).pack(side='left', padx=(8, 0))
        ttk.Separator(tab_rapi, orient='horizontal').grid(row=4, column=0, columnspan=3, sticky='ew', padx=10, pady=(6, 6))
        ttk.Label(tab_rapi, text='免费 key 注册入口（点了去官网注册，拿 key 回后台 Keys 页填）：', style='Panel.TLabel').grid(row=5, column=0, columnspan=3, sticky='w', padx=10, pady=(4, 6))
        links = [
            ('Groq（快，免费量大）', 'https://console.groq.com/keys'),
            ('SambaNova（免费）', 'https://cloud.sambanova.ai/'),
            ('Google Gemini（免费）', 'https://aistudio.google.com/apikey'),
            ('OpenRouter（免费模型）', 'https://openrouter.ai/keys'),
            ('NVIDIA NIM（免费）', 'https://build.nvidia.com/'),
            ('Cerebras（快）', 'https://cloud.cerebras.ai/'),
        ]
        for i, (name, url) in enumerate(links):
            r = 6 + i
            ttk.Label(tab_rapi, text=name, style='Dim.TLabel').grid(row=r, column=0, sticky='w', padx=(20, 4), pady=2)
            ttk.Button(tab_rapi, text=url, width=42, style='Link.TButton',
                       command=lambda u=url: webbrowser.open(u)).grid(row=r, column=1, columnspan=2, sticky='w', pady=2)
        self._rapi_poll()

        # ----- Tab 5：调试（环境体检） -----
        tab_dbg = ttk.Frame(nb, style='Panel.TFrame')
        nb.add(tab_dbg, text=' 调试 ')
        ttk.Label(tab_dbg, text='环境体检 & 调试', style='Panel.TLabel',
                  font=('Microsoft YaHei UI', 11, 'bold')).grid(row=0, column=0, columnspan=10, sticky='w', padx=10, pady=(10, 4))

        self.env_frames = {}   # kind -> (lamp_canvas, btn)
        env_row = ttk.Frame(tab_dbg)
        env_row.grid(row=1, column=0, columnspan=10, sticky='we', padx=10, pady=(0, 4))
        kinds = [('node', 'Node.js'), ('openclaw', 'OpenClaw'), ('llama', 'llama.cpp'),
                 ('models', '模型文件'), ('gpu', 'GPU体检'), ('cfg', '网关配置'),
                 ('tailscale', 'Tailscale')]
        for i, (kind, label) in enumerate(kinds):
            f = ttk.Frame(env_row)
            f.grid(row=i // 3, column=i % 3, padx=(0, 18), pady=2, sticky='w')
            lamp = tk.Canvas(f, width=14, height=14, bg=c['panel'], highlightthickness=0)
            lamp.pack(side='left', padx=(0, 4))
            ttk.Label(f, text=label, style='Panel.TLabel', width=12, anchor='w').pack(side='left')
            btn = ttk.Button(f, text='…', width=4, command=lambda k=kind: self.env_action(k))
            btn.pack(side='left', padx=(4, 0))
            self.env_frames[kind] = (lamp, btn)
        ttk.Label(tab_dbg, text='状态灯：●绿=就绪  ●红=缺失  ●灰=未检测', style='Dim.TLabel').grid(
            row=2, column=0, columnspan=10, sticky='w', padx=10, pady=(0, 4))

        dbg_row = ttk.Frame(tab_dbg)
        dbg_row.grid(row=3, column=0, columnspan=10, sticky='we', padx=10, pady=(0, 10))
        # 启动类（全清重启/重启网关）放最前面；统一按钮宽度对齐
        _BTN_W = 13
        ttk.Button(dbg_row, text='🔧 全清重启', width=_BTN_W, style='Accent.TButton', command=self.clean_restart_gw).pack(side='left', padx=(0, 8))
        ttk.Button(dbg_row, text='重启网关', width=_BTN_W, command=self.restart_gw).pack(side='left', padx=(0, 8))
        ttk.Button(dbg_row, text='测试模型API', width=_BTN_W, command=self.test_llm_api).pack(side='left', padx=(0, 8))
        ttk.Button(dbg_row, text='测试网关', width=_BTN_W, command=self.test_gw).pack(side='left', padx=(0, 8))
        ttk.Button(dbg_row, text='复制日志', width=_BTN_W, command=self.copy_log).pack(side='left', padx=(0, 8))
        dbg_row2 = ttk.Frame(tab_dbg)
        dbg_row2.grid(row=4, column=0, columnspan=10, sticky='we', padx=10, pady=(0, 10))
        ttk.Button(dbg_row2, text='🛜 启动Tailscale', width=_BTN_W, command=self.tailscale_start).pack(side='left', padx=(0, 8))
        ttk.Button(dbg_row2, text='⚙️ Tailscale一键', width=_BTN_W, style='Accent.TButton', command=self.tailscale_onekey).pack(side='left', padx=(0, 8))
        ttk.Button(dbg_row2, text='清理内存', width=_BTN_W, command=self.cleanup_ram).pack(side='left', padx=(0, 8))
        ttk.Button(dbg_row2, text='清理显存', width=_BTN_W, command=self.cleanup_vram).pack(side='left', padx=(0, 8))

        # ===== 日志区 =====（已移至独立磁吸窗口，顶部“日志”按钮打开）

        self.log('OpenClaw 控制台 v3.1 启动')
        self.log(f'模型目录: {MODELS_DIR}')
        self.log(f'网关: {DASH_URL}')

        # 窗口贴屏幕底边（固定高度，避免 Notebook 请求高度撑爆窗口贴顶）
        # 默认 840 宽；若 ui_state.json 有上次记忆的尺寸/位置则优先恢复
        try:
            _sw = root.winfo_screenwidth()
            _sh = root.winfo_screenheight()
            _saved = {}
            try:
                if os.path.isfile(_UI_STATE_CFG):
                    with io.open(_UI_STATE_CFG, 'r', encoding='utf-8') as _f:
                        _saved = json.load(_f)
            except Exception:
                _saved = {}
            _win_w = int(_saved.get('w', 840))
            _win_h = int(_saved.get('h', 540))
            _win_w = min(_win_w, _sw - 40)
            _win_h = min(_win_h, _sh - 90)
            _win_w = max(_win_w, 760)
            _win_h = max(_win_h, 440)
            if 'x' in _saved and 'y' in _saved:
                _x = int(_saved.get('x', 0))
                _y = int(_saved.get('y', 0))
            else:
                _x = max((_sw - _win_w) // 2, 0)
                _y = max(_sh - _win_h - 56, 24)
            root.geometry(f'{_win_w}x{_win_h}+{_x}+{_y}')
        except Exception:
            pass
        self.root.after(300, self._refresh_env)

    # ---------- 日志 ----------
    def log(self, msg, key='console'):
        line = f'[{time.strftime("%H:%M:%S")}] {msg}'
        with log_lock:
            bufs = getattr(self, '_log_buffers', None)
            if bufs is None:
                self._log_buffers = {'console': [], 'llm': [], 'comfy': [], 'gw': [], 'textgen': []}
                bufs = self._log_buffers
            buf = bufs.setdefault(key, [])
            buf.append(line)
            if len(buf) > 3000:
                buf[:] = buf[-3000:]
            if key == 'console':
                self._log_buffer = bufs['console']
        # 控件更新统一调度到主线程（tail 线程等调用方不直接碰 tkinter）
        try:
            self.root.after(0, lambda: self._log_insert(key, line))
        except Exception:
            pass

    def _log_insert(self, key, line):
        t = getattr(self, '_log_texts', {}).get(key)
        if t is not None:
            try:
                t.configure(state='normal')
                t.insert('end', line + '\n')
                t.see('end')
                t.configure(state='disabled')
            except Exception:
                pass

    # ---------- 状态灯 ----------
    def _set_lamp(self, canvas, on, color=None):
        canvas.delete('all')
        fill = color if color else (self.colors['green'] if on else self.colors['red'])
        w = int(canvas['width'])
        canvas.create_oval(1, 1, w-1, w-1, fill=fill, outline='#111', width=1)

    # ---------- 环境体检 ----------
    def _refresh_env(self):
        def check():
            res = {}
            res['node'] = os.path.isfile(NODE_EXE)
            res['openclaw'] = os.path.isfile(OPENCLAW_MJS)
            res['llama'] = os.path.isfile(LLAMA_SERVER)
            n_models = 0
            if os.path.isdir(MODELS_DIR):
                for f in os.listdir(MODELS_DIR):
                    if f.endswith('.gguf') and not f.lower().startswith('mmproj'):
                        n_models += 1
            res['models'] = n_models > 0
            res['cfg'] = os.path.isfile(CONFIG_PATH) and bool(gw_token())
            # GPU 体检（静态层：ggml-cuda.dll + CUDA12 运行时 DLL，不加载模型）
            res['gpu'] = self._gpu_static_ok()
            try:
                env = dict(os.environ)
                env['PATH'] = r'C:\Program Files\Tailscale;' + env.get('PATH', '')
                r = subprocess.run([TAILSCALE_EXE, 'status'], capture_output=True, text=True,
                                   timeout=10, env=env, creationflags=0x08000000)
                res['tailscale'] = r.returncode == 0
            except Exception:
                res['tailscale'] = False
            return res
        def apply(res):
            for kind, ok in res.items():
                lamp, btn = self.env_frames[kind]
                if kind == 'models' and ok:
                    n = sum(1 for f in os.listdir(MODELS_DIR)
                            if f.endswith('.gguf') and not f.lower().startswith('mmproj'))
                    btn.config(text='目录', state='normal')
                elif kind == 'gpu' and ok:
                    btn.config(text='检测', state='normal')
                elif ok:
                    btn.config(text='就绪', state='disabled')
                elif kind == 'models':
                    btn.config(text='建目录', state='normal')
                elif kind == 'cfg':
                    btn.config(text='查看', state='normal')
                elif kind == 'gpu':
                    btn.config(text='检测', state='normal')
                elif kind == 'tailscale':
                    btn.config(text='启动', state='normal')
                else:
                    btn.config(text='安装', state='normal')
                self._set_lamp(lamp, ok, color=self.colors['green'] if ok else self.colors['red'])
        threading.Thread(target=lambda: self.root.after(0, lambda: apply(check())), daemon=True).start()

    def env_action(self, kind):
        lamp, btn = self.env_frames[kind]
        if kind == 'models':
            try:
                os.makedirs(MODELS_DIR, exist_ok=True)
                os.startfile(MODELS_DIR)
            except Exception as e:
                self.log('打开模型目录失败: ' + str(e))
            return
        if kind == 'cfg':
            try:
                os.startfile(r'L:\OpenClaw\OpenClawData')
            except Exception as e:
                self.log('打开配置目录失败: ' + str(e))
            return
        if kind == 'gpu':
            self.run_gpu_check()
            return
        if kind == 'tailscale':
            self.tailscale_start()
            return
        ok = {'node': os.path.isfile(NODE_EXE),
              'openclaw': os.path.isfile(OPENCLAW_MJS),
              'llama': os.path.isfile(LLAMA_SERVER)}.get(kind, False)
        if ok:
            messagebox.showinfo('提示', '该项已就绪')
            return
        names = {'node': 'Node.js 24', 'openclaw': 'OpenClaw', 'llama': 'llama.cpp'}
        if not messagebox.askyesno('一键安装', f'要安装 {names[kind]} 吗？\n需要联网，首次安装可能耗时几分钟。'):
            return
        threading.Thread(target={
            'node': self._install_node,
            'openclaw': self._install_openclaw,
            'llama': self._install_llama,
        }[kind], daemon=True).start()

    # ---------- GPU 体检 ----------
    def _gpu_static_ok(self):
        """静态检测：ggml-cuda.dll 存在且 CUDA12 运行时 DLL 齐全（不加载模型，秒回）"""
        try:
            for root in (COMFY_ROOT, os.path.join(_BASE_DIR, '..', '..', 'ComfyUI')):
                lib = os.path.join(root, 'python_embeded', 'Lib', 'site-packages', 'llama_cpp', 'lib')
                if not os.path.isdir(lib):
                    continue
                gpu_dll = os.path.join(lib, 'ggml-cuda.dll')
                need = ('cudart64_12.dll', 'cublas64_12.dll', 'cublasLt64_12.dll')
                if os.path.isfile(gpu_dll) and all(os.path.isfile(os.path.join(lib, d)) for d in need):
                    return True
            return False
        except Exception:
            return False

    def run_gpu_check(self):
        """完整 GPU 体检（三层：文件/依赖链/运行时加载模型实测）"""
        script = os.path.join(_BASE_DIR, 'console', 'llama_gpu_check.py')
        if not os.path.isfile(script):
            self.log('GPU体检脚本不存在: ' + script)
            return
        def work():
            self.log('=== GPU 体检（运行时实测，需加载一次小模型，约10~30秒）===')
            self.log(f'使用脚本: {script}')
            try:
                # 用控制台自带 python 跑（内部会调 ComfyUI 的 python_embeded）
                py = os.path.join(_BASE_DIR, '..', 'python', 'python.exe')
                r = subprocess.run([py, script, COMFY_ROOT], capture_output=True, text=True,
                                   encoding='utf-8', errors='replace', timeout=240,
                                   creationflags=0x08000000)
                out = (r.stdout or '') + (r.stderr or '')
                for line in out.splitlines():
                    if line.strip():
                        self.log('  ' + line)
                if r.returncode == 0:
                    self.log('=== GPU 体检完成：全部正常，GPU 加速可用 ===')
                else:
                    self.log('=== GPU 体检完成：存在问题，见上方 [X] 项 ===')
                # 刷新状态灯
                self.root.after(0, self._refresh_env)
            except Exception as e:
                self.log('GPU体检失败: ' + str(e))
        threading.Thread(target=work, daemon=True).start()

    # ---------- Tailscale ----------
    def _ts(self, args, timeout=30):
        try:
            env = dict(os.environ)
            env['PATH'] = r'C:\Program Files\Tailscale;' + env.get('PATH', '')
            r = subprocess.run([TAILSCALE_EXE] + args, capture_output=True, text=True,
                               timeout=timeout, env=env, creationflags=0x08000000)
            return r.returncode == 0, (r.stdout + r.stderr).strip()
        except Exception as e:
            return False, str(e)

    def tailscale_start(self):
        """启动 Tailscale（tailscale up 连接 tailnet）"""
        def work():
            self.log('启动 Tailscale…')
            ok, out = self._ts(['up'], timeout=60)
            self.log(('✅ Tailscale 已连接' if ok else '❌ Tailscale 连接失败（可能需登录）')
                     + ('\n' + out if out else ''))
            self._refresh_env()
        threading.Thread(target=work, daemon=True).start()

    def tailscale_onekey(self):
        """一键 Tailscale：检查连接 -> up -> serve 状态 -> 走广域网复原流程"""
        def work():
            self.log('==== Tailscale 一键配置 ====')
            ok, out = self._ts(['status'])
            if not ok:
                self.log('Tailscale 未连接，执行 tailscale up…')
                ok2, out2 = self._ts(['up'], timeout=60)
                if not ok2:
                    self.log('❌ tailscale up 失败（可能需要登录 tailnet）：\n' + out2)
                    return
                self.log('✅ Tailscale 已连接')
            else:
                self.log('✅ Tailscale 已在线')
            sok, sout = self._ts(['serve', 'status'])
            self.log('当前 Serve 规则：\n' + (sout or '(空)'))
            self.log('→ 执行广域网一键复原…')
            self.wan_restore()
        threading.Thread(target=work, daemon=True).start()

    def _install_node(self):
        self.log('通过 winget 安装 Node.js LTS（如弹权限窗口请允许）…')
        try:
            r = subprocess.run(['winget', 'install', 'OpenJS.NodeJS.LTS',
                                '--accept-source-agreements', '--accept-package-agreements'],
                               capture_output=True, text=True, timeout=900,
                               encoding='utf-8', errors='replace',
                               creationflags=0x08000000)
            self.log('winget: ' + (r.stdout or r.stderr).strip()[-300:])
        except Exception as e:
            self.log('Node 安装失败: ' + str(e))
        self._refresh_env()

    def _install_openclaw(self):
        self.log('运行 OpenClaw 官方安装脚本（需联网，装到当前用户）…')
        try:
            ps = 'irm https://get.openclaw.ai/install.ps1 | iex'
            r = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', ps],
                               capture_output=True, text=True, timeout=1200,
                               encoding='utf-8', errors='replace',
                               creationflags=0x08000000)
            self.log('安装输出: ' + (r.stdout or r.stderr).strip()[-400:])
        except Exception as e:
            self.log('OpenClaw 安装失败: ' + str(e))
        self._refresh_env()

    def _install_llama(self):
        self.log('开始下载安装 llama.cpp…')
        try:
            os.makedirs(LLAMA_DIR, exist_ok=True)
            req = urllib.request.Request('https://api.github.com/repos/ggml-org/llama.cpp/releases/latest',
                                         headers={'User-Agent': 'OpenClawConsole'})
            rel = json.load(urllib.request.urlopen(req, timeout=30))
            tag = rel.get('tag_name', '?')
            asset = next((a for a in rel.get('assets', [])
                          if a.get('name', '').endswith('.zip') and 'win-cuda' in a['name']), None)
            if not asset:
                self.log('未找到 Windows CUDA 版本，可能需要梯子；也可手动去 GitHub 下载')
                return
            name = asset['name']
            size = asset.get('size', 0)
            self.log(f'版本 {tag}，下载 {name}（{size//1024//1024}MB）…')
            tmp = os.path.join(LLAMA_DIR, name)
            done = {'n': 0}
            def hook(blk, blksz, total):
                done['n'] += 1
                if done['n'] % 400 == 0 and total:
                    self.log(f'  …{min(blk*blksz*100//total, 100)}%')
            urllib.request.urlretrieve(asset['browser_download_url'], tmp, reporthook=hook)
            self.log('解压中…')
            import zipfile
            with zipfile.ZipFile(tmp) as z:
                z.extractall(LLAMA_DIR)
            os.remove(tmp)
            self.log('✅ llama.cpp 安装完成')
        except Exception as e:
            self.log('llama.cpp 下载失败: ' + str(e))
        self._refresh_env()

    # ---------- 调试 ----------
    def test_llm_api(self):
        try:
            r = urllib.request.urlopen(f'http://127.0.0.1:{PORT_LLM}/v1/models', timeout=5)
            d = json.load(r)
            ids = [m.get('id') for m in d.get('data', [])]
            self.log('✅ 模型API正常，模型: ' + (', '.join(ids) if ids else '（空）'))
        except Exception as e:
            self.log('❌ 模型API不可用: ' + str(e))

    def test_gw(self):
        try:
            r = urllib.request.urlopen(DASH_URL, timeout=5)
            self.log(f'✅ 网关注册正常，状态码 {r.status}')
        except Exception as e:
            self.log('❌ 网关不可用: ' + str(e))

    def restart_gw(self):
        self.log('重启网关…')
        self.stop_gw()
        time.sleep(2)
        self.start_gw()

    def kill_all_gw(self):
        """强杀所有 OpenClaw 相关 node 进程（防止重启后残留双实例）"""
        try:
            subprocess.run(['powershell', '-NoProfile', '-Command',
                "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | "
                "Where-Object { $_.CommandLine -match 'openclaw' } | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
                capture_output=True, text=True, timeout=30, encoding='utf-8', errors='replace',
                creationflags=0x08000000)
            self.log('已强杀全部 OpenClaw 残留进程')
        except Exception as e:
            self.log('清理进程失败: ' + str(e))

    def clean_restart_gw(self):
        """全清重启：停网关 -> 强杀残留进程 -> 直接 node 干净启动，根治双实例/重启循环"""
        self.log('🔧 全清重启网关…')
        self.stop_gw()
        time.sleep(2)
        self.kill_all_gw()
        time.sleep(2)
        self.start_gw()
        time.sleep(3)
        if gw_running():
            self.log('✅ 网关已干净重启，单实例运行')
        else:
            self.log('⚠ 网关未就绪，请查看日志')

    def copy_log(self):
        try:
            txt = '\n'.join(getattr(self, '_log_buffer', []))
            self.root.clipboard_clear()
            self.root.clipboard_append(txt)
            self.log('日志已复制到剪贴板')
        except Exception as e:
            self.log('复制日志失败: ' + str(e))

    # ---------- 磁吸日志窗口 ----------
    def open_log_window(self):
        if getattr(self, 'log_win', None) is not None:
            try:
                self.log_win.deiconify(); self.log_win.lift(); self.log_win.focus_force()
                return
            except Exception:
                self.log_win = None
        c = self.colors
        win = tk.Toplevel(self.root)
        win.title('运行日志 · 拖到屏幕边缘自动吸附')
        win.geometry('680x420+120+80')
        win.minsize(460, 260)
        win.configure(bg='#2b2b2b')
        win.configure(highlightthickness=0)
        nb = ttk.Notebook(win)
        nb.pack(fill='both', expand=True, padx=8, pady=8)
        self._log_texts = {}
        for key, label in [('console', '控制台'), ('llm', '模型 LLM'), ('comfy', 'ComfyUI'), ('gw', '网关'), ('textgen', '傻酒馆')]:
            page = tk.Frame(nb, bg='#1e1e1e')
            t = tk.Text(page, bg='#1e1e1e', fg='#c8c8c8', font=('Consolas', 9),
                        wrap='word', relief='flat', borderwidth=0, state='disabled')
            sb = ttk.Scrollbar(page, command=t.yview)
            t.configure(yscrollcommand=sb.set)
            sb.pack(side='right', fill='y')
            t.pack(side='left', fill='both', expand=True, padx=1, pady=1)
            nb.add(page, text=label)
            self._log_texts[key] = t
        self.log_win = win
        self._log_win_text = self._log_texts.get('console')
        self._logwin_magnet_on = True
        self._mag_last = None
        self._mag_t = 0
        win.protocol('WM_DELETE_WINDOW', self.close_log_window)
        self.root.after(150, self._logwin_magnet_poll)
        with log_lock:
            for key, t in self._log_texts.items():
                t.configure(state='normal')
                for line in self._log_buffers.get(key, []):
                    t.insert('end', line + '\n')
                t.configure(state='disabled')
                t.see('end')
        # 日志缓冲已常驻，无需重复启动尾随线程

    def close_log_window(self):
        self._logwin_magnet_on = False
        # 日志尾随线程保持运行（缓冲持续积累），仅关闭窗口
        win = getattr(self, 'log_win', None)
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass
        self.log_win = None
        self._log_win_text = None
        self._log_texts = {}

    def _logwin_magnet_poll(self):
        """静止吸附：窗口停稳(0.35s 位置不变)且靠近屏幕边缘 30px 内才贴边，拖动过程不抢"""
        win = getattr(self, 'log_win', None)
        if win is None or not getattr(self, '_logwin_magnet_on', False):
            return
        try:
            x = win.winfo_rootx(); y = win.winfo_rooty()
            w = win.winfo_width(); h = win.winfo_height()
            sw = win.winfo_screenwidth(); sh = win.winfo_screenheight()
            cur = (x, y)
            now = time.time()
            if cur == self._mag_last:
                if now - self._mag_t > 0.35:
                    S = 30
                    nx, ny = x, y
                    if x < S:
                        nx = 0
                    elif sw - (x + w) < S:
                        nx = sw - w
                    if y < S:
                        ny = 0
                    elif sh - (y + h) < S:
                        ny = sh - h
                    if (nx, ny) != (x, y):
                        win.geometry(f'+{nx}+{ny}')
            else:
                self._mag_last = cur
                self._mag_t = now
        except Exception:
            pass
        try:
            self.root.after(120, self._logwin_magnet_poll)
        except Exception:
            pass

    def _logwin_drag_start(self, e):
        win = getattr(self, 'log_win', None)
        if win is None:
            return
        try:
            self._drag_offx = e.x_root - win.winfo_x()
            self._drag_offy = e.y_root - win.winfo_y()
        except Exception:
            pass

    def _logwin_drag_move(self, e):
        win = getattr(self, 'log_win', None)
        if win is None:
            return
        try:
            x = e.x_root - self._drag_offx
            y = e.y_root - self._drag_offy
            w = win.winfo_width(); h = win.winfo_height()
            sw = win.winfo_screenwidth(); sh = win.winfo_screenheight()
            S = 40
            if x < S:
                x = 0
            elif sw - (x + w) < S:
                x = sw - w
            if y < S:
                y = 0
            elif sh - (y + h) < S:
                y = sh - h
            win.geometry(f'+{max(0, x)}+{max(0, y)}')
        except Exception:
            pass

    def _logwin_drag_end(self, e):
        win = getattr(self, 'log_win', None)
        if win is None:
            return
        try:
            x = win.winfo_x(); y = win.winfo_y()
            w = win.winfo_width(); h = win.winfo_height()
            sw = win.winfo_screenwidth(); sh = win.winfo_screenheight()
            S = 40
            if x < S:
                x = 0
            elif sw - (x + w) < S:
                x = sw - w
            if y < S:
                y = 0
            elif sh - (y + h) < S:
                y = sh - h
            win.geometry(f'+{x}+{y}')
        except Exception:
            pass

    def copy_gw_key(self):
        """一键复制 Gateway 密钥到剪贴板（用于打开 18789 控制台配对）"""
        try:
            token = gw_token()
            if not token:
                self.log('未找到 Gateway 密钥（openclaw.json 里 gateway.auth.token 为空）')
                return
            self.root.clipboard_clear()
            self.root.clipboard_append(token)
            self.log('Gateway 密钥已复制到剪贴板，去网页粘贴即可连接')
        except Exception as e:
            self.log('复制密钥失败: ' + str(e))

    # ---------- 模型下拉 ----------
    def _refresh_models_combo(self):
        self.models = list_models()
        names = []
        for n, p, sz, mm, cust in self.models:
            tag = '  🖼 视觉' if mm else ''
            if cust:
                tag += '  [自定义]'
            names.append(f'{n}  ({sz}MB){tag}')
        self.model_combo['values'] = names
        if names:
            # 默认选 Gemma Q4_0
            idx = 0
            for i, (n, p, sz, mm, cust) in enumerate(self.models):
                if 'Q4_0' in n:
                    idx = i
                    break
            self.model_combo.current(idx)

    def _selected_model(self):
        i = self.model_combo.current()
        if 0 <= i < len(self.models):
            return self.models[i]
        return None

    def _on_llm_mode_change(self, event=None):
        """推理模式切换：保存到 hw_profile.json，提示重启模型生效"""
        mode = self.llm_mode_var.get()
        try:
            prof = load_profile() or {}
            prof['llm_mode'] = mode
            save_profile(prof)
        except Exception as e:
            self.log('模式保存失败: ' + str(e))
            return
        self.log('推理模式已切换为: ' + mode + '（点「启动模型」重启生效）')

    # ---------- 自定义模型 ----------
    def _refresh_gen_models(self, initial=False):
        """从 ComfyUI 拉取生图模型列表（checkpoints + diffusion_models/unet）。
        网络请求放后台线程：ComfyUI 离线时不再卡住启动（原来主线程等 8s×2）。"""
        def fetch():
            ckpts = None
            try:
                import urllib.request, json as _json
                a = _json.load(urllib.request.urlopen(
                    f'http://127.0.0.1:{COMFY_PORT}/object_info/CheckpointLoaderSimple', timeout=8))
                b = _json.load(urllib.request.urlopen(
                    f'http://127.0.0.1:{COMFY_PORT}/object_info/UNETLoader', timeout=8))
                items = a['CheckpointLoaderSimple']['input']['required']['ckpt_name'][0] + \
                        b['UNETLoader']['input']['required']['unet_name'][0]
                seen, ckpts = set(), []
                for it in items:
                    k = it.replace('\\', '/')
                    if k not in seen:
                        seen.add(k)
                        ckpts.append(it)
            except Exception:
                ckpts = None
            try:
                self.root.after(0, lambda: self._apply_gen_models(ckpts, initial))
            except Exception:
                pass
        threading.Thread(target=fetch, daemon=True).start()

    def _apply_gen_models(self, ckpts, initial):
        """把拉取/兜底结果落到 UI（主线程）"""
        if ckpts is None:
            ckpts = [
                'Z-Image-Base-8steps-White_Marble-AIO_v2-fp8.safetensors',
                'diffusion_models\\Krea2\\Krea2-Moody-Mix-premium_int4_convrot.safetensors',
                'diffusion_models\\Krea2\\Krea2-1125Krea2AsianUtopian_v2_int4_convrot.safetensors',
                'diffusion_models\\z_image\\ZIT-moodyRealMix_zitV7_fp8.safetensors',
                'diffusion_models\\z_image\\ZIT-moodyProMix_zitV13_bf16.safetensors',
                'XL-写实\\IL-perfectionRealisticILXL_33.safetensors',
            ]
        self._gen_ckpts = ckpts
        self.gen_model_combo['values'] = ckpts
        # 读已保存的默认（gen_model.txt 记录 basename）
        saved = ''
        try:
            saved = open(os.path.join(_BASE_DIR, 'gen_model.txt'),
                                      encoding='utf-8').read().strip()
        except Exception:
            pass
        cur = ''
        if saved:
            for c in ckpts:
                if c.replace('\\', '/').rsplit('/', 1)[-1].lower() == saved.lower():
                    cur = c
                    break
            if not cur:
                for c in ckpts:
                    if saved.lower() in c.lower():
                        cur = c
                        break
        if not cur:
            for c in ckpts:
                if 'Z-Image' in c:
                    cur = c
                    break
        if cur:
            self.gen_model_var.set(cur)
        if not initial:
            self.log('生图模型列表已刷新（%d 个）' % len(ckpts))

    def _save_gen_model(self, _evt=None):
        """选择即保存：把选中的模型写进 gen_model.txt，MCP 生图时读取"""
        name = self.gen_model_var.get().strip()
        if not name:
            return
        base = name.replace('\\', '/').rsplit('/', 1)[-1]
        try:
            with open(os.path.join(_BASE_DIR, 'gen_model.txt'),
                                   'w', encoding='utf-8') as f:
                f.write(base)
            self.log('生图默认模型已设为：' + base)
        except Exception as e:
            self.log('保存生图模型失败：' + str(e))

    # ---------- 生图默认 LoRA ----------
    def _refresh_loras(self, initial=False):
        """从 ComfyUI loras 目录拉取 LoRA（krea2 / z-image 两个下拉），载入已保存默认"""
        root = os.path.join(COMFY_DIR, 'models', 'loras')
        loras = []
        if os.path.isdir(root):
            for dp, dns, fns in os.walk(root):
                for fn in sorted(fns):
                    if fn.lower().endswith(('.safetensors', '.ckpt', '.pt')):
                        loras.append(os.path.relpath(os.path.join(dp, fn), root))
        self._loras = loras
        self.lora_krea2_combo['values'] = loras
        self.lora_zimg_combo['values'] = loras
        for key, var in (('krea2', self.lora_krea2_var), ('zimg', self.lora_zimg_var)):
            saved = ''
            try:
                saved = io.open(os.path.join(_BASE_DIR, 'gen_lora_%s.txt' % key), encoding='utf-8').read().strip()
            except Exception:
                pass
            cur = ''
            if saved:
                for c in loras:
                    if c.replace('\\', '/').rsplit('/', 1)[-1].lower() == saved.lower():
                        cur = c
                        break
                if not cur:
                    for c in loras:
                        if saved.lower() in c.lower():
                            cur = c
                            break
            var.set(cur)
        if not initial:
            self.log('LoRA 列表已刷新（%d 个）' % len(loras))

    def _save_lora(self, kind):
        """选择即保存：krea2 -> gen_lora_krea2.txt，zimg -> gen_lora_zimg.txt"""
        var = self.lora_krea2_var if kind == 'krea2' else self.lora_zimg_var
        name = var.get().strip()
        if not name:
            return
        base = name.replace('\\', '/').rsplit('/', 1)[-1]
        path = os.path.join(_BASE_DIR, 'gen_lora_%s.txt' % kind)
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(base)
            self.log('生图默认LoRA(%s)已设为：%s' % (kind, base))
        except Exception as e:
            self.log('保存LoRA失败：' + str(e))

    # ---------- 生图工作流选择 ----------
    def _refresh_gen_workflows(self, initial=False):
        """递归列出 workflows 目录的 .json 模板（含子目录、不含 .ui.json/缓存），载入已保存默认"""
        try:
            files = []
            for dp, dns, fns in os.walk(WF_DIR):
                for fn in sorted(fns):
                    if not fn.endswith('.json') or fn.endswith('.ui.json'):
                        continue
                    if fn in ('object_info_cache.json',) or fn.startswith('object_info'):
                        continue
                    rel = os.path.relpath(os.path.join(dp, fn), WF_DIR).replace('\\', '/')
                    files.append(rel)
            files.sort()
        except Exception:
            files = []
        self.gen_wf_combo['values'] = files
        saved = ''
        fp = os.path.join(_BASE_DIR, 'gen_workflow.txt')
        try:
            with io.open(fp, encoding='utf-8') as f:
                saved = f.read().strip()
        except Exception:
            pass
        if saved in files:
            self.gen_wf_var.set(saved)
        elif initial and files:
            self.gen_wf_var.set('default.json' if 'default.json' in files else files[0])
        if initial:
            self.log('生图工作流已载入（%d 个模板）' % len(files))

    def _save_gen_workflow(self, event=None):
        """选择即保存：写 gen_workflow.txt，MCP 生图时读取"""
        v = self.gen_wf_var.get().strip()
        if not v:
            return
        fp = os.path.join(_BASE_DIR, 'gen_workflow.txt')
        try:
            with io.open(fp, 'w', encoding='utf-8', newline='\n') as f:
                f.write(v + '\n')
            self.log('生图工作流已设为：' + v)
        except Exception as e:
            self.log('保存生图工作流失败：' + str(e))

    def open_wf_dir(self):
        """打开生图工作流模板文件夹（workflows）"""
        try:
            os.makedirs(WF_DIR, exist_ok=True)
            os.startfile(WF_DIR)
            self.log('已打开工作流文件夹：' + WF_DIR)
        except Exception as e:
            self.log('打开工作流文件夹失败：' + str(e))

    def open_upload_dir(self):
        """打开上传文件夹（微信/网页收到的图）"""
        d = os.path.join(os.path.dirname(CONFIG_PATH), 'media', 'inbound')
        os.makedirs(d, exist_ok=True)
        os.startfile(d)
        self.log('已打开上传文件夹：' + d)

    def open_output_dir(self):
        """打开生图输出文件夹（跟随 ComfyUI 根目录配置）"""
        d = os.path.join(COMFY_DIR, 'output')
        os.makedirs(d, exist_ok=True)
        os.startfile(d)
        self.log('已打开生成文件夹：' + d)

    # ---------- 硬件配置 ----------
    # ---------- 模型 API 参数（maxTokens / timeoutSeconds） ----------
    def show_api_params(self):
        """弹窗：修改 openclaw.json 中 local-model 的 maxTokens 与 timeoutSeconds"""
        cfg_path = os.path.join(os.path.dirname(_BASE_DIR), 'openclaw.json')
        if not os.path.isfile(cfg_path):
            self.log('openclaw.json 不存在: ' + str(cfg_path))
            messagebox.showerror('错误', '未找到 openclaw.json：' + str(cfg_path))
            return

        def load_vals():
            try:
                cfg = json.loads(io.open(cfg_path, encoding='utf-8').read() or '{}')
                prov = (cfg.get('models') or {}).get('providers') or {}
                for k, v in prov.items():
                    for m in (v.get('models') or []):
                        if m.get('id') == 'local-model':
                            return int(m.get('maxTokens') or 16384), int(v.get('timeoutSeconds') or 300), v.get('baseUrl', '')
            except Exception:
                pass
            return 16384, 300, ''

        win = tk.Toplevel(self.root)
        win.title('模型 API 参数')
        win.configure(bg='#2d2d2d')
        win.geometry('560x560')
        win.transient(self.root)
        win.grab_set()
        f = ttk.Frame(win, style='Panel.TFrame')
        f.pack(fill='both', expand=True, padx=12, pady=12)

        mt, ts, base = load_vals()
        def load_remote():
            try:
                cfg = json.loads(io.open(cfg_path, encoding='utf-8').read() or '{}')
                prov = (cfg.get('models') or {}).get('providers') or {}
                for k, v in prov.items():
                    bu = v.get('baseUrl', '')
                    if '127.0.0.1' not in bu and 'localhost' not in bu:
                        mid = ''
                        for m in (v.get('models') or []):
                            mid = m.get('id', ''); break
                        return bu, v.get('apiKey', ''), mid
            except Exception:
                pass
            return '', '', ''
        rurl, rkey, rmodel = load_remote()
        ttk.Label(f, text='本地模型 API 参数（写入 openclaw.json）', style='Panel.TLabel',
                  font=('Microsoft YaHei UI', 11, 'bold')).grid(row=0, column=0, columnspan=2, sticky='w', padx=10, pady=(10, 8))
        ttk.Label(f, text='API 地址（本地）', style='Dim.TLabel').grid(row=1, column=0, sticky='w', padx=10, pady=4)
        ttk.Label(f, text=base or '（未识别）', style='Panel.TLabel').grid(row=1, column=1, sticky='w', padx=10, pady=4)
        ttk.Label(f, text='maxTokens（单次最大输出 token 数）', style='Dim.TLabel').grid(row=2, column=0, sticky='w', padx=10, pady=4)
        mt_var = tk.StringVar(value=str(mt))
        tk.Spinbox(f, from_=256, to=65536, increment=256, textvariable=mt_var, width=14,
                   bg='#3c3c3c', fg='#e8e8e8', insertbackground='#e8e8e8',
                   buttonbackground='#555').grid(row=2, column=1, sticky='w', padx=10, pady=4)
        ttk.Label(f, text='timeoutSeconds（请求超时，秒）', style='Dim.TLabel').grid(row=3, column=0, sticky='w', padx=10, pady=4)
        ts_var = tk.StringVar(value=str(ts))
        tk.Spinbox(f, from_=30, to=3600, increment=30, textvariable=ts_var, width=14,
                   bg='#3c3c3c', fg='#e8e8e8', insertbackground='#e8e8e8',
                   buttonbackground='#555').grid(row=3, column=1, sticky='w', padx=10, pady=4)
        ttk.Separator(f, orient='horizontal').grid(row=4, column=0, columnspan=2, sticky='ew', padx=10, pady=(12, 6))
        ttk.Label(f, text='远程 API 接入（OpenAI 兼容接口）', style='Panel.TLabel',
                  font=('Microsoft YaHei UI', 11, 'bold')).grid(row=5, column=0, columnspan=2, sticky='w', padx=10, pady=(4, 6))
        ttk.Label(f, text='API 地址（baseUrl）', style='Dim.TLabel').grid(row=6, column=0, sticky='w', padx=10, pady=4)
        rurl_var = tk.StringVar(value=rurl or 'http://127.0.0.1:3001/v1')
        tk.Entry(f, textvariable=rurl_var, width=42, bg='#3c3c3c', fg='#e8e8e8',
                 insertbackground='#e8e8e8').grid(row=6, column=1, sticky='w', padx=10, pady=4)
        ttk.Label(f, text='API Key', style='Dim.TLabel').grid(row=7, column=0, sticky='w', padx=10, pady=4)
        rkey_var = tk.StringVar(value=rkey or 'sk-')
        tk.Entry(f, textvariable=rkey_var, width=42, bg='#3c3c3c', fg='#e8e8e8',
                 insertbackground='#e8e8e8', show='*').grid(row=7, column=1, sticky='w', padx=10, pady=4)
        ttk.Label(f, text='模型名（model id）', style='Dim.TLabel').grid(row=8, column=0, sticky='w', padx=10, pady=4)
        rmodel_var = tk.StringVar(value=rmodel or 'auto')
        tk.Entry(f, textvariable=rmodel_var, width=42, bg='#3c3c3c', fg='#e8e8e8',
                 insertbackground='#e8e8e8').grid(row=8, column=1, sticky='w', padx=10, pady=4)
        ttk.Label(f, text='留空 API 地址则不启用远程 API；本地 FreeLLMAPI 填 http://127.0.0.1:3001/v1', style='Dim.TLabel').grid(row=9, column=0, columnspan=2, sticky='w', padx=10, pady=(6, 2))

        def save():
            try:
                mt2, ts2 = int(mt_var.get()), int(ts_var.get())
            except Exception:
                messagebox.showwarning('提示', '请输入数字')
                return
            try:
                cfg = json.loads(io.open(cfg_path, encoding='utf-8').read() or '{}')
                prov = (cfg.get('models') or {}).setdefault('providers', {})
                found = False
                for k, v in prov.items():
                    if v.get('baseUrl', '').find('127.0.0.1:8080') >= 0:
                        for m in (v.get('models') or []):
                            if m.get('id') == 'local-model':
                                m['maxTokens'] = mt2
                                v['timeoutSeconds'] = ts2
                                found = True
                if not found:
                    messagebox.showerror('错误', '未找到 local-model 配置')
                    return
                rurl2 = rurl_var.get().strip()
                rkey2 = rkey_var.get().strip()
                rmodel2 = rmodel_var.get().strip()
                if rurl2:
                    rid = 'remote-api'
                    if rid not in prov:
                        prov[rid] = {}
                    prov[rid]['baseUrl'] = rurl2
                    prov[rid]['apiKey'] = rkey2
                    prov[rid]['timeoutSeconds'] = ts2
                    prov[rid]['models'] = [{'id': rmodel2, 'maxTokens': mt2}]
                    self.log(f'远程 API 已配置: {rurl2} 模型={rmodel2}')
                else:
                    prov.pop('remote-api', None)
                    self.log('远程 API 已清空（仅用本地模型）')
                io.open(cfg_path, 'w', encoding='utf-8', newline='\n').write(
                    json.dumps(cfg, ensure_ascii=False, indent=2))
                self.log(f'模型 API 参数已保存: maxTokens={mt2}, timeoutSeconds={ts2}（重启网关生效）')
                win.destroy()
            except Exception as e:
                messagebox.showerror('错误', '保存失败: ' + str(e))

        btns = tk.Frame(win, bg='#2d2d2d')
        btns.pack(fill='x', padx=12, pady=(0, 12))
        ttk.Button(btns, text='保存', style='Accent.TButton', command=save).pack(side='right', padx=(8, 0))
        ttk.Button(btns, text='关闭', command=win.destroy).pack(side='right')

    def show_hardware(self):
        """检测硬件 -> 推荐配置 -> 一键应用"""
        hw = detect_hardware()
        rec = recommend_profile(hw)
        win = tk.Toplevel(self.root)
        win.title('硬件检测与推荐配置')
        win.configure(bg='#2d2d2d')
        win.geometry('560x430')
        f = ttk.Frame(win, style='Panel.TFrame')
        f.pack(fill='both', expand=True, padx=12, pady=12)
        ttk.Label(f, text='本机硬件', style='Panel.TLabel',
                  font=('Microsoft YaHei UI', 11, 'bold')).grid(row=0, column=0, sticky='w', padx=10, pady=(10, 6))
        hw_lines = [
            ('GPU', rec['gpu']),
            ('显存', str(rec['vram']) + ' GB'),
            ('内存', str(rec['ram']) + ' GB'),
            ('CPU 线程', str(rec['cpu'])),
        ]
        for i, (k, v) in enumerate(hw_lines):
            ttk.Label(f, text=k, style='Dim.TLabel').grid(row=1 + i, column=0, sticky='w', padx=10, pady=2)
            ttk.Label(f, text=v, style='Panel.TLabel').grid(row=1 + i, column=1, sticky='w', padx=10, pady=2)
        ttk.Label(f, text='推荐配置（%s）' % rec['tier'], style='Panel.TLabel',
                  font=('Microsoft YaHei UI', 11, 'bold')).grid(row=5, column=0, columnspan=2, sticky='w', padx=10, pady=(14, 6))
        rec_lines = [
            ('llama 显存层数', str(rec['ngl'])),
            ('上下文', str(rec['ctx'])),
            ('KV 量化', rec['kv']),
            ('模型建议', rec['model']),
        ]
        for i, (k, v) in enumerate(rec_lines):
            ttk.Label(f, text=k, style='Dim.TLabel').grid(row=6 + i, column=0, sticky='w', padx=10, pady=2)
            ttk.Label(f, text=v, style='Panel.TLabel').grid(row=6 + i, column=1, sticky='w', padx=10, pady=2)
        ttk.Label(f, text='应用后，下次启动模型将按此参数运行', style='Dim.TLabel'
                  ).grid(row=10, column=0, columnspan=2, sticky='w', padx=10, pady=(10, 4))

        def apply():
            save_profile(rec)
            self.log('已应用硬件配置: ' + rec['label'])
            win.destroy()

        btns = tk.Frame(win, bg='#2d2d2d')
        btns.pack(fill='x', padx=12, pady=(0, 12))
        ttk.Button(btns, text='应用此配置', style='Accent.TButton',
                   command=apply).pack(side='right', padx=(8, 0))
        ttk.Button(btns, text='关闭', command=win.destroy).pack(side='right')

    # ---------- 一键恢复 ----------
    def _restore_all(self):
        """一键恢复：修复配置 -> 拉起缺失的服务（llama/ComfyUI/网关）"""
        self.log('=== 一键恢复 ===')
        def work():
            try:
                subprocess.run(['setx', 'OPENCLAW_STATE_DIR', r'L:\OpenClaw\OpenClawData'],
                               capture_output=True, timeout=30)
                self.log('环境变量 OPENCLAW_STATE_DIR 已确认')
            except Exception as e:
                self.log('环境变量设置失败: ' + str(e))
            # 0. 修复配置（MCP / paths.json / tools.deny）
            self._fix_configs()
            # 1. llama
            if llama_running():
                self.log('模型服务已在运行')
            else:
                self.log('模型未运行，启动中...')
                try:
                    self.start_llm()
                except Exception as e:
                    self.log('模型启动异常: ' + str(e))
            # 2. ComfyUI
            if comfy_alive():
                self.log('ComfyUI 已在运行')
            else:
                self.log('ComfyUI 未运行，启动中...')
                try:
                    self.restart_comfy()
                except Exception as e:
                    self.log('ComfyUI 启动异常: ' + str(e))
            # 3. 网关（直接 node 启动，绕开 gateway.cmd --task-supervisor 重启循环）
            if gw_running():
                self.log('网关已在运行')
            else:
                self.log('网关未运行，启动中...')
                try:
                    gw_logf = io.open(os.path.join(LOG_DIR, 'gateway.log'), 'a',
                                      encoding='utf-8', errors='replace', buffering=1)
                    env = dict(os.environ)
                    env['OPENCLAW_STATE_DIR'] = r'L:\OpenClaw\OpenClawData'
                    env['PATH'] = os.path.dirname(NODE_EXE) + ';' + env.get('PATH', '')
                    subprocess.Popen([NODE_EXE, '--max-old-space-size=8192', OPENCLAW_MJS,
                                      'gateway', '--port', '18789'],
                                     stdout=gw_logf, stderr=subprocess.STDOUT,
                                     creationflags=subprocess.CREATE_NO_WINDOW, env=env)
                    self.log('已后台启动网关（直接 node）...')
                except Exception as e:
                    self.log('网关启动异常: ' + str(e))
            time.sleep(2)
            try:
                self.root.after(0, self._refresh_env)
            except Exception:
                pass
            self.log('=== 一键恢复完成 ===')
        threading.Thread(target=work, daemon=True).start()

    def _fix_configs(self):
        """修复 openclaw.json（MCP 配置 + tools.deny）与 paths.json（comfy_root 等路径）。
        幂等：配置正确时不改动。"""
        # ---- paths.json ----
        try:
            pcfg = _load_paths()
            changed = False
            expected = {
                'llama_dir': r'L:\OpenClaw\llama',
                'comfy_root': r'L:\OpenClaw\ComfyUI',
                'openclaw_data': r'L:\OpenClaw\OpenClawData',
                'openclaw_npm': r'L:\OpenClaw\npm',
                'node_exe': r'C:\Program Files\nodejs\node.exe',
            }
            # 只补缺失键，不覆盖用户已保存的自定义路径（如 comfy_root 迁到 L:\ComfyUI）
            for k, v in expected.items():
                if not pcfg.get(k):
                    pcfg[k] = v
                    changed = True
            if changed:
                _save_paths(pcfg)
                _apply_paths(pcfg)
                self.log('paths.json 已补全缺失键（已有自定义路径保持不变）')
        except Exception as e:
            self.log('paths.json 修复失败: ' + str(e))
        # ---- openclaw.json ----
        try:
            if not os.path.isfile(CONFIG_PATH):
                self.log('openclaw.json 不存在: ' + str(CONFIG_PATH))
            else:
                cfg = json.loads(io.open(CONFIG_PATH, encoding='utf-8').read() or '{}')
                changed = False
                mcp = cfg.setdefault('mcp', {}).setdefault('servers', {}).setdefault('comfyui', {})
                pcfg2 = _load_paths()
                ocd = pcfg2.get('openclaw_data') or r'L:\OpenClaw\OpenClawData'
                py_exe = os.path.join(os.path.dirname(ocd), 'python', 'python.exe')
                mcp_script = os.path.join(ocd, 'console', 'comfyui_mcp_server.py')
                mcp_cwd = os.path.join(ocd, 'console')
                if mcp.get('command') != py_exe:
                    mcp['command'] = py_exe
                    changed = True
                if mcp.get('args') != [mcp_script]:
                    mcp['args'] = [mcp_script]
                    changed = True
                if mcp.get('enabled') is not True:
                    mcp['enabled'] = True
                    changed = True
                if mcp.get('transport') != 'stdio':
                    mcp['transport'] = 'stdio'
                    changed = True
                if mcp.get('cwd') != mcp_cwd:
                    mcp['cwd'] = mcp_cwd
                    changed = True
                if mcp.get('requestTimeoutMs') != 300000:
                    mcp['requestTimeoutMs'] = 300000
                    changed = True
                tools = cfg.setdefault('tools', {})
                deny = tools.setdefault('deny', [])
                if 'image_generate' not in deny:
                    deny.append('image_generate')
                    changed = True
                if tools.get('profile') != 'coding':
                    tools['profile'] = 'coding'
                    changed = True
                if changed:
                    bak = CONFIG_PATH + '.bak_restore_' + time.strftime('%Y%m%d%H%M%S')
                    try:
                        import shutil
                        shutil.copy2(CONFIG_PATH, bak)
                    except Exception:
                        pass
                    with io.open(CONFIG_PATH, 'w', encoding='utf-8', newline='\n') as f:
                        json.dump(cfg, f, ensure_ascii=False, indent=2)
                    self.log('openclaw.json 已修复（MCP 路径/enabled/transport + tools.deny）')
                else:
                    self.log('openclaw.json 配置正确')
        except Exception as e:
            self.log('openclaw.json 修复失败: ' + str(e))

    def manage_model_dirs(self):
        """管理额外模型扫描目录 + 自定义单文件（合并后的入口）"""
        win = tk.Toplevel(self.root)
        win.title('模型扫描目录')
        win.geometry('600x560')
        win.minsize(520, 460)
        win.transient(self.root)
        win.grab_set()
        # ---- ① 额外扫描目录 ----
        ttk.Label(win, text='① 额外扫描目录（模型文件所在文件夹，可添加多个）：',
                  style='Panel.TLabel').pack(anchor='w', padx=12, pady=(12, 4))
        lb = tk.Listbox(win, font=('Microsoft YaHei UI', 9), height=6)
        lb.pack(fill='both', expand=True, padx=12, pady=(0, 4))
        for d in load_extra_dirs():
            lb.insert('end', d)
        def add_dir():
            from tkinter import filedialog
            d = filedialog.askdirectory(title='选择模型目录（含 .gguf 文件）')
            if not d:
                return
            cur = load_extra_dirs()
            if d in cur:
                self.log('目录已在列表: ' + d)
                return
            cur.append(d)
            save_extra_dirs(cur)
            lb.insert('end', d)
            self.models = list_models()
            self._refresh_models_combo()
            self.log('已添加扫描目录: ' + d)
        def rm_dir():
            sel = lb.curselection()
            if not sel:
                return
            d = lb.get(sel[0])
            cur = load_extra_dirs()
            if d in cur:
                cur.remove(d)
                save_extra_dirs(cur)
            lb.delete(sel[0])
            self.models = list_models()
            self._refresh_models_combo()
            self.log('已移除扫描目录: ' + d)
        row1 = ttk.Frame(win)
        row1.pack(fill='x', padx=12)
        ttk.Button(row1, text='添加目录', command=add_dir).pack(side='left', padx=(0, 8))
        ttk.Button(row1, text='移除选中', command=rm_dir).pack(side='left', padx=(0, 8))
        # ---- ② 自定义单文件 ----
        ttk.Label(win, text='② 自定义模型（单个 .gguf 文件，不在扫描目录里）：',
                  style='Panel.TLabel').pack(anchor='w', padx=12, pady=(12, 4))
        lb2 = tk.Listbox(win, font=('Microsoft YaHei UI', 9), height=4)
        lb2.pack(fill='both', expand=True, padx=12, pady=(0, 4))
        for it in load_customs():
            p = it.get('path', '')
            lb2.insert('end', os.path.basename(p) + '   <-   ' + p)
        def add_file():
            from tkinter import filedialog
            p = filedialog.askopenfilename(
                title='选择模型文件 (.gguf)',
                filetypes=[('GGUF 模型', '*.gguf'), ('所有文件', '*.*')])
            if not p:
                return
            # 拦截视觉投影文件：引导选同目录主模型
            if os.path.basename(p).lower().startswith('mmproj'):
                d = os.path.dirname(p)
                mains = [f for f in sorted(os.listdir(d))
                         if f.endswith('.gguf') and not f.lower().startswith('mmproj')]
                if len(mains) == 1:
                    p = os.path.join(d, mains[0])
                    self.log('选中的是视觉投影，已自动改为同目录主模型: ' + mains[0])
                elif mains:
                    messagebox.showinfo('提示',
                        '这是视觉投影文件(mmproj)，不能作为主模型运行。\n\n'
                        '请选择主模型，同目录下有：\n' + '\n'.join(mains))
                    return
                else:
                    messagebox.showwarning('提示', '这是视觉投影文件(mmproj)，不能作为主模型运行')
                    return
            items = load_customs()
            for it in items:
                if os.path.normcase(it.get('path', '')) == os.path.normcase(p):
                    self.log('该模型已在列表: ' + os.path.basename(p))
                    return
            items.append({'path': p})
            save_customs(items)
            lb2.insert('end', os.path.basename(p) + '   <-   ' + p)
            self.models = list_models()
            self._refresh_models_combo()
            self.log('已添加自定义模型: ' + os.path.basename(p))
        def rm_file():
            sel = lb2.curselection()
            if not sel:
                return
            items = load_customs()
            it = items[sel[0]]
            items.remove(it)
            save_customs(items)
            lb2.delete(sel[0])
            self.models = list_models()
            self._refresh_models_combo()
            self.log('已移除自定义模型: ' + os.path.basename(it.get('path', '')))
        row2 = ttk.Frame(win)
        row2.pack(fill='x', padx=12)
        ttk.Button(row2, text='添加文件', command=add_file).pack(side='left', padx=(0, 8))
        ttk.Button(row2, text='移除选中', command=rm_file).pack(side='left', padx=(0, 8))
        # ---- 底部 ----
        def refresh():
            self.models = list_models()
            self._refresh_models_combo()
            self.log('已刷新模型下拉')
        row3 = ttk.Frame(win)
        row3.pack(fill='x', padx=12, pady=(0, 12))
        ttk.Button(row3, text='刷新下拉', command=refresh).pack(side='left', padx=(0, 8))
        ttk.Button(row3, text='关闭', command=win.destroy).pack(side='right')

    def add_custom_model(self):
        from tkinter import filedialog
        p = filedialog.askopenfilename(
            title='选择模型文件 (.gguf)',
            filetypes=[('GGUF 模型', '*.gguf'), ('所有文件', '*.*')])
        if not p:
            return
        # 拦截视觉投影文件：引导选同目录主模型
        if os.path.basename(p).lower().startswith('mmproj'):
            d = os.path.dirname(p)
            mains = [f for f in sorted(os.listdir(d))
                     if f.endswith('.gguf') and not f.lower().startswith('mmproj')]
            if len(mains) == 1:
                p = os.path.join(d, mains[0])
                self.log('选中的是视觉投影，已自动改为同目录主模型: ' + mains[0])
            elif mains:
                messagebox.showinfo('提示',
                    '这是视觉投影文件(mmproj)，不能作为主模型运行。\n\n'
                    '请选择主模型，同目录下有：\n' + '\n'.join(mains))
                return
            else:
                messagebox.showwarning('提示', '这是视觉投影文件(mmproj)，不能作为主模型运行')
                return
        items = load_customs()
        for it in items:
            if os.path.normcase(it.get('path', '')) == os.path.normcase(p):
                self.log('该模型已在列表: ' + os.path.basename(p))
                return
        items.append({'path': p})
        save_customs(items)
        self.models = list_models()
        self._refresh_models_combo()
        # 定位到新加的
        for i, (n, pp, sz, mm, cust) in enumerate(self.models):
            if os.path.normcase(pp) == os.path.normcase(p):
                self.model_combo.current(i)
                break
        self.log('已添加自定义模型: ' + os.path.basename(p))

    def del_custom_model(self):
        m = self._selected_model()
        if not m or not m[4]:
            messagebox.showinfo('提示', '当前选中项不是自定义模型（只有 [自定义] 标记的可以删除）')
            return
        p = m[1]
        items = [it for it in load_customs() if os.path.normcase(it.get('path', '')) != os.path.normcase(p)]
        save_customs(items)
        self.models = list_models()
        self._refresh_models_combo()
        self.log('已删除自定义模型: ' + os.path.basename(p))

    # ---------- 一键启动/关闭所有模块 ----------
    def start_all(self):
        self.log('🚀 启动所有模块 …')
        threading.Thread(target=self._start_all_worker, daemon=True).start()

    def _start_all_worker(self):
        self.start_llm()
        time.sleep(1)
        self.restart_comfy()
        self.start_gw()
        self.log('✅ 启动所有模块完成（详见各模块日志）')

    def stop_all(self):
        self.log('⏹ 关闭所有模块 …')
        threading.Thread(target=self._stop_all_worker, daemon=True).start()

    def _stop_all_worker(self):
        self.stop_llm()
        self.stop_comfy()
        self.stop_gw()
        self.log('✅ 关闭所有模块完成')

    # ---------- FreeLLMAPI（聚合免费模型网关，独立启停） ----------
    FREELMAPI_DIR = r'L:\OpenClaw\FreeLLMAPI'
    NODE_EXE = r'L:\OpenClaw\node\node.exe'
    def start_freelmapi(self):
        global freelmapi_proc
        try:
            if freelmapi_proc is not None and freelmapi_proc.poll() is None:
                self.log('FreeLLMAPI 已在运行')
                return
            logf = open(os.path.join(os.path.dirname(_BASE_DIR), 'freellmapi.log'), 'a', encoding='utf-8')
            freelmapi_proc = subprocess.Popen(
                [self.NODE_EXE, 'server\\dist\\index.js'],
                cwd=self.FREELMAPI_DIR, stdout=logf, stderr=subprocess.STDOUT,
                creationflags=0x08000000)
            self.log(f'FreeLLMAPI 已启动 PID={freelmapi_proc.pid} 端口 3001')
        except Exception as e:
            self.log('FreeLLMAPI 启动失败: ' + str(e))
    def _rapi_poll(self):
        try:
            import urllib.request
            try:
                import urllib.request as _u
                req = _u.Request('http://127.0.0.1:3001/v1/models', headers={'Authorization':'Bearer freellmapi-d5898bfbd4c0fe5153ac039b9d0f1f8fa1e02ad304ccd481'})
                _u.urlopen(req, timeout=1.5)
                self.rapi_status.itemconfig(self.rapi_lamp, fill='#4caf50')
            except Exception:
                self.rapi_status.itemconfig(self.rapi_lamp, fill='#555')
        except Exception:
            pass
        self.root.after(10000, self._rapi_poll)

    def stop_freelmapi(self):
        global freelmapi_proc
        try:
            if freelmapi_proc is not None and freelmapi_proc.poll() is None:
                freelmapi_proc.terminate()
                try:
                    freelmapi_proc.wait(timeout=5)
                except Exception:
                    freelmapi_proc.kill()
                self.log('FreeLLMAPI 已关闭')
            freelmapi_proc = None
        except Exception as e:
            self.log('FreeLLMAPI 关闭失败: ' + str(e))

    # ---------- 模型服务 ----------
    def start_llm(self):
        global llm_proc
        # 强制单实例：启动前先杀光所有残留 llama-server（防止多实例吃爆内存）
        try:
            subprocess.run(['taskkill', '/F', '/IM', 'llama-server.exe'],
                           capture_output=True, timeout=30,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            # 等端口真正释放，避免残留实例占端口导致误判"已在运行"
            for _ in range(10):
                if get_llm_pid() is None:
                    break
                time.sleep(0.5)
        except Exception:
            pass
        if llama_running():
            self.log('模型服务已在运行')
            return
        # 根据运行模式决定模型路径 / -ngl / 名称
        mode = self.llm_mode_var.get()
        mmproj = None
        if mode.startswith('4B CPU'):
            path, ngl, name = LLM_4B_PATH, '0', 'Qwen3.5-4B Q6_K (CPU 常驻)'
        elif mode.startswith('4B GPU'):
            path, ngl, name = LLM_4B_PATH, '999', 'Qwen3.5-4B Q6_K (GPU)'
        elif mode.startswith('9B GPU'):
            path, ngl, name = LLM_9B_PATH, '35', 'Qwen3.5-9B Q4_K_M (GPU)'
        else:
            # 自定义：跟随上方推理模型下拉，-ngl 按硬件配置/大小自适应
            m = self._selected_model()
            if not m:
                messagebox.showwarning('提示', '没有可用模型，请检查模型目录')
                return
            name, path, sz, mmproj, cust = m
            prof = load_profile()
            if prof:
                ngl = str(prof.get('ngl', 999))
                self.log('使用硬件配置: ' + prof.get('label', ''))
            elif sz > 8192:
                ngl = '35'
            else:
                ngl = '999'
        ctx = '65536'
        alias = 'local-model'
        cmd = [LLAMA_SERVER, '-m', path, '-ngl', ngl, '-c', ctx,
               '--host', '0.0.0.0', '--port', str(PORT_LLM), '--alias', alias,
               '--chat-template', 'chatml',
               '--reasoning', 'off', '--cache-type-k', 'q8_0', '--cache-type-v', 'q8_0']
        if mmproj:
            cmd += ['--mmproj', mmproj]
            self.log('视觉模型: ' + os.path.basename(mmproj))
        # 部分离载（非999非0）时关闭显存自动拟合，避免 fit 因手动 -ngl 中止加载
        if ngl not in ('999', '0'):
            cmd += ['--fit', 'off']
            self.log('部分离载模式: 已关闭显存自动拟合 (--fit off)')
        self.log('启动模型: ' + name + f'  (-ngl {ngl} -c {ctx})')
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            llm_logf = io.open(LLAMA_LOG, 'a', encoding='utf-8', errors='replace', buffering=1)
            llm_proc = subprocess.Popen(cmd, stdout=llm_logf, stderr=subprocess.STDOUT,
                                        creationflags=0x08000000)
        except Exception as e:
            self.log('启动失败: ' + str(e))
            return
        self.btn_llm_start.config(state='disabled')
        self._start_tail(LLAMA_LOG, 'llm')
        # 等待就绪
        threading.Thread(target=self._wait_llm_ready, daemon=True).start()

    def _read_llm_output(self, proc):
        try:
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    self.log(line[:200])
        except Exception:
            pass

    def _start_tail(self, paths, key):
        """后台线程：先回填日志文件尾部若干行，再增量读新日志，写入对应页签缓冲
        支持 paths 传字符串或路径列表（多日志合一页签，带 [文件名] 前缀）"""
        if isinstance(paths, str):
            paths = [paths]
        if key in getattr(self, '_tail_started', set()):
            return
        self._tail_started.add(key)
        def run():
            pos = {}
            try:
                for path in paths:
                    if os.path.isfile(path):
                        with io.open(path, 'r', encoding='utf-8', errors='replace') as f:
                            data = f.read()
                        lines = data.splitlines()
                        for line in lines[-400:]:
                            if line.strip():
                                self.log(f'[{os.path.basename(path)}] {line}'[:320], key)
                        pos[path] = os.path.getsize(path)
            except Exception:
                pass
            while getattr(self, '_tails_on', True):
                try:
                    for path in paths:
                        if not os.path.isfile(path):
                            continue
                        size = os.path.getsize(path)
                        if size > pos.get(path, 0):
                            with io.open(path, 'r', encoding='utf-8', errors='replace') as f:
                                f.seek(pos.get(path, 0))
                                data = f.read()
                            pos[path] = size
                            for line in data.splitlines():
                                if line.strip():
                                    self.log(f'[{os.path.basename(path)}] {line}'[:320], key)
                except Exception:
                    pass
                time.sleep(0.8)
        threading.Thread(target=run, daemon=True).start()

    def _start_tails(self):
        self._tails_on = True
        self._start_tail(LLAMA_LOG, 'llm')
        self._start_tail(COMFY_LOG, 'comfy')
        self._start_tail(GATEWAY_LOG, 'gw')
        self._start_tail(TEXTGEN_LOG, 'textgen')

    def _wait_llm_ready(self):
        for _ in range(180):
            if llama_running():
                self.log('✅ 模型服务就绪 (127.0.0.1:8080)')
                self.root.after(0, lambda: self.btn_llm_start.config(state='normal'))
                return
            time.sleep(1)
        self.log('⚠ 模型加载超时（180 秒），请查看上方日志')

    def stop_llm(self):
        if not llama_running():
            self.log('模型服务未在运行')
            return
        pid = get_llm_pid()
        if pid:
            stop_pid(pid)
            self.log(f'已停止模型服务 (PID {pid})')
        else:
            self.log('未找到模型进程')
        time.sleep(1)
        if llama_running():
            self.log('⚠ 服务仍在运行，请稍后重试')

    # ---------- ComfyUI ----------
    def cleanup_ram(self):
        self.log('清理内存 ...')
        threading.Thread(target=self._cleanup_worker, args=('RAMCleanup',), daemon=True).start()

    def stop_comfy(self):
        self.log('停止 ComfyUI ...')
        threading.Thread(target=self._stop_comfy_worker, daemon=True).start()

    def _stop_comfy_worker(self):
        pid = get_comfy_pid()
        if pid:
            stop_pid(pid)
            self.log(f'已停止 ComfyUI (PID {pid})')
        else:
            self.log(f'ComfyUI 未在运行（{COMFY_PORT}）')

    def cleanup_vram(self):
        self.log('清理显存 ...')
        threading.Thread(target=self._cleanup_worker, args=('VRAMCleanup',), daemon=True).start()

    def _cleanup_worker(self, node):
        for i in range(5):
            if comfy_alive():
                break
            time.sleep(2)
        else:
            self.log(f'ComfyUI 未运行（{COMFY_PORT}），无法执行 {node}')
            return
        try:
            if node == 'RAMCleanup':
                inputs = {'clean_file_cache': True, 'clean_processes': True,
                          'clean_dlls': True, 'retry_times': 3}
            else:  # VRAMCleanup
                inputs = {'offload_model': True, 'offload_cache': True}
            workflow = {node: {'class_type': node, 'inputs': inputs}}
            # 清理显存时链式挂上 RAMCleanup，释放更彻底（模型/缓存一次卸干净）
            if node == 'VRAMCleanup':
                workflow['RAMCleanup'] = {'class_type': 'RAMCleanup', 'inputs': {
                    'clean_file_cache': True, 'clean_processes': True,
                    'clean_dlls': True, 'retry_times': 3}}
            req = urllib.request.Request(
                f'http://127.0.0.1:{COMFY_PORT}/prompt',
                data=json.dumps({'prompt': workflow}).encode('utf-8'),
                headers={'Content-Type': 'application/json'})
            r = urllib.request.urlopen(req, timeout=30)
            body = r.read().decode('utf-8', 'replace')
            self.log(f'{node} 提交完成: {body[:100]}')
        except Exception as e:
            self.log(f'{node} 提交失败: {e}')

    # ---------- 傻酒馆 text-generation-webui ----------
    def _tg_refresh_profiles(self):
        for i in self.tg_profiles_tree.get_children():
            self.tg_profiles_tree.delete(i)
        p = _load_paths()
        for x in p.get('textgen_api_profiles', []):
            self.tg_profiles_tree.insert('', 'end', values=(x.get('name', ''), x.get('url', ''), x.get('model', '')))

    def _tg_load_profile(self):
        sel = self.tg_profiles_tree.selection()
        if not sel:
            return
        vals = self.tg_profiles_tree.item(sel[0], 'values')
        name = vals[0]
        p = _load_paths()
        for x in p.get('textgen_api_profiles', []):
            if x.get('name') == name:
                self.tg_api_url_var.set(x.get('url', ''))
                self.tg_api_key_var.set(x.get('key', ''))
                self.tg_api_model_var.set(x.get('model', ''))
                self.log('已加载配置: %s（双击行后填到上方输入框）' % name)
                return

    def _tg_profile_delete(self):
        sel = self.tg_profiles_tree.selection()
        if not sel:
            self.log('请先在表格里选中一行')
            return
        name = self.tg_profiles_tree.item(sel[0], 'values')[0]
        p = _load_paths()
        p['textgen_api_profiles'] = [x for x in p.get('textgen_api_profiles', []) if x.get('name') != name]
        _save_paths(p)
        self._tg_refresh_profiles()
        self.log('已删除配置: %s' % name)

    def _tg_api_save(self):
        try:
            p = _load_paths()
            url = self.tg_api_url_var.get().strip()
            key = self.tg_api_key_var.get().strip()
            mdl = self.tg_api_model_var.get().strip()
            # 用域名+模型名作为 profile 名
            try:
                dom = url.replace('https://', '').replace('http://', '').split('/')[0]
            except Exception:
                dom = url
            name = dom + ' / ' + mdl
            profs = p.get('textgen_api_profiles', [])
            replaced = False
            for i, x in enumerate(profs):
                if x.get('name') == name:
                    profs[i] = {'name': name, 'url': url, 'key': key, 'model': mdl}
                    replaced = True
                    break
            if not replaced:
                profs.append({'name': name, 'url': url, 'key': key, 'model': mdl})
            p['textgen_api_profiles'] = profs
            # 同时同步默认三字段（兼容旧逻辑）
            p['textgen_api_url'] = url
            p['textgen_api_key'] = key
            p['textgen_api_model'] = mdl
            _save_paths(p)
            self._tg_refresh_profiles()
            self.log('✅ 已保存为配置: %s' % name)
        except Exception as e:
            self.log('保存失败: ' + str(e))

    def _tg_set_lamp(self, color, tip=''):
        c = {'green': '#4caf50', 'red': '#e53935', 'yellow': '#ffc107', 'gray': '#666666'}.get(color, '#666666')
        try:
            self._tg_lamp.delete('all')
            self._tg_lamp.create_oval(2, 2, 14, 14, fill=c, outline='')
            self._tg_lamp.configure(cursor='question' if tip else '')
            if tip:
                self._tg_lamp.bind('<Enter>', lambda e: self._lamp_tip(tip))
                self._tg_lamp.bind('<Leave>', lambda e: self._lamp_tip(''))
        except Exception:
            pass

    def _lamp_tip(self, text):
        # 简单 tooltip：用一个 Toplevel 浮层
        try:
            if not text:
                if hasattr(self, '_tip_win') and self._tip_win:
                    self._tip_win.destroy(); self._tip_win = None
                return
            if hasattr(self, '_tip_win') and self._tip_win:
                self._tip_win.destroy()
            x = self._tg_lamp.winfo_rootx() + 10
            y = self._tg_lamp.winfo_rooty() + 20
            self._tip_win = tw = tk.Toplevel(self.root)
            tw.wm_overrideredirect(True)
            tw.wm_geometry('+%d+%d' % (x, y))
            tk.Label(tw, text=text, bg='#222', fg='#eee', padx=6, pady=2, font=('Microsoft YaHei UI', 9)).pack()
        except Exception:
            pass

    def _tg_api_test(self):
        url = self.tg_api_url_var.get().strip().rstrip('/')
        key = self.tg_api_key_var.get().strip()
        mdl = self.tg_api_model_var.get().strip()
        if not url or not key or not mdl:
            self.log('⚠️ API 地址 / Key / 模型 都要填')
            from tkinter import messagebox as _mb
            _mb.showwarning('在线API测试', 'API 地址 / Key / 模型 都要填')
            return
        self.log('测试在线API: %s 模型=%s ...' % (url, mdl))
        self._tg_set_lamp('yellow', '测试中...')
        threading.Thread(target=self._tg_api_test_worker, args=(url, key, mdl), daemon=True).start()

    def _tg_api_test_worker(self, url, key, mdl):
        import urllib.request, json as _json
        body = _json.dumps({
            'model': mdl,
            'messages': [{'role': 'user', 'content': 'ping'}],
            'max_tokens': 5,
        }).encode('utf-8')
        req = urllib.request.Request(
            url + '/chat/completions',
            data=body,
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key},
            method='POST')
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = _json.loads(r.read().decode('utf-8'))
                msg = data.get('choices', [{}])[0].get('message', {}).get('content', '')
                self.log('✅ API 正常，回复: ' + str(msg)[:80])
                self.root.after(0, lambda: self._tg_set_lamp('green', '正常'))
        except Exception as e:
            self.log('❌ API 测试失败: ' + str(e))
            self.root.after(0, lambda: self._tg_set_lamp('red', str(e)[:60]))

    def _tg_mode_changed(self):
        m = self.tg_mode_var.get()
        # 默认全部隐藏
        self.tg_model_row.grid_remove()
        self.tg_api_row.grid_remove()
        if m == 'remote':
            self.tg_hint_var.set('复用模式：启动后在网页 Model 页 API type 选 OpenAI，URL 填 http://127.0.0.1:%d/v1，点 Connect（不重复加载模型）' % PORT_LLM)
        elif m == 'local':
            self.tg_hint_var.set('本地模式：启动时用 --model 加载所选模型到显存；请先刷新并选一个 models/ 下的模型')
            self.tg_model_row.grid()
            self._tg_refresh_models()
        else:
            self.tg_hint_var.set('在线模式：填 API 地址/Key/模型名；启动后在网页 Model 页 API type 选 OpenAI，粘贴以下配置即可（首次配置后 textgen 会记住）')
            self.tg_api_row.grid()
            self._tg_refresh_profiles()

    def _tg_refresh_models(self):
        d = self.textgen_dir_var.get().strip()
        root = os.path.join(d, 'models') if d else ''
        items = []
        if os.path.isdir(root):
            for name in sorted(os.listdir(root)):
                p = os.path.join(root, name)
                if os.path.isdir(p) or name.lower().endswith(('.gguf', '.bin', '.safetensors')):
                    items.append(name)
        cur = self.tg_model_combo.get()
        self.tg_model_combo['values'] = items
        if cur in items:
            self.tg_model_combo.set(cur)
        elif items:
            self.tg_model_combo.set(items[0])

    # ---------- 傻酒馆 text-generation-webui ----------
    def _browse_textgen_dir(self):
        try:
            import tkinter.filedialog as fd
            d = fd.askdirectory(initialdir=self.textgen_dir_var.get() or 'L:\\')
            if d:
                self.textgen_dir_var.set(d)
                # 自动写回 paths.json
                try:
                    p = _load_paths()
                    p['textgen_dir'] = d
                    _save_paths(p)
                    self.log('傻酒馆目录已保存: ' + d)
                except Exception as se:
                    self.log('保存目录失败: ' + str(se))
        except Exception as e:
            self.log('浏览目录失败: ' + str(e))

    def start_textgen(self):
        d = self.textgen_dir_var.get().strip()
        if not d or not os.path.isdir(d):
            messagebox.showwarning('傻酒馆', '请先选择 text-generation-webui 安装目录')
            return
        server_py = os.path.join(d, 'server.py')
        if not os.path.isfile(server_py):
            messagebox.showwarning('傻酒馆', '目录下找不到 server.py，确认是 text-generation-webui 根目录')
            return
        # 选解释器：优先 installer_files/env/python.exe，其次 portable_env/python.exe，最后系统 python
        py = os.path.join(d, 'installer_files', 'env', 'python.exe')
        if not os.path.isfile(py):
            py = os.path.join(d, 'portable_env', 'python.exe')
        if not os.path.isfile(py):
            py = 'python'
        self._tg_py = py
        mode = self.tg_mode_var.get()
        self.log('启动傻酒馆（模式: %s）...' % {'remote':'复用llama-server','local':'本地模型','online':'在线API'}[mode])
        threading.Thread(target=lambda: self._start_textgen_worker(d, py, server_py), daemon=True).start()

    def _start_textgen_worker(self, d, py, server_py):
        try:
            os.makedirs(os.path.dirname(TEXTGEN_LOG), exist_ok=True)
            # 确保 user_data 下 textgen 需要的子目录都存在
            ud = os.path.join(d, 'user_data')
            for sub in ['models', 'loras', 'characters', 'prompts', 'instruction-notebooks', 'stories', 'history']:
                os.makedirs(os.path.join(ud, sub), exist_ok=True)
            lf = io.open(TEXTGEN_LOG, 'a', encoding='utf-8', errors='replace', buffering=1)
            cmd = [py, server_py, '--listen', '--listen-host', '0.0.0.0', '--listen-port', str(TEXTGEN_PORT)]
            if self.tg_mode_var.get() == 'local':
                mdl = self.tg_model_combo.get().strip()
                if mdl:
                    cmd += ['--model', mdl]
            env = os.environ.copy()
            env['PYTHONIOENCODING'] = 'utf-8'
            env['PYTHONUTF8'] = '1'
            subprocess.Popen(cmd, cwd=d, stdout=lf, stderr=subprocess.STDOUT,
                             env=env, creationflags=0x08000000)
            self._start_tail(TEXTGEN_LOG, 'textgen')
            self.log('傻酒馆后台启动中（日志在「日志→傻酒馆」页签）...')
        except Exception as e:
            self.log('傻酒馆启动失败: ' + str(e))
            return
        mode = self.tg_mode_var.get()
        for i in range(90):
            time.sleep(2)
            if textgen_alive():
                self.log('✅ 傻酒馆就绪 http://127.0.0.1:%d' % TEXTGEN_PORT)
                if mode == 'online':
                    url = self.tg_api_url_var.get().strip()
                    key = self.tg_api_key_var.get().strip()
                    mdl = self.tg_api_model_var.get().strip()
                    self.log('→ 在线API：网页 Model 页 API type 选 OpenAI，填 URL=%s Key=%s Model=%s' % (url, (key[:6]+'...' if key else '(空)'), mdl))
                    try:
                        with io.open(_PATHS_CFG, 'r', encoding='utf-8') as f:
                            _p = json.load(f)
                    except Exception:
                        _p = {}
                    _p['textgen_api_url'] = url
                    _p['textgen_api_key'] = key
                    _p['textgen_api_model'] = mdl
                    _save_paths(_p)
                return
        self.log('⚠ 傻酒馆 180 秒未就绪，请查看 textgen.log')

    def stop_textgen(self):
        pid = get_port_pid(TEXTGEN_PORT)
        if pid:
            stop_pid(pid)
            self.log('已停止傻酒馆 (PID %d)' % pid)
        else:
            self.log('傻酒馆未在运行（%d 未监听）' % TEXTGEN_PORT)

    def open_textgen(self):
        webbrowser.open('http://127.0.0.1:%d/' % TEXTGEN_PORT)

    def open_textgen_dir(self):
        d = self.textgen_dir_var.get().strip()
        if os.path.isdir(d):
            webbrowser.open(d)

    def start_sillytavern(self):
        d = self.st_dir_var.get().strip()
        if st_alive():
            self.log('SillyTavern 已在运行 http://127.0.0.1:%d' % ST_PORT)
            webbrowser.open('http://127.0.0.1:%d/' % ST_PORT)
            return
        if not d or not os.path.isfile(os.path.join(d, 'server.js')):
            messagebox.showwarning('SillyTavern', '找不到目录下的 server.js，确认是 SillyTavern 根目录')
            return
        try:
            os.makedirs(os.path.dirname(ST_LOG), exist_ok=True)
            lf = io.open(ST_LOG, 'a', encoding='utf-8', errors='replace', buffering=1)
            env = os.environ.copy()
            env['NODE_ENV'] = 'production'
            # 优先用整合包自带的 node
            node_exe = 'node'
            for cand in [r'L:\SillyTavern-1.11.5整合包\SillyTavern-1.11.5\node.exe',
                         r'L:\SillyTavern-1.11.5整合包\node.exe']:
                if os.path.isfile(cand):
                    node_exe = cand
                    break
            subprocess.Popen([node_exe, 'server.js'], cwd=d, stdout=lf, stderr=subprocess.STDOUT,
                             env=env, creationflags=0x08000000)
            self.log('SillyTavern 启动中（日志 logs/sillytavern.log）...')
        except Exception as e:
            self.log('SillyTavern 启动失败: ' + str(e))
            return
        # 等待就绪
        for i in range(30):
            time.sleep(2)
            if st_alive():
                self.log('✅ SillyTavern 就绪 http://127.0.0.1:%d' % ST_PORT)
                webbrowser.open('http://127.0.0.1:%d/' % ST_PORT)
                return
        self.log('⚠ SillyTavern 60秒未就绪，查看 logs/sillytavern.log')

    def _browse_st_dir(self):
        try:
            import tkinter.filedialog as fd
            d = fd.askdirectory(initialdir=self.st_dir_var.get() or 'L:\\')
            if d:
                self.st_dir_var.set(d)
                try:
                    p = _load_paths()
                    p['sillytavern_dir'] = d
                    _save_paths(p)
                    self.log('SillyTavern 目录已保存: ' + d)
                except Exception as se:
                    self.log('保存目录失败: ' + str(se))
        except Exception as e:
            self.log('浏览目录失败: ' + str(e))

    def stop_sillytavern(self):
        pid = get_port_pid(ST_PORT)
        if pid:
            try:
                os.kill(pid, 9)
                self.log('已停止 SillyTavern（PID %d）' % pid)
            except Exception as e:
                self.log('停止失败: ' + str(e))
        else:
            self.log('SillyTavern 未在运行（%d 未监听）' % ST_PORT)

    def open_sillytavern(self):
        webbrowser.open('http://127.0.0.1:%d/' % ST_PORT)

    def _save_st_backend(self):
        '''保存酒馆后端模式配置'''
        try:
            import json as _json
            p = os.path.join(_BASE_DIR, 'paths.json')
            cfg = _json.load(open(p, 'r', encoding='utf-8')) if os.path.isfile(p) else {}
            cfg['st_backend'] = self.st_backend_var.get()
            cfg['st_online_api_url'] = self.st_api_url_var.get().strip()
            cfg['st_online_api_key'] = self.st_api_key_var.get().strip()
            _json.dump(cfg, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
            self.log('✅ 酒馆后端配置已保存: ' + self.st_backend_var.get())
        except Exception as e:
            self.log('保存失败: ' + str(e))

    def restart_comfy(self):
        self.log('重启 ComfyUI ...')
        threading.Thread(target=self._restart_comfy_worker, daemon=True).start()

    def _restart_comfy_worker(self):
        pid = get_comfy_pid()
        if pid:
            stop_pid(pid)
            self.log(f'已停止 ComfyUI (PID {pid})')
            time.sleep(2)
        else:
            self.log(f'未发现 ComfyUI 进程（{COMFY_PORT} 未监听）')
        if not os.path.isfile(COMFY_PY):
            self.log('找不到 ' + COMFY_PY)
            return
        if not os.path.isfile(os.path.join(COMFY_DIR, 'main.py')):
            self.log('找不到 ' + os.path.join(COMFY_DIR, 'main.py'))
            return
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            comfy_logf = io.open(COMFY_LOG, 'a', encoding='utf-8', errors='replace', buffering=1)
            subprocess.Popen([COMFY_PY, os.path.join(COMFY_DIR, 'main.py'),
                              '--port', str(COMFY_PORT), '--listen', '0.0.0.0'],
                             cwd=COMFY_DIR, stdout=comfy_logf, stderr=subprocess.STDOUT,
                             creationflags=0x08000000)
            self.log('已后台启动 ComfyUI（日志进 日志窗口→ComfyUI 页签）...')
            self._start_tail(COMFY_LOG, 'comfy')
        except Exception as e:
            self.log('启动失败: ' + str(e))
            return
        for i in range(90):
            time.sleep(2)
            if comfy_alive():
                self.log(f'✅ ComfyUI 就绪（{2 + i * 2}s）')
                self.root.after(0, self._refresh_gen_models)
                return
        self.log('⚠ ComfyUI 180 秒未就绪，请打开日志窗口查看 ComfyUI 页签')

    # ---------- 网关 ----------
    # ---------- 广域网（Tailscale）与路径设置 ----------
    def _browse_path(self, key, var):
        try:
            import tkinter.filedialog as fd
            initial = var.get() or _DEFAULT_PATHS.get(key, '')
            d = fd.askdirectory(initialdir=initial if os.path.isdir(initial) else os.path.dirname(initial))
            if d:
                var.set(d)
        except Exception as e:
            self.log('浏览路径失败: ' + str(e))

    def _apply_patches_after_path_change(self):
        """路径变更后自动重打生图链路补丁（媒体白名单跟随新 comfy_root），不自动重启网关。
        补丁脚本 apply_openclaw_patches.py 需与 paths.json 同目录（L:\\OpenClaw\\OpenClawData\\console）。"""
        script = os.path.join(_BASE_DIR, 'apply_openclaw_patches.py')
        if not os.path.isfile(script):
            self.log('未找到 apply_openclaw_patches.py（应与本程序同目录），跳过补丁重打')
            return
        py = r'L:\OpenClaw\python\python.exe'
        if not os.path.isfile(py):
            py = sys.executable
        try:
            subprocess.Popen([py, script, '--no-restart'],
                             creationflags=0x08000000,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.log('✅ 已自动重打生图链路补丁（白名单已跟随新 ComfyUI 目录；重启网关后生效）')
        except Exception as e:
            self.log('自动补丁重打失败: ' + str(e))

    def save_paths_ui(self):
        try:
            p = dict(_DEFAULT_PATHS)
            p.update({k: v.get().strip().rstrip('\\/') for k, v in self.path_vars.items()})
            if _save_paths(p):
                _apply_paths(p)  # 即时更新内存路径，无需重启控制台
                self._apply_patches_after_path_change()
                self.log('✅ 路径已保存并即时生效（重启对应服务后使用新路径）')
                messagebox.showinfo('路径设置', '路径已保存并即时生效。\n已自动重打生图链路补丁（媒体白名单跟随新 ComfyUI 目录）。\n重启对应服务（模型/网关/生图）即使用新路径。\n重装系统后改完路径，点「一键复原广域网」即可复原。')
            else:
                messagebox.showerror('路径设置', '保存失败，请检查目录权限')
        except Exception as e:
            self.log('保存路径失败: ' + str(e))

    # ---------- 生图方式（固定 ComfyUI / 自动选择） ----------
    def _gen_mode_current(self):
        """读取当前生图方式：tools.deny 含 image_generate 则固定 ComfyUI"""
        try:
            if not os.path.isfile(CONFIG_PATH):
                return 'comfy'
            cfg = json.loads(io.open(CONFIG_PATH, encoding='utf-8').read() or '{}')
            deny = (cfg.get('tools') or {}).get('deny') or []
            return 'comfy' if 'image_generate' in deny else 'auto'
        except Exception:
            return 'comfy'

    def save_gen_mode(self):
        """保存生图方式：固定 ComfyUI 写 tools.deny + AGENTS.md 指令；自动选择则移除"""
        try:
            mode = self.gen_mode_var.get()
            cfg = {}
            if os.path.isfile(CONFIG_PATH):
                try:
                    cfg = json.loads(io.open(CONFIG_PATH, encoding='utf-8').read() or '{}')
                except Exception:
                    cfg = {}
            tools = cfg.setdefault('tools', {})
            deny = [d for d in (tools.get('deny') or []) if d != 'image_generate']
            if mode == 'comfy':
                deny.append('image_generate')
            if deny:
                tools['deny'] = deny
            else:
                tools.pop('deny', None)
            with io.open(CONFIG_PATH, 'w', encoding='utf-8', newline='\n') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            # AGENTS.md 写/清生图指令（双保险，防模型自作主张用内置工具）
            ws = ((cfg.get('agents') or {}).get('defaults') or {}).get('workspace') or \
                 os.path.join(os.path.expanduser('~'), '.openclaw', 'workspace')
            agents_md = os.path.join(ws, 'AGENTS.md')
            import re as _re
            line_on = ('## 生图方式\n'
                       '- 生图一律使用 `comfyui__generate_image` 工具（ComfyUI MCP），不要使用内置 `image_generate` 工具。\n'
                       '- 不要向用户弹出“选择图生图工具”，直接用本地 ComfyUI 生图。\n')
            block_on = '<!-- gen-mode:comfy -->\n' + line_on + '<!-- /gen-mode:comfy -->\n'
            txt = ''
            if os.path.isfile(agents_md):
                txt = io.open(agents_md, encoding='utf-8').read()
            txt = _re.sub(r'<!-- gen-mode:comfy -->.*?<!-- /gen-mode:comfy -->\n?', '', txt, flags=_re.S)
            if mode == 'comfy':
                txt = txt.rstrip() + '\n\n' + block_on
            with io.open(agents_md, 'w', encoding='utf-8', newline='\n') as f:
                f.write(txt)
            label = '固定 ComfyUI' if mode == 'comfy' else '自动选择'
            self.log('✅ 生图方式已设为：' + label + '（重启网关生效）')
            messagebox.showinfo('生图方式', '已保存，重启网关后生效。\n\n固定 ComfyUI：微信/各通道生图直接走本地 ComfyUI，不再弹工具选择框。')
        except Exception as e:
            self.log('保存生图方式失败: ' + str(e))
            messagebox.showerror('生图方式', '保存失败：' + str(e))

    def _wan_hostname(self):
        exe = TAILSCALE_EXE
        if not os.path.isfile(exe):
            self.log('未找到 Tailscale，请先安装（winget install Tailscale.Tailscale）')
            return ''
        try:
            r = subprocess.run([exe, 'status', '--json'], capture_output=True, text=True,
                               timeout=15, encoding='utf-8', errors='replace')
            j = json.loads(r.stdout or '{}')
            dns = ((j.get('Self') or {}).get('DNSName') or '').rstrip('.')
            return 'https://' + dns + '/' if dns else ''
        except Exception as e:
            self.log('读取 Tailscale 地址失败: ' + str(e))
            return ''

    def wan_copy_url(self):
        host = self._wan_hostname()
        if host:
            self.root.clipboard_clear(); self.root.clipboard_append(host)
            self.log('已复制远程地址: ' + host)

    def wan_open_url(self):
        host = self._wan_hostname()
        if host:
            webbrowser.open(host)

    def _open_comfy_remote(self):
        """打开 ComfyUI 远程地址（8443 通道）"""
        host = self._wan_hostname()
        if host:
            webbrowser.open(host.rstrip('/') + ':8443')

    def wan_restore(self):
        """一键复原广域网（终态）：
        Tailscale PATH 注入 -> serve reset 清旧规则 -> openclaw.json 写 serve 托管配置
        -> 重启网关（OpenClaw 自动 claim 443 根路径）-> ComfyUI 走 8443 通道"""
        def work():
            self.log('🔧 一键复原广域网…')
            exe = TAILSCALE_EXE
            if not os.path.isfile(exe):
                self.log('❌ 未找到 Tailscale，请先安装：winget install Tailscale.Tailscale')
                self.wan_info.set('未安装 Tailscale')
                return
            try:
                r = subprocess.run([exe, 'status'], capture_output=True, text=True, timeout=15,
                                   encoding='utf-8', errors='replace')
                s = (r.stdout + r.stderr)
                if 'stopped' in s.lower() or 'starting' in s.lower() or 'NoState' in s:
                    self.log('Tailscale 未连接，尝试拉起…')
                    subprocess.run([exe, 'up'], capture_output=True, text=True, timeout=30)
                    time.sleep(6)
            except Exception as e:
                self.log('检查 Tailscale 失败: ' + str(e))
            # 1) PATH 注入（重装系统后 spawn tailscale 依赖）
            try:
                ts_dir = os.path.dirname(exe)
                cur = os.environ.get('PATH', '')
                if ts_dir and ts_dir not in cur:
                    os.environ['PATH'] = ts_dir + ';' + cur
                    subprocess.run(['powershell', '-NoProfile', '-Command',
                                    "$p=[Environment]::GetEnvironmentVariable('Path','User'); "
                                    "if($p -notlike '*Tailscale*'){[Environment]::SetEnvironmentVariable('Path', 'C:\\Program Files\\Tailscale;' + $p, 'User')}"],
                                   capture_output=True, text=True, timeout=15,
                                   creationflags=0x08000000)
                    self.log('✅ 已把 Tailscale 加入用户 PATH')
            except Exception as e:
                self.log('PATH 注入失败: ' + str(e))
            # 2) 清空旧 serve 规则（防止 443 被旧规则占用导致 OpenClaw 无法接管）
            subprocess.run([exe, 'serve', 'reset'], capture_output=True, text=True, timeout=30)
            self.log('已清空旧 serve 规则')
            # 3) 写入 openclaw.json 终态 gateway 配置（serve 托管 + allowTailscale）
            try:
                oc_cfg = os.path.join(OPENCLAW_DATA, 'openclaw.json')
                cfg = {}
                if os.path.isfile(oc_cfg):
                    try:
                        cfg = json.loads(io.open(oc_cfg, encoding='utf-8').read() or '{}')
                    except Exception:
                        cfg = {}
                gw = cfg.setdefault('gateway', {})
                gw['mode'] = 'local'
                gw['bind'] = 'loopback'
                gw['port'] = PORT_GW
                auth = gw.setdefault('auth', {})
                auth['mode'] = 'token'
                auth['allowTailscale'] = True
                if not auth.get('token'):
                    auth['token'] = gw_token() or 'replace-with-your-token'
                gw['tailscale'] = {'mode': 'serve'}
                gw.pop('trustedProxies', None)
                with io.open(oc_cfg, 'w', encoding='utf-8', newline='\n') as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
                self.log('✅ openclaw.json 已写入 serve 托管配置')
            except Exception as e:
                self.log('写入 openclaw.json 失败: ' + str(e))
            # 4) 重启网关（OpenClaw 自动 claim 443 根路径）
            self.stop_gw()
            time.sleep(2)
            self.start_gw()
            time.sleep(7)
            # 5) ComfyUI 8443 通道（443 被 OpenClaw claim，ComfyUI 走独立端口；根路径全代理，前端相对路径/ws/api 全覆盖）
            subprocess.run([exe, 'serve', '--bg', '--https=8443',
                            f'http://127.0.0.1:{COMFY_PORT}'], capture_output=True, text=True, timeout=30)
            host = self._wan_hostname()
            if host:
                self.wan_info.set('已启用 · ' + host + ' · ComfyUI: ' + host + ':8443')
                self.log('✅ 广域网已复原：' + host + ' · ComfyUI: ' + host + ':8443')
            else:
                self.wan_info.set('配置已写入（Tailscale 未登录，登录后重试）')
                self.log('⚠ 配置已写入，Tailscale 未登录，请登录后重试')
        threading.Thread(target=work, daemon=True).start()

    def start_gw(self):
        self.log('启动网关...')
        # 直接 node 启动，绕开 gateway.cmd 的 --task-supervisor（会导致 supervisor 反复重启网关子进程）
        try:
            gw_logf = io.open(os.path.join(LOG_DIR, 'gateway.log'), 'a',
                              encoding='utf-8', errors='replace', buffering=1)
            env = dict(os.environ)
            env['OPENCLAW_STATE_DIR'] = os.path.dirname(CONFIG_PATH)
            ts_dir = os.path.dirname(TAILSCALE_EXE) if os.path.isfile(TAILSCALE_EXE) else ''
            env['PATH'] = (ts_dir + ';' if ts_dir else '') + os.path.dirname(NODE_EXE) + ';' + env.get('PATH', '')
            subprocess.Popen([NODE_EXE, '--max-old-space-size=8192', OPENCLAW_MJS,
                              'gateway', '--port', str(PORT_GW)],
                             stdout=gw_logf, stderr=subprocess.STDOUT,
                             creationflags=subprocess.CREATE_NO_WINDOW, env=env)
            self.log('已后台启动网关（直接 node）...')
        except Exception as e:
            self.log('网关启动异常: ' + str(e))
            return
        time.sleep(3)
        if gw_running():
            self.log('✅ 网关运行中')
        else:
            self.log('⚠ 网关未就绪，请查看日志')

    def stop_gw(self):
        self.log('停止网关...')
        # CLI 服务管理命令（gateway stop）在非默认 state dir 下会被拒绝，
        # 直接终止监听网关端口的进程（含子进程树）
        try:
            out = subprocess.run(
                ['powershell', '-NoProfile', '-Command',
                 f'(Get-NetTCPConnection -LocalPort {PORT_GW} -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess)'],
                capture_output=True, text=True, timeout=15, encoding='utf-8', errors='replace',
                creationflags=0x08000000).stdout.strip()
            pids = [int(x) for x in out.split() if x.isdigit()]
            if pids:
                for pid in pids:
                    stop_pid(pid)
                self.log('✅ 网关已停止（PID %s）' % pids)
            else:
                self.log('网关未在运行')
        except Exception as e:
            self.log('停止网关异常: ' + str(e))

    def open_dashboard(self):
        url = DASH_URL
        token = gw_token()
        # 优先用带令牌的配对链接，避免浏览器又要手动粘贴
        code, out = run_cli(['gateway', 'dashboard'], timeout=30)
        if 'http' in out.lower() or 'http' in out.lower():
            for line in out.splitlines():
                if 'http://127.0.0.1' in line or 'http' in line and 'pair' in line.lower():
                    url = line.strip().split(' ')[-1]
                    break
        webbrowser.open(url)
        self.log('已在浏览器打开控制台')


    # ---------- 开机自启 ----------
    def _autostart_exists(self):
        return os.path.isfile(AUTOSTART_VBS)

    def toggle_autostart(self):
        if self.autostart_var.get():
            self._enable_autostart()
        else:
            self._disable_autostart()

    def _enable_autostart(self):
        m = self._selected_model()
        if not m:
            messagebox.showwarning('提示', '没有可用模型')
            self.autostart_var.set(False)
            return
        name, path, sz, mmproj, cust = m
        if sz > 8192:
            ngl, ctx = '35', '8192'
        else:
            ngl, ctx = '999', '65536'
        extra = ''
        if 'gemma' in name.lower():
            extra += ' --reasoning off --cache-type-k q8_0 --cache-type-v q8_0'
        if mmproj:
            ctx = '65536'
            extra += f' --mmproj """{mmproj}"""'
        if ngl != '999':
            extra += ' --fit off'
        vbs = (
            'Set ws = CreateObject("Wscript.Shell")\n'
            f'ws.Run """{LLAMA_SERVER}"" -m ""{path}"" -ngl {ngl} -c {ctx}'
            f'{extra} --host 127.0.0.1 --port {PORT_LLM} --alias local-model", 0, False\n'
        )
        try:
            with open(AUTOSTART_VBS, 'w', encoding='utf-8') as f:
                f.write(vbs)
            self.log(f'已启用开机自启: {name}')
        except Exception as e:
            self.log('设置开机自启失败: ' + str(e))
            self.autostart_var.set(False)

    def _disable_autostart(self):
        try:
            if os.path.isfile(AUTOSTART_VBS):
                os.remove(AUTOSTART_VBS)
            self.log('已取消开机自启')
        except Exception as e:
            self.log('取消开机自启失败: ' + str(e))

    # ---------- 网关开机自启（计划任务） ----------
    GW_TASK = 'OpenClaw Gateway'

    def _gw_autostart_exists(self):
        r = subprocess.run(['schtasks', '/Query', '/TN', self.GW_TASK],
                           capture_output=True, text=True, errors='replace', timeout=20)
        return 'SUCCESS' in (r.stdout or '') or 'Running' in (r.stdout or '')

    def toggle_gw_autostart(self):
        if self.gw_autostart_var.get():
            self._enable_gw_autostart()
        else:
            self._disable_gw_autostart()

    def _enable_gw_autostart(self):
        try:
            # 先删旧任务再建（旧任务指向 gateway.vbs→gateway.cmd 含 --task-supervisor，会导致重启循环）
            subprocess.run(['schtasks', '/Delete', '/TN', self.GW_TASK, '/F'],
                           capture_output=True, text=True, errors='replace', timeout=30)
            node = NODE_EXE
            mjs = OPENCLAW_MJS
            tr = ('"' + node + '" --max-old-space-size=8192 "' + mjs +
                  '" gateway --port ' + str(PORT_GW))
            r = subprocess.run(
                ['schtasks', '/Create', '/TN', self.GW_TASK,
                 '/TR', tr, '/SC', 'ONLOGON', '/F'],
                capture_output=True, text=True, errors='replace', timeout=30)
            out = (r.stdout or r.stderr or '').strip()
            if 'SUCCESS' in out:
                self.log('已启用网关开机自启')
            else:
                self.log('启用网关开机自启失败: ' + out)
                self.gw_autostart_var.set(False)
        except Exception as e:
            self.log('启用网关开机自启失败: ' + str(e))
            self.gw_autostart_var.set(False)

    # ---------- ComfyUI 开机自启（启动目录 VBS） ----------
    def _comfy_autostart_exists(self):
        return os.path.isfile(COMFY_AUTOSTART_VBS)

    def toggle_comfy_autostart(self):
        if self.comfy_autostart_var.get():
            self._enable_comfy_autostart()
        else:
            self._disable_comfy_autostart()

    def _enable_comfy_autostart(self):
        try:
            vbs = ('Set ws = CreateObject("Wscript.Shell")\n'
                   'ws.Run "wscript \"%s\\comfy_start.vbs\"", 0, False\n' % COMFY_ROOT)
            with open(COMFY_AUTOSTART_VBS, 'w', encoding='utf-8') as f:
                f.write(vbs)
            self.log('已启用 ComfyUI 开机自启')
        except Exception as e:
            self.log('设置 ComfyUI 自启失败: ' + str(e))
            self.comfy_autostart_var.set(False)

    def _disable_comfy_autostart(self):
        try:
            if os.path.isfile(COMFY_AUTOSTART_VBS):
                os.remove(COMFY_AUTOSTART_VBS)
            self.log('已取消 ComfyUI 开机自启')
        except Exception as e:
            self.log('取消 ComfyUI 自启失败: ' + str(e))

    def _disable_gw_autostart(self):
        try:
            if not self._gw_autostart_exists():
                self.log('网关开机自启已取消')
                return
            r = subprocess.run(['schtasks', '/Delete', '/TN', self.GW_TASK, '/F'],
                               capture_output=True, text=True, errors='replace', timeout=30)
            out = (r.stdout or r.stderr or '').strip()
            if 'SUCCESS' in out:
                self.log('已取消网关开机自启')
            else:
                self.log('取消网关开机自启失败: ' + out)
                self.gw_autostart_var.set(True)
        except Exception as e:
            self.log('取消网关开机自启失败: ' + str(e))
            self.gw_autostart_var.set(True)

    # ---------- 状态轮询 ----------
    def _poll_loop(self):
        while self._polling:
            try:
                llm_on = llama_running()
                gw_on = gw_running()
                comfy_on = comfy_alive()
                tg_on = textgen_alive()
                self.root.after(0, lambda: self._update_status(llm_on, gw_on, comfy_on, tg_on))
                st = sys_stats()
                self.root.after(0, lambda: self._update_sys(st))
            except Exception:
                pass
            time.sleep(2)

    def _update_sys(self, st):
        try:
            parts = []
            if st.get('cpu') is not None:
                parts.append('CPU %d%%' % round(st['cpu']))
            if st.get('mem_pct') is not None:
                parts.append('内存 %d%%' % st['mem_pct'])
            g = st.get('gpu')
            if g:
                parts.append('GPU %d%%' % round(g['util']))
                parts.append('显存 %.1f/%.1fGB' % (g['used_mb'] / 1024.0, g['total_mb'] / 1024.0))
                parts.append('温度 %d°' % round(g['temp']))
            text = '系统 ' + ('  '.join(parts) if parts else '--')
            if hasattr(self, 'lbl_hw'):
                self.lbl_hw.config(text=text)
            if hasattr(self, 'lbl_sys'):
                self.lbl_sys.config(text=text)
        except Exception:
            pass

    def _update_status(self, llm_on, gw_on, comfy_on=None, tg_on=None):
        self._set_lamp(self.lamp_llm, llm_on)
        self._set_lamp(self.lamp_gw, gw_on)
        if gw_on:
            self.gw_info.set(f'运行中 · {DASH_URL}')
        else:
            self.gw_info.set('未运行')
        # 模型按钮状态
        self.btn_llm_start.config(state='normal' if not llm_on else 'disabled')
        self.btn_llm_stop.config(state='disabled' if not llm_on else 'normal')
        if hasattr(self, 'lamp_comfy'):
            if comfy_on is None:
                comfy_on = comfy_alive()
            self._set_lamp(self.lamp_comfy, comfy_on)
        if hasattr(self, 'lamp_textgen'):
            if tg_on is None:
                tg_on = textgen_alive()
            self._set_lamp(self.lamp_textgen, tg_on)
            self.textgen_info.set('运行中 · http://127.0.0.1:%d' % TEXTGEN_PORT if tg_on else '未运行')
            try:
                st_on = st_alive()
                self.st_info.set('运行中 · http://127.0.0.1:%d' % ST_PORT if st_on else '未运行')
            except Exception:
                pass

    def on_close(self):
        self._polling = False
        # 记忆窗口大小/位置，下次启动恢复
        try:
            geo = self.root.geometry()   # 形如 '980x540+100+50'
            import re as _re
            m = _re.match(r'(\d+)x(\d+)\+(-?\d+)\+(-?\d+)', geo)
            if m:
                _state = {'w': int(m.group(1)), 'h': int(m.group(2)),
                          'x': int(m.group(3)), 'y': int(m.group(4))}
                with io.open(_UI_STATE_CFG, 'w', encoding='utf-8') as _f:
                    json.dump(_state, _f)
        except Exception:
            pass
        self.root.destroy()

def _ensure_single_instance():
    """Windows 互斥量单实例锁：已有实例在跑则返回 False（防止开一个程序弹两个窗口）"""
    global _SINGLE_MUTEX
    try:
        import ctypes
        _SINGLE_MUTEX = ctypes.windll.kernel32.CreateMutexW(None, False,
                                                            'OpenClawConsole_SingleInstance')
        # ERROR_ALREADY_EXISTS = 183
        return ctypes.windll.kernel32.GetLastError() != 183
    except Exception:
        return True

_SINGLE_MUTEX = None

def main():
    # DPI 感知：按物理像素布局，避免系统缩放（125%/150%）把窗口放大到巨大
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
    if not _ensure_single_instance():
        try:
            messagebox.showwarning('OpenClaw 控制台', '控制台已在运行（单实例）。\n请到已打开的窗口操作。')
        except Exception:
            pass
        return
    root = tk.Tk()
    app = App(root)
    root.protocol('WM_DELETE_WINDOW', app.on_close)
    root.mainloop()

if __name__ == '__main__':
    main()

