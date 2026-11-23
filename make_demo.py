"""Generate deterministic synthetic media for playback verification only."""
import json
import subprocess
from pathlib import Path
import numpy as np
from cloud import pack

base=Path('recordings/demo-pipe')
base.mkdir(exist_ok=False)
r=np.random.default_rng(46)
x=r.uniform(-2.6,2.6,8000); theta=r.uniform(0,2*np.pi,len(x)); noise=r.normal(0,.012,len(x))
subprocess.run(['gst-launch-1.0','-q','videotestsrc','pattern=ball','num-buffers=240','!',
                'video/x-raw,width=480,height=270,framerate=20/1','!','x264enc','speed-preset=ultrafast','key-int-max=20','!',
                'video/x-h264,profile=baseline','!','mp4mux','faststart=true','!','filesink',f'location={base/"demo.mp4"}'],check=True)
m=dict(version=1,title='合成管段回放',demo=True,clock='shared',videos={'camera':dict(label='合成测试视频',file='demo.mp4')},clouds={})
for name in ('raw','segmented'):
    (base/name).mkdir()
    frames=[]
    for i in range(61):
        show=(x<-.7+i*.062)|(x>2.23)
        rr=.7+noise+.035*np.sin(5*x+i*.17)
        xyz=np.column_stack((x,rr*np.cos(theta),rr*np.sin(theta)))[show]
        label=np.where(np.abs(x-1.35)<.12,7,np.where((np.abs(x+.7)<.4)&(np.abs(theta-2.2)<.43),1,0))[show] if name=='segmented' else None
        file=f'{name}/{i:03}.pwc';(base/file).write_bytes(pack(xyz,labels=label));frames.append([i*.2,file])
    m['clouds'][name]=dict(label='合成分割着色' if name=='segmented' else '合成原始点云',frame='demo',segmented=name=='segmented',frames=frames)
(base/'session.json').write_text(json.dumps(m,ensure_ascii=False),encoding='utf-8')
