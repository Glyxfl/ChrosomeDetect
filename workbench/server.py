"""Loopback-only local HTTP API and static frontend; no web framework required."""
import argparse
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, unquote, parse_qs
from .config import model_paths
from .service import Service
from .store import Conflict
from .metrics import evaluate, aggregate
from .exporting import overlay, bundle

STATIC = Path(__file__).parent / 'web'

def handler(service):
    class Handler(BaseHTTPRequestHandler):
        def zip_reply(self,ids):
            with bundle(service,ids) as f:
                f.seek(0,2);length=f.tell();f.seek(0)
                self.send_response(200)
                self.send_header('Content-Type','application/zip')
                self.send_header('Content-Disposition','attachment; filename="chromosome-results.zip"')
                self.send_header('Content-Length',str(length));self.send_header('Cache-Control','no-store');self.end_headers()
                while chunk:=f.read(1024*1024):self.wfile.write(chunk)
        def reply(self, status, data, mime='application/json; charset=utf-8'):
            content = json.dumps(data, ensure_ascii=False).encode() if mime.startswith('application/json') else data
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'")
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            try:
                path = urlparse(self.path).path
                parts = path.strip('/').split('/')
                if path == '/api/health':
                    return self.reply(200, {'status': 'ok', 'models': {k: {'path': str(v), 'available': v.is_file()} for k, v in model_paths().items()}})
                if path == '/api/projects':
                    return self.reply(200, service.store.list())
                if path == '/api/metrics':return self.reply(200,aggregate(service.store.list()))
                if path == '/api/batches':return self.reply(200,[service.batch_status(b['id']) for b in service.store.records('batches')])
                if path == '/api/bundle':return self.zip_reply(parse_qs(urlparse(self.path).query).get('id',[]))
                if len(parts)==3 and parts[:2]==['api','batches']:return self.reply(200,service.batch_status(parts[2]))
                if len(parts) == 3 and parts[:2] == ['api', 'jobs']:
                    return self.reply(200, service.jobs[parts[2]])
                if len(parts) >= 3 and parts[:2] == ['api', 'projects']:
                    p = service.store.get(parts[2])
                    if len(parts) == 3 or (len(parts) == 4 and parts[3] == 'export'):
                        return self.reply(200, p)
                    if len(parts) == 4 and parts[3] == 'image':
                        b = io.BytesIO()
                        service.image(p['id']).save(b, format='PNG')
                        return self.reply(200, b.getvalue(), 'image/png')
                    if len(parts)==4 and parts[3]=='metrics':return self.reply(200,evaluate(p))
                    if len(parts)==4 and parts[3]=='history':return self.reply(200,service.store.history(p['id']))
                    if len(parts)==4 and parts[3]=='bundle':return self.zip_reply([p['id']])
                    if len(parts)==4 and parts[3]=='overlay':
                        b=io.BytesIO();overlay(service.image(p['id']),p).save(b,format='PNG')
                        return self.reply(200,b.getvalue(),'image/png')
                    if len(parts) == 5 and parts[3] == 'crops':
                        row = next(r for r in p['results']['clusters']+p['results']['instances'] if r['id'] == parts[4])
                        from .inference import crop
                        im = crop(service.image(p['id']), row['bbox'])
                        if parse_qs(urlparse(self.path).query).get('full') != ['1']:
                            im.thumbnail((640, 640))
                        b = io.BytesIO()
                        im.save(b, format='PNG')
                        return self.reply(200, b.getvalue(), 'image/png')
                files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
                if path in files:
                    name, mime = files[path]
                    return self.reply(200, (STATIC / name).read_bytes(), mime)
                self.reply(404, {'error': '未找到资源'})
            except (KeyError, StopIteration):
                self.reply(404, {'error': '未找到记录'})
            except ValueError as exc:
                self.reply(400, {'error': str(exc)})
            except Exception as exc:
                self.reply(500, {'error': str(exc)})

        def do_POST(self):
            try:
                # Local service: reject cross-origin browser mutations and DNS rebinding.
                host = self.headers.get('Host', '')
                if host.split(':')[0] not in ('127.0.0.1', 'localhost'):
                    return self.reply(403, {'error': '仅允许本机访问'})
                origin = self.headers.get('Origin')
                if origin and origin != 'http://' + host:
                    return self.reply(403, {'error': '请求来源无效'})
                length = int(self.headers.get('Content-Length', 0))
                if length <= 0 or length > 64 * 1024 * 1024:
                    return self.reply(413, {'error': '文件不能为空，且不得超过 64 MB'})
                data = self.rfile.read(length)
                path = urlparse(self.path).path
                if path == '/api/projects':
                    return self.reply(201, service.upload(data, unquote(self.headers.get('X-Filename', 'image'))))
                if path == '/api/batches':return self.reply(202,service.batch(json.loads(data)))
                parts = path.strip('/').split('/')
                if len(parts) == 4 and parts[:2] == ['api', 'projects']:
                    functions = {'annotations': service.edit, 'confirm': service.confirm, 'run': service.run, 'review':service.review}
                    if parts[3] in functions:
                        body = json.loads(data)
                        return self.reply(202 if parts[3] == 'run' else 200, functions[parts[3]](parts[2], body))
                self.reply(404, {'error': '未找到接口'})
            except Conflict as exc:
                self.reply(409, {'error': str(exc)})
            except KeyError as exc:
                self.reply(400, {'error': '缺少字段或项目不存在: ' + str(exc)})
            except (ValueError, TypeError) as exc:
                self.reply(400, {'error': str(exc)})
            except Exception as exc:
                self.reply(500, {'error': str(exc)})
    return Handler

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--host',choices=['127.0.0.1','0.0.0.0'],default='127.0.0.1')
    args = parser.parse_args()
    service = Service()
    server = ThreadingHTTPServer((args.host, args.port), handler(service))
    print(f'Chromosome Workbench: http://127.0.0.1:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service.pool.shutdown(wait=True)

if __name__ == '__main__':
    main()
