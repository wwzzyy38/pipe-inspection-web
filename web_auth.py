"""One shared appliance password, local secret storage, and short-lived sessions."""
import argparse
from collections import OrderedDict, deque
import getpass
import hashlib
import hmac
from http.cookies import CookieError, SimpleCookie
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
import threading
import time

COOKIE = '__Host-pipe_session'
TTL = 8*60*60
WINDOW = 60
MAX_PASSWORD = 256


def derive(password, salt):
    return hashlib.scrypt(password, salt=salt, n=2**15, r=8, p=1, dklen=32, maxmem=64*1024*1024)


def save_password(path, password, *, first=False):
    if not 12 <= len(password) <= MAX_PASSWORD or not password.strip():
        raise ValueError('Use 12 to 256 bytes for the web password.')
    path=Path(path)
    salt=secrets.token_bytes(16)
    value={'version':1,'salt':salt.hex(),'digest':derive(password,salt).hex()}
    with tempfile.NamedTemporaryFile(mode='w',encoding='ascii',dir=path.parent,prefix='.password-',delete=False) as handle:
        tmp=Path(handle.name)
        try:
            json.dump(value,handle)
            handle.flush();os.fsync(handle.fileno())
            if first:os.link(tmp,path)
            else:tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)


class PasswordGate:
    def __init__(self,path):
        self.path=Path(path)
        self.lock=threading.RLock()
        self.work=threading.BoundedSemaphore(2)
        self.signature=None
        self.pair=None
        self.sessions=OrderedDict()
        self.attempts=OrderedDict()
        self.total=deque()

    def reload(self):
        try:
            with self.path.open('rb') as f:
                s=os.fstat(f.fileno())
                if not stat.S_ISREG(s.st_mode) or s.st_mode&0o077 or s.st_uid!=os.getuid() or s.st_size>4096:
                    raise ValueError('password file must be a small owner-only file')
                sig=(s.st_dev,s.st_ino,s.st_mtime_ns,s.st_size)
                if sig==self.signature:return
                data=json.loads(f.read())
            salt=bytes.fromhex(data['salt']); digest=bytes.fromhex(data['digest'])
            if data['version']!=1 or len(salt)!=16 or len(digest)!=32:raise ValueError('invalid hash')
            pair=(salt,digest)
        except (OSError,ValueError,KeyError,TypeError):
            sig=pair=None
        if sig!=self.signature:
            self.signature,self.pair=sig,pair
            self.sessions.clear()

    @property
    def setup_file(self):
        return self.path.with_name(self.path.name+'.setup.json')

    def setup_allowed(self,ip,device):
        if device!='tailscale0':return False
        with self.lock:
            if os.path.lexists(self.path):return False
            try:
                with self.setup_file.open('rb') as f:
                    info=os.fstat(f.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077 or info.st_size>1024:
                        return False
                    data=json.loads(f.read())
                expires=data['expires']
                return data['ip']==ip and type(expires) is int and 0<expires-time.time()<=1800
            except (OSError,TypeError,ValueError,KeyError):return False

    def setup(self,ip,device,password,confirmation):
        with self.lock:
            if not self.setup_allowed(ip,device):return 'denied'
            if len(password)<12 or len(password.encode('utf-8'))>MAX_PASSWORD or not password.strip():
                return 'invalid'
            if password!=confirmation:return 'mismatch'
            try:save_password(self.path,password.encode('utf-8'),first=True)
            except FileExistsError:return 'denied'
            self.reload()
            try:self.setup_file.unlink(missing_ok=True)
            except OSError:pass # The password file alone permanently disables this grant.
            return 'ok'

    def login(self,ip,password):
        with self.lock:
            self.reload()
            if not self.pair:return 'unconfigured',None
            now=time.monotonic();recent=self.attempts.setdefault(ip,deque());self.attempts.move_to_end(ip)
            while len(self.attempts)>256:self.attempts.popitem(last=False)
            for stamps in (recent,self.total):
                while stamps and now-stamps[0]>=WINDOW:stamps.popleft()
            if len(recent)>=6 or len(self.total)>=40:return 'limited',None
            if not self.work.acquire(blocking=False):return 'limited',None
            recent.append(now);self.total.append(now);signature=self.signature;salt,digest=self.pair
        try:
            ok=len(password)<=MAX_PASSWORD and hmac.compare_digest(derive(password,salt),digest)
        finally:
            self.work.release()
        with self.lock:
            self.reload()
            if not ok or signature!=self.signature:return 'incorrect',None
            self.attempts.pop(ip,None)
            token=secrets.token_urlsafe(32)
            self.sessions[token]=time.monotonic()+TTL
            while len(self.sessions)>128:self.sessions.popitem(last=False)
            return 'ok',token

    @staticmethod
    def token(header):
        if not header or len(header)>4096:return ''
        try:
            jar=SimpleCookie(header); return jar[COOKIE].value if COOKIE in jar else ''
        except CookieError:return ''

    def authorized(self, header):
        token=self.token(header)
        with self.lock:
            self.reload()
            expires=self.sessions.get(token,0)
            if expires>time.monotonic():return True
            self.sessions.pop(token,None)
            return False

    def logout(self, header):
        with self.lock:self.sessions.pop(self.token(header),None)

    @staticmethod
    def cookie(token,max_age=TTL):
        return f'{COOKIE}={token}; Path=/; Max-Age={max_age}; Secure; HttpOnly; SameSite=Strict'


def main():
    parser=argparse.ArgumentParser(description='Set the customer web password without echo.');parser.add_argument('password_file',type=Path);args=parser.parse_args()
    if not os.isatty(0):parser.error('An interactive SSH terminal is required.')
    a=getpass.getpass('New web password (minimum 12 characters): ')
    if len(a)<12:parser.error('Use at least 12 characters.')
    b=getpass.getpass('Confirm web password: ')
    if a!=b:parser.error('Passwords did not match. No changes were made.')
    try:save_password(args.password_file,a.encode('utf-8'))
    except (OSError,ValueError) as e:parser.error(str(e))
    print('Web password saved. Existing web sessions must sign in again.')

if __name__=='__main__':main()
