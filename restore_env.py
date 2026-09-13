# -*- coding: utf-8 -*-
"""
OpenClaw 本地 AI 环境一键恢复（重装系统后使用）
用法：双击 L:/OpenClaw/一键恢复.bat
做什么：设环境变量 -> 启动 llama(8080) -> 启动 ComfyUI(8189) -> 启动网关(18789) -> 打开控制台
"""
import os, sys, subprocess, time, socket, io

BASE = r'L:\OpenClaw'
DATA = os.path.join(BASE, 'OpenClawData')
LOGS = os.path.join(DATA, 'console', 'logs')
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

log('=' * 50)
log('  OpenClaw 本地 AI 环境一键恢复')
log('  ' + BASE)
log('=' * 50)

# ---------- 1. 环境变量 ----------
log('[1/5] 设置环境变量 OPENCLAW_STATE_DIR ...')
r = subprocess.run(['setx', 'OPENCLAW_STATE_DIR', DATA],
                   capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
log('  setx:', 'OK' if r.returncode == 0 else (r.stderr or r.stdout or 'FAIL').strip()[:100])

# ---------- 2. llama ----------
log('[2/5] 启动 llama 模型服务 (8080) ...')
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
            '--cache-type-k', 'q8_0', '--cache-type-v', 'q8_0']
    p = subprocess.Popen(argv, stdout=llama_log, stderr=subprocess.STDOUT,
                         creationflags=subprocess.CREATE_NO_WINDOW)
    log('  llama PID:', p.pid)
wait_port(8080, 'llama', 120)

# ---------- 3. ComfyUI ----------
log('[3/5] 启动 ComfyUI 生图服务 (8189) ...')
if proc_alive(8189):
    log('  已在运行，跳过')
else:
    subprocess.Popen(['wscript', os.path.join(BASE, 'ComfyUI', 'comfy_start.vbs')],
                     creationflags=subprocess.CREATE_NO_WINDOW)
wait_port(8189, 'ComfyUI', 90)

# ---------- 4. 网关 ----------
log('[4/5] 启动 OpenClaw 网关 (18789) ...')
if proc_alive(18789):
    log('  已在运行，跳过')
else:
    gw_log = open(os.path.join(LOGS, 'gateway.log'), 'ab')
    env = dict(os.environ)
    env['OPENCLAW_STATE_DIR'] = DATA
    env['PATH'] = r'C:\Program Files\nodejs;' + os.path.join(BASE, 'node') + ';' + env.get('PATH', '')
    p = subprocess.Popen(['cmd', '/c', os.path.join(DATA, 'gateway.cmd')],
                         stdout=gw_log, stderr=subprocess.STDOUT,
                         creationflags=subprocess.CREATE_NO_WINDOW, env=env)
    log('  网关 PID:', p.pid)
wait_port(18789, 'gateway', 60)

# ---------- 5. 结果 ----------
log('[5/5] 最终状态：')
ports = [(8080, 'llama 模型'), (8189, 'ComfyUI 生图'), (18789, 'OpenClaw 网关')]
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
    exe = os.path.join(BASE, 'OpenClaw控制台_v2.1.exe')
    if os.path.isfile(exe):
        subprocess.Popen([exe])
    else:
        log('未找到控制台 exe，请手动打开浏览器访问 http://127.0.0.1:18789/chat/main')
else:
    log('部分服务未就绪，请查看上方日志排查')
log('=' * 50)
