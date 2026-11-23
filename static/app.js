import {Viewer,names,colors} from './viewer.js';
const $=id=>document.getElementById(id), player=$('player'), seek=$('seek');
const viewer=new Viewer($('viewer'));
let space='body',reference=null,preferredSpace=null,referenceKey='';
let mode='replay', sessions=[],session=null,selected='', selection=0,mediaEpoch=0;
let ct=null,vt=null,position=0,playing=false,lastTick=0,frameIndex=-2,loading=null,lastFetch=0;
let scrub=null,exact=null,failedIndex=-2,cachedBytes=0;
const frames=new Map();
let predictionBusy=false,predictionRequest=false;
let liveInfo=null,lastCloud='',lastImage='',imageBlob=null,liveEpoch=0,busy=false;
let lastInfrared='',infraredBlob=null;
let sensorView='radar';
const sensorButtons=document.querySelectorAll('.camera-switch button');
const showsRadar=()=>sensorView==='all'||sensorView==='radar';
const showVisibleCamera=()=>sensorView==='all'||sensorView==='visible';
const showInfraredCamera=()=>sensorView==='all'||sensorView==='infrared';
const locale=n=>n.toLocaleString('zh-CN');
const endpoint=(sid,kind,track)=>`/api/sessions/${encodeURIComponent(sid)}/${kind}/${encodeURIComponent(track)}`;
const msg=(id,text)=>{const el=$(id);if(el.textContent!==text)el.textContent=text;};
const visible=(id,show)=>{$(id).hidden=!show;};
function connection(state,text){const el=$('connection');if(el.dataset.state!==state)el.dataset.state=state;msg('connection',text);}
async function get(url){const r=await fetch(url);if(r.status===401&&location.protocol==='https:')location.assign('/login');if(!r.ok)throw Error(`HTTP ${r.status}`);return r.json();}
if(location.protocol==='https:'){
 visible('signOut',true);
 $('signOut').onclick=async()=>{
  try{const r=await fetch('/logout',{method:'POST',headers:{'X-Inspection-Action':'1'},redirect:'manual'});if(r.type==='opaqueredirect'||r.status===401)location.assign('/login');else connection('error','退出未完成，请重试');}
  catch{connection('error','退出未完成，请检查连接');}
 };
}
function options(id,choices,wanted=''){const n=$(id);n.replaceChildren(...choices.map(([v,t])=>new Option(t,v)));if(choices.some(([v])=>v===wanted))n.value=wanted;n.disabled=!choices.length;}
function emptyCloud(text,refit=false){viewer.clear(refit);msg('cloudEmpty',text);visible('cloudEmpty',true);visible('legend',false);}
function draw(buf,meta,segmented){viewer.setReference(space==='start'&&$('showGrid').checked);const {n,stats}=viewer.set(buf);msg('cloudMeta',`${locale(n)} 点 / ${meta}`);visible('cloudEmpty',!n||!viewer.gl);msg('cloudEmpty',viewer.gl?'此帧没有可显示的点':'浏览器的 WebGL 不可用');
 const legend=$('legend');legend.replaceChildren();visible('legend',segmented&&n>0);if(!segmented)return;
 for(const [i,label] of names.entries()){if(!stats[i])continue;const span=document.createElement('span'),svg=document.createElementNS('http://www.w3.org/2000/svg','svg'),dot=document.createElementNS(svg.namespaceURI,'circle');svg.setAttribute('viewBox','0 0 10 10');svg.setAttribute('width','10');svg.setAttribute('height','10');dot.setAttribute('cx','5');dot.setAttribute('cy','5');dot.setAttribute('r','4');dot.setAttribute('fill',colors[i]);svg.append(dot);span.append(svg,`${label} ${locale(stats[i])}`);legend.append(span);}
 if(!legend.childElementCount)visible('legend',false);
}
function referenceUI(){
 const selector=$('cloudSpace'),grid=$('showGrid');
 selector.options[1].disabled=!reference?.available;selector.value=space;
 grid.disabled=space!=='start'||!reference?.available;
 viewer.setReference(!grid.disabled&&grid.checked);
 if(space==='start'&&reference?.available){
  const anchor=mode==='live'?'实时输入起点':'录制起始状态';
  msg('referenceNote',`${anchor} · XY 平面（Z = 0，非实测地面）`);
 }else if(reference?.available){msg('referenceNote','可切换到起始坐标查看 XY 参考面');}
 else if(reference?.pending){msg('referenceNote','正在读取同步位姿…');}
 else{msg('referenceNote','没有可用的同步位姿，未绘制参考面');}
}
function changeSpace(next){
 if(next==='start'&&!reference?.available)return;
 if(next===space){referenceUI();return;}
 space=next;referenceUI();
 if(mode==='live'){
  liveEpoch++;lastCloud='';emptyCloud('等待点云…',true);viewer.reset();
 }else setTracks(false,true,true);
}
async function loadReference(obj,nonce){
 if(obj.kind!=='rosbag2')return;
 try{
  const r=await get(`/api/sessions/${encodeURIComponent(obj.id)}/reference`);
  if(mode!=='replay'||session!==obj||nonce!==selection)return;
  reference=r;
 }catch{
  if(mode!=='replay'||session!==obj||nonce!==selection)return;
  reference={available:false};
 }
 changeSpace(reference.available&&preferredSpace!=='body'?'start':'body');
}
$('cloudSpace').onchange=()=>{preferredSpace=$('cloudSpace').value;changeSpace(preferredSpace);};
$('showGrid').onchange=referenceUI;
for(const b of document.querySelectorAll('[data-view]'))b.onclick=()=>viewer.view(b.dataset.view);
function renderList(){
 const shelf=$('sessions'),scroll=shelf.scrollTop,focused=shelf.contains(document.activeElement)?document.activeElement.dataset.session:null;
 const items=sessions.map(s=>{
  const b=document.createElement('button'),sub=document.createElement('small');
  b.type='button';b.textContent=s.title;b.dataset.session=s.id;b.setAttribute('aria-current',String(s.id===selected));
  sub.textContent=s.kind==='rosbag2'?`ROS 2 bag / ${locale(s.frames)} 帧`:[s.demo?'演示':'实录',s.cloud?'点云':'',s.video?'视频':''].filter(Boolean).join(' / ');
  b.append(sub);b.onclick=()=>choose(s.id);return b;
 });
 shelf.replaceChildren(...items);shelf.scrollTop=scroll;
 if(focused)items.find(b=>b.dataset.session===focused)?.focus({preventScroll:true});
 msg('sessionCount',locale(sessions.length));visible('sessionCount',sessions.length>0);
}
async function reload(){try{const data=await get('/api/sessions');sessions=data.sessions.sort((a,b)=>Number(a.demo)-Number(b.demo));const notes=[];if(data.bagsSkipped)notes.push(`${data.bagsSkipped} 段 bag 尚不可播放`);if(data.invalid.length)notes.push(`${data.invalid.length} 个无效清单`);msg('listNote',notes.join(' / ')||(sessions.length?'':'没有可播放的机上记录。'));renderList();if(mode==='replay'){const chosen=sessions.some(s=>s.id===selected)?selected:sessions[0]?.id;await choose(chosen||'');}}catch{msg('listNote','无法读取飞机记录。');}}
async function choose(id){const nonce=++selection;selected=id;renderList();if(mode!=='replay')return;session=null;space='body';reference=null;preferredSpace=null;referenceUI();configureReplay();if(!id)return;
 if(sessions.find(x=>x.id===id)?.kind==='rosbag2'){emptyCloud('正在读取 bag 时间索引…');msg('cloudMeta','只读读取 /cloud_registered_body');}
 try{const result=await get(`/api/sessions/${encodeURIComponent(id)}`);if(nonce===selection&&mode==='replay'){session=result;if(result.kind==='rosbag2')reference={pending:true};referenceUI();configureReplay();loadReference(result,nonce);}}
 catch{if(nonce===selection&&mode==='replay'){msg('listNote','无法打开该会话。请确认记录已结束且所有分卷齐全。');emptyCloud('无法读取这段录制');msg('cloudMeta','未加载');}}
}
function updateDownloads(){
 const ir=session?.infrared??[];
 const pick=$('infraredTrack');
 if(pick.dataset.session!==(session?.id??'')){
  options('infraredTrack',ir.map(t=>[t.id,t.label]));pick.dataset.session=session?.id??'';
 }
 const currentInfrared=ir.find(t=>t.id===pick.value);
 const replay=mode==='replay';
 const groups=[['cloud','pointcloud',ct,'此记录没有点云轨道','ZIP · 录制坐标'],
               ['video','visible',vt,'此记录没有可见光录像','原始 MP4'],
               ['infrared','infrared',currentInfrared,'此记录没有红外录像','原始 MP4']];
 for(const [prefix,kind,track,empty,ready] of groups){
  visible(`${prefix}Downloads`,replay);
  const link=$(`${prefix}Download`),available=replay&&!!session?.id&&!!track;
  if(available)link.href=`${endpoint(session.id,'downloads',kind)}/${encodeURIComponent(track.id)}`;
  else link.removeAttribute('href');
  link.setAttribute('aria-disabled',String(!available));
  msg(`${prefix}DownloadNote`,!session?'选择记录后下载':available?ready:empty);
 }
 visible('infraredTrackLabel',replay&&ir.length>1);
 if(replay){
  msg('infraredStatus',currentInfrared?'可下载':'无录像');
  msg('infraredEmptyText','尚无红外图像');
  msg('infraredMeta',currentInfrared?'已登记红外录像，本页暂不预览':'此记录没有红外录像');
 }else{
  msg('infraredStatus','等待画面');
  msg('infraredEmptyText','等待红外图像');
  msg('infraredMeta','等待红外相机发布 JPEG');
 }
}
$('infraredTrack').onchange=updateDownloads;
function clearVideo(){player.pause();player.removeAttribute('src');player.load();visible('player',false);}
function configureReplay(){options('cloudTrack',(session?.clouds||[]).map(x=>[x.id,x.label]));options('videoTrack',(session?.videos||[]).map(x=>[x.id,x.label]));visible('badge',!!session?.demo);setTracks(true);visible('predictionPanel',mode==='replay'&&session?.kind==='rosbag2');if(session?.kind==='rosbag2'){msg('predictionStatus','正在检查分割结果…');$('predictionAction').disabled=true;}predictionPoll();}
function setTracks(resetVideo,preserve=false,recenter=false){
 const t=positionNow(),resume=exact?.resume??running();
 mediaEpoch++;resetFrameQueue();lastTick=0;playing=false;position=0;
 ct=session?.clouds.find(x=>x.id===$('cloudTrack').value)||null;vt=session?.videos.find(x=>x.id===$('videoTrack').value)||null;
 if(resetVideo){clearVideo();if(vt){player.src=endpoint(selected,'video',vt.id);player.playbackRate=Number($('speed').value);visible('player',true);}}
 visible('videoEmpty',!vt);msg('videoEmpty',session?'此会话没有视频':'选择视频记录');msg('videoMeta',vt?vt.label:'没有视频轨道');
 updateDownloads();
 emptyCloud(ct?'载入点云帧…':session?'此会话没有点云':'选择点云记录',!preserve||recenter);if(!preserve||recenter)viewer.reset();msg('cloudMeta',ct?ct.frame:'没有点云轨道');
 msg('syncNote',session?.kind==='rosbag2'?'直接读取机上 ROS 2 bag 的 /cloud_registered_body，不做地图拼接。拖动下方时间轴可跳到指定时刻。':synced()?'有共同采集时间基准。拖动共享时间轴或视频进度条，两路一起跳转。':'没有共同时间基准。点云与视频各自播放，视频可拖动自己的进度条。');
 msg('timeTitle',synced()?'共享时间轴':'点云时间轴');if(preserve){jump(Math.min(t,duration()),resume);}else{updateTime();requestFrame();}
}
const synced=()=>Boolean(session?.sync&&ct&&vt);
const duration=()=>synced()?(Number.isFinite(player.duration)?player.duration:0):(ct?.times.at(-1)||0);
function timeText(v){const ds=Math.floor(Math.max(0,v)*10),s=Math.floor(ds/10);return `${String(Math.floor(s/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}.${ds%10}`;}
function positionNow(){return scrub?.time??exact?.time??(synced()?player.currentTime:position);}
function running(){return synced()?!player.paused:playing;}
function updateTime(){
 const d=duration(),t=positionNow(),limit=String(d||1);
 if(seek.max!==limit)seek.max=limit;
 seek.disabled=!d;if(!scrub&&Math.abs(Number(seek.value)-t)>.006)seek.value=String(t);
 $('play').disabled=!d||!!scrub;$('begin').disabled=!d||!!scrub;
 msg('play',(exact?.resume??running())?'暂停':'播放');msg('clock',`${timeText(t)} / ${timeText(d)}`);
}
function targetFrame(time=positionNow()){
 if(!ct)return -1;const t=time+(synced()?vt.offset-ct.offset:0),a=ct.times;
 if(t<a[0]-.001||t>a.at(-1)+.25)return -1;
 let lo=0,hi=a.length;while(lo<hi){const m=(lo+hi)>>1;if(a[m]<=t+.001)lo=m+1;else hi=m;}
 return Math.max(0,lo-1);
}
function abortFrame(){const prior=loading;loading=null;prior?.controller.abort();}
function resetFrameQueue(){abortFrame();frameIndex=-2;failedIndex=-2;lastFetch=0;scrub=null;exact=null;cachedBytes=0;frames.clear();}
function remember(index,buf){
 frames.set(index,buf);cachedBytes+=buf.byteLength;
 while(frames.size>24||cachedBytes>6*1024*1024){const first=frames.keys().next().value;cachedBytes-=frames.get(first).byteLength;frames.delete(first);}
}
function finishExact(idx){
 if(!exact||exact.index!==idx)return;
 const resume=exact.resume;exact=null;
 if(resume){lastTick=0;if(synced())player.play().catch(()=>msg('videoMeta','无法恢复播放'));else playing=position<duration();}
 updateTime();
}
function showFrame(idx,buf){
 const t=ct.times[idx];draw(buf,`${space==='start'?'起始坐标':ct.frame} / ${timeText(t)} / ${idx+1} / ${ct.times.length} 帧`,ct.segmented);frameIndex=idx;
 finishExact(idx);
}
async function requestFrame(){
 if(mode!=='replay')return;
 if(!ct||!showsRadar()){if(exact)finishExact(exact.index);return;}
 const idx=exact?.index??targetFrame();
 if(idx===frameIndex||idx===failedIndex){finishExact(idx);return;}
 if(idx<0){emptyCloud('此时刻没有点云');frameIndex=-1;finishExact(idx);return;}
 if(frames.has(idx)){
  const buf=frames.get(idx);frames.delete(idx);frames.set(idx,buf);showFrame(idx,buf);return;
 }
 if(loading)return;
 const now=performance.now();if(!exact&&now-lastFetch<(scrub?85:running()?50:0))return;
 const epoch=mediaEpoch,controller=new AbortController();loading={controller,index:idx};lastFetch=now;
 try{
  const r=await fetch(`${endpoint(selected,'cloud',ct.id)}/${idx}${space==='start'?'?space=start':''}`,{signal:controller.signal});
  if(!r.ok)throw Error(r.status);const buf=await r.arrayBuffer();
  if(epoch!==mediaEpoch||controller.signal.aborted||mode!=='replay')return;
  remember(idx,buf);
  const desired=exact?.index??targetFrame();
  if(idx===desired||(!exact&&!frames.has(desired)))showFrame(idx,buf);
 }catch(e){
  if(epoch===mediaEpoch&&!controller.signal.aborted&&idx===(exact?.index??targetFrame())){
   failedIndex=idx;
   if(space==='start'&&e.message==='422'){viewer.setReference(false);if(frameIndex<0)emptyCloud('该帧缺少同步位姿，可切回原始坐标');else msg('cloudMeta','该帧缺少同步位姿，保留上一帧');}
   else if(frameIndex<0)emptyCloud('此点云帧读取失败');else msg('cloudMeta','该帧未读取，保留上一帧');
   finishExact(idx);
  }
 }finally{if(loading?.controller===controller)loading=null;}
}
function jump(t,resume=exact?.resume??running()){
 if(mode!=='replay')return;
 if(synced()){player.pause();player.currentTime=t;}else{playing=false;position=t;}
 exact={time:t,index:targetFrame(t),resume};
 if(loading?.index!==exact.index)abortFrame();
 failedIndex=-2;updateTime();requestFrame();
}
function tick(now){
 if(mode==='replay'&&ct){
  if(playing&&!scrub&&!exact&&!synced()&&lastTick){position=Math.min(duration(),position+(now-lastTick)*.001*Number($('speed').value));if(position>=duration())playing=false;}
  lastTick=now;updateTime();requestFrame();
 }
 requestAnimationFrame(tick);
}
seek.addEventListener('pointerdown',e=>{
 if(e.button!==0||seek.disabled||scrub)return;
 scrub={pointer:e.pointerId,time:positionNow(),resume:exact?.resume??running()};
 exact=null;if(synced())player.pause();else playing=false;
});
function endScrub(e){
 if(scrub?.pointer!==e.pointerId)return;
 const {resume}=scrub,t=Number(seek.value);scrub=null;jump(t,resume);
}
window.addEventListener('pointerup',endScrub);
window.addEventListener('pointercancel',endScrub);
window.addEventListener('blur',()=>{if(scrub){const {time,resume}=scrub;scrub=null;jump(time,resume);}});
seek.oninput=()=>{const t=Number(seek.value);if(scrub){scrub.time=t;updateTime();requestFrame();}else jump(t);};
$('begin').onclick=()=>jump(0);
$('play').onclick=async()=>{
 if(exact){exact.resume=!exact.resume;updateTime();return;}
 if(synced()){
  if(!player.paused)player.pause();
  else if(player.ended)jump(0,true);
  else try{await player.play();}catch{msg('videoMeta','无法播放此视频');}
 }else if(playing){playing=false;}
 else if(position>=duration()){jump(0,true);}
 else{lastTick=0;playing=true;}
 updateTime();
};
$('speed').onchange=()=>{player.playbackRate=Number($('speed').value);};$('reset').onclick=()=>viewer.reset();$('reload').onclick=reload;
player.addEventListener('loadedmetadata',()=>{if(vt&&mode==='replay'){msg('videoMeta',`${vt.label} / ${player.videoWidth}×${player.videoHeight}`);updateTime();}});
player.addEventListener('error',()=>{if(mode==='replay'&&vt){msg('videoMeta','无法播放。请检查视频文件和编码（推荐 H.264 MP4）。');}});
const stateText={online:'输出在线',not_configured:'未配置',starting:'正在启动',waiting:'等待输出',stale:'输出过期',failed:'进程失败',no_source:'等待输入',idle:'待启动',ros_unavailable:'ROS 未连接'};
function selectMode(which){if(mode===which)return;mode=which;liveEpoch++;mediaEpoch++;resetFrameQueue();space='body';reference=null;preferredSpace=null;referenceKey='';referenceUI();clearVideo();clearImage();clearInfrared();emptyCloud('等待数据…',true);viewer.reset();lastCloud='';lastImage='';
 const live=mode==='live';$('tabReplay').setAttribute('aria-pressed',String(!live));$('tabLive').setAttribute('aria-pressed',String(live));for(const id of ['library','replayNote','timeline'])visible(id,!live);visible('modelPanel',live);visible('badge',false);visible('predictionPanel',false);updateDownloads();
 msg('title',live?'实时识别':'记录回放');msg('subtitle',live?'查看机载相机、点云与实时识别输出。':'选择会话，回看管内图像和三维点云。');
 if(live){options('cloudTrack',[['cloud','原始点云'],['segment','分割结果']]);options('videoTrack',[['video','原始画面'],['yolo','YOLO 结果']]);renderModels();livePoll();}else choose(selected);
}
function clearImage(){if(imageBlob){URL.revokeObjectURL(imageBlob);imageBlob=null;}$('liveImage').removeAttribute('src');visible('liveImage',false);}
function clearInfrared(){
 if(infraredBlob){URL.revokeObjectURL(infraredBlob);infraredBlob=null;}
 lastInfrared='';$('infraredImage').removeAttribute('src');visible('infraredImage',false);visible('infraredEmpty',true);
}
function infraredUnavailable(feed){
 clearInfrared();
 msg('infraredStatus',feed?.publisher?'等待画面':'离线');
 msg('infraredEmptyText',feed?.publisher?'等待新的红外图像':'未收到红外图像');
 msg('infraredMeta',feed?.publisher?'红外话题在线，等待新帧':'没有红外图像发布者');
}
function setSensorView(next){
 if(next===sensorView||!['all','radar','visible','infrared'].includes(next))return;
 sensorView=next;
 $('workbenchBoard').dataset.sensorView=next;
 visible('radarPanel',showsRadar());
 visible('visiblePanel',showVisibleCamera());
 visible('infraredPanel',showInfraredCamera());
 for(const button of sensorButtons)button.setAttribute('aria-pressed',String(button.dataset.sensorView===next));
 if(mode==='live'){
  liveEpoch++;
  if(!showsRadar()){lastCloud='';emptyCloud('等待雷达点云');}
  if(!showVisibleCamera()){lastImage='';clearImage();visible('videoEmpty',true);msg('videoEmpty','等待可见光画面');}
  if(!showInfraredCamera())clearInfrared();
  livePoll();
 }else{
  if(!showsRadar())abortFrame();
  requestFrame();
 }
}
for(const button of sensorButtons)button.onclick=()=>setSensorView(button.dataset.sensorView);
function showInfrared(blob,id,feed){
 const old=infraredBlob;infraredBlob=URL.createObjectURL(blob);$('infraredImage').src=infraredBlob;
 if(old)URL.revokeObjectURL(old);
 lastInfrared=id;visible('infraredImage',true);visible('infraredEmpty',false);
 msg('infraredStatus','实时');msg('infraredMeta',`红外画面 / ${feed.stamp||'无时间戳'}`);
}
$('tabReplay').onclick=()=>selectMode('replay');$('tabLive').onclick=()=>selectMode('live');
$('cloudTrack').onchange=()=>{if(mode==='live'){liveEpoch++;lastCloud='';emptyCloud('等待此轨道数据…',true);viewer.reset();}else setTracks(false,true);};
$('videoTrack').onchange=()=>{if(mode==='live'){liveEpoch++;lastImage='';clearImage();visible('videoEmpty',true);}else setTracks(true);};
function renderModels(){if(!liveInfo)return;const root=$('modelRows');for(const [key,title] of [['yolo','YOLO26m 检测'],['segment','PointNeXt-B 分割']]){let el=root.querySelector(`[data-model="${key}"]`);if(!el){el=document.createElement('div');el.className='model';el.dataset.model=key;const head=document.createElement('div'),strong=document.createElement('strong'),state=document.createElement('span'),foot=document.createElement('div'),detail=document.createElement('small'),btn=document.createElement('button');strong.textContent=title;state.className='state';head.append(strong,state);foot.append(detail,btn);el.append(head,foot);root.append(el);}const m=liveInfo.models[key];el.querySelector('.state').textContent=stateText[m.state]||m.state;el.querySelector('small').textContent=m.external?'由现有 ROS 节点提供':m.missing.length?'缺少 '+m.missing.join('、'):m.state==='failed'?`退出码 ${m.exit}；检查飞机日志`:m.source?'输入就绪':'等待正确的输入话题';const b=el.querySelector('button'),act=m.owned?'stop':'start';b.textContent=m.external?'复用运行中节点':act==='stop'?'停止':'启动';b.disabled=!m.owned&&!m.canStart;b.onclick=()=>control(key,act,b);}}
async function control(key,act,b){b.disabled=true;try{const r=await fetch(`/api/control/${key}/${act}`,{method:'POST',headers:{'X-Inspection-Action':'1'}});const j=await r.json();msg('modelNote',r.ok?(act==='start'?'启动请求已提交，等待实际输出。':'工作台启动的进程已停止。'):`未执行：${stateText[j.detail]||j.detail||j.error}`);if(r.ok&&act==='start')$(key==='yolo'?'videoTrack':'cloudTrack').value=key;}catch{msg('modelNote','无法提交操作，请检查连接。');}finally{b.disabled=false;livePoll();}}
async function livePoll(){
 if(busy||mode!=='live')return;busy=true;const epoch=liveEpoch;
 try{const st=await get('/api/live/status');if(epoch!==liveEpoch||mode!=='live')return;liveInfo=st;renderModels();
  const r=st.reference,key=`${r?.available}/${r?.version}`;
  if(key!==referenceKey){
   referenceKey=key;const hadReference=reference?.available;reference=r;
   const next=r?.available&&preferredSpace!=='body'?'start':'body';
   if(space!==next){changeSpace(next);return;}
   if(next==='start'&&hadReference&&r?.available){lastCloud='';emptyCloud('等待新的起始坐标…',true);viewer.reset();}
   referenceUI();
  }
  connection(st.ros?'ready':'paused',st.ros?'ROS 2 已连接':'ROS 2 未连接');const ck=$('cloudTrack').value,vk=$('videoTrack').value,c=st.feeds[ck],v=st.feeds[vk],ir=st.feeds.thermal;
  const fetchLatest=async(kind,feed,version,apply,unavailable)=>{if(!feed?.available){unavailable();return;}if(version===feed.version)return;const aligned=space==='start'&&(kind==='cloud'||kind==='segment');const r=await fetch(`/api/live/${kind}${aligned?'?space=start':''}`);if(r.status!==200){if(kind==='thermal'&&epoch===liveEpoch&&mode==='live')unavailable();if(aligned&&r.status===422&&epoch===liveEpoch){viewer.setReference(false);msg('cloudMeta','该帧缺少同步位姿');}return;}const data=kind==='cloud'||kind==='segment'?await r.arrayBuffer():await r.blob();if(epoch===liveEpoch&&mode==='live')apply(data,r.headers.get('X-Frame-Version'));};
  await Promise.allSettled([
   showsRadar()&&fetchLatest(ck,c,lastCloud,(b,id)=>{draw(b,`${space==='start'?'起始坐标':c.frame||'未注明坐标系'} / ${c.stamp||'无时间戳'}`,ck==='segment');lastCloud=id;},()=>{lastCloud='';emptyCloud(c?.publisher?'已发现话题，等待有效点云':'没有点云发布者');msg('cloudMeta','无实时点云');}),
   showVisibleCamera()&&fetchLatest(vk,v,lastImage,(b,id)=>{const old=imageBlob;imageBlob=URL.createObjectURL(b);$('liveImage').src=imageBlob;if(old)URL.revokeObjectURL(old);visible('liveImage',true);visible('videoEmpty',false);msg('videoMeta',`${vk==='yolo'?'YOLO 带框预览':'相机画面'} / ${v.stamp||'无时间戳'}`);lastImage=id;},()=>{lastImage='';clearImage();visible('videoEmpty',true);msg('videoEmpty',v?.publisher?'已发现话题，等待有效画面':'没有图像发布者');msg('videoMeta','无实时视频');}),
   showInfraredCamera()&&fetchLatest('thermal',ir,lastInfrared,(b,id)=>showInfrared(b,id,ir),()=>infraredUnavailable(ir)).catch(()=>{
    if(epoch===liveEpoch&&mode==='live')infraredUnavailable(ir);
   })]);
 }catch{if(mode==='live'){connection('error','与飞机网页的连接中断');msg('modelNote','恢复连接后可重试。');clearImage();visible('videoEmpty',true);msg('videoEmpty','连接中断');infraredUnavailable(null);msg('infraredMeta','与飞机网页的连接中断');emptyCloud('连接中断');lastCloud='';lastImage='';}}
 finally{busy=false;}
}
const predWords={
 other_job:'另一段记录正在生成，请稍后再试。',in_flight:'飞机正在起飞、飞行或降落，暂不生成。',
 armed:'飞机已解锁，解除解锁后可在地面生成。',telemetry_unavailable:'飞控遥测不可用，无法确认已落地且未解锁。',
 confirming_ground:'正在确认飞机已落地且未解锁…',flight_stopped:'飞机解锁或进入飞行状态，已停止生成。',telemetry_stopped:'失去可靠的地面状态，已停止生成。',disk_full:'飞机剩余存储不足。',model_missing:'飞机未配置分割模型。',
 bench_fcu_seen:'已检测到飞控，台架生成已禁用。',bench_waiting_graph:'无法确认台架状态，暂不生成。',bench_expired:'台架生成达到 60 分钟上限，未保存部分结果。',
 failed:'分割未完成，请重试。',cancelled:'已取消，未保存部分结果。'
};
function setPrediction(d){
 const button=$('predictionAction'),bar=$('predictionProgress'),n=d.total??1;
 if(d.state==='ready'){
  msg('predictionStatus','已生成 · 模型预测，非缺陷确认');button.textContent='查看分割';button.dataset.action='show';button.disabled=false;
 }else if(d.state==='running'||d.state==='cancelling'){
  msg('predictionStatus',d.state==='running'?`${d.bench?'台架生成':'正在生成'}（${locale(d.done??0)} / ${locale(n)} 帧）`:d.reason?(predWords[d.reason]||'飞行状态变化，正在停止生成…'):'正在取消…');
  button.textContent='取消生成';button.dataset.action='cancel';button.disabled=d.state==='cancelling';
 }else if(d.benchAvailable){
  msg('predictionStatus',d.last==='bench_expired'?predWords.bench_expired:'仅限飞控和电机断电时使用，每次生成一段记录。');
  button.textContent='仅板卡供电：生成分割';button.dataset.action='start-bench';button.disabled=false;
 }else{
  msg('predictionStatus',predWords[['flight_stopped','telemetry_stopped','bench_fcu_seen','bench_waiting_graph','bench_expired'].includes(d.last)?d.last:d.reason||d.state]||'在飞机上生成，完成后可拖动回放。');
  button.textContent=['cancelled','failed','flight_stopped','telemetry_stopped'].includes(d.state)?'重新生成':'生成分割结果';button.dataset.action='start';button.disabled=d.state==='blocked';
 }
 bar.max=n;bar.value=d.done??0;visible('predictionProgress',d.state==='running'||d.state==='cancelling');
 if(predictionRequest)button.disabled=true;
}
async function predictionPoll(){
 if(predictionBusy||mode!=='replay'||session?.kind!=='rosbag2')return;
 const current=session;predictionBusy=true;
 try{
  const d=await get(`/api/sessions/${encodeURIComponent(current.id)}/prediction`);
  if(current!==session||mode!=='replay')return;
  if(d.state==='ready'&&!current.clouds.some(c=>c.id==='pointnext')){
   const ready=await get(`/api/sessions/${encodeURIComponent(current.id)}`);if(current!==session||mode!=='replay')return;
   current.clouds=ready.clouds;options('cloudTrack',current.clouds.map(c=>[c.id,c.label]),ct?.id);
  }
  setPrediction(d);
 }catch{if(current===session&&mode==='replay')msg('predictionStatus','无法更新分割任务状态。');}
 finally{predictionBusy=false;}
}
$('predictionAction').onclick=async()=>{
 if(mode!=='replay'||session?.kind!=='rosbag2'||predictionRequest)return;
 const button=$('predictionAction'),which=button.dataset.action,selectedSession=session;
 if(which==='show'){$('cloudTrack').value='pointnext';setTracks(false,true);return;}
 if(!['start','start-bench','cancel'].includes(which))return;
 const bench=which==='start-bench';
 if(bench&&!window.confirm('请确认飞机已落地，仅 Jetson 板卡供电，飞控和电机均已断电。任务完成或取消之前，请勿重新给飞控或电机上电。确认后只生成当前这一段。'))return;
 predictionRequest=true;button.disabled=true;
 try{
  const r=await fetch(`/api/sessions/${encodeURIComponent(selectedSession.id)}/prediction/${which}`,{method:'POST',headers:{'X-Inspection-Action':'1',...(bench?{'X-Inspection-Bench':'power-isolated'}:{})}});
  const d=await r.json();if(selectedSession!==session||mode!=='replay')return;
  if(!r.ok){msg('predictionStatus',predWords[d.detail]||'操作未完成，请重试。');}
  else if(which!=='cancel')setPrediction({state:'running',bench,total:ct?.times.length,done:0});
  else setPrediction({state:'cancelling',total:ct?.times.length,done:0});
 }catch{if(selectedSession===session)msg('predictionStatus','与飞机连接中断，请重试。');}
 finally{predictionRequest=false;predictionPoll();}
};
setInterval(predictionPoll,850);
requestAnimationFrame(tick);setInterval(livePoll,210);
get('/api/live/status').then(x=>connection(x.ros?'ready':'paused',x.ros?'ROS 2 已连接':'ROS 2 未连接')).catch(()=>connection('error','工作台连接失败'));
reload();
