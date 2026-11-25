export const names=['正常','剥落','堵塞','腐蚀','错口','沉积','脱节','密封圈'];
export const colors=['#a6bac5','#f5a66c','#75def2','#f58fb6','#a9d97e','#cab4ff','#ffdf8b','#66d8bd'];
export class Viewer {
 constructor(canvas){
  this.canvas=canvas; this.count=0; this.center=[0,0,0]; this.radius=1; this.fitted=false;
  this.gl=canvas.getContext('webgl',{antialias:true}); if(!this.gl)return;
  const g=this.gl;
  const vert=`attribute vec3 xyz; attribute vec3 rgb; varying vec3 col;
  uniform vec3 center; uniform vec3 rightAxis; uniform vec3 upAxis; uniform vec3 depthAxis;
  uniform vec2 pan; uniform vec2 screen; uniform float scale; uniform float size;
  void main(){vec3 p=(xyz-center)*scale;
  vec3 r=vec3(dot(rightAxis,p),dot(upAxis,p),dot(depthAxis,p));
  gl_Position=vec4(r.xy*screen+pan,clamp(r.z*0.17,-0.99,0.99),1.0); gl_PointSize=size; col=rgb;}`;
  const frag=`precision mediump float; varying vec3 col; uniform bool pointMode;
  void main(){if(pointMode&&distance(gl_PointCoord,vec2(.5))>.5)discard; gl_FragColor=vec4(col,1.);}`;
  const shader=(kind,source)=>{const s=g.createShader(kind);g.shaderSource(s,source);g.compileShader(s);if(!g.getShaderParameter(s,g.COMPILE_STATUS))throw Error(g.getShaderInfoLog(s));return s;};
  this.program=g.createProgram();g.attachShader(this.program,shader(g.VERTEX_SHADER,vert));g.attachShader(this.program,shader(g.FRAGMENT_SHADER,frag));g.linkProgram(this.program);if(!g.getProgramParameter(this.program,g.LINK_STATUS))throw Error(g.getProgramInfoLog(this.program));g.useProgram(this.program);
  this.u={};for(const key of ['center','rightAxis','upAxis','depthAxis','pan','screen','scale','size','pointMode'])this.u[key]=g.getUniformLocation(this.program,key);
  this.buffer=g.createBuffer();this.gridBuffer=g.createBuffer();this.gridCount=0;this.reference=false;
  this.xyz=g.getAttribLocation(this.program,'xyz');this.rgb=g.getAttribLocation(this.program,'rgb');
  g.enableVertexAttribArray(this.xyz);g.enableVertexAttribArray(this.rgb);
  g.enable(g.DEPTH_TEST);g.clearColor(.086,.149,.176,1);new ResizeObserver(()=>this.draw()).observe(canvas);
  this.pointers=new Map();this.paint=0;
  canvas.addEventListener('pointerdown',e=>{
   if(e.pointerType!=='touch'&&![0,1,2].includes(e.button))return;
   if(e.pointerType!=='touch'&&this.pointers.size)return;
   canvas.focus({preventScroll:true});canvas.setPointerCapture(e.pointerId);
   this.pointers.set(e.pointerId,{x:e.clientX,y:e.clientY,touch:e.pointerType==='touch',pan:e.button!==0});
  });
  canvas.addEventListener('pointermove',e=>{
   const prev=this.pointers.get(e.pointerId);if(!prev)return;
   const next={...prev,x:e.clientX,y:e.clientY},dx=next.x-prev.x,dy=next.y-prev.y;
   this.pointers.set(e.pointerId,next);
   const pair=[...this.pointers.values()].filter(p=>p.touch);
   if(prev.touch&&pair.length>=2){
    if(!pair.includes(next))return;
    const other=pair.find(p=>p!==next);
    const oldDist=Math.hypot(prev.x-other.x,prev.y-other.y),newDist=Math.hypot(next.x-other.x,next.y-other.y);
    if(oldDist>4&&newDist>4)this.zoomAt(newDist/oldDist,(prev.x+other.x)/2,(prev.y+other.y)/2);
    this.translate(dx/2,dy/2);
   }else if(prev.pan||e.shiftKey)this.translate(dx,dy);
   else this.rotate(dx*.006,dy*.006);
   this.draw();
  });
  for(const event of ['pointerup','pointercancel','lostpointercapture'])canvas.addEventListener(event,e=>this.pointers.delete(e.pointerId));
  canvas.addEventListener('contextmenu',e=>e.preventDefault());
  canvas.addEventListener('wheel',e=>{
   if(!this.count)return;
   e.preventDefault();const magnitude=e.deltaY*(e.deltaMode===1?16:e.deltaMode===2?canvas.clientHeight:1);
   this.zoomAt(Math.exp(-Math.max(-200,Math.min(200,magnitude))*.0018),e.clientX,e.clientY);this.draw();
  },{passive:false});
  canvas.addEventListener('keydown',e=>{
   switch(e.key){
    case 'ArrowLeft':this.rotate(-.12,0);break;case 'ArrowRight':this.rotate(.12,0);break;
    case 'ArrowUp':this.rotate(0,-.12);break;case 'ArrowDown':this.rotate(0,.12);break;
    case '+':case '=':this.zoomAt(1.15);break;case '-':this.zoomAt(1/1.15);break;
    case 'Home':this.reset();break;default:return;
   }
   e.preventDefault();this.draw();
  });
  this.reset();
 }
 rotate(dx,dy){
  this.yaw=(this.yaw+dx)%(2*Math.PI);
  this.pitch=Math.max(-Math.PI/2,Math.min(Math.PI/2,this.pitch-dy));
 }
 view(which){
  const angles={oblique:[.53,-.32],front:[0,0],top:[0,-Math.PI/2],bottom:[0,Math.PI/2]};
  if(!angles[which])return;[this.yaw,this.pitch]=angles[which];this.draw();
 }
 setReference(on){this.reference=Boolean(on);this.draw();}
 makeGrid(){
  if(!this.gl)return;
  const range=Math.max(.5,this.radius*1.6),mag=10**Math.floor(Math.log10(range/5));
  const step=([1,2,5,10].find(n=>n*mag>=range/5)||10)*mag;
  const cx=Math.round(this.center[0]/step)*step,cy=Math.round(this.center[1]/step)*step;
  const half=step*7,vertices=[];
  const line=(a,b,c,d,rgb)=>vertices.push(a,b,0,...rgb,c,d,0,...rgb);
  for(let i=-7;i<=7;i++){
   const x=cx+i*step,y=cy+i*step;
   line(x,cy-half,x,cy+half,Math.abs(x)<step*.01?[.37,.50,.55]:[.19,.28,.32]);
   line(cx-half,y,cx+half,y,Math.abs(y)<step*.01?[.37,.50,.55]:[.19,.28,.32]);
  }
  const g=this.gl;g.bindBuffer(g.ARRAY_BUFFER,this.gridBuffer);g.bufferData(g.ARRAY_BUFFER,new Float32Array(vertices),g.STATIC_DRAW);this.gridCount=vertices.length/6;
 }
 translate(dx,dy){this.pan[0]+=2*dx/Math.max(1,this.canvas.clientWidth);this.pan[1]-=2*dy/Math.max(1,this.canvas.clientHeight);}
 zoomAt(factor,x,y){
  const next=Math.max(.1,Math.min(32,this.zoom*factor)),ratio=next/this.zoom;
  const r=this.canvas.getBoundingClientRect();
  const ax=x===undefined?0:2*(x-r.left)/Math.max(1,r.width)-1,ay=y===undefined?0:1-2*(y-r.top)/Math.max(1,r.height);
  this.pan[0]=ax+(this.pan[0]-ax)*ratio;this.pan[1]=ay+(this.pan[1]-ay)*ratio;this.zoom=next;
 }
 fit(){
  if(!this.frameData||!this.count)return;
  const d=this.frameData,lo=[Infinity,Infinity,Infinity],hi=[-Infinity,-Infinity,-Infinity];
  for(let i=0;i<this.count;i++)for(let c=0;c<3;c++){
   const v=d.getFloat32(8+16*i+4*c,true);if(!Number.isFinite(v))throw Error('点云含无效坐标');
   lo[c]=Math.min(lo[c],v);hi[c]=Math.max(hi[c],v);
  }
  this.center=lo.map((v,c)=>(v+hi[c])/2);this.radius=Math.max(.01,Math.hypot(...hi.map((v,c)=>v-lo[c]))*.57);this.fitted=true;this.makeGrid();
 }
 reset(){this.pan=[0,0];this.zoom=1;this.fit();this.view('oblique');}
 clear(refit=false){this.count=0;this.frameData=null;if(refit)this.fitted=false;this.draw();}
 set(buf,displayPitchDeg=0){
  if(buf.byteLength<8)throw Error('无效点云帧');let d=new DataView(buf);const n=d.getUint32(4,true);
  if(d.getUint32(0,true)!==0x31435750||n>30000||8+16*n!==buf.byteLength)throw Error('不支持的点云帧');
  if(displayPitchDeg){
   // Rotate only the displayed points about +Y. Keep the original frame and its point attributes.
   buf=buf.slice(0);d=new DataView(buf);
   const angle=displayPitchDeg*Math.PI/180,c=Math.cos(angle),s=Math.sin(angle);
   for(let i=0;i<n;i++){
    const p=8+i*16,x=d.getFloat32(p,true),z=d.getFloat32(p+8,true);
    d.setFloat32(p,c*x+s*z,true);d.setFloat32(p+8,-s*x+c*z,true);
   }
  }
  if(displayPitchDeg!==this.displayPitchDeg){this.displayPitchDeg=displayPitchDeg;this.fitted=false;}
  const stats=Array(8).fill(0);
  for(let i=0;i<n;i++){const p=8+16*i,k=d.getUint8(p+15);if(k<8){stats[k]++;for(let c=0;c<3;c++)d.setUint8(p+12+c,parseInt(colors[k].slice(1+c*2,3+c*2),16));}}
  this.count=n;this.frameData=d;if(!this.fitted)this.fit();
  if(this.gl){this.gl.bindBuffer(this.gl.ARRAY_BUFFER,this.buffer);this.gl.bufferData(this.gl.ARRAY_BUFFER,new Uint8Array(buf,8),this.gl.DYNAMIC_DRAW);}
  this.draw();return {n,stats};
 }
 draw(){if(!this.gl||this.paint)return;this.paint=requestAnimationFrame(()=>{this.paint=0;this.render();});}
 render(){const g=this.gl;if(!g)return;const c=this.canvas,dpr=Math.min(2,devicePixelRatio||1),w=Math.max(1,Math.round(c.clientWidth*dpr)),h=Math.max(1,Math.round(c.clientHeight*dpr));if(c.width!==w||c.height!==h){c.width=w;c.height=h;}
  g.viewport(0,0,w,h);g.clear(g.COLOR_BUFFER_BIT|g.DEPTH_BUFFER_BIT);if(!this.count)return;const u=this.u;
  const a=Math.cos(this.yaw),b=Math.sin(this.yaw),cp=Math.cos(this.pitch),d=Math.sin(this.pitch);
  g.uniform3fv(u.center,this.center);g.uniform3f(u.rightAxis,a,-b,0);
  g.uniform3f(u.upAxis,-d*b,-d*a,cp);g.uniform3f(u.depthAxis,cp*b,cp*a,d);
  g.uniform2fv(u.pan,this.pan);g.uniform2f(u.screen,Math.min(1,h/w),Math.min(1,w/h));g.uniform1f(u.scale,this.zoom/this.radius);
  if(this.reference&&this.gridCount){
   g.bindBuffer(g.ARRAY_BUFFER,this.gridBuffer);g.vertexAttribPointer(this.xyz,3,g.FLOAT,false,24,0);g.vertexAttribPointer(this.rgb,3,g.FLOAT,false,24,12);
   g.uniform1i(u.pointMode,0);g.depthMask(false);g.drawArrays(g.LINES,0,this.gridCount);g.depthMask(true);
  }
  g.bindBuffer(g.ARRAY_BUFFER,this.buffer);g.vertexAttribPointer(this.xyz,3,g.FLOAT,false,16,0);g.vertexAttribPointer(this.rgb,3,g.UNSIGNED_BYTE,true,16,12);
  g.uniform1i(u.pointMode,1);g.uniform1f(u.size,dpr*2.7);g.drawArrays(g.POINTS,0,this.count);
 }
}
