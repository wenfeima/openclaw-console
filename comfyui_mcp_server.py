# -*- coding: utf-8 -*-
"""
ComfyUI 生图 MCP server —— 供 OpenClaw 调用
功能：自动管理本地模型(llama-server)显存、自动拉起 ComfyUI、提交文生图工作流、回传图片地址
用法：python comfyui_mcp_server.py   （stdio 模式，由 OpenClaw 网关拉起）
"""
import json
import os
import re
import subprocess
import sys
import time
import io
import urllib.request

COMFY_PORT = 8188
LLM_PORT = 8080
# 路径跟随 paths.json（与 openclaw_console.py 同目录；设置页改路径后重启网关生效）
def _load_paths():
    p = {}
    try:
        base = os.path.dirname(os.path.abspath(__file__))
        with io.open(os.path.join(base, 'paths.json'), encoding='utf-8') as f:
            p = json.load(f)
    except Exception:
        pass
    return p
_PATHS = _load_paths()
COMFY_ROOT = _PATHS.get('comfy_root', r'L:\OpenClaw\ComfyUI')
LLAMA_DIR  = _PATHS.get('llama_dir', r'L:\OpenClaw\llama')
COMFY_PY = os.path.join(COMFY_ROOT, 'python_embeded', 'python.exe')   # ComfyUI 自带 python 环境
COMFY_MAIN = os.path.join(COMFY_ROOT, 'ComfyUI', 'main.py')            # ComfyUI 源码入口
COMFY_CWD = os.path.join(COMFY_ROOT, 'ComfyUI')
CKPT_DIR = os.path.join(COMFY_CWD, 'models', 'checkpoints')
OUT_DIR = os.path.join(COMFY_CWD, 'output')
OUT_URL = f'http://127.0.0.1:{COMFY_PORT}'
WF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'workflows')


def _refresh_paths():
    """每次生图/列模型前重新读取 paths.json，跟随用户在控制台设置页的修改，
    避免 MCP 进程常驻导致改路径后仍用旧目录（旧目录/新目录模型列表不一致的根因）。"""
    global _PATHS, COMFY_ROOT, LLAMA_DIR, COMFY_PY, COMFY_MAIN, COMFY_CWD, CKPT_DIR, OUT_DIR, OUT_URL, LLM_EXE
    _PATHS = _load_paths()
    COMFY_ROOT = _PATHS.get('comfy_root') or r'L:\OpenClaw\ComfyUI'
    LLAMA_DIR = _PATHS.get('llama_dir') or r'L:\OpenClaw\llama'
    COMFY_PY = os.path.join(COMFY_ROOT, 'python_embeded', 'python.exe')
    COMFY_MAIN = os.path.join(COMFY_ROOT, 'ComfyUI', 'main.py')
    COMFY_CWD = os.path.join(COMFY_ROOT, 'ComfyUI')
    CKPT_DIR = os.path.join(COMFY_CWD, 'models', 'checkpoints')
    OUT_DIR = os.path.join(COMFY_CWD, 'output')
    OUT_URL = f'http://127.0.0.1:{COMFY_PORT}'
    LLM_EXE = os.path.join(LLAMA_DIR, 'llama-server.exe')
# 默认用完整 SDXL 写实模型（FP8/新架构模型无内嵌 CLIP，标准工作流跑不了）
DEFAULT_CKPT = os.path.join('XL-写实', 'IL-perfectionRealisticILXL_33.safetensors')

CREATE_NO_WINDOW = 0x08000000

# llama 默认启动配置（stop_llm 抓取命令行失败时用此兜底恢复）
LLM_EXE = os.path.join(LLAMA_DIR, 'llama-server.exe')
LLM_DEFAULT_ARGV = [
    LLM_EXE,
    '-m', os.path.join(LLAMA_DIR, 'models', 'gemma-4-12b-it-Q4_0.gguf'),
    '-ngl', '999', '-c', '65536',
    '--host', '127.0.0.1', '--port', '8080',
    '--alias', 'local-model',
    '--mmproj', os.path.join(LLAMA_DIR, 'models', 'mmproj-gemma-4-12B-it-Q8_0.gguf'),
    '--reasoning', 'off',
    '--cache-type-k', 'q8_0', '--cache-type-v', 'q8_0',
]


def _tcp_pid(port):
    try:
        r = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             f'(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess)'],
            capture_output=True, text=True, timeout=15, encoding='utf-8', errors='replace',
            creationflags=CREATE_NO_WINDOW)
        return r.stdout.strip() or None
    except Exception:
        return None


def _http_ok(url, timeout=3):
    try:
        return urllib.request.urlopen(url, timeout=timeout).status == 200
    except Exception:
        return False


def _llm_cmdline(pid):
    """抓取 llama-server 进程的原始命令行，用于恢复"""
    try:
        r = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             f'(Get-CimInstance Win32_Process -Filter "ProcessId={pid}").CommandLine'],
            capture_output=True, text=True, timeout=15, encoding='utf-8', errors='replace',
            creationflags=CREATE_NO_WINDOW)
        return (r.stdout or '').strip() or None
    except Exception:
        return None


def _parse_llm_cmd(cmdline):
    """从命令行提取启动参数，重建干净的命令"""
    if not cmdline:
        return None
    exe = cmdline.split('"')[1] if cmdline.startswith('"') else cmdline.split()[0]
    argv = cmdline.split('"')
    parts = []
    i = 1
    # 简单词法：按引号/空格切出参数
    args = []
    for tok in re.findall(r'"[^"]*"|\S+', cmdline):
        args.append(tok.strip('"'))
    args = args[1:]  # 去掉 exe
    keep = ['-m', '-ngl', '-c', '--mmproj', '--alias', '--host', '--port', '--reasoning', '--cache-type-k', '--cache-type-v', '--fit', '-fit']
    out = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in keep and i + 1 < len(args):
            out += [a, args[i + 1]]
            i += 2
        elif a == '--fit' or a == '-fit':
            i += 2
        else:
            i += 1
    return [exe] + out


def _proc_name(pid):
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-Command',
                            f'(Get-Process -Id {pid} -ErrorAction SilentlyContinue).ProcessName'],
                           capture_output=True, text=True, timeout=15,
                           encoding='utf-8', errors='replace', creationflags=CREATE_NO_WINDOW)
        return (r.stdout or '').strip() or None
    except Exception:
        return None


def _vram_free_mb():
    """查询 GPU 空闲显存(MB)，失败返回 99999（不限制）"""
    try:
        r = subprocess.run(['nvidia-smi', '--query-gpu=memory.free', '--format=csv,noheader,nounits'],
                           capture_output=True, text=True, timeout=10,
                           creationflags=CREATE_NO_WINDOW)
        return int((r.stdout or '').strip().split('\n')[0])
    except Exception:
        return 99999


def _kill_all_llama():
    """强制单实例：杀掉机器上所有 llama-server 进程（taskkill 兜底）"""
    try:
        subprocess.run(['taskkill', '/F', '/IM', 'llama-server.exe'],
                       capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW)
    except Exception:
        pass


def stop_llm():
    """停止 llama-server，返回恢复参数；没在跑返回 None（只认 llama-server 进程）"""
    pid = _tcp_pid(LLM_PORT)
    if not pid:
        return None
    name = _proc_name(pid)
    if not name or 'llama' not in name.lower():
        return None
    cmd = _llm_cmdline(pid)
    _kill_all_llama()
    for _ in range(30):
        if not _tcp_pid(LLM_PORT):
            break
        time.sleep(0.5)
    # 抓取命令行失败时用默认配置兜底，确保 llama 一定能被恢复
    parsed = _parse_llm_cmd(cmd) if cmd else None
    return parsed if parsed and os.path.isfile(parsed[0]) else LLM_DEFAULT_ARGV


def restore_llm(argv):
    """按原参数恢复 llama-server；抓取的命令行无效时用默认配置兜底。
    启动后不等待就绪（llama 在后台自行启动），避免 MCP 进程被网关杀掉导致恢复中断。"""
    if not argv or not os.path.isfile(argv[0]):
        argv = LLM_DEFAULT_ARGV
    if not os.path.isfile(argv[0]):
        return False
    try:
        _kill_all_llama()
        time.sleep(1)
        subprocess.Popen(argv, creationflags=CREATE_NO_WINDOW)
        return True  # 不等待就绪，后台自行启动
    except Exception:
        return False


def ensure_comfy():
    """确保 ComfyUI 在运行，不在则拉起"""
    _refresh_paths()
    if _http_ok(f'http://127.0.0.1:{COMFY_PORT}/system_stats'):
        return True
    if not os.path.isfile(COMFY_MAIN):
        return False
    py = COMFY_PY if os.path.isfile(COMFY_PY) else sys.executable
    try:
        subprocess.Popen([py, COMFY_MAIN, '--port', str(COMFY_PORT)],
                         cwd=COMFY_CWD, creationflags=CREATE_NO_WINDOW)
    except Exception:
        return False
    for _ in range(120):
        if _http_ok(f'http://127.0.0.1:{COMFY_PORT}/system_stats'):
            return True
        time.sleep(1)
    return False


def list_ckpts():
    """列出可用的 checkpoint（完整 SD 系优先，含子目录相对路径）"""
    _refresh_paths()
    out = []
    if not os.path.isdir(CKPT_DIR):
        return out
    for root, _dirs, files in os.walk(CKPT_DIR):
        for f in sorted(files):
            if f.endswith('.safetensors') or f.endswith('.ckpt'):
                rel = os.path.relpath(os.path.join(root, f), CKPT_DIR)
                out.append(rel)
    return out


def list_unets():
    """列出 diffusion_models 与 unet 目录的 UNET 模型（Z-Image/Krea2 等新架构）"""
    _refresh_paths()
    out = []
    for base in ('diffusion_models', 'unet'):
        d = os.path.join(COMFY_CWD, 'models', base)
        if os.path.isdir(d):
            for root, _dirs, files in os.walk(d):
                for f in sorted(files):
                    if f.endswith('.safetensors') or f.endswith('.ckpt') or f.endswith('.gguf'):
                        out.append(os.path.join(base, os.path.relpath(os.path.join(root, f), d)))
    return out


def all_gen_models():
    """合并全部可选生图模型：checkpoints + UNET(diffusion_models/unet)"""
    seen, out = set(), []
    for c in list_ckpts() + list_unets():
        key = c.replace('\\', '/')
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _default_lora(ckpt=''):
    """按生图模型读控制台配置的默认 LoRA：krea2 系读 gen_lora_krea2.txt，
    其余（z-image 等）读 gen_lora_zimg.txt；没有配置返回 ''（不挂 LoRA）"""
    base = os.path.dirname(os.path.abspath(__file__))
    name = os.path.basename((ckpt or '').replace('\\', '/'))
    cfg = os.path.join(base, 'gen_lora_krea2.txt' if 'krea2' in name.lower()
                       else 'gen_lora_zimg.txt')
    try:
        with open(cfg, encoding='utf-8') as f:
            want = f.read().strip()
    except Exception:
        return ''
    if not want:
        return ''
    for l in _list_loras():
        if l.replace('\\', '/').rsplit('/', 1)[-1].lower() == want.lower():
            return l
    for l in _list_loras():
        if want.lower() in l.lower():
            return l
    return ''


def _default_ckpt():
    ckpts = all_gen_models()
    # 优先读控制台配置的"生图默认模型"
    cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gen_model.txt')
    try:
        with open(cfg, encoding='utf-8') as f:
            want = f.read().strip()
        if want:
            for c in ckpts:
                if c.replace('\\', '/').rsplit('/', 1)[-1].lower() == want.lower():
                    return c
            for c in ckpts:
                if want.lower() in c.lower():
                    return c
    except Exception:
        pass
    # 默认 Z-Image（v0.35 新组合：qwen_image CLIP + qwen_image_vae，省显存）
    for c in ckpts:
        if 'zit' in c.lower() and 'fp8' in c.lower():
            return c
    for c in ckpts:
        if 'krea' in c.lower() and 'int4' in c.lower():
            return c
    # 完整模型优先（大小 5~8GB 的 SD/SDXL 系；排除 fp8/新架构）
    pref = [c for c in ckpts if DEFAULT_CKPT in c]
    if pref:
        return pref[0]
    good = [c for c in ckpts if not any(k in c.lower() for k in
            ('fp8', 'krea', 'qwen', 'z-image', 'wan', 'sam3', 'mmgp'))]
    return good[0] if good else (ckpts[0] if ckpts else '')


def _unet_rel(ckpt):
    """去掉 list_unets 加的 'diffusion_models\\'/'unet\\' 前缀，还原 UNETLoader 认可的相对路径"""
    for pre in ('diffusion_models\\', 'diffusion_models/', 'unet\\', 'unet/'):
        if ckpt.startswith(pre):
            return ckpt[len(pre):]
    return ckpt


def build_workflow(ckpt, positive, negative, width, height, steps, seed, lora_name='', lora_strength=0.8):
    is_zimage = 'z-image' in ckpt.lower() or 'zit' in ckpt.lower()
    is_krea = 'krea' in ckpt.lower()
    if is_krea:
        # Krea2 专属工作流：UNETLoader + qwen3vl 编码器(krea2) + qwen_image_vae
        # turbo 参数：5 步 / cfg 1 / euler+beta / EmptyFlux2LatentImage
        unet = _unet_rel(ckpt)
        # UNETLoader 只认 diffusion_models 目录；若解析到了 checkpoints 目录的 Krea2，
        # 回退到确认存在的 diffusion_models 版本，避免 400 value_not_in_list
        unet_b = unet.replace('\\', '/').rsplit('/', 1)[-1]
        if not any(u.replace('\\', '/').rsplit('/', 1)[-1] == unet_b for u in list_unets()):
            print(f'[krea] UNET "{unet}" 不在 diffusion_models，回退默认 Krea2 int4', file=sys.stderr, flush=True)
            unet = 'krea2\\Krea2-Moody-Mix-premium_int4_convrot.safetensors'
        if 'bf16' in ckpt.lower() or 'bf16' in unet.lower():
            # 全精度 11~19GB 爆显存，强制降级到同系 int4
            unet = 'krea2\\Krea2-Moody-Mix-premium_int4_convrot.safetensors'
        wf = {
            '3': {'class_type': 'KSampler', 'inputs': {
                'cfg': 1.0, 'denoise': 1.0, 'seed': seed,
                'steps': 5, 'sampler_name': 'euler', 'scheduler': 'beta',
                'latent_image': ['6', 0], 'model': ['4', 0],
                'positive': ['7', 0], 'negative': ['8', 0]}},
            '4': {'class_type': 'UNETLoader', 'inputs': {
                'unet_name': unet, 'weight_dtype': 'default'}},
            '5': {'class_type': 'VAELoader', 'inputs': {'vae_name': 'qwen_image_vae.safetensors'}},
            '6': {'class_type': 'EmptyFlux2LatentImage', 'inputs': {
                'batch_size': 1, 'width': width * 2, 'height': height * 2}},
            '7': {'class_type': 'CLIPTextEncode', 'inputs': {'clip': ['9', 0], 'text': positive}},
            '8': {'class_type': 'CLIPTextEncode', 'inputs': {'clip': ['9', 0], 'text': negative}},
            '9': {'class_type': 'CLIPLoader', 'inputs': {
                'clip_name': 'qwen3vl_4b_fp8_scaled.safetensors',
                'type': 'krea2', 'device': 'default'}},
            '10': {'class_type': 'VAEDecode', 'inputs': {'samples': ['3', 0], 'vae': ['5', 0]}},
            '11': {'class_type': 'SaveImage', 'inputs': {
                'filename_prefix': 'mcp_krea', 'images': ['12', 0]}},
            '12': {'class_type': 'VRAMCleanup', 'inputs': {
                'anything': ['10', 0], 'offload_model': True, 'offload_cache': True}},
            '13': {'class_type': 'RAMCleanup', 'inputs': {
                'anything': ['12', 0],
                'clean_file_cache': True, 'clean_processes': True, 'clean_dlls': True,
                'retry_times': 3}},
        }
    if is_zimage:
        # Z-Image 小模型组合（省显存）：UNETLoader int8 + Qwen3-4B 编码器(Q8) + Z-image-vae
        # turbo 参数：8 步 / cfg 1 / euler+simple / EmptyFlux2LatentImage
        wf = {
            '3': {'class_type': 'KSampler', 'inputs': {
                'cfg': 1.0, 'denoise': 1.0, 'seed': seed,
                'steps': 8, 'sampler_name': 'euler', 'scheduler': 'simple',
                'latent_image': ['6', 0], 'model': ['4', 0],
                'positive': ['7', 0], 'negative': ['8', 0]}},
            '4': {'class_type': 'UNETLoader', 'inputs': {
                'unet_name': _unet_rel(ckpt) if any(u.replace('\\', '/').rsplit('/', 1)[-1].lower() == _unet_rel(ckpt).replace('\\', '/').rsplit('/', 1)[-1].lower() for u in list_unets()) else 'z_image\\z_image_turbo_int8_convrot.safetensors',
                'weight_dtype': 'default'}},
            '5': {'class_type': 'VAELoader', 'inputs': {'vae_name': 'Z-image-vae.safetensors'}},
            '6': {'class_type': 'EmptyFlux2LatentImage', 'inputs': {
                'batch_size': 1, 'width': width * 2, 'height': height * 2}},
            '7': {'class_type': 'CLIPTextEncode', 'inputs': {'clip': ['9', 0], 'text': positive}},
            '8': {'class_type': 'CLIPTextEncode', 'inputs': {'clip': ['9', 0], 'text': negative}},
            '9': {'class_type': 'CLIPLoader', 'inputs': {
                'clip_name': 'qwen_3_4b.safetensors',
                'type': 'lumina2', 'device': 'default'}},
            '10': {'class_type': 'VAEDecode', 'inputs': {'samples': ['3', 0], 'vae': ['5', 0]}},
            '11': {'class_type': 'SaveImage', 'inputs': {
                'filename_prefix': 'mcp_zimg', 'images': ['12', 0]}},
            '12': {'class_type': 'VRAMCleanup', 'inputs': {
                'anything': ['10', 0], 'offload_model': True, 'offload_cache': True}},
            '13': {'class_type': 'RAMCleanup', 'inputs': {
                'anything': ['12', 0],
                'clean_file_cache': True, 'clean_processes': True, 'clean_dlls': True,
                'retry_times': 3}},
        }
    if not is_krea and not is_zimage:
        wf = {
        '3': {'class_type': 'KSampler', 'inputs': {
            'cfg': 7.0, 'denoise': 1.0, 'seed': seed,
            'steps': steps, 'sampler_name': 'euler', 'scheduler': 'normal',
            'latent_image': ['5', 0], 'model': ['4', 0],
            'positive': ['6', 0], 'negative': ['7', 0]}},
        '4': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': ckpt}},
        '5': {'class_type': 'EmptyLatentImage', 'inputs': {
            'batch_size': 1, 'width': width, 'height': height}},
        '6': {'class_type': 'CLIPTextEncode', 'inputs': {'clip': ['4', 1], 'text': positive}},
        '7': {'class_type': 'CLIPTextEncode', 'inputs': {'clip': ['4', 1], 'text': negative}},
        '8': {'class_type': 'VAEDecode', 'inputs': {'samples': ['3', 0], 'vae': ['4', 2]}},
        '9': {'class_type': 'SaveImage', 'inputs': {
            'filename_prefix': 'mcp_gen', 'images': ['10', 0]}},
        '10': {'class_type': 'VRAMCleanup', 'inputs': {
            'anything': ['8', 0], 'offload_model': True, 'offload_cache': True}},
        '11': {'class_type': 'RAMCleanup', 'inputs': {
            'anything': ['10', 0],
                'clean_file_cache': True, 'clean_processes': True, 'clean_dlls': True,
                'retry_times': 3}},
        }
    if lora_name:
        # 通用 LoRA 注入：按分支取 CLIPTextEncode 节点号，KSampler/CLIP 改接 LoraLoader
        if is_krea or is_zimage:
            pos_node, neg_node, clip_src = '7', '8', ['9', 0]
        else:
            pos_node, neg_node, clip_src = '6', '7', ['4', 1]
        wf['3']['inputs']['model'] = ['14', 0]
        wf[pos_node]['inputs']['clip'] = ['14', 1]
        wf[neg_node]['inputs']['clip'] = ['14', 1]
        wf['14'] = {'class_type': 'LoraLoader', 'inputs': {
            'model': ['4', 0], 'clip': clip_src,
            'lora_name': lora_name,
            'strength_model': lora_strength, 'strength_clip': lora_strength}}
    return wf


# ============ 工作流文件模板（编辑器可改）============
def _read_gen_workflow():
    """读取控制台保存的生图工作流偏好（gen_workflow.txt），无则 None"""
    fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gen_workflow.txt')
    try:
        with io.open(fp, encoding='utf-8') as f:
            v = f.read().strip()
            return v if v else None
    except Exception:
        return None


def _load_wf_template(ckpt, wf_name=None):
    """按模型分支加载 workflows/{krea2,zimage,default}.json（编辑器保存的模板）。
    指定 wf_name 时优先加载该文件（含自建工作流）。
    无模板文件时返回 None，回退 build_workflow 旧逻辑。"""
    if wf_name:
        base = wf_name if wf_name.endswith('.json') else wf_name + '.json'
        fp = os.path.abspath(os.path.join(WF_DIR, base))
        wf_abs = os.path.abspath(WF_DIR)
        if os.path.commonpath([wf_abs, fp]) == wf_abs and os.path.isfile(fp):
            try:
                with io.open(fp, encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return None
    low = ckpt.lower()
    key = 'default'
    if 'krea' in low:
        key = 'krea2'
    elif 'z-image' in low or 'zit' in low:
        key = 'zimage'
    fp = os.path.join(WF_DIR, key + '.json')
    if not os.path.isfile(fp):
        return None
    try:
        with io.open(fp, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _fill_template(tpl, **vals):
    """递归替换 {{key}} 占位符；整值占位（{{seed}}）保留原类型（int/float），
    字符串内嵌占位（prefix_{{seed}}）转字符串。"""
    def fill(v):
        if isinstance(v, dict):
            return {k: fill(x) for k, x in v.items()}
        if isinstance(v, list):
            return [fill(x) for x in v]
        if isinstance(v, str):
            m = re.fullmatch(r'\{\{\s*(\w+)\s*\}\}', v.strip())
            if m and m.group(1) in vals:
                return vals[m.group(1)]
            def rep(mo):
                k = mo.group(1)
                return str(vals[k]) if k in vals else mo.group(0)
            return re.sub(r'\{\{\s*(\w+)\s*\}\}', rep, v)
        return v
    return fill(tpl)


def _build_wf_from_template(ckpt, positive, negative, width, height, steps, seed,
                            lora_name='', lora_strength=0.8, workflow=None):
    """优先用编辑器保存的工作流模板（可指定 workflow 文件名）；无模板回退 build_workflow（含旧 LoRA 注入）。"""
    tpl = _load_wf_template(ckpt, workflow)
    if tpl is None:
        return build_workflow(ckpt, positive, negative, width, height, steps, seed,
                              lora_name=lora_name, lora_strength=lora_strength)
    is_krea = 'krea' in ckpt.lower()
    is_zimage = 'z-image' in ckpt.lower() or 'zit' in ckpt.lower()
    unet_v = _unet_rel(ckpt)
    if is_krea:
        unet_b = unet_v.replace('\\', '/').rsplit('/', 1)[-1]
        if not any(u.replace('\\', '/').rsplit('/', 1)[-1] == unet_b for u in list_unets()):
            unet_v = 'krea2\\Krea2-Moody-Mix-premium_int4_convrot.safetensors'
        if 'bf16' in ckpt.lower() or 'bf16' in unet_v.lower():
            unet_v = 'krea2\\Krea2-Moody-Mix-premium_int4_convrot.safetensors'
    elif is_zimage:
        unet_v = 'z_image\\z_image_turbo_int8_convrot.safetensors'
    wf = _fill_template(tpl,
                        positive=positive, negative=negative,
                        width=int(width), height=int(height),
                        width2=int(width) * 2, height2=int(height) * 2,
                        steps=int(steps), seed=int(seed), ckpt=ckpt,
                        unet=unet_v, lora_name=lora_name, lora_strength=lora_strength)
    # 模板若含 LoraLoader 且 lora_name 为空：删节点并修复引用（KSampler/CLIP 回退模型源）
    if not lora_name:
        lora_ids = [nid for nid, nd in wf.items() if nd.get('class_type') == 'LoraLoader']
        if lora_ids:
            src_id = None
            for nid, nd in wf.items():
                if nd.get('class_type') in ('UNETLoader', 'CheckpointLoaderSimple'):
                    src_id = nid
                    break
            if src_id is not None:
                clip_src = [src_id, 1] if wf[src_id]['class_type'] == 'CheckpointLoaderSimple' else [src_id, 0]
                for nid in lora_ids:
                    wf.pop(nid, None)
                for nd in wf.values():
                    for k, v in nd.get('inputs', {}).items():
                        if isinstance(v, list) and v and str(v[0]) in lora_ids:
                            if k == 'model':
                                nd['inputs'][k] = [src_id, 0]
                            elif k == 'clip':
                                nd['inputs'][k] = clip_src
    return wf


UNSUPPORTED_ARCH = ('qwen-image', 'wan2', 'sam3', 'mmgp', 'rapid-aio')


def _resolve_ckpt(name):
    """把用户/agent 传的模型名解析成 ComfyUI 认可的完整相对路径"""
    ckpts = all_gen_models()
    if not name:
        return _default_ckpt()
    name = name.replace('\\', '/').strip()
    # 1) 精确匹配（含子目录）
    for c in ckpts:
        if c.replace('\\', '/') == name:
            return _guard_arch(c)
    # 2) 按 basename 匹配
    base = name.rsplit('/', 1)[-1]
    for c in ckpts:
        if c.replace('\\', '/').rsplit('/', 1)[-1] == base:
            return _guard_arch(c)
    # 3) 模糊包含
    low = name.lower()
    for c in ckpts:
        if low in c.lower():
            return _guard_arch(c)
    # 4) 兜底：匹配不到就回退默认模型（避免 ComfyUI 400 value_not_in_list）
    print(f'[resolve] 模型名 "{name}" 未匹配到任何 checkpoint，回退默认模型', file=sys.stderr, flush=True)
    return _default_ckpt()


def _guard_arch(c):
    """新架构模型（Krea2/Z-Image/Qwen-Image/Wan 等）不兼容标准 SDXL 工作流，强制回退默认"""
    low = c.lower()
    if any(k in low for k in UNSUPPORTED_ARCH):
        print(f'[resolve] 模型 "{c}" 架构不兼容标准工作流，回退默认模型', file=sys.stderr, flush=True)
        return _default_ckpt()
    return c


def generate(positive, negative, width, height, steps, ckpt, seed, lora_name='', lora_strength=0.8, workflow=None):
    _refresh_paths()
    if not ensure_comfy():
        return '错误：ComfyUI 启动失败（请确认 ComfyUI 安装目录正确）'
    ckpt = _resolve_ckpt(ckpt)
    if not ckpt:
        return '错误：没有可用 checkpoint 模型（请放入 ComfyUI/models/checkpoints）'
    seed = int(seed)
    if seed < 0:
        # KSampler 不接受负种子（会 400 value_smaller_than_min），-1 表示随机
        seed = int(time.time() * 1000) % (2 ** 31 - 1)
    if not workflow:
        workflow = _read_gen_workflow()
    wf = _build_wf_from_template(ckpt, positive, negative, int(width), int(height), int(steps), seed,
                                 lora_name=lora_name or _default_lora(ckpt or _default_ckpt()),
                                 lora_strength=lora_strength, workflow=workflow)
    body = json.dumps({'prompt': wf}).encode('utf-8')
    req = urllib.request.Request(f'{OUT_URL}/prompt', data=body,
                                 headers={'Content-Type': 'application/json'})
    try:
        resp = json.load(urllib.request.urlopen(req, timeout=60))
    except Exception as e:
        return f'错误：提交工作流失败：{e}'
    pid = resp.get('prompt_id')
    if not pid:
        return '错误：ComfyUI 未返回任务 ID：' + json.dumps(resp, ensure_ascii=False)
    # 轮询结果
    for _ in range(300):
        time.sleep(1)
        try:
            h = json.load(urllib.request.urlopen(f'{OUT_URL}/history/{pid}', timeout=10))
        except Exception:
            continue
        entry = h.get(pid)
        if not entry:
            continue
        if entry.get('status', {}).get('status_str') == 'error':
            msgs = entry.get('status', {}).get('messages', [])
            return '错误：出图失败：' + json.dumps(msgs, ensure_ascii=False)[:500]
        outs = entry.get('outputs', {})
        for node, data in outs.items():
            imgs = data.get('images', [])
            if imgs:
                fname = imgs[0]['filename']
                sub = imgs[0].get('subfolder', '')
                img_url = f'{OUT_URL}/view?filename={fname}&subfolder={sub}&type=output'
                local_path = os.path.join(OUT_DIR, sub, fname)
                return (f'图片已生成（{ckpt}，{width}x{height}，{steps}步）。\n'
                        f'MEDIA:{local_path}')
    return '错误：出图超时（5 分钟）'


# ============ MCP 接口 ============
from mcp.server.mcpserver import MCPServer

mcp = MCPServer('comfyui', version='0.1.0')


@mcp.tool()
def generate_image(prompt: str,
                   negative_prompt: str = 'lowres, bad anatomy, bad hands, blurry, watermark',
                   width: int = 640,
                   height: int = 960,
                   steps: int = 25,
                   ckpt_name: str = '',
                   seed: int = -1,
                   stop_llm_first: bool = False,
                   lora_name: str = '',
                   lora_strength: float = 0.8,
                   workflow: str = ''):
    """用 ComfyUI 生成本地图片（文生图）。prompt 为画面描述（可中文）；ckpt_name 留空则用默认模型；
    seed 默认 -1 随机；workflow 可指定 workflows 目录下的工作流文件（留空用控制台选择/模型分支默认）。
    生图前自动暂停本地聊天模型以释放显存，完成后自动恢复。
    重要：生图成功后，给用户的回复中必须用 markdown 图片语法引用图片：
    ![图片](本地绝对路径)，即返回内容中给出的 path 字段（形如 L:\\ComfyUI\\ComfyUI\\output\\xxx.png），
    禁止使用 http://127.0.0.1:8188/view? 这类 URL 引用图片，也禁止不引用图片。"""
    llm_argv = None
    # 显存不足（<6GB）时自动暂停 llama 释放显存，否则同时运行不等待
    if stop_llm_first or _vram_free_mb() < 6000:
        llm_argv = stop_llm()
    try:
        result = generate(prompt, negative_prompt, width, height, steps,
                          ckpt_name or _default_ckpt(), seed,
                          lora_name=lora_name, lora_strength=lora_strength,
                          workflow=workflow or None)
    finally:
        if llm_argv:
            restore_llm(llm_argv)
    # 成功出图时：返回文本 + 图片内容块（MCP 标准 image content，OpenClaw 前端/微信通道直接渲染）
    import re, base64
    m = re.search(r'MEDIA:(.+)$', result, re.M)
    if m and os.path.isfile(m.group(1).strip()):
        img_path = m.group(1).strip()
        try:
            with open(img_path, 'rb') as f:
                b64 = base64.b64encode(f.read()).decode('ascii')
            text_part = re.sub(r'\n?MEDIA:.+$', '', result, flags=re.M).strip()
            from urllib.parse import quote
            subfolder = os.path.relpath(os.path.dirname(img_path), OUT_DIR)
            if subfolder == '.':
                subfolder = ''
            img_url = f'{OUT_URL}/view?filename={quote(os.path.basename(img_path))}&subfolder={quote(subfolder)}&type=output'
            # 在文本末尾附上 markdown 图片引用（本地绝对路径），供回复时直接采用：
            # OpenClaw 会把它作为附件投递并转存为网关 URL，前端可直接显示。
            text_with_img = text_part + f'\n\n![图片]({img_path})'
            from mcp_types import CallToolResult, TextContent
            return CallToolResult(
                content=[
                    TextContent(type='text', text=text_with_img),
                    {'type': 'image', 'data': b64, 'mimeType': 'image/png'},
                ],
                structured_content={
                    'media': {
                        'path': img_path,
                        'trustedLocalMedia': True,
                    },
                },
            )
        except Exception as e:
            return result + f'\n（图片内嵌失败：{e}）'
    return result


def _list_loras():
    """列出纯净版 loras 目录全部可用 LoRA（含子目录）"""
    _refresh_paths()
    root = os.path.join(COMFY_CWD, 'models', 'loras')
    out = []
    if os.path.isdir(root):
        for dp, dns, fns in os.walk(root):
            for fn in sorted(fns):
                if fn.lower().endswith(('.safetensors', '.ckpt', '.pt')):
                    rel = os.path.relpath(os.path.join(dp, fn), root)
                    out.append(rel)
    return out


@mcp.tool()
def list_loras() -> str:
    """列出 ComfyUI 当前可用的 LoRA（可选生图风格/功能）列表"""
    loras = _list_loras()
    return ('可用 LoRA：\n' + '\n'.join(f'- {c}' for c in loras)) if loras else '没有可用 LoRA'


@mcp.tool()
def list_checkpoints() -> str:
    """列出 ComfyUI 当前可用的出图模型（checkpoint + UNET 新架构）列表"""
    ckpts = [c for c in all_gen_models() if 'bf16' not in c.lower()]
    return ('可用模型：\n' + '\n'.join(f'- {c}' for c in ckpts)) if ckpts else '没有可用模型'


@mcp.tool()
def comfy_status() -> str:
    """检查 ComfyUI 与本地模型服务的运行状态"""
    c = '运行中' if _http_ok(f'http://127.0.0.1:{COMFY_PORT}/system_stats') else '未运行'
    l = '运行中' if _http_ok(f'http://127.0.0.1:{LLM_PORT}/v1/models') else '未运行'
    return f'ComfyUI: {c}（端口 {COMFY_PORT}）\n本地模型: {l}（端口 {LLM_PORT}）'


if __name__ == '__main__':
    mcp.run()
