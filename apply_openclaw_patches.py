# -*- coding: utf-8 -*-
"""
apply_openclaw_patches.py — OpenClaw 生图链路完全修复脚本（幂等，可重复执行）
======================================================================
用法:  python apply_openclaw_patches.py [--no-restart]

背景：OpenClaw 升级会把 dist/*.mjs 覆盖回原版，导致以下 4 处手工补丁失效，
     生图成功但 webchat/微信 不显示图片。本脚本把它们全部固化为可重跑补丁。

覆盖 4 处：
  1. dist/local-roots-*.mjs       媒体白名单追加 {comfy_root}\\ComfyUI\\output 与 \\models
                                  （从 paths.json 动态读取，用户改 ComfyUI 目录后重跑即更新）
  2. dist/mcp-content-*.mjs       projectMcpCallToolResult details 摊平 structuredContent
                                  （把 media 抬到 details 顶层，供投递链读取）
  3. dist/embedded-agent-tool-media-*.mjs  filterToolResultMediaUrls 放行 trustedLocalMedia 本地媒体
  4. console/comfyui_mcp_server.py  校验 CallToolResult 返回结构（缺失则明确报警，不静默改长代码）
尾部：重启网关（--no-restart 可跳过）。
"""
import io, os, re, sys, glob, json, subprocess, time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

BASE = os.path.dirname(os.path.abspath(__file__))
DIST = r'L:\OpenClaw\npm\node_modules\openclaw\dist'
PATHS_JSON = os.path.join(BASE, 'paths.json')
MCP_PY = os.path.join(BASE, 'comfyui_mcp_server.py')
BACKUP_DIR = os.path.join(BASE, 'patches_backup')
NODE = r'C:\Program Files\nodejs\node.exe'
OPENCLAW = r'L:\OpenClaw\npm\node_modules\openclaw\openclaw.mjs'
STATE_DIR = r'L:\OpenClaw\OpenClawData'
GW_LOG = os.path.join(STATE_DIR, 'console', 'logs', 'gateway.log')

ok, warn = [], []


def find_dist(feature):
    for f in glob.glob(os.path.join(DIST, '*.mjs')):
        try:
            if feature in open(f, encoding='utf-8', errors='ignore').read():
                return f
        except Exception:
            pass
    return None


def backup(fpath):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    bak = os.path.join(BACKUP_DIR, os.path.basename(fpath) + '.orig.bak')
    if not os.path.exists(bak):
        try:
            with open(fpath, 'rb') as f:
                data = f.read()
            with open(bak, 'wb') as f:
                f.write(data)
            return bak
        except Exception as e:
            warn.append(f'备份失败 {fpath}: {e}')
    return bak


def write_utf8(fpath, content):
    with io.open(fpath, 'w', encoding='utf-8', newline='\n') as f:
        f.write(content)


# ---------------- 1. local-roots 白名单（动态路径） ----------------
def patch_local_roots():
    f = find_dist('function buildMediaLocalRoots')
    if not f:
        warn.append('找不到 local-roots-*.mjs，跳过')
        return
    c = open(f, encoding='utf-8', errors='ignore').read()
    # 读 paths.json 派生当前 ComfyUI output/models
    root = r'L:\ComfyUI'
    try:
        p = json.load(io.open(PATHS_JSON, encoding='utf-8'))
        root = p.get('comfy_root') or root
    except Exception:
        pass
    root = os.path.normpath(root)
    root_js = root.replace('\\', '\\\\')  # JS 源码双反斜杠转义
    out_js = root_js + '\\\\ComfyUI\\\\output'
    mod_js = root_js + '\\\\ComfyUI\\\\models'
    # 1) 移除所有旧的 ComfyUI output/models 目录行（整行删除，含行尾逗号；保留 sandboxes 行尾逗号）
    c = re.sub(r'\n\t\t"[^"]*ComfyUI\\\\output",?', '', c)
    c = re.sub(r'\n\t\t"[^"]*ComfyUI\\\\models",?', '', c)
    # 2) 若当前路径未在，追加（锚点允许带/不带尾逗号，统一成单逗号）
    if out_js not in c:
        m = re.search(r'path\.join\(resolvedStateDir, "sandboxes"\)(,?)', c)
        if m:
            backup(f)
            tail = c[m.end():]
            c = (c[:m.start()]
                 + 'path.join(resolvedStateDir, "sandboxes"),\n\t\t"' + out_js + '",\n\t\t"' + mod_js + '"'
                 + tail)
            write_utf8(f, c)
            ok.append('local-roots 白名单已更新为: ' + root + ' (output/models)')
        else:
            warn.append('local-roots 锚点丢失，未自动追加白名单')
    else:
        ok.append('local-roots 白名单已就绪（当前路径已包含）')


# ---------------- 2. mcp-content details 摊平 ----------------
def patch_mcp_content():
    f = find_dist('function projectMcpCallToolResult')
    if not f:
        warn.append('找不到 mcp-content-*.mjs，跳过')
        return
    c = open(f, encoding='utf-8', errors='ignore').read()
    if '{ ...result.structuredContent, structuredContent' in c:
        ok.append('mcp-content details 摊平已就绪')
        return
    # 形态B：details 块内多行裸 key（已还原的原版形态）
    b = '\t\t\tstructuredContent: result.structuredContent,'
    if b in c:
        backup(f)
        c = c.replace(b, '\t\t\t...result.structuredContent !== void 0 ? { ...result.structuredContent, structuredContent: result.structuredContent } : {},')
        write_utf8(f, c)
        ok.append('mcp-content details 摊平已重打（形态B）')
        return
    # 形态A：单行 details
    old = 'details: {structuredContent: result.structuredContent}'
    if old in c:
        backup(f)
        write_utf8(f, c.replace(old, 'details: {...result.structuredContent, structuredContent: result.structuredContent}'))
        ok.append('mcp-content details 摊平已重打（形态A）')
        return
    warn.append('mcp-content 未找到待打锚点（可能结构已变，需人工核对）')


# ---------------- 3. embedded 放行 trustedLocalMedia ----------------
def patch_embedded():
    f = find_dist('function filterToolResultMediaUrls')
    if not f:
        warn.append('找不到 embedded-agent-tool-media-*.mjs，跳过')
        return
    c = open(f, encoding='utf-8', errors='ignore').read()
    if 'detailsMedia.trustedLocalMedia === true' in c:
        ok.append('embedded 本地媒体放行已就绪')
        return
    m = re.search(r'function filterToolResultMediaUrls\([\s\S]*?\n\treturn mediaUrls\.filter\(\(url\) => HTTP_URL_RE\.test\(url\.trim\(\)\)\);\n\}', c)
    if not m:
        warn.append('embedded 未找到函数体锚点，跳过')
        return
    patch = (
        '\t// [local patch] MCP 工具返回的本地媒体：显式 trustedLocalMedia 标记则直接放行（供 ComfyUI 生图投递本地路径）\n'
        '\tconst detailsMedia = readToolResultDetailsMedia(result);\n'
        '\tif (detailsMedia && detailsMedia.trustedLocalMedia === true) return mediaUrls;\n'
        '\treturn mediaUrls.filter((url) => HTTP_URL_RE.test(url.trim()));\n}'
    )
    backup(f)
    write_utf8(f, c[:m.start()] + c[m.start():m.end()].rsplit('\n\treturn mediaUrls.filter', 1)[0] + patch + c[m.end():])
    ok.append('embedded 本地媒体放行已重打')


# ---------------- 4. comfyui_mcp_server.py 校验 ----------------
def check_mcp_py():
    if not os.path.isfile(MCP_PY):
        warn.append('comfyui_mcp_server.py 不存在！')
        return
    c = open(MCP_PY, encoding='utf-8').read()
    marks = {
        'CallToolResult 导入': 'CallToolResult, TextContent' in c,
        '本地路径 markdown': '![图片]({img_path})' in c,
        'trustedLocalMedia': "'trustedLocalMedia': True" in c,
        'media 无 url': "'url': img_url" not in c,
    }
    missing = [k for k, v in marks.items() if not v]
    if missing:
        warn.append('comfyui_mcp_server.py 补丁缺失: ' + ', '.join(missing) + ' —— 请用工作版本（含 CallToolResult 返回）覆盖该文件')
    else:
        ok.append('comfyui_mcp_server.py 结构校验通过')


# ---------------- 网关重启 ----------------
def restart_gateway():
    ps = (
        "$c = Get-NetTCPConnection -LocalPort 18789 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; "
        "if ($c) { taskkill /PID $c.OwningProcess /F /T | Out-Null }; "
        "Start-Sleep -Seconds 2; "
        "$env:OPENCLAW_STATE_DIR = 'L:\\OpenClaw\\OpenClawData'; "
        "$env:PATH = 'C:\\Program Files\\nodejs;' + $env:PATH; "
        "Start-Process -FilePath 'C:\\Program Files\\nodejs\\node.exe' "
        "-ArgumentList '--max-old-space-size=8192','L:\\OpenClaw\\npm\\node_modules\\openclaw\\openclaw.mjs','gateway','--port','18789' "
        "-WorkingDirectory 'L:\\OpenClaw\\OpenClawData' -WindowStyle Hidden "
        "-RedirectStandardOutput 'L:\\OpenClaw\\OpenClawData\\console\\logs\\gateway.log' "
        "-RedirectStandardError 'L:\\OpenClaw\\OpenClawData\\console\\logs\\gateway.log.err'; "
        "Start-Sleep -Seconds 12; "
        "$c2 = Get-NetTCPConnection -LocalPort 18789 -State Listen -ErrorAction SilentlyContinue; "
        "if ($c2) { 'GATEWAY UP PID=' + $c2.OwningProcess } else { 'GATEWAY NOT UP' }"
    )
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-Command', ps],
                           capture_output=True, text=True, timeout=60,
                           encoding='utf-8', errors='replace')
        out = (r.stdout or '').strip()
        ok.append('网关重启: ' + out)
    except Exception as e:
        warn.append('网关重启失败: ' + str(e))


def main():
    print('=' * 56)
    print('OpenClaw 生图链路完全修复脚本')
    print('=' * 56)
    patch_local_roots()
    patch_mcp_content()
    patch_embedded()
    check_mcp_py()
    if '--no-restart' not in sys.argv:
        restart_gateway()
    print('\n---- 结果 ----')
    for x in ok:
        print('  [OK]   ' + x)
    for x in warn:
        print('  [WARN] ' + x)
    print('\n说明：dist 补丁升级 OpenClaw 后会被覆盖，届时重跑本脚本即可；')
    print('      paths.json 的 comfy_root 变更后，重跑本脚本会自动更新白名单。')
    return 0 if not warn else 1


if __name__ == '__main__':
    sys.exit(main())
