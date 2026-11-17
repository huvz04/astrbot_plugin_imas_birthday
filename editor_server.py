"""Optional password-protected editor; all edits reuse the plugin's own services."""
import asyncio
import hmac
import secrets
import time
from pathlib import Path

from aiohttp import web

LOGIN = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>素材管理登录</title><style>body{font:16px system-ui;background:#f6f7fb;max-width:360px;margin:15vh auto;padding:24px}input,button{box-sizing:border-box;width:100%;padding:12px;margin:10px 0}#error{color:#b33}</style><h1>图片与资料管理</h1><form><label>访问密码<input type="password" name="password" autocomplete="current-password" required></label><button>登录</button><p id="error"></p></form><script>document.querySelector('form').onsubmit=async e=>{e.preventDefault();let r=await fetch('/login',{method:'POST',headers:{'Content-Type':'application/json','X-Imas-Editor':'1'},body:JSON.stringify({password:e.target.password.value})});if(r.ok)location.href='/';else document.querySelector('#error').textContent='密码错误或尝试过于频繁，请稍后重试。'}</script></html>'''
BRIDGE = '''<script>window.AstrBotPluginPage={ready:async()=>{},apiGet:async(p,q={})=>{let r=await fetch('/api/'+p+'?'+new URLSearchParams(q),{headers:{'X-Imas-Editor':'1'}});if(r.status===401){location.href='/login';throw Error('登录已过期')}let d=await r.json();if(!r.ok)throw Error(d.error);return d},apiPost:async(p,v)=>{let r=await fetch('/api/'+p,{method:'POST',headers:{'Content-Type':'application/json','X-Imas-Editor':'1'},body:JSON.stringify(v)});let d=await r.json();if(!r.ok)throw Error(d.error);return d},upload:async(p,file)=>{let f=new FormData();f.append('file',file);let r=await fetch('/api/'+p,{method:'POST',headers:{'X-Imas-Editor':'1'},body:f});let d=await r.json();if(!r.ok)throw Error(d.error);return d}};</script>'''


class EditorServer:
    def __init__(self, plugin):
        self.plugin = plugin
        self.runner = None
        self.sessions = {}
        self.failures = []

    async def start(self):
        if self.runner or not self.plugin.config.get('editor_web_enabled', False):
            return
        self.password = str(self.plugin.config.get('editor_web_password', ''))
        if len(self.password) < 12:
            raise ValueError('独立编辑页需要至少 12 位访问密码。')
        host = str(self.plugin.config.get('editor_web_host', '127.0.0.1'))
        port = int(self.plugin.config.get('editor_web_port', 8097))
        if not 1024 <= port <= 65535:
            raise ValueError('编辑页端口需为 1024—65535。')
        app = web.Application(client_max_size=11 * 1024 * 1024, middlewares=[self.security])
        app.router.add_route('*', '/login', self.login)
        app.router.add_route('*', '/api/{module}/{action}', self.api)
        app.router.add_get('/{path:.*}', self.asset)
        runner = web.AppRunner(app, access_log=None)
        try:
            await runner.setup()
            await web.TCPSite(runner, host, port).start()
        except BaseException:
            await runner.cleanup()
            raise
        self.runner = runner

    async def close(self):
        if self.runner:
            await self.runner.cleanup()
            self.runner = None
        self.sessions.clear()

    @web.middleware
    async def security(self, request, handler):
        origin = request.headers.get('Origin')
        if origin and origin != f'{request.scheme}://{request.host}':
            raise web.HTTPForbidden()
        if request.method not in {'GET', 'HEAD'} and request.headers.get('X-Imas-Editor') != '1':
            raise web.HTTPForbidden()
        now = time.monotonic()
        self.sessions = {token: expiry for token, expiry in self.sessions.items() if expiry > now}
        if request.path != '/login' and request.cookies.get('imas_editor') not in self.sessions:
            if request.path.startswith('/api/'):
                return web.json_response({'error': '请先登录。'}, status=401)
            raise web.HTTPFound('/login')
        response = await handler(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        return response

    async def login(self, request):
        if request.method == 'GET':
            return web.Response(text=LOGIN, content_type='text/html')
        if request.method != 'POST':
            raise web.HTTPMethodNotAllowed(request.method, ['GET', 'POST'])
        now = time.monotonic()
        self.failures = [t for t in self.failures if now - t < 60]
        if len(self.failures) >= 10:
            return web.json_response({'error': '请稍后重试。'}, status=429)
        try:
            data = await request.json()
            password = data.get('password', '')
        except (ValueError, AttributeError):
            password = ''
        if not isinstance(password, str) or not hmac.compare_digest(password.encode(), self.password.encode()):
            self.failures.append(now)
            return web.json_response({'error': '密码错误。'}, status=401)
        if len(self.sessions) >= 64:
            self.sessions.pop(next(iter(self.sessions)))
        token = secrets.token_urlsafe(32)
        self.sessions[token] = now + 8 * 3600
        response = web.json_response({'ok': True})
        response.set_cookie('imas_editor', token, httponly=True, samesite='Strict', secure=request.secure, max_age=8 * 3600)
        return response

    async def api(self, request):
        module, action = request.match_info['module'], request.match_info['action']
        editor = self.plugin.editor if module == 'editor' else getattr(self.plugin, 'people_editor', None) if module == 'people' else None
        if editor is None or action not in editor.actions:
            raise web.HTTPNotFound()
        method = 'POST' if action in {'save', 'upload'} else 'GET'
        if request.method != method:
            raise web.HTTPMethodNotAllowed(request.method, [method])
        try:
            if action == 'list':
                result = await editor.list_records()
            elif action == 'detail':
                result = await editor.detail(request.query.get('name', ''))
            elif action == 'save':
                result = await editor.save(await request.json())
            elif action == 'preview':
                result = await editor.preview(request.query.get('name', ''), request.query.get('columns', '1'))
            elif action == 'source':
                result = await editor.load_source(request.query.get('name', ''))
            else:
                reader = await request.multipart()
                part = await reader.next()
                if part is None or part.name != 'file':
                    raise ValueError('请选择图片。')
                data = bytearray()
                while chunk := await part.read_chunk():
                    data.extend(chunk)
                    if len(data) > 10 * 1024 * 1024:
                        raise ValueError('图片不能超过 10 MB。')
                result = await asyncio.to_thread(editor.import_image, bytes(data))
            return web.json_response(result)
        except (ValueError, TypeError) as exc:
            return web.json_response({'error': str(exc)}, status=400)

    async def asset(self, request):
        root = (self.plugin.plugin_dir / 'pages' / 'editor').resolve()
        path = (root / (request.match_info['path'] or 'index.html')).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.suffix not in {'.html', '.js', '.css', '.png'}:
            raise web.HTTPNotFound()
        if path.name == 'index.html':
            return web.Response(text=path.read_text(encoding='utf-8').replace('<head>', '<head>' + BRIDGE, 1), content_type='text/html')
        return web.FileResponse(path)
