import React,{useEffect,useState} from 'react';
import {api,Badge,Empty,Panel,Table,time} from './ui';
import {Inspect,PagedTable} from './result-drilldown';
import {minutes,payload,Run,statusOf} from './result-data';

type Job={
 id:string;order:string;batch:string;operation:string;initial_status:string;initial_resource:string;
 destination:string|null;execution:string|null;started_s:number|null;operation_completed_s:number|null;
 final_status:string;final_resource:string;waiting_original_operation:boolean;completed_s:number|null;
};
type Outcome={
 resource:string;incident_id:string;start_s:number;end_s:number;maintenance_until:number;batches:number;jobs:Job[];
 orders:(Run['summary']['orders'][number]&{affected:number;affected_completed:number})[];
};
type Reports=Record<string,Outcome|null>;

export function useMaintenanceResults(runs:Run[],archiveCase?:string){
 const runIds=runs.map(r=>r.task.id).join(',');
 const [state,setState]=useState<{key:string;reports:Reports;unavailable:number}|null>(null);
 const key=(archiveCase||'')+':'+runIds;
 useEffect(()=>{
  let current=true;
  const ids=runIds.split(',').filter(Boolean);
  Promise.allSettled(ids.map(id=>api(archiveCase
   ? '/evidence-cases/'+encodeURIComponent(archiveCase)+'/runs/'+encodeURIComponent(id)+'/maintenance-outcomes'
   : '/tasks/'+encodeURIComponent(id)+'/maintenance-outcomes'))).then(rows=>{
   if(!current)return;
   const reports:Reports={};
   rows.forEach((row,index)=>{if(row.status==='fulfilled')reports[ids[index]]=row.value});
   setState({key,reports,unavailable:rows.filter(row=>row.status==='rejected').length});
  });
  return()=>{current=false};
 },[runIds,archiveCase,key]);
 return state?.key===key?state:null;
}

export function MaintenanceComparison({reports,runs,onRun}:{
 reports:Reports;runs:Run[];onRun:(id:string)=>void;
}){
 const rows=runs.flatMap(run=>{
  const report=reports[run.task.id];
  return report?[{id:run.task.id,run,report}]:[];
 });
 if(!rows.length)return null;
 const scope=rows[0].report;
 return <Panel title="维护影响与工件去向" action={<button onClick={()=>onRun(rows[0].id)}>查看工件明细 ↗</button>}>
  <div className="result-toolbar">
   <strong>{scope.resource} · 基线影响 {scope.jobs.length} 件</strong>
   <span>{scope.orders.length} 张工单 · {scope.batches} 个批次</span>
   <span>分析起点 {time(scope.start_s)} · 基线预计维护至 {time(scope.maintenance_until)}</span>
  </div>
  <Table rows={rows} columns={[
   {key:'run',title:'方案 / 样本',render:(r:typeof rows[number])=><button className="link" onClick={()=>onRun(r.id)}>{payload(r.run).patch.name} · 样本 {(payload(r.run).sample_index??0)+1}</button>},
   {key:'same',title:'在原机开工',render:(r:typeof rows[number])=>r.report.jobs.filter(j=>j.destination===r.report.resource).length},
   {key:'other',title:'在其他设备开工',render:(r:typeof rows[number])=>{
    const destinations=new Map<string,number>();
    for(const j of r.report.jobs){
     if(j.destination&&j.destination!==r.report.resource)destinations.set(j.destination,(destinations.get(j.destination)||0)+1);
    }
    return destinations.size?[...destinations].sort(([a],[b])=>a.localeCompare(b)).map(([id,count])=>id+' '+count+' 件').join(' · '):'0 件';
   }},
   {key:'waiting',title:'该工序仍排队',render:(r:typeof rows[number])=>r.report.jobs.filter(j=>!j.destination&&j.waiting_original_operation).length},
   {key:'not_started',title:'其余未开工',render:(r:typeof rows[number])=>r.report.jobs.filter(j=>!j.destination&&!j.waiting_original_operation).length},
   {key:'done',title:'影响工件最终完工',render:(r:typeof rows[number])=>r.report.jobs.filter(j=>j.completed_s!=null).length+' / '+r.report.jobs.length},
   {key:'orders',title:'受影响工单按期',render:(r:typeof rows[number])=>r.report.orders.filter(o=>statusOf(o,r.report.end_s)==='按期完工').length+' / '+r.report.orders.length},
  ]}/>
  <div className="flow-note">去向按基线时那道工序的实际加工记录统计。仅换了队列但尚未开工的工件仍归入等待。最终完工指工件完成全部工序。</div>
 </Panel>;
}

export function MaintenanceDetail({report,inspect,baselineContext,runContext,trace}:{
 report:Outcome;inspect:Inspect;baselineContext:string;runContext:string;trace:(job:string)=>void;
}){
 const [order,setOrder]=useState(''),[search,setSearch]=useState('');
 const jobs=report.jobs.filter(j=>(!order||j.order===order)&&[j.id,j.batch,j.destination].some(v=>(v||'').toLowerCase().includes(search.trim().toLowerCase())));
 return <>
  <Panel title="受影响工单交付" action={<button onClick={()=>inspect(report.incident_id,baselineContext)}>维护状态依据 ↗</button>}>
   <Table rows={report.orders} columns={[
    {key:'id',title:'工单',render:(r:any)=><button className="link" onClick={()=>setOrder(r.id)}>{r.id} · 查看工件 ↗</button>},
    {key:'affected',title:'基线影响件数'},
    {key:'affected_completed',title:'影响工件完工'},
    {key:'completed',title:'整单累计完工',render:(r:any)=>r.completed+' / '+r.quantity},
    {key:'due_s',title:'交期',render:(r:any)=>time(r.due_s)},
    {key:'completion_s',title:'整单完工时刻',render:(r:any)=>time(r.completion_s)},
    {key:'status',title:'交付状态',render:(r:any)=>statusOf(r,report.end_s)+(r.tardiness_s>0?' · '+minutes(r.tardiness_s)+' 分':'')},
   ]}/>
  </Panel>
  <Panel title={'基线影响工件 · '+jobs.length+' / '+report.jobs.length+' 件'}>
   <div className="result-toolbar">
    <label>工单<select aria-label="筛选影响工单" value={order} onChange={e=>setOrder(e.target.value)}><option value="">全部</option>{report.orders.map(o=><option key={o.id} value={o.id}>{o.id}</option>)}</select></label>
    <label>搜索<input aria-label="搜索影响工件" placeholder="工件、批次或去向设备" value={search} onChange={e=>setSearch(e.target.value)}/></label>
   </div>
   {jobs.length?<PagedTable key={order+':'+search} rows={jobs} columns={[
    {key:'id',title:'工件',render:(j:Job)=><button className="link" onClick={()=>trace(j.id)}>{j.id} · 甘特轨迹 ↗</button>},
    {key:'batch',title:'批次'},{key:'operation',title:'基线工序'},
    {key:'initial',title:'基线状态',render:(j:Job)=><>{j.initial_resource||'—'} <Badge value={j.initial_status}/></>},
    {key:'destination',title:'该工序加工设备',render:(j:Job)=>j.execution?<button className="link" onClick={()=>inspect(j.execution!,runContext)}>{j.destination} ↗</button>:j.waiting_original_operation?'仍在 '+j.final_resource+' 等待':'未开工'},
    {key:'started_s',title:'该工序开工',render:(j:Job)=>time(j.started_s)},
    {key:'operation_completed_s',title:'该工序完成',render:(j:Job)=>time(j.operation_completed_s)},
    {key:'final_status',title:'终点状态',render:(j:Job)=><Badge value={j.final_status}/>},
    {key:'completed_s',title:'工件最终完工',render:(j:Job)=>time(j.completed_s)},
   ]}/>:<Empty>该范围没有匹配的工件。</Empty>}
  </Panel>
 </>;
}
