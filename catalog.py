import json
import math
import re
import threading
import time
from pathlib import Path

ID=re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z')


def file_in(base,name,suffix):
    if not isinstance(name,str) or Path(name).is_absolute():
        raise ValueError('relative path required')
    p=(base/name).resolve(strict=True)
    if not p.is_relative_to(base) or not p.is_file() or p.suffix.lower()!=suffix:
        raise ValueError('invalid media path')
    return p


class Catalog:
    def __init__(self,directory):
        self.root=Path(directory).resolve(strict=True)
        self.cache,self.errors,self.checked={},[],-10
        self.lock=threading.Lock()

    def get(self):
        with self.lock:
            if time.monotonic()-self.checked<3:
                return self.cache,self.errors
            out,err={},[]
            for path in sorted(self.root.iterdir())[:500]:
                if path.is_symlink() or not ID.fullmatch(path.name):
                    continue
                m=path/'session.json'
                if not path.is_dir() or not m.is_file():
                    continue
                try:
                    if m.is_symlink() or m.stat().st_size>4*1024*1024:
                        raise ValueError('invalid manifest size or path')
                    out[path.name]=self.read(path,json.loads(m.read_text(encoding='utf-8')))
                except (OSError,ValueError,TypeError,KeyError,AttributeError):
                    err.append(path.name)
            self.cache,self.errors,self.checked=out,err,time.monotonic()
            return out,err

    @staticmethod
    def read(base,m):
        if m.get('version')!=1:
            raise ValueError('unsupported manifest')
        public=dict(id=base.name,title=str(m.get('title',base.name))[:120],demo=m.get('demo') is True,videos=[],infrared=[],clouds=[])
        refs={'videos':{},'infrared':{},'clouds':{}}
        for key in refs:
            entries=m.get(key,{})
            if not isinstance(entries,dict) or len(entries)>8:
                raise ValueError('invalid tracks')
            for name,track in entries.items():
                if not ID.fullmatch(name):
                    raise ValueError('invalid track name')
                offset=track.get('offset',0)
                if not isinstance(offset,(int,float)) or not math.isfinite(offset) or abs(offset)>10**7:
                    raise ValueError('invalid offset')
                item=dict(id=name,label=str(track.get('label',name))[:80],offset=float(offset))
                if key in ('videos','infrared'):
                    refs[key][name]=track['file']
                    file_in(base,track['file'],'.mp4')
                else:
                    samples=track['frames']
                    if not isinstance(samples,list) or not 0<len(samples)<=100000:
                        raise ValueError('invalid frame count')
                    last=-1
                    for t,f in samples:
                        if not isinstance(t,(int,float)) or not math.isfinite(t) or not 0<=t<=10**7 or t<=last:
                            raise ValueError('timestamps must increase')
                        if not isinstance(f,str) or Path(f).is_absolute() or '..' in Path(f).parts:
                            raise ValueError('invalid frame filename')
                        last=t
                    refs[key][name]=samples
                    item.update(times=[t for t,_ in samples],frame=str(track.get('frame','未注明'))[:64],segmented=track.get('segmented') is True)
                public[key].append(item)
        if not any(refs.values()):
            raise ValueError('no tracks')
        public['sync']=m.get('clock')=='shared' and bool(refs['videos'] and refs['clouds'])
        return public,refs,base
