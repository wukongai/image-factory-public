#!/usr/bin/env python3
"""Optional user-owned Images API adapter. Preview by default; never stores keys."""
import argparse
import base64
import getpass
import io
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from PIL import Image
from accept_image import accept

DEFAULT_BASE = 'https://api.openai.com/v1'
DEFAULT_MODEL = 'gpt-image-2'
MAX_RESPONSE = 64 * 1024 * 1024

class BackendError(Exception):
    pass

def endpoint(base):
    p = urllib.parse.urlsplit(base)
    if (p.scheme != 'https' or not p.hostname or p.username or p.password
            or p.query or p.fragment or any(c.isspace() for c in base)):
        raise BackendError('API 地址必须是 HTTPS，且不能包含凭证、查询参数或片段。')
    return base.rstrip('/')

def prepare(manifest, destination, base, model):
    base = endpoint(base)
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', model):
        raise BackendError('请填写服务商提供的图片模型名称。')
    m = json.loads(Path(manifest).read_text())
    if m.get('public_edition') != 'image-factory-public/v1':
        raise BackendError('请先用公开版编译图片任务。')
    h = m['render_handoff']
    dst = Path(destination).absolute()
    if dst.suffix.lower() != '.png' or dst.exists() or dst.is_symlink() or dst.with_suffix('.receipt.json').exists() or dst.with_suffix('.unverified.bin').exists():
        raise BackendError('输出必须是全新的 .png 文件；已有图片请先验收，不要重复付费。')
    prompt = Path(h['prompt_file']).read_text()
    if not prompt.strip() or not re.fullmatch(r'\d{3,5}x\d{3,5}', h['size']):
        raise BackendError('任务缺少有效提示词或尺寸。')
    assets = []
    for asset in h.get('assets', []):
        path = Path(asset['path'])
        if not path.is_file():
            if asset.get('required', True):
                raise BackendError('必需参考图不存在；请补齐再生成。')
            continue
        if path.stat().st_size > 20 * 1024 * 1024:
            raise BackendError('单张参考图不能超过 20 MiB。')
        data = path.read_bytes()
        with Image.open(io.BytesIO(data)) as im:
            fmt = im.format
            im.verify()
        mime = {'PNG':'image/png','JPEG':'image/jpeg','WEBP':'image/webp'}.get(fmt)
        if not mime:
            raise BackendError('参考图仅支持 PNG、JPEG、WebP。')
        assets.append((data, mime))
    if len(assets) > 16:
        raise BackendError('一次最多使用 16 张参考图。')
    fields = {'model':model, 'prompt':prompt, 'size':h['size'], 'n':1, 'output_format':'png'}
    if not assets:
        body = json.dumps(fields).encode()
        content_type = 'application/json'
        route = '/images/generations'
    else:
        boundary = 'ImageFactory' + uuid.uuid4().hex
        chunks = []
        for name, value in fields.items():
            chunks.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n').encode())
        for i, (data, mime) in enumerate(assets):
            ext = {'image/png':'png','image/jpeg':'jpg','image/webp':'webp'}[mime]
            chunks.append((f'--{boundary}\r\nContent-Disposition: form-data; name="image[]"; filename="reference-{i}.{ext}"\r\nContent-Type: {mime}\r\n\r\n').encode() + data + b'\r\n')
        chunks.append(f'--{boundary}--\r\n'.encode())
        body = b''.join(chunks)
        content_type = 'multipart/form-data; boundary=' + boundary
        route = '/images/edits'
    return {'url':base+route, 'body':body, 'content_type':content_type,
            'preview':{'mode':'preview','host':urllib.parse.urlsplit(base).hostname,
                       'route':route,'model':model,'size':h['size'],'reference_count':len(assets),
                       'destination':str(dst),'network_called':False}}

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def transport(request):
    # No redirect or automatic retry: never forward credentials to a second host.
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=180) as response:
            data = response.read(MAX_RESPONSE + 1)
        if len(data) > MAX_RESPONSE:
            raise BackendError('响应过大，已停止；请查看服务商请求记录后再决定是否重试。')
        return data
    except urllib.error.HTTPError as e:
        raise BackendError(f'生图服务返回 HTTP {e.code}；未自动重试，请检查服务商后台。') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise BackendError('网络失败或超时；可能已计费，请先检查服务商记录，不要直接重试。') from None

def render(plan, manifest, destination, key, send=transport):
    if not key or any(c.isspace() for c in key):
        raise BackendError('缺少有效 API Key。请在本地终端隐藏输入，或配置 IMAGE_FACTORY_API_KEY。')
    # Reserve output before spending money; concurrent calls cannot bill the same target twice.
    dst = Path(destination).absolute()
    dst.parent.mkdir(parents=True, exist_ok=True)
    lock = dst.with_suffix('.render-lock')
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise BackendError('存在生图任务锁；请先检查服务商记录与输出，确认不会重复计费后再移除锁。') from None
    os.close(fd)
    succeeded = False
    try:
        if dst.exists() or dst.is_symlink() or dst.with_suffix('.receipt.json').exists() or dst.with_suffix('.unverified.bin').exists():
            raise BackendError('目标已存在，未调用 API。')
        request = urllib.request.Request(plan['url'], data=plan['body'],
                  headers={'Authorization':'Bearer '+key, 'Content-Type':plan['content_type']}, method='POST')
        raw = send(request)
        try:
            data = base64.b64decode(json.loads(raw)['data'][0]['b64_json'], validate=True)
        except (ValueError, KeyError, IndexError, TypeError):
            raise BackendError('接口未返回有效 b64_json 图片；请核对 Images API 兼容性，勿直接重试。') from None
        # Preserve paid output before validation, including unexpected decoder failures.
        retained = dst.with_suffix('.unverified.bin')
        with retained.open('xb') as f:
            f.write(data)
        try:
            result = accept(manifest, retained, dst)
        except (ValueError, OSError, KeyError, Image.DecompressionBombError):
            raise BackendError('已收到图片但验收失败；原响应保留在输出旁的 .unverified.bin，需检查后处理。') from None
        retained.unlink()
        succeeded = True
        return result
    finally:
        # Ambiguous failures retain the lock to prevent accidental duplicate charges.
        if succeeded:
            lock.unlink()

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['doctor','render'])
    p.add_argument('--manifest'); p.add_argument('--output')
    p.add_argument('--base-url', default=DEFAULT_BASE)
    p.add_argument('--model', default=DEFAULT_MODEL)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--allow-paid', action='store_true')
    args = p.parse_args()
    try:
        endpoint(args.base_url)
        if args.action == 'doctor':
            print(json.dumps({'key_in_environment':bool(os.environ.get('IMAGE_FACTORY_API_KEY')),
                'hidden_terminal_input_available':sys.stdin.isatty(), 'network_called':False,
                'note':'仅检查配置入口，不代表 Key 有效、模型可用或生图成功。'}, ensure_ascii=False))
            return 0
        if not args.manifest or not args.output:
            p.error('render 需要 --manifest 和 --output')
        plan = prepare(args.manifest, args.output, args.base_url, args.model)
        print(json.dumps(plan['preview'], ensure_ascii=False))
        if not args.execute:
            return 0
        if not args.allow_paid:
            raise BackendError('实际请求可能计费并上传提示词/参考图；确认后添加 --allow-paid。')
        key = os.environ.get('IMAGE_FACTORY_API_KEY')
        if not key and sys.stdin.isatty():
            key = getpass.getpass('API Key（仅本次内存使用，不保存、不显示）: ')
        print(json.dumps(render(plan,args.manifest,args.output,key),ensure_ascii=False))
        return 0
    except BackendError as e:
        print(str(e),file=sys.stderr)
    except (ValueError, OSError, KeyError, TypeError):
        print('本地任务或图片数据无效；请检查输入文件和输出权限。',file=sys.stderr)
    except (EOFError, KeyboardInterrupt):
        print('已取消。',file=sys.stderr)
    return 1

if __name__ == '__main__':
    raise SystemExit(main())
