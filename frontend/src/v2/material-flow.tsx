import React,{useEffect,useState} from 'react';
import {ArrowRight,Box,CheckCircle2,ChevronLeft,ChevronRight,Clock3,PackageOpen,Pause,Play,RotateCcw,Truck} from 'lucide-react';
import {StationDiagram} from './station-diagram';
import {activityTitle,api,Badge,Empty,Panel,time} from './ui';
import './material-flow.css';

type Piece={id:string;product:string;order:string;batch:string;status:string;assigned:string;location:string;queue_seq:number;op_index:number;attempt:number};
type Product={id:string;name:string};
type Asset={id:string;resource:string;status:string;busy:string;maintenance_until:number};
type Equipment={id:string;name:string};
type FlowStep={id:string;name:string;index:number;preferred:string;qualified:string[];duration_s:number};
type Route={steps:FlowStep[];rework_to:string;transfer_s:number};
type Movement={sequence:number;time_s:number;activity:string;status:string;assigned:string;location:string;op_index:number;attempt:number};
type History={steps:Movement[]};
type FlowData={
 workspace?:{id:string};
 runtime:{jobs:Piece[];assets:Asset[];revision:number;time_s:number};
 spec:{products:Product[];resources:Equipment[]};
};

function position(row:Pick<Piece,'status'|'assigned'|'location'>){
 switch(row.status){
  case 'pending':return '待投产区';
  case 'material_wait':return '待料';
  case 'queued':return row.assigned+' · 等待缓冲区';
  case 'processing':
  case 'setup':return row.assigned+' · 加工工位';
  case 'transfer':return '共享转运区';
  case 'done':return '成品出口';
  case 'scrapped':return '报废出口';
  default:return row.location||'位置待确认';
 }
}

export function MaterialFlow({data,context,focus,inspect}:{data:FlowData;context:string;focus:string;inspect:(id:string,context:string)=>void}){
 const runtime=data.runtime;
 const {products,resources}=data.spec;
 const initialProduct=products.find(p=>p.id===focus)?.id||runtime.jobs.find(j=>[j.batch,j.order,j.id].includes(focus))?.product||products[0]?.id;
 const [product,setProduct]=useState(initialProduct),[piece,setPiece]=useState('');
 const [route,setRoute]=useState<Route|null>(null),[routeError,setRouteError]=useState('');
 const [history,setHistory]=useState<History|null>(null),[historyError,setHistoryError]=useState('');
 const [frame,setFrame]=useState<number|null>(null),[playing,setPlaying]=useState(false);
 const [equipment,setEquipment]=useState(resources.some(resource=>resource.id===focus)?focus:'');
 const selected=runtime.jobs.find(j=>j.id===piece&&j.product===product);
 const scoped=runtime.jobs.filter(j=>j.product===product);
 const asset=runtime.assets.find(a=>a.resource===equipment);
 const count=(...statuses:string[])=>scoped.filter(j=>statuses.includes(j.status)).length;
 const marker=(statuses:string[])=>selected&&statuses.includes(selected.status)?' traced':'';
 const signature=selected&&[selected.status,selected.assigned,selected.location,selected.op_index,selected.attempt].join('|');
 const movements=history?.steps||[];
 const index=frame==null?movements.length-1:Math.min(frame,movements.length-1);
 const movement=movements[index];
 useEffect(()=>{
  let active=true;
  setRoute(null);setRouteError('');
  if(product)api('/flow/route?'+new URLSearchParams({product,context}))
   .then(value=>{if(active)setRoute(value)}).catch(e=>{if(active)setRouteError(e.message)});
  return()=>{active=false};
 },[context,product]);
 useEffect(()=>{
  let active=true;
  setHistoryError('');
  if(selected)api('/flow/workpieces/'+encodeURIComponent(selected.id)+'?'+new URLSearchParams({context,through:String(runtime.revision)}))
   .then(value=>{if(active)setHistory(value)}).catch(e=>{if(active)setHistoryError(e.message)});
  return()=>{active=false};
 },[context,selected?.id,signature]);
 useEffect(()=>{
  if(!playing||index>=movements.length-1)return;
  const id=setTimeout(()=>{
   setFrame(index+1);
   if(index+1>=movements.length-1)setPlaying(false);
  },830);
  return()=>clearTimeout(id);
 },[playing,index,movements.length]);
 function choose(id:string){
  setPiece(id);setHistory(null);setHistoryError('');setFrame(null);setPlaying(false);
 }
 function changeProduct(id:string){setProduct(id);choose('');setEquipment('')}
 function scrub(i:number){setPlaying(false);setFrame(i)}
 function focusEquipment(id:string){
  setEquipment(id);
  document.getElementById('flow-station-'+id)?.scrollIntoView({block:'nearest',inline:'nearest'});
 }
 return <>
  <Panel title="物料流动路线" className="material-flow-panel" action={<span>版本 {runtime.revision} · {time(runtime.time_s)}</span>}>
   <div className="material-flow-controls">
    <label>产品<select aria-label="流动路线产品" value={product||''} onChange={e=>changeProduct(e.target.value)}>
     {products.map(p=><option key={p.id} value={p.id}>{p.name} · {p.id}</option>)}
    </select></label>
    <label>追踪工件<select aria-label="追踪工件" value={piece} onChange={e=>choose(e.target.value)}>
     <option value="">选择工件</option>
     {scoped.map(j=><option key={j.id} value={j.id}>{j.id} · {j.batch}</option>)}
    </select></label>
    {selected&&<span className="flow-piece-current"><Box size={15}/><strong>{selected.id}</strong><Badge value={selected.status}/>{position(selected)}</span>}
   </div>
   {routeError?<div className="notice error" role="alert">{routeError}</div>:!route?<Empty>正在读取产品工艺…</Empty>:<>
    <div className="factory-flow-track" aria-label="物料从投产到完工的路线">
     <div className={'flow-terminal'+marker(['pending','material_wait'])}>
      <PackageOpen size={24}/><strong>上料 / 投产</strong><small>待投产 {count('pending')} 件</small>
      {!!count('material_wait')&&<small>待料 {count('material_wait')} 件</small>}
     </div>
     {route.steps.map(step=>{
      const current=scoped.filter(j=>j.op_index===step.index);
      const active=selected&&selected.op_index===step.index&&['queued','processing','setup','transfer'].includes(selected.status);
      return <React.Fragment key={step.id}>
       <ArrowRight className="factory-flow-arrow" size={18} aria-hidden/>
       <article className={'factory-flow-step'+(active?' traced':'')}>
        <small>工序 {String(step.index+1).padStart(2,'0')}</small>
        <button className="link flow-step-title" onClick={()=>inspect(step.id,context)}>{step.name}</button>
        <div className="flow-step-count"><span>等 {current.filter(j=>j.status==='queued').length}</span><span>工位 {current.filter(j=>['processing','setup'].includes(j.status)).length}</span></div>
        <div className="flow-devices">{step.qualified.map(r=><button key={r} className={selected?.assigned===r&&active?'selected':''} onClick={()=>focusEquipment(r)} title={r===step.preferred?'首选设备':'合格设备'}>{r}{r===step.preferred&&<small>首选</small>}</button>)}</div>
       </article>
      </React.Fragment>
     })}
     <ArrowRight className="factory-flow-arrow" size={18} aria-hidden/>
     <div className={'flow-terminal flow-finished'+marker(['done','scrapped'])}>
      <CheckCircle2 size={24}/><strong>检验后出站</strong><small>成品 {count('done')} · 报废 {count('scrapped')}</small>
     </div>
    </div>
    <div className="flow-branches">
     <span><Truck size={17}/> 工序间经过共享转运区 · {route.transfer_s} 秒 · 当前转运 {count('transfer')} 件</span>
     <span><RotateCcw size={15}/> 检验失败返回 {route.steps.find(step=>step.id===route.rework_to)?.name||route.rework_to} 返工一次；复检失败送报废出口</span>
    </div>
   </>}
  </Panel>
  <Panel title="工位与队列" action={<span>设备状态取自当前上下文 · 点击等待工件可追踪</span>}>
   <div className="flow-stations">
    {resources.map(resource=>{
     const state=runtime.assets.find(a=>a.resource===resource.id);
     const waiting=runtime.jobs.filter(j=>j.assigned===resource.id&&j.status==='queued').sort((a,b)=>a.queue_seq-b.queue_seq||a.id.localeCompare(b.id));
     const active=selected?.assigned===resource.id&&['queued','processing','setup'].includes(selected.status);
     return <article key={resource.id} id={'flow-station-'+resource.id} className={'flow-station'+(equipment===resource.id?' focused':'')}>
      <div className="flow-station-title"><div><strong>{resource.id}</strong><small>{resource.name}</small></div>{state&&<Badge value={state.status}/>}</div>
      <StationDiagram waiting={waiting.length} occupant={state?.busy} marker={active?{id:selected.id,position:selected.status==='queued'?'buffer':'station'}:undefined}/>
      <div className="flow-station-actions">
       <button className="link" onClick={()=>setEquipment(resource.id)}>查看队列 {waiting.length} 件</button>
       {state?.busy&&<button className="link" onClick={()=>{
        const job=runtime.jobs.find(j=>j.id===state.busy);
        if(job){setProduct(job.product);choose(job.id)}
       }}>追踪 {state.busy}</button>}
       {state?.status==='maintenance'&&<small><Clock3 size={12}/>维护至 {time(state.maintenance_until)}</small>}
      </div>
     </article>
    })}
   </div>
   {asset&&<div className="flow-queue">
    <div><strong>{asset.resource} · 等待缓冲区</strong><button className="link" onClick={()=>inspect(asset.id,context)}>设备状态依据 ↗</button><button className="link" onClick={()=>setEquipment('')}>收起队列</button></div>
    <div className="chips">{runtime.jobs.filter(j=>j.assigned===asset.resource&&j.status==='queued').sort((a,b)=>a.queue_seq-b.queue_seq||a.id.localeCompare(b.id)).map(j=>
     <button key={j.id} onClick={()=>{setProduct(j.product);choose(j.id)}}>{j.queue_seq} · {j.id}</button>)}</div>
    {!runtime.jobs.some(j=>j.assigned===asset.resource&&j.status==='queued')&&<p className="subtle">缓冲区为空</p>}
   </div>}
  </Panel>
  <Panel title="工件流转记录" action={selected&&<button onClick={()=>inspect(selected.id,context)}>查看 {selected.id} 的本体依据 ↗</button>}>
   {!selected?<Empty>从产品路线或设备队列中选择一件工件。</Empty>:historyError?<div className="notice error" role="alert">{historyError}</div>:!history?<Empty>正在读取 {selected.id} 的记录…</Empty>:!movement?<Empty>该工件暂无已记录的流转。</Empty>:<>
    <div className="flow-history-player">
     <button aria-label={playing?'暂停工件回放':'播放工件回放'} disabled={movements.length<2} onClick={()=>{
      if(!playing&&index>=movements.length-1)setFrame(0);
      setPlaying(!playing);
     }}>{playing?<Pause size={15}/>:<Play size={15}/>}</button>
     <button aria-label="上一条流转" disabled={index<=0} onClick={()=>scrub(index-1)}><ChevronLeft size={15}/></button>
     <input aria-label="工件流转位置" type="range" min={0} max={movements.length-1} value={index} onChange={e=>scrub(+e.target.value)}/>
     <button aria-label="下一条流转" disabled={index>=movements.length-1} onClick={()=>scrub(index+1)}><ChevronRight size={15}/></button>
     <span>{index+1} / {movements.length} · {time(movement.time_s)}</span>
    </div>
    <div className="flow-history-strip">
     {movements.map((event,i)=><button key={event.sequence} aria-pressed={i===index} className={i===index?'selected':''} onClick={()=>scrub(i)}>
      <small>{time(event.time_s)}</small><strong>{position(event)}</strong><Badge value={event.status}/>
     </button>)}
    </div>
    <p className="note">{selected.id} · {activityTitle(movement.activity)} · 版本 {movement.sequence}
     {route?.steps[movement.op_index]&&' · '+route.steps[movement.op_index].name}{movement.attempt>0?' · 返工':''}</p>
   </>}
  </Panel>
 </>;
}
