# -*- coding: utf-8 -*-
"""
OpenClaw 本地 AI 环境一键恢复（重装系统后使用）
用法：双击 L:/OpenClaw/一键恢复.bat
做什么：
  1. 设环境变量 OPENCLAW_STATE_DIR
  2. 修复 openclaw.json（MCP 路径/enabled/transport + tools.deny=image_generate）
  3. 修复 paths.json（comfy_root 等路径）
  4. 检查/补齐 ComfyUI llama_cpp 的 CUDA 12 运行时（缺则自动下载 DLL）
  5. 启动 llama(8080) -> ComfyUI(8188) -> 网关(18789)
  6. 打开控制台
"""
import os, sys, subprocess, time, socket, io, json, shutil

BASE = r'L:\OpenClaw'
DATA = os.path.join(BASE, 'OpenClawData')
CONSOLE = os.path.join(DATA, 'console')
LOGS = os.path.join(CONSOLE, 'logs')
os.makedirs(LOGS, exist_ok=True)
ENCODING = 'utf-8'
out = lambda *a: print(' '.join(str(x) for x in a), flush=True)

def log(*a):
    out(*a)

def wait_port(port, name, tries=90):
    for i in range(tries):
        time.sleep(2)
        s = socket.socket(); s.settimeout(1)
        try:
            s.connect(('127.0.0.1', port)); s.close()
            log(f'  [OK] {name} {port} 就绪 ({i*2+2}s)')
            return True
        except Exception:
            continue
    log(f'  [!!] {name} {port} 未就绪（{tries*2}s 超时）')
    return False

def proc_alive(port):
    s = socket.socket(); s.settimeout(1)
    try:
        s.connect(('127.0.0.1', port)); s.close()
        return True
    except Exception:
        return False

def load_json(path):
    with io.open(path, encoding='utf-8') as f:
        return json.load(f)

def save_json(path, obj):
    with io.open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

log('=' * 55)
log('  OpenClaw 本地 AI 环境一键恢复')
log('  ' + BASE)
log('=' * 55)

# ---------- 1. 环境变量 ----------
log('[1/6] 设置环境变量 OPENCLAW_STATE_DIR ...')
r = subprocess.run(['setx', 'OPENCLAW_STATE_DIR', DATA],
                   capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
log('  setx:', 'OK' if r.returncode == 0 else (r.stderr or r.stdout or 'FAIL').strip()[:100])

# ---------- 2. 修复 openclaw.json ----------
log('[2/6] 修复 openclaw.json（MCP 配置 + tools.deny）...')
oc_json = os.path.join(DATA, 'openclaw.json')
if os.path.isfile(oc_json):
    try:
        cfg = load_json(oc_json)
        changed = False
        # MCP 配置
        mcp = cfg.setdefault('mcp', {}).setdefault('servers', {}).setdefault('comfyui', {})
        py_exe = os.path.join(BASE, 'python', 'python.exe')
        mcp_script = os.path.join(CONSOLE, 'comfyui_mcp_server.py')
        if mcp.get('command') != py_exe:
            mcp['command'] = py_exe; changed = True
        if mcp.get('args') != [mcp_script]:
            mcp['args'] = [mcp_script]; changed = True
        if mcp.get('enabled') is not True:
            mcp['enabled'] = True; changed = True
        if mcp.get('transport') != 'stdio':
            mcp['transport'] = 'stdio'; changed = True
        if mcp.get('cwd') != CONSOLE:
            mcp['cwd'] = CONSOLE; changed = True
        if mcp.get('requestTimeoutMs') != 300000:
            mcp['requestTimeoutMs'] = 300000; changed = True
        # tools.deny 禁内置 image_generate（防弹选择框）
        tools = cfg.setdefault('tools', {})
        deny = tools.setdefault('deny', [])
        if 'image_generate' not in deny:
            deny.append('image_generate'); changed = True
        if tools.get('profile') != 'coding':
            tools['profile'] = 'coding'; changed = True
        if changed:
            bak = oc_json + '.bak_restore_' + time.strftime('%Y%m%d%H%M%S')
            shutil.copy2(oc_json, bak)
            save_json(oc_json, cfg)
            log('  openclaw.json 已修复（备份:', os.path.basename(bak) + '）')
        else:
            log('  openclaw.json 配置正确，无需修改')
    except Exception as e:
        log('  [!!] openclaw.json 修复失败:', e)
else:
    log('  [!!] 未找到 openclaw.json:', oc_json)

# ---------- 3. 修复 paths.json ----------
log('[3/6] 修复 paths.json（comfy_root 等路径）...')
paths_json = os.path.join(CONSOLE, 'paths.json')
if os.path.isfile(paths_json):
    try:
        pcfg = load_json(paths_json)
        changed = False
        expected = {
            'llama_dir': os.path.join(BASE, 'llama'),
            'comfy_root': os.path.join(BASE, 'ComfyUI'),
            'openclaw_data': DATA,
            'openclaw_npm': os.path.join(BASE, 'npm'),
            'node_exe': r'C:\Program Files\nodejs\node.exe',
        }
        # 只补缺失键，不覆盖用户已保存的自定义路径（如 comfy_root 迁到 L:\ComfyUI）
        for k, v in expected.items():
            if not pcfg.get(k):
                pcfg[k] = v; changed = True
        if changed:
            bak = paths_json + '.bak_restore_' + time.strftime('%Y%m%d%H%M%S')
            shutil.copy2(paths_json, bak)
            save_json(paths_json, pcfg)
            log('  paths.json 已修复（备份:', os.path.basename(bak) + '）')
        else:
            log('  paths.json 配置正确，无需修改')
    except Exception as e:
        log('  [!!] paths.json 修复失败:', e)
else:
    log('  [!!] 未找到 paths.json:', paths_json)

# ---------- 4. ComfyUI llama_cpp CUDA 运行时检查 ----------
log('[4/7] 检查 ComfyUI llama_cpp CUDA 运行时 ...')
try:
    pcfg = load_json(paths_json) if os.path.isfile(paths_json) else {}
    # 收集所有可能的 ComfyUI 根目录（去重、去空）
    roots = []
    for r in (pcfg.get('comfy_root'), os.path.join(BASE, 'ComfyUI')):
        if r and os.path.isdir(os.path.join(str(r), 'python_embeded')):
            rn = os.path.normpath(str(r))
            if rn not in roots:
                roots.append(rn)
    if not roots:
        log('  [!!] 未找到任何 ComfyUI（python_embeded）目录')
    need = {
        'cudart64_12.dll': 'nvidia-cuda-runtime-cu12',
        'cublas64_12.dll': 'nvidia-cublas-cu12',
        'cublasLt64_12.dll': 'nvidia-cublas-cu12',
    }
    for comfy_root in roots:
        llama_cpp_lib = os.path.join(comfy_root, 'python_embeded', 'Lib', 'site-packages', 'llama_cpp', 'lib')
        if not os.path.isdir(llama_cpp_lib):
            log(f'  [!!] {comfy_root}: 未找到 llama_cpp 目录')
            continue
        missing = {dll: pkg for dll, pkg in need.items() if not os.path.isfile(os.path.join(llama_cpp_lib, dll))}
        if not missing:
            log(f'  [OK] {comfy_root}: llama_cpp CUDA 12 运行时完整')
            continue
        log(f'  {comfy_root}: 缺少 CUDA 12 运行时 DLL:', ', '.join(missing))
        py_embed = os.path.join(comfy_root, 'python_embeded', 'python.exe')
        tmpdir = os.path.join(LOGS, '_cudart_dl')
        os.makedirs(tmpdir, exist_ok=True)
        pkgs = sorted(set(missing.values()))
        for pkg in pkgs:
            log(f'    下载 {pkg} ...')
            r = subprocess.run([py_embed, '-m', 'pip', 'download', pkg, '--no-deps',
                                '-d', tmpdir, '--index-url', 'https://pypi.org/simple/'],
                               capture_output=True, text=True, encoding='utf-8',
                               errors='replace', timeout=300)
            if r.returncode != 0:
                log(f'    [!!] 下载 {pkg} 失败: {(r.stderr or r.stdout).strip()[:200]}')
        # 从下载的 wheel 里提取缺失 DLL
        import zipfile
        for fn in os.listdir(tmpdir):
            if not fn.endswith('.whl'):
                continue
            whl = os.path.join(tmpdir, fn)
            try:
                with zipfile.ZipFile(whl) as z:
                    for n in z.namelist():
                        if not n.lower().endswith('.dll'):
                            continue
                        base = os.path.basename(n)
                        if base in missing:
                            target = os.path.join(llama_cpp_lib, base)
                            with z.open(n) as src, open(target, 'wb') as out:
                                shutil.copyfileobj(src, out)
                            log(f'    已补齐 {base}')
            except Exception as e:
                log(f'    [!!] 提取 {fn} 失败: {e}')
        shutil.rmtree(tmpdir, ignore_errors=True)
        # 复核
        still = [d for d in missing if not os.path.isfile(os.path.join(llama_cpp_lib, d))]
        if still:
            log('    [!!] 仍有缺失:', ', '.join(still), '—— 请检查网络或手动补齐 CUDA 12 运行时')
        else:
            log(f'  [OK] {comfy_root}: CUDA 12 运行时已修复（Qwen 增强将走 GPU）')
except Exception as e:
    log('  [!!] llama_cpp CUDA 检查失败:', e)

# ---------- 5. llama ----------
log('[5/7] 启动 llama 模型服务 (8080) ...')
if proc_alive(8080):
    log('  已在运行，跳过')
else:
    llama_log = open(os.path.join(LOGS, 'llama.log'), 'ab')
    argv = [os.path.join(BASE, 'llama', 'llama-server.exe'),
            '-m', os.path.join(BASE, 'llama', 'models', 'gemma-4-12b-it-Q4_0.gguf'),
            '-ngl', '999', '-c', '65536',
            '--host', '127.0.0.1', '--port', '8080',
            '--alias', 'local-model',
            '--mmproj', os.path.join(BASE, 'llama', 'models', 'mmproj-gemma-4-12B-it-Q8_0.gguf'),
            '--reasoning', 'off',
            '--cache-type-k', 'q8_0', '--cache-type-v', 'q8_0',
            '--no-warmup']
    p = subprocess.Popen(argv, stdout=llama_log, stderr=subprocess.STDOUT,
                         creationflags=subprocess.CREATE_NO_WINDOW)
    log('  llama PID:', p.pid)
    wait_port(8080, 'llama', 120)

# ---------- 6. ComfyUI ----------
log('[6/7] 启动 ComfyUI 生图服务 (8188) ...')
if proc_alive(8188):
    log('  已在运行，跳过')
else:
    vbs = os.path.join(BASE, 'ComfyUI', 'comfy_start.vbs')
    if os.path.isfile(vbs):
        subprocess.Popen(['wscript', vbs], creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        # fallback: 直接用 python 启动
        comfy_main = os.path.join(BASE, 'ComfyUI', 'ComfyUI', 'main.py')
        py_embed = os.path.join(BASE, 'ComfyUI', 'python_embeded', 'python.exe')
        if os.path.isfile(py_embed) and os.path.isfile(comfy_main):
            comfy_log = open(os.path.join(LOGS, 'comfyui.log'), 'ab')
            subprocess.Popen([py_embed, comfy_main, '--port', '8188'],
                             stdout=comfy_log, stderr=subprocess.STDOUT,
                             creationflags=subprocess.CREATE_NO_WINDOW,
                             cwd=os.path.join(BASE, 'ComfyUI', 'ComfyUI'))
        else:
            log('  [!!] 未找到 ComfyUI 启动方式，请手动启动')
    wait_port(8188, 'ComfyUI', 90)

# ---------- 7. 网关 ----------
log('[7/7] 启动 OpenClaw 网关 (18789) ...')
if proc_alive(18789):
    log('  已在运行，跳过')
else:
    gw_log = open(os.path.join(LOGS, 'gateway.log'), 'ab')
    env = dict(os.environ)
    env['OPENCLAW_STATE_DIR'] = DATA
    # 直接用 node 启动（不用 gateway.cmd，避免 --task-supervisor 重启循环）
    node_exe = r'C:\Program Files\nodejs\node.exe'
    gw_entry = os.path.join(BASE, 'npm', 'node_modules', 'openclaw', 'dist', 'index.js')
    p = subprocess.Popen([node_exe, gw_entry, 'gateway', '--port', '18789'],
                         stdout=gw_log, stderr=subprocess.STDOUT,
                         creationflags=subprocess.CREATE_NO_WINDOW, env=env)
    log('  网关 PID:', p.pid)
    wait_port(18789, 'gateway', 60)

# ---------- 最终状态 ----------
log()
log('=' * 55)
log('  最终状态：')
ports = [(8080, 'llama 模型'), (8188, 'ComfyUI 生图'), (18789, 'OpenClaw 网关')]
ok = True
for port, name in ports:
    if proc_alive(port):
        log(f'  [OK] {name}: http://127.0.0.1:{port}')
    else:
        log(f'  [!!] {name}: 未启动')
        ok = False

log()
if ok:
    log('全部服务已就绪，正在打开控制台...')
    # 找最新版控制台 exe
    dist_dir = os.path.join(CONSOLE, 'dist')
    exe = None
    if os.path.isdir(dist_dir):
        exes = [f for f in os.listdir(dist_dir) if f.startswith('OpenClaw控制台') and f.endswith('.exe')]
        if exes:
            exes.sort(reverse=True)
            exe = os.path.join(dist_dir, exes[0])
    if exe and os.path.isfile(exe):
        log('  打开:', os.path.basename(exe))
        subprocess.Popen([exe])
    else:
        log('  未找到控制台 exe，请手动打开浏览器访问 http://127.0.0.1:18789/chat/main')
else:
    log('部分服务未就绪，请查看上方日志排查')
log('=' * 55)
log()
log('提示：微信端生图如弹"选择图生图工具"框，说明 MCP 未连上，')
log('      检查 openclaw.json 的 mcp.servers.comfyui 配置是否正确。')
log('      微信端图片不显示，确认 comfyui_mcp_server.py 返回 details.path。')
