import React,{memo,useEffect,useRef,useState} from 'react';
import {Activity,BarChart3,Boxes,Clock3,Factory,PackageCheck,Wrench} from 'lucide-react';
import {api,KeyValues,Panel,time} from './ui';
import {Analysis,Run,amount,payload,statusOf,windowJobs} from './result-data';
import {ResultComparison} from './result-comparison';
import {FlowView,Inspect,OrdersView,QualityView,ResourcesView,TimelineView} from './result-drilldown';
import {MaintenanceComparison,MaintenanceDetail,useMaintenanceResults} from './maintenance-results';
import './scenario-results.css';

const tabs=[
 ['comparison','方案对比',BarChart3],
 ['maintenance','维护影响',Wrench],
 ['orders','交付与工单',PackageCheck],
 ['resources','设备与瓶颈',Factory],
 ['flow','吞吐与物流',Boxes],
 ['quality','质量与返工',Activity],
 ['timeline','甘特与轨迹',Clock3],
] as const;
type Tab=typeof tabs[number][0];

export const ScenarioResults=memo(function ScenarioResults({runs,expected,inspect,archiveCase,initialRun}:{
 runs:Run[];expected:number;inspect:Inspect;archiveCase?:string;initialRun?:string;
}){
 const [selected,setSelected]=useState(initialRun||''),[tab,setTab]=useState<Tab>('comparison');
 const [order,setOrder]=useState('');
 const [filter,setFilter]=useState({job:'',resource:''});
 const [loaded,setLoaded]=useState<{id:string;data?:Analysis;error?:string}|null>(null);
 const [paired,setPaired]=useState<{id:string;data:Analysis}|null>(null);
 const [retry,setRetry]=useState(0);
 const toolbar=useRef<HTMLDivElement>(null);
 const run=runs.find(r=>r.task.id===selected)||runs[0];
 const {id}=run.task,k=run.summary.kpi,p=payload(run);
 const maintenance=useMaintenanceResults(runs,archiveCase);
 const hasMaintenance=!!maintenance&&Object.values(maintenance.reports).some(Boolean);
 const currentMaintenance=maintenance?.reports[id];
 const analysis=loaded?.id===id?loaded.data:undefined;
 const error=loaded?.id===id?loaded.error:undefined;
 const analysisPath=(runId:string)=>archiveCase
  ? '/evidence-cases/'+encodeURIComponent(archiveCase)+'/artifact/'+encodeURIComponent(runId)+'/analysis.json'
  : '/tasks/'+encodeURIComponent(runId)+'/analysis';
 useEffect(()=>{
  let active=true;
  api(analysisPath(id)).then(data=>{
   if(active)setLoaded({id,data});
  }).catch(error=>{
   if(active)setLoaded({id,error:error.message});
  });
  return()=>{active=false};
 },[id,retry,archiveCase]);
 const reference=runs.find(other=>{
  const candidate=payload(other),window=other.summary.kpi;
  return other.task.id!==id&&candidate.patch.name==='保持现状'&&p.comparison_id&&p.comparison_id===candidate.comparison_id
   &&p.seed!=null&&p.seed===candidate.seed&&p.sample_index===candidate.sample_index
   &&k.window_start_s===window.window_start_s&&k.window_end_s===window.window_end_s;
 });
 const referenceId=tab==='flow'||tab==='resources'?reference?.task.id:undefined;
 useEffect(()=>{
  if(!referenceId)return;
  let active=true;
  api(analysisPath(referenceId)).then(data=>{
   if(active)setPaired({id:referenceId,data});
  }).catch(()=>{});
  return()=>{active=false};
 },[referenceId,archiveCase]);
 const referenceAnalysis=paired?.id===referenceId?paired?.data:undefined;
 const evidence=(identity:string)=>inspect(identity,archiveCase?archiveCase+'~'+id:id);
 function switchRun(taskId:string){
  setSelected(taskId);setFilter({job:'',resource:''});setOrder('');
 }
 function openMaintenance(taskId:string){
  switchRun(taskId);setTab('maintenance');
  requestAnimationFrame(()=>toolbar.current?.scrollIntoView({block:'start'}));
 }
 function trace(job:string,resource=''){
  setFilter({job,resource});
  if(tab!=='timeline'){
   setTab('timeline');
   requestAnimationFrame(()=>toolbar.current?.scrollIntoView({block:'start'}));
  }
 }
 const output=analysis?analysis.completion_times.length:windowJobs(run,'done').length;
 const overdue=run.summary.orders.filter(o=>statusOf(o,k.window_end_s)==='到期未完工').length;
 const span=(k.window_end_s-k.window_start_s)/3600;
 const cards=[
  {name:'窗口完工',value:output+' 件',note:'累计 '+k.completed+' / '+k.total+' 件',tab:'flow'},
  {name:'按期订单',value:k.on_time_orders+' / '+run.summary.orders.length,note:'到期未完工 '+overdue+' 单',tab:'orders'},
  {name:'小时产出',value:span>0?amount(output/span)+' 件':'—',note:'按整个统计窗口',tab:'flow'},
  {name:'平均在制',value:amount(analysis?.average_wip??k.average_wip)+' 件',note:analysis?'峰值 '+analysis.peak_wip+' 件':'时间加权平均',tab:'flow'},
  {name:'窗口报废',value:windowJobs(run,'scrapped').length+' 件',note:'累计 '+k.scrapped+' 件',tab:'quality'},
 ] as const;
 return <section className="scenario-results" id="scenario-results" aria-label="方案结果分析">
  <Panel title="方案结果分析" action={<button onClick={()=>evidence('result/'+id)}>结果证据 ↗</button>}>
   <div className="result-toolbar" ref={toolbar}>
    <label>查看运行<select aria-label="查看运行" value={id} onChange={e=>switchRun(e.target.value)}>{runs.map(r=>{
     const sample=payload(r);
     return <option key={r.task.id} value={r.task.id}>{sample.patch.name} · 样本 {(sample.sample_index??0)+1} · {r.task.id.slice(-6)}</option>;
    })}</select></label>
    <span>统计窗口 <b>{time(k.window_start_s)} – {time(k.window_end_s)}</b></span>
    <span className={run.native.verified?'positive':'subtle'}>{run.native.verified?'原生核对通过':'原生核对未记录'}</span>
    <button className="link" onClick={()=>evidence('policy')}>派工与统计依据 ↗</button>
   </div>
   <div className="result-cards">{cards.map(card=><button key={card.name} className="result-card" onClick={()=>setTab(card.tab)}>
    <span>{card.name}</span><strong>{card.value}</strong><small>{card.note}</small>
   </button>)}</div>
   <div className="result-tabs" role="tablist" aria-label="结果分析维度">
    {tabs.filter(([value])=>value!=='maintenance'||hasMaintenance).map(([value,label,Icon])=><button key={value} type="button" role="tab" aria-selected={tab===value} onClick={()=>setTab(value)}><Icon size={15}/>{label}</button>)}
   </div>
  </Panel>
  {(tab==='comparison'||tab==='maintenance')&&maintenance&&
   <MaintenanceComparison reports={maintenance.reports} runs={runs} onRun={openMaintenance}/>}
  {(tab==='comparison'||tab==='maintenance')&&!!maintenance?.unavailable&&
   <div className="note" role="status">{maintenance.unavailable} 次运行暂无法核对基线工件去向。</div>}
  {tab==='maintenance'&&(currentMaintenance
   ? <MaintenanceDetail key={id} report={currentMaintenance} inspect={inspect} trace={trace}
      baselineContext={archiveCase?archiveCase+'~baseline':p.checkpoint_id||'live'} runContext={archiveCase?archiveCase+'~'+id:id}/>
   : <div className="note">当前运行暂无可用的维护影响明细。</div>)}
  {tab==='comparison'&&<ResultComparison runs={runs} selected={run} onSelect={switchRun} expected={expected}
   onOrder={(taskId,orderId)=>{switchRun(taskId);setOrder(orderId);setTab('orders');requestAnimationFrame(()=>toolbar.current?.scrollIntoView({block:'start'}))}}/>}
  {tab==='orders'&&<OrdersView key={id+':'+order} run={run} reference={reference} analysis={analysis} initialOrder={order} trace={trace} evidence={evidence}/>}
  {tab!=='comparison'&&tab!=='maintenance'&&!analysis&&<div className={error?'notice error':'note'} role="status">{error?'无法读取运行明细：'+error:'正在读取已保存的仿真事件…'}{error&&<button onClick={()=>{setLoaded(null);setRetry(retry+1)}}>重试</button>}</div>}
  {analysis&&<>
   {tab==='resources'&&<ResourcesView key={id} analysis={analysis} reference={referenceAnalysis} trace={trace} evidence={evidence} openTimeline={resource=>trace('',resource)}/>}
   {tab==='flow'&&<FlowView key={id} analysis={analysis} reference={referenceAnalysis} run={run} trace={trace} evidence={evidence}/>}
   {tab==='quality'&&<QualityView key={id} analysis={analysis} run={run} trace={trace} evidence={evidence}/>}
   {tab==='timeline'&&<TimelineView key={id} analysis={analysis} run={run} job={filter.job} resource={filter.resource} filter={trace} evidence={evidence}/>}
  </>}
  <details className="result-evidence"><summary><h3>原生核对证据</h3><span>{run.native.verified?'通过 · '+run.native.engine:'暂无核对记录'}</span></summary>
   <KeyValues data={{运行编号:id,原生引擎:run.native.engine||'—',核对完工数:run.native.completed??'—',原生业务终点:time(run.native.clock_s),本地时钟原点:time(run.native.origin_s),计算用时:run.task.elapsed_s==null?'—':run.task.elapsed_s+' 秒',档案摘要:analysis?.graph_head||run.summary.hash}}/>
  </details>
 </section>;
});
