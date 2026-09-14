# -*- coding: utf-8 -*-
"""
OpenClaw 控制台 v2.2
管理本地模型服务(llama-server) + OpenClaw Gateway + 控制台入口
"""
import os, sys, json, time, glob, io, subprocess, threading, webbrowser, tkinter as tk
from tkinter import ttk, messagebox
import urllib.request

# ============ 常量 ============
# 路径配置（paths.json 可自定义，重装系统后一键复原的依据）
# PyInstaller onefile 下 __file__ 指向临时解压目录，写入会随进程退出丢失，
# 故固定状态目录用真实路径（paths.json 所在目录）；源码运行则用脚本目录。
_BASE_DIR = r'L:\OpenClaw\OpenClawData\console'
if not os.path.isdir(_BASE_DIR):
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_PATHS_CFG = os.path.join(_BASE_DIR, 'paths.json')
_DEFAULT_PATHS = {
    'llama_dir':      r'L:\OpenClaw\llama',
    'comfy_root':     r'L:\OpenClaw\ComfyUI',
    'openclaw_data':  r'L:\OpenClaw\OpenClawData',
    'openclaw_npm':   r'L:\OpenClaw\npm',
    'node_exe':       r'C:\Program Files\nodejs\node.exe',
    'tailscale_exe':  r'C:\Program Files\Tailscale\tailscale.exe',
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
PORT_LLM      = 8080
PORT_GW       = 18789
DASH_URL      = f'http://127.0.0.1:{PORT_GW}/'
STARTUP_DIR   = os.path.join(os.environ['APPDATA'], r'Microsoft\Windows\Start Menu\Programs\Startup')
AUTOSTART_VBS = os.path.join(STARTUP_DIR, 'OpenClawModel.vbs')
COMFY_AUTOSTART_VBS = os.path.join(STARTUP_DIR, 'OpenClawComfy.vbs')

llm_proc = None          # llama-server 子进程
llm_pid  = None          # 记录 pid（含外部已启动的）
log_lock = threading.Lock()

# ============ 工具函数 ============
def http_ok(url, timeout=3):
    try:
        r = urllib.request.urlopen(url, timeout=timeout)
        return r.status == 200
    except Exception:
        return False

def llama_running():
    # 双通道判活：HTTP 通算活；HTTP 忙（大 prompt 处理中）但端口在监听也算活
    if http_ok(f'http://127.0.0.1:{PORT_LLM}/v1/models', timeout=5):
        return True
    return get_llm_pid() is not None

def gw_running():
    return http_ok(f'http://127.0.0.1:{PORT_GW}/')

COMFY_PORT = 8189
COMFY_ROOT = _PATHS['comfy_root']
COMFY_BAT  = os.path.join(COMFY_ROOT, 'start_comfy.bat')
COMFY_PY   = os.path.join(COMFY_ROOT, 'python_embeded', 'python.exe')
COMFY_DIR  = os.path.join(COMFY_ROOT, 'ComfyUI')
LOG_DIR    = r'L:\OpenClaw\OpenClawData\console\logs'
LLAMA_LOG  = os.path.join(LOG_DIR, 'llama.log')
COMFY_LOG  = os.path.join(LOG_DIR, 'comfyui.log')

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
                           creationflags=subprocess.CREATE_NO_WINDOW)
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
    """查 8188 端口 ComfyUI 的 PID"""
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
    """调用 openclaw CLI（用系统 Node 24）"""
    try:
        r = subprocess.run([NODE_EXE, OPENCLAW_MJS] + args,
                           capture_output=True, text=True, timeout=timeout,
                           encoding='utf-8', errors='replace', creationflags=subprocess.CREATE_NO_WINDOW)
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
        root.title('OpenClaw 控制台 v2.2')
        root.geometry('980x540')
        root.minsize(760, 440)
        root.configure(bg='#2b2b2b')

        self.models = list_models()
        self.model_proc = None   # 本程序启动的 llama-server 进程
        self.log_win = None
        self._log_win_text = None
        self._log_buffer = []
        self._log_buffers = {'console': [], 'llm': [], 'comfy': []}
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

        # ===== 主 Notebook（翻页式布局，适配小屏）=====
        nb = ttk.Notebook(root)
        nb.pack(fill='both', expand=True, padx=10, pady=(30, 10))
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

        # 启动/停止/硬件配置/一键恢复：LoRA 下方一排紧贴（硬件配置/一键恢复同刷新大小）
        row6 = tk.Frame(tab_model, bg=c['panel'])
        row6.grid(row=6, column=0, columnspan=6, sticky='w', padx=10, pady=(4, 8))
        self.btn_llm_start = ttk.Button(row6, text='▶ 启动模型', style='Accent.TButton', command=self.start_llm)
        self.btn_llm_start.pack(side='left', padx=(0, 6))
        self.btn_llm_stop = ttk.Button(row6, text='■ 停止模型', style='Stop.TButton', command=self.stop_llm)
        self.btn_llm_stop.pack(side='left', padx=(0, 16))
        self.btn_hw = ttk.Button(row6, text='硬件配置', width=7, command=self.show_hardware)
        self.btn_hw.pack(side='left', padx=(0, 6))
        self.btn_restore = ttk.Button(row6, text='一键恢复', width=7, command=self._restore_all)
        self.btn_restore.pack(side='left')

        # 生图默认模型（选即保存，MCP 生图时读取；与推理模型同一列对齐）
        self.gen_model_var = tk.StringVar()
        self.gen_model_combo = ttk.Combobox(tab_model, textvariable=self.gen_model_var, width=52,
                                            style='Dark.TCombobox')
        ttk.Label(tab_model, text='生图默认模型', style='Panel.TLabel').grid(row=2, column=0, sticky='w', padx=(10, 8), pady=(0, 4))
        self.gen_model_combo.grid(row=2, column=1, columnspan=2, sticky='we', padx=(6, 8), pady=(0, 4))
        self.gen_model_combo.bind('<<ComboboxSelected>>', self._save_gen_model)
        self.btn_gen_refresh = ttk.Button(tab_model, text='刷新', width=4, command=self._refresh_gen_models)
        self.btn_gen_refresh.grid(row=2, column=3, sticky='w')
        self._refresh_gen_models(initial=True)

        # 生图默认 LoRA（两排：Krea2 / Z-Image，输入框与上面同列对齐）
        self.lora_krea2_var = tk.StringVar()
        self.lora_krea2_combo = ttk.Combobox(tab_model, textvariable=self.lora_krea2_var, width=34,
                                             style='Dark.TCombobox')
        ttk.Label(tab_model, text='Krea2', style='Panel.TLabel').grid(row=3, column=0, sticky='w', padx=(10, 8), pady=(0, 4))
        self.lora_krea2_combo.grid(row=3, column=1, columnspan=2, sticky='we', padx=(6, 8), pady=(0, 4))
        self.lora_krea2_combo.bind('<<ComboboxSelected>>', lambda e: self._save_lora('krea2'))
        self.btn_lora_refresh = ttk.Button(tab_model, text='刷新', width=4, command=self._refresh_loras)
        self.btn_lora_refresh.grid(row=3, column=3, sticky='w')

        self.lora_zimg_var = tk.StringVar()
        self.lora_zimg_combo = ttk.Combobox(tab_model, textvariable=self.lora_zimg_var, width=34,
                                            style='Dark.TCombobox')
        ttk.Label(tab_model, text='Z-Image', style='Panel.TLabel').grid(row=4, column=0, sticky='w', padx=(10, 8), pady=(0, 8))
        self.lora_zimg_combo.grid(row=4, column=1, columnspan=2, sticky='we', padx=(6, 8), pady=(0, 8))
        self.lora_zimg_combo.bind('<<ComboboxSelected>>', lambda e: self._save_lora('zimg'))
        self._refresh_loras(initial=True)

        # 系统负载监控行（仅负载显示）
        sys_row = tk.Frame(tab_model, bg=c['panel'])
        sys_row.grid(row=7, column=0, columnspan=6, sticky='we', padx=10, pady=(0, 10))
        ttk.Label(sys_row, text='系统', style='Panel.TLabel').pack(side='left', padx=(0, 6))
        self.lbl_sys = ttk.Label(sys_row, text='--', style='Panel.TLabel')
        self.lbl_sys.pack(side='left')
        tab_model.columnconfigure(1, weight=1)

        # ----- Tab 2：ComfyUI（页签名：生图）-----
        tab_comfy = ttk.Frame(nb, style='Panel.TFrame')
        ttk.Label(tab_comfy, text='生图服务（ComfyUI）', style='Panel.TLabel',
                  font=('Microsoft YaHei UI', 11, 'bold')).grid(row=0, column=0, columnspan=4, sticky='w', padx=10, pady=(14, 6))

        comfy_row = tk.Frame(tab_comfy, bg='#383838')
        comfy_row.grid(row=1, column=0, columnspan=6, sticky='we', padx=10, pady=(0, 10))
        # 启动类（重启）在前、停止在后
        self.btn_comfy_restart = ttk.Button(comfy_row, text='▶  重启', style='Accent.TButton', command=self.restart_comfy)
        self.btn_comfy_restart.pack(side='left')
        self.btn_comfy_stop = ttk.Button(comfy_row, text='■  停止', style='Stop.TButton', command=self.stop_comfy)
        self.btn_comfy_stop.pack(side='left', padx=(8, 0))
        # 远程地址做成可点击超链接
        self.comfy_url_lbl = tk.Label(comfy_row, text='远程: https://game.tail8c09f5.ts.net:8443/comfy',
                                      bg='#383838', fg='#6cb4ee', font=('Microsoft YaHei UI', 9),
                                      cursor='hand2')
        self.comfy_url_lbl.pack(side='right', padx=10)
        self.comfy_url_lbl.bind('<Button-1>', lambda e: self._open_comfy_remote())

        # ComfyUI 工具行（清理 + 文件夹）
        comfy_tools_row = tk.Frame(tab_comfy, bg='#383838')
        comfy_tools_row.grid(row=2, column=0, columnspan=6, sticky='we', padx=10, pady=(0, 6))
        self.btn_comfy_ram = ttk.Button(comfy_tools_row, text='清理内存', command=self.cleanup_ram)
        self.btn_comfy_ram.pack(side='left')
        self.btn_comfy_vram = ttk.Button(comfy_tools_row, text='清理显存', command=self.cleanup_vram)
        self.btn_comfy_vram.pack(side='left', padx=(8, 0))
        self.btn_comfy_upload = ttk.Button(comfy_tools_row, text='上传文件夹', command=self.open_upload_dir)
        self.btn_comfy_upload.pack(side='left', padx=(16, 0))
        self.btn_comfy_output = ttk.Button(comfy_tools_row, text='生成文件夹', command=self.open_output_dir)
        self.btn_comfy_output.pack(side='left', padx=(8, 0))
        tab_comfy.columnconfigure(1, weight=1)

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
        for key, label in [('llama_dir', 'LLM 模型目录'), ('comfy_root', 'ComfyUI 根目录'), ('openclaw_data', 'OpenClaw 数据目录')]:
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

        # ----- Tab 5：调试（环境体检） -----
        tab_dbg = ttk.Frame(nb, style='Panel.TFrame')
        nb.add(tab_dbg, text=' 调试 ')
        ttk.Label(tab_dbg, text='环境体检 & 调试', style='Panel.TLabel',
                  font=('Microsoft YaHei UI', 11, 'bold')).grid(row=0, column=0, columnspan=10, sticky='w', padx=10, pady=(10, 4))

        self.env_frames = {}   # kind -> (lamp_canvas, btn)
        env_row = ttk.Frame(tab_dbg)
        env_row.grid(row=1, column=0, columnspan=10, sticky='we', padx=10, pady=(0, 4))
        kinds = [('node', 'Node.js'), ('openclaw', 'OpenClaw'), ('llama', 'llama.cpp'),
                 ('models', '模型文件'), ('cfg', '网关配置')]
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
        # 启动类（全清重启/重启网关）放最前面
        ttk.Button(dbg_row, text='🔧 全清重启', style='Accent.TButton', command=self.clean_restart_gw).pack(side='left', padx=(0, 8))
        ttk.Button(dbg_row, text='重启网关', command=self.restart_gw).pack(side='left', padx=(0, 8))
        ttk.Button(dbg_row, text='测试模型API', command=self.test_llm_api).pack(side='left', padx=(0, 8))
        ttk.Button(dbg_row, text='测试网关', command=self.test_gw).pack(side='left', padx=(0, 8))
        ttk.Button(dbg_row, text='复制日志', command=self.copy_log).pack(side='left', padx=(0, 8))

        # ===== 日志区 =====（已移至独立磁吸窗口，顶部“日志”按钮打开）

        self.log('OpenClaw 控制台 v2.2 启动')
        self.log(f'模型目录: {MODELS_DIR}')
        self.log(f'网关: {DASH_URL}')

        # 窗口贴屏幕底边（固定高度，避免 Notebook 请求高度撑爆窗口贴顶）
        try:
            _sw = root.winfo_screenwidth()
            _sh = root.winfo_screenheight()
            _win_w = min(980, _sw - 40)
            _win_h = min(540, _sh - 90)
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
                self._log_buffers = {'console': [], 'llm': [], 'comfy': []}
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
        canvas.create_oval(2, 2, 14, 14, fill=fill, outline='')

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
            return res
        def apply(res):
            for kind, ok in res.items():
                lamp, btn = self.env_frames[kind]
                if kind == 'models' and ok:
                    n = sum(1 for f in os.listdir(MODELS_DIR)
                            if f.endswith('.gguf') and not f.lower().startswith('mmproj'))
                    btn.config(text='目录', state='normal')
                elif ok:
                    btn.config(text='就绪', state='disabled')
                elif kind == 'models':
                    btn.config(text='建目录', state='normal')
                elif kind == 'cfg':
                    btn.config(text='查看', state='normal')
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

    def _install_node(self):
        self.log('通过 winget 安装 Node.js LTS（如弹权限窗口请允许）…')
        try:
            r = subprocess.run(['winget', 'install', 'OpenJS.NodeJS.LTS',
                                '--accept-source-agreements', '--accept-package-agreements'],
                               capture_output=True, text=True, timeout=900,
                               encoding='utf-8', errors='replace',
                               creationflags=subprocess.CREATE_NO_WINDOW)
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
                               creationflags=subprocess.CREATE_NO_WINDOW)
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
                creationflags=subprocess.CREATE_NO_WINDOW)
            self.log('已强杀全部 OpenClaw 残留进程')
        except Exception as e:
            self.log('清理进程失败: ' + str(e))

    def clean_restart_gw(self):
        """全清重启：停计划任务 -> 强杀残留进程 -> 干净启动，根治双实例问题"""
        self.log('🔧 全清重启网关…')
        code, out = run_cli(['gateway', 'stop'], timeout=30)
        self.log((out or 'OK').strip()[:200])
        time.sleep(2)
        self.kill_all_gw()
        time.sleep(2)
        code, out = run_cli(['gateway', 'start'])
        self.log((out or 'OK').strip()[:300])
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
        for key, label in [('console', '控制台'), ('llm', '模型 LLM'), ('comfy', 'ComfyUI')]:
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

    # ---------- 自定义模型 ----------
    def _refresh_gen_models(self, initial=False):
        """从 ComfyUI 拉取生图模型列表（checkpoints + diffusion_models/unet），载入已保存默认"""
        try:
            import urllib.request, json as _json
            a = _json.load(urllib.request.urlopen(
                'http://127.0.0.1:8188/object_info/CheckpointLoaderSimple', timeout=8))
            b = _json.load(urllib.request.urlopen(
                'http://127.0.0.1:8188/object_info/UNETLoader', timeout=8))
            items = a['CheckpointLoaderSimple']['input']['required']['ckpt_name'][0] + \
                    b['UNETLoader']['input']['required']['unet_name'][0]
            seen, self._gen_ckpts = set(), []
            for it in items:
                k = it.replace('\\', '/')
                if k not in seen:
                    seen.add(k)
                    self._gen_ckpts.append(it)
        except Exception:
            self._gen_ckpts = [
                'Z-Image-Base-8steps-White_Marble-AIO_v2-fp8.safetensors',
                'diffusion_models\\Krea2\\Krea2-Moody-Mix-premium_int4_convrot.safetensors',
                'diffusion_models\\Krea2\\Krea2-1125Krea2AsianUtopian_v2_int4_convrot.safetensors',
                'diffusion_models\\z_image\\ZIT-moodyRealMix_zitV7_fp8.safetensors',
                'diffusion_models\\z_image\\ZIT-moodyProMix_zitV13_bf16.safetensors',
                'XL-写实\\IL-perfectionRealisticILXL_33.safetensors',
            ]
        self.gen_model_combo['values'] = self._gen_ckpts
        # 读已保存的默认（gen_model.txt 记录 basename）
        saved = ''
        try:
            saved = open(os.path.join(_BASE_DIR, 'gen_model.txt'),
                                      encoding='utf-8').read().strip()
        except Exception:
            pass
        cur = ''
        if saved:
            for c in self._gen_ckpts:
                if c.replace('\\', '/').rsplit('/', 1)[-1].lower() == saved.lower():
                    cur = c
                    break
            if not cur:
                for c in self._gen_ckpts:
                    if saved.lower() in c.lower():
                        cur = c
                        break
        if not cur:
            for c in self._gen_ckpts:
                if 'Z-Image' in c:
                    cur = c
                    break
        if cur:
            self.gen_model_var.set(cur)
        if not initial:
            self.log('生图模型列表已刷新（%d 个）' % len(self._gen_ckpts))

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
        root = r'L:\OpenClaw\ComfyUI\ComfyUI\models\loras'
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

    def open_upload_dir(self):
        """打开上传文件夹（微信/网页收到的图）"""
        d = r'L:\OpenClaw\OpenClawData\media\inbound'
        os.makedirs(d, exist_ok=True)
        os.startfile(d)
        self.log('已打开上传文件夹：' + d)

    def open_output_dir(self):
        """打开生图输出文件夹"""
        d = r'L:\OpenClaw\ComfyUI\ComfyUI\output'
        os.makedirs(d, exist_ok=True)
        os.startfile(d)
        self.log('已打开生成文件夹：' + d)

    # ---------- 硬件配置 ----------
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
        """一键恢复：检查并拉起缺失的服务（llama/ComfyUI/网关）"""
        self.log('=== 一键恢复 ===')
        def work():
            try:
                subprocess.run(['setx', 'OPENCLAW_STATE_DIR', r'L:\OpenClaw\OpenClawData'],
                               capture_output=True, timeout=30)
                self.log('环境变量 OPENCLAW_STATE_DIR 已确认')
            except Exception as e:
                self.log('环境变量设置失败: ' + str(e))
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
            # 3. 网关（gateway.cmd 直启，绕开 non-default state dir 限制）
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
                    subprocess.Popen(['cmd', '/c', r'L:\OpenClaw\OpenClawData\gateway.cmd'],
                                     stdout=gw_logf, stderr=subprocess.STDOUT,
                                     creationflags=subprocess.CREATE_NO_WINDOW, env=env)
                    self.log('已后台启动网关（gateway.cmd）...')
                except Exception as e:
                    self.log('网关启动异常: ' + str(e))
            time.sleep(2)
            try:
                self.root.after(0, self._refresh_env)
            except Exception:
                pass
            self.log('=== 一键恢复完成 ===')
        threading.Thread(target=work, daemon=True).start()

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

    # ---------- 模型服务 ----------
    def start_llm(self):
        global llm_proc
        # 强制单实例：启动前先杀光所有残留 llama-server（防止多实例吃爆内存）
        try:
            subprocess.run(['taskkill', '/F', '/IM', 'llama-server.exe'],
                           capture_output=True, timeout=30,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            time.sleep(1)
        except Exception:
            pass
        if llama_running():
            self.log('模型服务已在运行')
            return
        m = self._selected_model()
        if not m:
            messagebox.showwarning('提示', '没有可用模型，请检查 L:\\llama\\models 目录')
            return
        name, path, sz, mmproj, cust = m
        # 参数：优先硬件配置文件，其次按模型大小自适应
        prof = load_profile()
        if prof:
            ngl, ctx = str(prof.get('ngl', 999)), str(prof.get('ctx', 65536))
            self.log('使用硬件配置: ' + prof.get('label', ''))
        elif sz > 8192:
            ngl, ctx = '35', '8192'
        else:
            ngl, ctx = '999', '65536'
        alias = 'local-model'
        cmd = [LLAMA_SERVER, '-m', path, '-ngl', ngl, '-c', ctx,
               '--host', '127.0.0.1', '--port', str(PORT_LLM), '--alias', alias]
        # gemma 系列：关闭思考模式(否则 reasoning_content 吃满输出 token)，量化 KV 省显存
        if 'gemma' in name.lower():
            cmd += ['--reasoning', 'off', '--cache-type-k', 'q8_0', '--cache-type-v', 'q8_0']
        if mmproj:
            cmd += ['--mmproj', mmproj]
            ctx = '65536'
            cmd[cmd.index('-c') + 1] = ctx
            self.log('视觉模型: ' + os.path.basename(mmproj) + ' (上下文 65536)')
        # 部分离载时关闭显存自动拟合，避免 fit 因手动 -ngl 中止加载
        if ngl != '999':
            cmd += ['--fit', 'off']
            self.log('部分离载模式: 已关闭显存自动拟合 (--fit off)')
        self.log('启动模型: ' + name + f'  (-ngl {ngl} -c {ctx})')
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            llm_logf = io.open(LLAMA_LOG, 'a', encoding='utf-8', errors='replace', buffering=1)
            llm_proc = subprocess.Popen(cmd, stdout=llm_logf, stderr=subprocess.STDOUT,
                                        creationflags=subprocess.CREATE_NO_WINDOW)
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

    def _start_tail(self, path, key):
        """后台线程：先回填日志文件尾部若干行，再增量读新日志，写入对应页签缓冲"""
        if key in getattr(self, '_tail_started', set()):
            return
        self._tail_started.add(key)
        def run():
            pos = 0
            try:
                if os.path.isfile(path):
                    with io.open(path, 'r', encoding='utf-8', errors='replace') as f:
                        data = f.read()
                    lines = data.splitlines()
                    for line in lines[-400:]:
                        if line.strip():
                            self.log(line[:300], key)
                    pos = os.path.getsize(path)
            except Exception:
                pos = 0
            while getattr(self, '_tails_on', True):
                try:
                    if os.path.isfile(path):
                        size = os.path.getsize(path)
                        if size > pos:
                            with io.open(path, 'r', encoding='utf-8', errors='replace') as f:
                                f.seek(pos)
                                data = f.read()
                            pos = size
                            for line in data.splitlines():
                                if line.strip():
                                    self.log(line[:300], key)
                except Exception:
                    pass
                time.sleep(0.8)
        threading.Thread(target=run, daemon=True).start()

    def _start_tails(self):
        self._tails_on = True
        self._start_tail(LLAMA_LOG, 'llm')
        self._start_tail(COMFY_LOG, 'comfy')

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
            workflow = {node: {'class_type': node, 'inputs': {
                'offload_model': True, 'offload_cache': True}}}
            req = urllib.request.Request(
                f'http://127.0.0.1:{COMFY_PORT}/prompt',
                data=json.dumps({'prompt': workflow}).encode('utf-8'),
                headers={'Content-Type': 'application/json'})
            r = urllib.request.urlopen(req, timeout=20)
            body = r.read().decode('utf-8', 'replace')
            self.log(f'{node} 提交完成: {body[:100]}')
        except Exception as e:
            self.log(f'{node} 提交失败: {e}')

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
                              '--port', str(COMFY_PORT)],
                             cwd=COMFY_DIR, stdout=comfy_logf, stderr=subprocess.STDOUT,
                             creationflags=subprocess.CREATE_NO_WINDOW)
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

    def save_paths_ui(self):
        try:
            p = dict(_DEFAULT_PATHS)
            p.update({k: v.get().strip().rstrip('\\/') for k, v in self.path_vars.items()})
            if _save_paths(p):
                self.log('✅ 路径已保存到 paths.json（重启控制台后生效）')
                messagebox.showinfo('路径设置', '路径已保存，重启控制台后生效。\n重装系统后改完路径，点「一键复原广域网」即可复原。')
            else:
                messagebox.showerror('路径设置', '保存失败，请检查目录权限')
        except Exception as e:
            self.log('保存路径失败: ' + str(e))

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
            webbrowser.open(host.rstrip('/') + ':8443/comfy')

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
                                   creationflags=subprocess.CREATE_NO_WINDOW)
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
            # 5) ComfyUI 8443 通道（443 被 OpenClaw claim，ComfyUI 走独立端口）
            subprocess.run([exe, 'serve', '--bg', '--https=8443', '--set-path=/comfy',
                            'http://127.0.0.1:8188'], capture_output=True, text=True, timeout=30)
            host = self._wan_hostname()
            if host:
                self.wan_info.set('已启用 · ' + host + ' · ComfyUI: ' + host + ':8443/comfy')
                self.log('✅ 广域网已复原：' + host + ' · ComfyUI: ' + host + ':8443/comfy')
            else:
                self.wan_info.set('配置已写入（Tailscale 未登录，登录后重试）')
                self.log('⚠ 配置已写入，Tailscale 未登录，请登录后重试')
        threading.Thread(target=work, daemon=True).start()

    def start_gw(self):
        self.log('启动网关...')
        # CLI 服务管理命令（gateway start）在非默认 state dir 下会被拒绝，
        # 改用 gateway.cmd 直启（与「一键恢复」同路径，绕开该限制）
        try:
            gw_logf = io.open(os.path.join(LOG_DIR, 'gateway.log'), 'a',
                              encoding='utf-8', errors='replace', buffering=1)
            env = dict(os.environ)
            env['OPENCLAW_STATE_DIR'] = r'L:\OpenClaw\OpenClawData'
            ts_dir = os.path.dirname(TAILSCALE_EXE) if os.path.isfile(TAILSCALE_EXE) else ''
            env['PATH'] = (ts_dir + ';' if ts_dir else '') + os.path.dirname(NODE_EXE) + ';' + env.get('PATH', '')
            subprocess.Popen(['cmd', '/c', r'L:\OpenClaw\OpenClawData\gateway.cmd'],
                             stdout=gw_logf, stderr=subprocess.STDOUT,
                             creationflags=subprocess.CREATE_NO_WINDOW, env=env)
            self.log('已后台启动网关（gateway.cmd）...')
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
                creationflags=subprocess.CREATE_NO_WINDOW).stdout.strip()
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
            extra = f' --mmproj """{mmproj}"""'
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
            if self._gw_autostart_exists():
                self.log('网关开机自启已存在')
                return
            r = subprocess.run(
                ['schtasks', '/Create', '/TN', self.GW_TASK,
                 '/TR', r'L:\OpenClaw\OpenClawData\gateway.vbs',
                 '/SC', 'ONLOGON', '/F'],
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
                   'ws.Run "wscript \"L:\\OpenClaw\\ComfyUI\\comfy_start.vbs\"", 0, False\n')
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
                self.root.after(0, lambda: self._update_status(llm_on, gw_on, comfy_on))
                st = sys_stats()
                self.root.after(0, lambda: self._update_sys(st))
            except Exception:
                pass
            time.sleep(2)

    def _update_sys(self, st):
        if not hasattr(self, 'lbl_sys'):
            return
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
            self.lbl_sys.config(text='  '.join(parts) if parts else '--')
        except Exception:
            pass

    def _update_status(self, llm_on, gw_on, comfy_on=None):
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

    def on_close(self):
        self._polling = False
        self.root.destroy()

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
    root = tk.Tk()
    app = App(root)
    root.protocol('WM_DELETE_WINDOW', app.on_close)
    root.mainloop()

if __name__ == '__main__':
    main()
