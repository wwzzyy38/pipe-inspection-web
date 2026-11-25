import argparse
from contextlib import ExitStack
import ipaddress
import ssl
import subprocess
import socket
import sys
import json
import mimetypes
import os
import re
import signal
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs,urlsplit
from catalog import Catalog,file_in
from web_auth import PasswordGate
from bag_replay import BagCatalog
from replay_predictions import Predictions
from recording_downloads import (ArchiveBusy, ArchiveSpace, archive_name, imported_frames,
                                 prediction_frames, workspace, zip_rosbag, zip_track)
from live import JPEG_FEEDS, Live, MODELS, TOPICS

ROOT=Path(__file__).resolve().parent
LOCAL=re.compile(r'(?:localhost|127\.0\.0\.1)(?::[0-9]{1,5})?\Z')
ASSETS={'/':'index.html','/style.css':'style.css','/app.js':'app.js','/viewer.js':'viewer.js'}
KINDS={**dict.fromkeys(JPEG_FEEDS, 'image/jpeg'), 'cloud':'application/octet-stream','segment':'application/octet-stream',
       'detections':'application/json','summary':'application/json'}

class InspectionHTTP(ThreadingHTTPServer):
    def __init__(self, address, tls, gate, allowed, hostname=None, device=None, workers=None):
        self.tls,self.gate,self.allowed=tls,gate,allowed
        self.scheme='https' if tls else 'http'
        suffix=f':{address[1]}' if address[1] != (443 if tls else 80) else ''
        self.host=f'{hostname or address[0]}{suffix}'
        self.hosts={self.host,f'{address[0]}{suffix}'}
        self.workers=workers if workers is not None else threading.BoundedSemaphore(32)
        self.device=device
        super().__init__(address, Handler)

    def server_bind(self):
        if self.device is not None:
            self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_BINDTODEVICE,self.device.encode('ascii')+bytes(1))
        super().server_bind()

    def verify_request(self, request, client_address):
        return self.allowed is None or ipaddress.ip_address(client_address[0]) in self.allowed

    def process_request(self, request, client_address):
        if not self.workers.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try: super().process_request(request,client_address)
        except BaseException:
            self.workers.release();raise

    def process_request_thread(self, request, client_address):
        try:
            if self.tls:
                try:
                    request.settimeout(5)
                    request=self.tls.wrap_socket(request,server_side=True)
                except (OSError,TimeoutError):
                    request.close();return
            super().process_request_thread(request,client_address)
        finally:
            self.workers.release()


class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def end_headers(self, *, form_document=False):
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        # Native form navigations need a non-opaque Origin for strict same-origin validation.
        self.send_header('Referrer-Policy','same-origin' if form_document else 'no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self' blob:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        super().end_headers()

    def body(self,status,kind,data,headers=None,*,form_document=False):
        self.send_response(status)
        self.send_header('Content-Type',kind)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        for k,v in (headers or {}).items():
            self.send_header(k,v)
        self.end_headers(form_document=form_document)
        if self.command!='HEAD' and data:
            self.wfile.write(data)

    def reply(self,status,content):
        self.body(status,'application/json; charset=utf-8',json.dumps(content,ensure_ascii=False).encode())

    def do_HEAD(self):
        self.do_GET()

    def valid_host(self):
        hosts=self.headers.get_all('Host') or []
        return len(hosts)==1 and (hosts[0].lower() in self.server.hosts if self.server.gate is not None else LOCAL.fullmatch(hosts[0]))

    def same_origin(self):
        host=self.headers.get('Host','')
        return self.headers.get_all('Origin')==[f'{self.server.scheme}://{host}']

    def redirect(self, location, cookie=None):
        headers={'Location':location}
        if cookie is not None:headers['Set-Cookie']=cookie
        self.body(303,'text/plain',b'',headers)

    def login_page(self, status=200, result='ready'):
        messages={
            'ready':'输入设备密码，查看巡检记录和实时识别。',
            'incorrect':'密码不正确，请重新输入。',
            'limited':'尝试次数过多，请等一分钟再试。',
            'unconfigured':'设备尚未设置访问密码，请联系交付人员。',
        }
        template=(ROOT/'static'/'login.html').read_text(encoding='utf-8')
        body=template.replace('{{MESSAGE}}',messages[result]).replace('{{LOCKED}}','disabled' if result=='unconfigured' else '')
        self.body(status,'text/html; charset=utf-8',body.encode(),{'Retry-After':'60'} if status==429 else None,form_document=True)

    def read_form(self, names):
        lengths=self.headers.get_all('Content-Length') or []
        if (len(lengths)!=1 or not re.fullmatch(r'[0-9]{1,4}',lengths[0]) or not 0<int(lengths[0])<=3072
            or self.headers.get('Transfer-Encoding') or self.headers.get_content_type()!='application/x-www-form-urlencoded'):
            raise ValueError('invalid form')
        raw=self.rfile.read(int(lengths[0])).decode('ascii')
        fields=parse_qs(raw,keep_blank_values=True,strict_parsing=True,errors='strict',max_num_fields=len(names))
        if set(fields)!=set(names) or any(len(x)!=1 for x in fields.values()):raise ValueError('invalid form')
        return tuple(fields[k][0] for k in names)

    def login_request(self):
        self.close_connection=True
        if not self.same_origin():self.reply(403,{'error':'origin rejected'});return
        try:password=self.read_form(('password',))[0].encode('utf-8')
        except ValueError:self.reply(400,{'error':'invalid login form'});return
        result,token=self.server.gate.login(self.client_address[0],password)
        if token is not None:self.redirect('/',self.server.gate.cookie(token))
        else:self.login_page({'unconfigured':503,'incorrect':401,'limited':429}[result],result)

    def setup_allowed(self):
        return self.server.gate.setup_allowed(self.client_address[0],self.server.device)

    def setup_page(self,status=200,result='ready'):
        messages={'ready':'为这台无人机设置访问密码。此页面仅在首次设置时可用。',
                  'invalid':'请输入至少 12 个字符，且不超过 256 字节的密码。',
                  'mismatch':'两次输入的密码不一致，请重新输入。'}
        html=(ROOT/'static'/'setup.html').read_text(encoding='utf-8').replace('{{MESSAGE}}',messages[result])
        self.body(status,'text/html; charset=utf-8',html.encode(),form_document=True)

    def setup_request(self):
        self.close_connection=True
        if not self.same_origin() or not self.setup_allowed():
            self.reply(403,{'error':'first-time setup is not authorized'});return
        try:password,confirmation=self.read_form(('password','confirm'))
        except ValueError:self.reply(400,{'error':'invalid setup form'});return
        result=self.server.gate.setup(self.client_address[0],self.server.device,password,confirmation)
        if result=='ok':self.redirect('/login')
        elif result=='denied':self.reply(403,{'error':'first-time setup is no longer authorized'})
        else:self.setup_page(400,result)

    def do_GET(self):
        if not self.valid_host():
            self.close_connection=True
            self.reply(403,{'error':'host rejected'})
            return
        try:
            path=urlsplit(self.path).path
            gate=self.server.gate
            if gate is not None and path!='/style.css':
                allowed=gate.authorized(self.headers.get('Cookie'))
                if path=='/setup':
                    if self.setup_allowed():self.setup_page()
                    else:self.reply(403,{'error':'first-time setup is not authorized'})
                    return
                if path=='/login':
                    if allowed:self.redirect('/')
                    elif self.setup_allowed():self.redirect('/setup')
                    else:self.login_page(200 if gate.pair else 503,'ready' if gate.pair else 'unconfigured')
                    return
                if not allowed:
                    if path=='/':self.redirect('/setup' if self.setup_allowed() else '/login')
                    else:self.reply(401,{'error':'login required'})
                    return
            self.get_route(path,urlsplit(self.path).query)
        except (BrokenPipeError,ConnectionResetError,TimeoutError):
            self.close_connection=True
        except (KeyError,IndexError,ValueError,FileNotFoundError,PermissionError):
            self.reply(404,{'error':'not found'})

    def log_request(self,code='-',size='-'):
        if str(code).startswith('5'):
            super().log_request(code,size)

    def get_route(self,path,query=""):
        aligned=query=="space=start"
        if path in ASSETS:
            f=ROOT/'static'/ASSETS[path]
            kind=mimetypes.guess_type(f.name)[0] or 'application/octet-stream'
            data=f.read_bytes()
            if path=='/' and self.server.gate is not None:
                data=data.replace(b'</head>',b'<meta name="inspection-auth" content="required">\n</head>',1)
            self.body(200,kind+('; charset=utf-8' if kind.startswith('text/') else ''),data)
            return
        parts=path.strip('/').split('/')
        if parts==['api','live','status']:
            self.reply(200,self.server.live.status())
        elif len(parts)==3 and parts[:2]==['api','live'] and parts[2] in TOPICS:
            kind=parts[2]; data=self.server.live.frame(kind)
            if not data:
                self.body(204,KINDS[kind],b'')
            else:
                payload=self.server.live.aligned_frame(kind,data) if aligned else data[2]
                if payload is None:self.reply(422,{'error':'missing matching start-frame pose'});return
                self.body(200,KINDS[kind],payload,{'X-Frame-Version':str(data[1])})
        elif parts[:2]==['api','sessions']:
            catalog,err=self.server.catalog.get()
            bags,skipped=self.server.bags.scan()
            if len(parts)==2:
                normal=[dict(id=x['id'],title=x['title'],demo=x['demo'],video=bool(x['videos']),cloud=bool(x['clouds'])) for x,_,_ in catalog.values() if x['id'] not in bags]
                native=[dict(id=b.id,title=b.title,demo=False,video=False,cloud=True,kind='rosbag2',frames=b.count) for b in bags.values()]
                self.reply(200,{'sessions':native+normal,'invalid':err,'bagsSkipped':len(skipped)})
            elif len(parts)==6 and parts[3]=='downloads':
                self.serve_download(parts, catalog, bags)
            elif parts[2] in bags:
                self.serve_bag(parts,bags[parts[2]],aligned)
            else:
                public,refs,base=catalog[parts[2]]
                if base.is_symlink() or base.resolve()!=base:
                    raise ValueError('invalid session directory')
                if len(parts)==3:
                    self.reply(200,public)
                elif len(parts)==5 and parts[3]=='video':
                    self.video(file_in(base,refs['videos'][parts[4]],'.mp4'))
                elif len(parts)==6 and parts[3]=='cloud' and re.fullmatch(r'[0-9]{1,6}',parts[5]):
                    f=file_in(base,refs['clouds'][parts[4]][int(parts[5])][1],'.pwc')
                    with f.open('rb') as fp:
                        data=fp.read(480009)
                    if len(data)>480008:
                        raise ValueError('frame too large')
                    self.body(200,'application/octet-stream',data)
                else:
                    raise KeyError(path)
        else:
            raise KeyError(path)

    def serve_bag(self,parts,bag,aligned=False):
        if parts[3:]==['prediction']:
            self.reply(200,self.server.predictions.status(bag))
            return
        cloud=len(parts)==6 and parts[3]=='cloud' and parts[4] in ('body','pointnext') and re.fullmatch(r'[0-9]{1,6}',parts[5])
        reference=parts[3:]==['reference']
        if len(parts)!=3 and not (cloud or reference): raise KeyError('unknown bag route')
        try:
            index=self.server.bags.index(bag)
            if reference:
                self.reply(200,index.reference()[0])
            elif len(parts)==3:
                session={**index.session,'clouds':list(index.session['clouds'])}
                session['clouds'][0]={**session['clouds'][0],'label':'原始点云'}
                if self.server.predictions.status(bag)['state']=='ready':
                    session['clouds'].append({**session['clouds'][0],'id':'pointnext','label':'PointNeXt 分割','segmented':True})
                self.reply(200,session)
            else:
                i=int(parts[5])
                if i>=len(index.refs):raise IndexError(i)
                data=index.frame(i) if parts[4]=='body' else self.server.predictions.read(bag,i)
                if aligned:
                    data=index.aligned_frame(i,data)
                    if data is None:self.reply(422,{'error':'missing matching start-frame pose'});return
                self.body(200,'application/octet-stream',data)
        except (sqlite3.Error,RuntimeError,ImportError,TypeError,OverflowError,BufferError) as exc:
            self.log_error('Bag %s: %s',bag.id,type(exc).__name__)
            self.reply(422,{'error':'could not decode recorded PointCloud2'})

    def serve_download(self, parts, catalog, bags):
        _, _, session, _, kind, track = parts
        if kind not in ('visible', 'infrared', 'pointcloud'):
            raise KeyError('unknown download type')
        if self.headers.get('Sec-Fetch-Site') not in (None, 'same-origin', 'none'):
            self.reply(403, {'error':'download must be requested from this site'})
            return
        filename = archive_name(session, kind, track, 'zip' if kind=='pointcloud' else 'mp4')
        root = self.server.catalog.root
        if session in bags:
            if kind != 'pointcloud' or track not in ('body', 'pointnext'):
                raise KeyError('unknown bag track')
            bag = bags[session]
            if track == 'body':
                required = 2 * sum(part.stat().st_size for part in bag.parts)
                prepare = lambda directory: zip_rosbag(bag, directory)
            else:
                path = self.server.predictions.destination(bag)
                if not path.is_file():
                    raise FileNotFoundError('prediction not finalized')
                required = path.stat().st_size
                index = self.server.bags.index(bag)
                public = index.session
                item = dict(public['clouds'][0], id='pointnext', label='PointNeXt 分割', segmented=True)
                prepare = lambda directory: zip_track(directory, public, item, prediction_frames(path))
        else:
            public, refs, base = catalog[session]
            if base.is_symlink() or base.resolve() != base:
                raise ValueError('invalid session directory')
            if kind != 'pointcloud':
                group = 'videos' if kind == 'visible' else 'infrared'
                path = file_in(base, refs[group][track], '.mp4')
                if not path.stat().st_size:
                    raise FileNotFoundError('video empty')
                self.video(path, filename)
                return
            paths = imported_frames(base, refs['clouds'][track])
            item = next(x for x in public['clouds'] if x['id'] == track)
            required = sum(path.stat().st_size for path in paths)
            prepare = lambda directory: zip_track(directory, public, item, paths)
        if self.command == 'HEAD':
            self.send_response(200)
            self.send_header('Content-Type','application/zip')
            self.send_header('Content-Disposition',f'attachment; filename="{filename}"')
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            return
        try:
            with workspace(root, required) as directory:
                try:
                    path = prepare(directory)
                except (ImportError, RuntimeError, sqlite3.Error):
                    self.reply(422, {'error':'pointcloud export could not be prepared'})
                    return
                self.video(path, filename, kind='application/zip')
        except ArchiveBusy:
            self.body(503,'application/json',b'{"error":"another archive is being prepared or downloaded"}',{'Retry-After':'10'})
        except ArchiveSpace:
            self.reply(507, {'error':'not enough space for the temporary archive'})

    def video(self,path,filename=None,*,kind='video/mp4'):
        with path.open('rb') as f:
            total=os.fstat(f.fileno()).st_size
            lo,hi,status=0,total-1,200
            r=self.headers.get('Range') if kind=='video/mp4' else None
            if r is not None:
                m=re.fullmatch(r'bytes=([0-9]{0,20})-([0-9]{0,20})',r)
                if m and any(m.groups()) and total:
                    a,b=m.groups()
                    if a:
                        lo,hi=int(a),min(int(b),hi) if b else hi
                    else:
                        lo=max(0,total-int(b))
                    status=206 if lo<=hi else 416
                else:
                    status=416
                if status==416:
                    self.body(416,'video/mp4',b'',{'Content-Range':f'bytes */{total}','Accept-Ranges':'bytes'})
                    return
            remaining=hi-lo+1
            self.send_response(status)
            self.send_header('Content-Type',kind)
            self.send_header('Content-Length',str(remaining))
            if kind=='video/mp4':self.send_header('Accept-Ranges','bytes')
            if filename:self.send_header('Content-Disposition',f'attachment; filename="{filename}"')
            self.send_header('Cache-Control','no-store' if filename else 'no-cache')
            if status==206:
                self.send_header('Content-Range',f'bytes {lo}-{hi}/{total}')
            self.end_headers()
            if self.command=='HEAD':
                return
            f.seek(lo)
            while remaining:
                chunk=f.read(min(remaining,256*1024))
                if not chunk:
                    self.close_connection=True
                    break
                self.wfile.write(chunk)
                remaining-=len(chunk)

    def gimbal_pitch_request(self):
        self.close_connection=True
        if self.server.gate is None:
            self.reply(401,{'error':'login required for gimbal control'});return
        lengths=self.headers.get_all('Content-Length') or []
        if (len(lengths)!=1 or not re.fullmatch(r'[0-9]{1,3}',lengths[0]) or not 0<int(lengths[0])<=256
            or self.headers.get('Transfer-Encoding') or self.headers.get_content_type()!='application/json'):
            self.reply(400,{'error':'invalid gimbal command body'});return
        try:
            data=json.loads(self.rfile.read(int(lengths[0])).decode('utf-8'))
            if not isinstance(data,dict) or set(data)!={'angle'}:raise ValueError('invalid angle fields')
        except (ValueError,RecursionError):
            self.reply(400,{'error':'invalid gimbal command body'});return
        ok,detail=self.server.live.set_gimbal_pitch(data['angle'])
        status={'invalid_angle':400,'rate_limited':429,'publish_failed':503}.get(detail,409)
        self.reply(200 if ok else status,{'ok':ok,'detail':detail})

    def do_POST(self):
        if not self.valid_host() or not self.same_origin():
            self.close_connection=True
            self.reply(403,{'error':'origin rejected'})
            return
        gate=self.server.gate
        path=urlsplit(self.path).path
        if gate is not None and path=='/setup':
            self.setup_request();return
        if gate is not None and path=='/login':
            self.login_request();return
        if self.headers.get_all('X-Inspection-Action')!=['1']:
            self.close_connection=True;self.reply(403,{'error':'action header required'});return
        if gate is not None and not gate.authorized(self.headers.get('Cookie')):
            self.close_connection=True;self.reply(401,{'error':'login required'});return
        if path=='/api/control/gimbal/pitch':
            self.gimbal_pitch_request();return
        if gate is not None and path=='/logout':
            if self.headers.get_all('Content-Length',[ '0' ])!=['0'] or self.headers.get('Transfer-Encoding'):
                self.close_connection=True;self.reply(400,{'error':'no body expected'});return
            gate.logout(self.headers.get('Cookie'));self.redirect('/login',gate.cookie('',0));return
        p=path.strip('/').split('/')
        live=len(p)==4 and p[:2]==['api','control'] and p[2] in MODELS and p[3] in ('start','stop')
        offline=len(p)==5 and p[:2]==['api','sessions'] and p[3]=='prediction' and p[4] in ('start','start-bench','cancel')
        if not (live or offline):
            self.reply(404,{'error':'unknown action'})
            return
        if self.headers.get_all('Content-Length',['0'])!=['0'] or self.headers.get('Transfer-Encoding'):
            self.close_connection=True
            self.reply(400,{'error':'no body expected'})
            return
        try:
            if offline:
                if p[4]=='start-bench' and self.headers.get('X-Inspection-Bench')!='power-isolated':
                    self.reply(403,{'error':'physical power isolation confirmation required'});return
                bag=self.server.bags.scan()[0].get(p[2])
                if bag is None:
                    self.reply(404,{'error':'unknown recording'});return
                ok,detail=self.server.predictions.action(bag,p[4])
            else:
                ok,detail=self.server.live.control(p[2],p[3])
            self.reply(200 if ok else 409,{'ok':ok,'detail':detail})
        except OSError:
            self.reply(500,{'error':'model launch failed; check logs'})


def network_addresses():
    raw=subprocess.check_output(['ip','-j','-4','addr','show','scope','global','up'],text=True)
    return {
        device['ifname']:[ipaddress.IPv4Interface(f"{item['local']}/{item['prefixlen']}")
                          for item in device.get('addr_info',[]) if item['family']=='inet']
        for device in json.loads(raw)
    }


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=18120)
    parser.add_argument('--recordings',default=str(ROOT/'recordings'))
    parser.add_argument('--auto-wifi',action=argparse.BooleanOptionalAction,default=False,
                        help='derive the LAN address and subnet from the active Wi-Fi interface')
    parser.add_argument('--wifi-interface',default=os.environ.get('PIPE_WEB_WIFI_INTERFACE'))
    parser.add_argument('--http',action='store_true',help='serve authenticated plain HTTP on the private LAN')
    parser.add_argument('--bind',default=os.environ.get('PIPE_WEB_BIND','127.0.0.1'))
    parser.add_argument('--allow',default=os.environ.get('PIPE_WEB_ALLOW'))
    parser.add_argument('--tail-bind',default=os.environ.get('PIPE_WEB_TAIL_BIND'))
    parser.add_argument('--hostname',default=os.environ.get('PIPE_WEB_HOSTNAME'))
    parser.add_argument('--cert',default=os.environ.get('PIPE_WEB_CERT'))
    parser.add_argument('--key',default=os.environ.get('PIPE_WEB_KEY'))
    parser.add_argument('--password-file',default=os.environ.get('PIPE_WEB_PASSWORD_FILE'))
    args=parser.parse_args()
    if args.auto_wifi:
        try:
            addresses=network_addresses()
            wifi=[address for name,items in addresses.items()
                  if (args.wifi_interface==name if args.wifi_interface
                      else (Path('/sys/class/net')/name/'wireless').is_dir())
                  for address in items]
        except (OSError,ValueError,subprocess.CalledProcessError) as exc:
            parser.error(f'cannot read Wi-Fi addresses: {exc}')
        if len(wifi)!=1:
            parser.error('expected one Wi-Fi IPv4 address; connect Wi-Fi or select --wifi-interface/--no-auto-wifi')
        args.bind,args.allow=str(wifi[0].ip),str(wifi[0].network)
        tail=addresses.get('tailscale0',[])
        args.tail_bind=str(tail[0].ip) if tail else None
    if args.http:
        args.tail_bind=None
    bind=ipaddress.IPv4Address(args.bind)
    if not 1<=args.port<=65535:parser.error('port must be 1–65535')
    tls=gate=allowed=None
    private=[ipaddress.ip_network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')]
    needs_gate=args.http or not bind.is_loopback or any(
        (args.cert,args.key,args.password_file,args.allow,args.tail_bind))
    if needs_gate:
        if not args.password_file or not args.allow:
            parser.error('LAN listening requires a password file and a private subnet')
        allowed=ipaddress.IPv4Network(args.allow,strict=True)
        if bind not in allowed or not any(allowed.subnet_of(net) for net in private):
            parser.error('bind to one address in an explicit private subnet')
        if args.http:
            gate=PasswordGate(args.password_file,cookie_name='pipe_inspection_http',secure=False)
        else:
            if not args.cert or not args.key:
                parser.error('HTTPS requires a TLS certificate and key')
            if Path(args.key).stat().st_mode & 0o077:parser.error('TLS private key must be owner-only')
            tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.minimum_version=ssl.TLSVersion.TLSv1_2
            tls.load_cert_chain(args.cert,args.key)
            gate=PasswordGate(args.password_file)
    if args.hostname and (gate is None or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9-]{0,62}(?<!-)',args.hostname)):
        parser.error('hostname must be a single DNS label on an authenticated listener')
    tailnet=ipaddress.IPv4Network('100.64.0.0/10')
    tail_bind=ipaddress.IPv4Address(args.tail_bind) if args.tail_bind else None
    if tail_bind is not None and (tail_bind not in tailnet or not tls or tail_bind==bind):
        parser.error('the secondary listener requires a distinct Tailscale IPv4 address and TLS')
    catalog=Catalog(args.recordings)
    hostname=args.hostname.lower() if args.hostname else None
    worker_slots=threading.BoundedSemaphore(32)
    with ExitStack() as stack:
        primary=stack.enter_context(InspectionHTTP((str(bind),args.port),tls,gate,allowed,hostname,workers=worker_slots))
        servers=[primary]
        if tail_bind is not None:
            try:
                tail=InspectionHTTP((str(tail_bind),args.port),tls,gate,tailnet,hostname,device='tailscale0',workers=worker_slots)
            except OSError as exc:
                print(f'Tailscale listener unavailable: {exc}; keeping LAN only',file=sys.stderr,flush=True)
            else:
                servers.append(stack.enter_context(tail))
        bags=BagCatalog(os.environ.get('PIPE_WEB_BAG_ROOT') or Path.home()/'.ros/log/pipe_hover')
        live=Live(ROOT)
        predictions=Predictions(ROOT,bags,live)
        for server in servers:
            server.catalog,server.bags,server.live,server.predictions=catalog,bags,live,predictions
        def stop(*_):
            for server in servers:
                threading.Thread(target=server.shutdown,daemon=True).start()
        signal.signal(signal.SIGTERM,stop)
        signal.signal(signal.SIGINT,stop)
        for server in servers:
            print(f'Inspection web: {server.scheme}://{server.host} on {server.server_address[0]}',flush=True)
        threads=[threading.Thread(target=s.serve_forever,daemon=True) for s in servers[1:]]
        for thread in threads:thread.start()
        try:
            primary.serve_forever()
        finally:
            for server in servers[1:]:server.shutdown()
            for thread in threads:thread.join(3)
            predictions.close()
            live.close()

if __name__=='__main__':
    main()
