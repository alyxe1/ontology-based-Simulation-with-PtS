export type Job={
 id:string;order:string;product:string;batch:string;material_lot:string;status:string;
 assigned:string;released_at:number;completed_at:number;attempt:number;
};
export type Order={
 id:string;product:string;quantity:number;completed:number;scrapped:number;
 release_s:number;due_s:number;completion_s:number|null;tardiness_s:number|null;
};
export type Run={
 task:{id:string;payload:string;elapsed_s?:number};
 native:{verified?:boolean;completed?:number;clock_s?:number;origin_s?:number;engine?:string};
 summary:{
  kpi:{window_start_s:number;window_end_s:number;completed:number;total:number;wip:number;average_wip:number;on_time_orders:number;scrapped:number;reworked:number;mean_queue_wait_per_released_piece_s?:number};
  jobs:Job[];orders:Order[];hash:string;
 };
};
export type Resource={
 id:string;name:string;processing_s:number;setup_s:number;maintenance_s:number;off_shift_s:number;idle_s:number;shift_hours:number;
 utilization:number;average_queue:number;peak_queue:number;end_queue:number;operations:number;maintenance_periods:number;queue_area_s:number;queue_curve:number[][];
};
export type Segment={
 id:string;kind:'processing'|'setup'|'maintenance'|'queue';job:string;resource:string;operation_name?:string;
 start_s:number;end_s:number;completed?:boolean;open_at_end?:boolean;
};
export type Analysis={
 start_s:number;end_s:number;graph_head:string;curve:number[][];completion_times:number[];average_wip:number;peak_wip:number;
 resources:Resource[];segments:Segment[];queue_spells:Omit<Segment,'kind'|'id'>[];job_wait_s:Record<string,number>;
 products:Record<string,string>;
 fixtures:{id:string;name:string;quantity:number;average:number;peak:number;curve:number[][]}[];
 inspections:{id:string;job:string;product:string;batch:string;material_lot:string;result:string;time_s:number;attempt:number|null;execution:string}[];
};
export const colors=['#377e9f','#358675','#b5773f','#7767a3','#b55354','#6b8798'];
export const minutes=(seconds:number|null|undefined)=>seconds==null?'—':(seconds/60).toFixed(1);
export const amount=(value:number|null|undefined)=>value==null?'—':Number(value.toFixed(2)).toLocaleString('zh-CN');
export const average=(values:number[])=>values.length?values.reduce((a,b)=>a+b,0)/values.length:null;
export const statusOf=(order:Order,end:number)=>order.completion_s!=null
 ? order.tardiness_s>1e-6?'延期完工':'按期完工'
 : end>=order.due_s?'到期未完工':'未到期';
export const kindLabels={processing:'加工',setup:'换型',maintenance:'维护',queue:'排队'};

export function payload(run:Run):{patch:{name:string};sample_index:number;seed?:number;scenario_index?:number;comparison_id?:string;checkpoint_id?:string}{
 return JSON.parse(run.task.payload);
}
export function windowJobs(run:Run,status:string){
 const {window_start_s:start,window_end_s:end}=run.summary.kpi;
 return run.summary.jobs.filter(j=>j.status===status&&j.completed_at>start+1e-6&&j.completed_at<=end+1e-6);
}
export const metrics=[
 {id:'output',label:'窗口新增完工',unit:'件',value:(r:Run)=>windowJobs(r,'done').length},
 {id:'total',label:'累计完工',unit:'件',value:(r:Run)=>r.summary.kpi.completed},
 {id:'ontime',label:'按期订单',unit:'单',value:(r:Run)=>r.summary.kpi.on_time_orders},
 {id:'overdue',label:'到期未完工',unit:'单',value:(r:Run)=>r.summary.orders.filter(o=>statusOf(o,r.summary.kpi.window_end_s)==='到期未完工').length},
 {id:'tardiness',label:'已交付订单平均延期',unit:'分',value:(r:Run)=>average(r.summary.orders.flatMap(o=>o.tardiness_s==null?[]:[o.tardiness_s/60]))},
 {id:'wip',label:'平均在制',unit:'件',value:(r:Run)=>r.summary.kpi.average_wip},
 {id:'queue',label:'每件排队',unit:'分',value:(r:Run)=>r.summary.kpi.mean_queue_wait_per_released_piece_s==null?null:r.summary.kpi.mean_queue_wait_per_released_piece_s/60},
 {id:'scrap',label:'窗口新增报废',unit:'件',value:(r:Run)=>windowJobs(r,'scrapped').length},
 {id:'lead',label:'窗口完工件平均流转',unit:'分',value:(r:Run)=>average(windowJobs(r,'done').filter(j=>j.released_at>=0).map(j=>(j.completed_at-j.released_at)/60))},
];

export function compareMetric(reference:Run[], alternatives:Run[], value:(r:Run)=>number|null) {
 const pairKey=(r:Run)=>{
  const p=payload(r),k=r.summary.kpi;
  return p.comparison_id&&p.seed!=null?JSON.stringify([p.comparison_id,p.sample_index,p.seed,k.window_start_s,k.window_end_s]):'';
 };
 const deltas=alternatives.flatMap(run=>{
  const key=pairKey(run);
  if(!key)return [];
  const matches=reference.filter(ref=>pairKey(ref)===key);
  const other=value(run);
  if(matches.length!==1||other==null)return [];
  const control=value(matches[0]);
  return control==null?[]:[other-control];
 });
 return {delta:average(deltas),pairs:deltas.length};
}
