import React,{useEffect,useState} from 'react';
import {api,Empty,Panel,Table,time,stamp} from './ui';
import {Playback} from './playback';
import {ScenarioResults} from './scenario-results';
import {Run} from './result-data';
import {ProvenanceGraph} from './provenance-graph';
import './evidence-cases.css';

const casePath='/evidence-cases';
const labels:Record<string,string>={
 AdoptionDecision:'采纳决定',FieldExecution:'现场执行',FieldOutcome:'现场实测',
 OutcomeEvaluation:'预测与实际对照',ModelImprovement:'模型改进',
};
const input=(form:FormData,key:string)=>String(form.get(key)||'').trim();
const iso=(form:FormData,key:string)=>new Date(input(form,key)).toISOString();

export function CaseSeal({checkpoint,comparison,act,busy,setPage}:any){
 const [open,setOpen]=useState(false);
 const previous=sessionStorage.getItem('case-next-improvement');
 return <Panel title="形成闭环档案" action={<button onClick={()=>setOpen(!open)}>{open?'收起':'封存本批比较'}</button>}>
  {open&&<form className="case-form" onSubmit={e=>{
   e.preventDefault();const form=new FormData(e.currentTarget);
   act(async()=>{
    const result=await api(casePath,{checkpoint_id:checkpoint,comparison_id:comparison,title:input(form,'title'),
     objective:input(form,'objective'),...(previous?{previous_improvement:previous}:{})});
    sessionStorage.setItem('evidence-case',result.id);
    sessionStorage.removeItem('case-next-improvement');
    setPage('evidence');
   },'比较和运行数据已封存');
  }}>
   <label>档案名称<input name="title" placeholder="例如：CNC01 维护后的交付保障" required maxLength={120}/></label>
   <label>分析目标<textarea name="objective" placeholder="例如：减少到期未完工订单，同时控制换型负担" required maxLength={4000}/></label>
   {previous&&<small>关联上一轮改进记录 {previous}</small>}
   <button className="primary" disabled={busy}>{busy?'正在封存…':'保存基线和全部运行'}</button>
  </form>}
 </Panel>;
}

export function EvidenceCases({act,busy,readOnly,inspect,setPage,revision}:any){
 const [items,setItems]=useState<any[]>([]),[selected,setSelected]=useState('');
 const [loaded,setLoaded]=useState<any>(null),[reload,setReload]=useState(0),[error,setError]=useState('');
 useEffect(()=>{
  let active=true;
  api(casePath).then(rows=>{
   if(!active)return;
   const saved=sessionStorage.getItem('evidence-case');
   const remembered=rows.find((r:any)=>r.id===saved);
   if(saved&&!remembered)sessionStorage.removeItem('evidence-case');
   setItems(rows);setSelected(id=>rows.some((r:any)=>r.id===id)?id:remembered?.id||rows[0]?.id||'');
  }).catch(e=>{if(active)setError(e.message)});
  return()=>{active=false};
 },[reload,revision]);
 useEffect(()=>{
  let active=true;
  if(selected)api(casePath+'/'+selected).then(row=>{if(active)setLoaded(row)}).catch(e=>{if(active)setError(e.message)});
  return()=>{active=false};
 },[selected,reload]);
 return <>
  <Panel title="推演闭环档案" action={<button onClick={()=>setPage('scenarios')}>去方案比较 ↗</button>}>
   {items.length>0?<div className="case-toolbar"><label>选择档案<select aria-label="选择档案" value={selected} onChange={e=>{
    setSelected(e.target.value);sessionStorage.setItem('evidence-case',e.target.value);setError('');
   }}>{items.map(row=><option value={row.id} key={row.id}>{row.name} · {stamp(row.created_at)}</option>)}</select></label></div>:
    <Empty>比较结束后，在「异常与方案」封存该批比较，建立第一份闭环档案。</Empty>}
  </Panel>
  {error&&<div className="notice error" role="alert">{error}</div>}
  {loaded?.id===selected&&<CaseDetail key={selected} record={loaded} act={act} busy={busy} readOnly={readOnly}
   inspect={inspect} refresh={()=>setReload(value=>value+1)} setPage={setPage}/>}
 </>;
}

function CaseDetail({record,act,busy,readOnly,inspect,refresh,setPage}:any){
 const [view,setView]=useState(''),[reports,setReports]=useState<Run[]>([]),[reportError,setReportError]=useState('');
 const [verify,setVerify]=useState<any>(null);
 const [selectedAnalysis,setSelectedAnalysis]=useState('');
 const viewPanel=React.useRef<HTMLDivElement>(null);
 const base=casePath+'/'+record.id;
 const baseline=record.baseline;
 const finished=record.runs.filter((run:any)=>run.status==='completed');
 const decision=record.entries.find((entry:any)=>entry.type==='AdoptionDecision');
 const execution=record.entries.find((entry:any)=>entry.type==='FieldExecution');
 const evaluations=record.entries.filter((entry:any)=>entry.type==='OutcomeEvaluation');
 const improvements=record.entries.filter((entry:any)=>entry.type==='ModelImprovement');
 const adopted=finished.find((run:any)=>run.id===decision?.run);
 const adoptedReport=reports.find(run=>run.task.id===decision?.run);
 const frozen=(item:string)=>record.id+'~'+item;
 useEffect(()=>{
  let active=true;
  Promise.all(finished.map((run:any)=>api(base+'/artifact/'+run.id+'/result.json')))
   .then(rows=>{if(active)setReports(rows)}).catch(e=>{if(active)setReportError(e.message)});
  return()=>{active=false};
 },[record.id]);
 function save(e:React.FormEvent<HTMLFormElement>,path:string,body:(data:FormData)=>any){
  e.preventDefault();const form=new FormData(e.currentTarget);
  act(async()=>{await api(base+'/'+path,body(form));refresh()},'记录已追加到证据链');
 }
 const steps=[
  ['起点已封存',true],['比较已封存',true],['记录采纳',decision],['现场执行',execution],
  ['对照效果',evaluations.length],['模型改进',improvements.length],
 ];
 return <>
  <Panel title={record.name} action={<div className="actions">
   <button disabled={busy} onClick={()=>act(async()=>setVerify(await api(base+'/verify')),'档案核验完成')}>核验档案</button>
   <a className="button" href={'/api/v2'+base+'/export'}>导出完整档案</a>
  </div>}>
   <div className="case-summary">
    <p>{record.objective}</p>
    <div className="case-steps">{steps.map(([name,done])=><span key={name as string} className={done?'done':''}>{done?'✓ ':''}{name}</span>)}</div>
    <p className="subtle">分析起点 {time(baseline.time_s)} · 本体版本 {baseline.graph_sequence} · {baseline.incident_title||'现场基线'}{baseline.incident_jobs?.length?(' · 影响 '+baseline.incident_jobs.length+' 件'):''}</p>
    {baseline.impact&&<details><summary>查看受影响订单、批次与工件</summary>
     <p>订单 {baseline.impact.orders?.join('、')||'—'} · 批次 {baseline.impact.batches?.join('、')||'—'}</p>
     <p className="wrap">工件 {baseline.impact.jobs?.join('、')||'—'}</p>
    </details>}
    {record.previous_improvement&&<button className="link" onClick={()=>inspect(record.previous_improvement,'catalog')}>上一轮改进依据 ↗</button>}
    <p className="subtle">回执 {record.receipt.sequence} · {record.receipt.hash.slice(0,18)}…</p>
    {verify&&<p className={verify.valid?'positive':'negative'}>{verify.valid?'签名、历史顺序和封存文件核验通过':verify.error}</p>}
   </div>
  </Panel>
  <ProvenanceGraph caseId={record.id} revision={record.receipt.sequence} onAction={(action,verb)=>{
   if(verb==='inspect'&&action.inspect){
    inspect(action.inspect,action.context==='catalog'?'catalog':frozen(action.context||'baseline'));
   }else if(verb==='play'&&action.play){
    setView(action.play);
    requestAnimationFrame(()=>viewPanel.current?.scrollIntoView({block:'start'}));
   }else if(verb==='analysis'&&action.analysis){
    setSelectedAnalysis(action.analysis);setView('analysis');
    requestAnimationFrame(()=>viewPanel.current?.scrollIntoView({block:'start'}));
   }
  }}/>
  <Panel title="分析起点与仿真运行" action={<button onClick={()=>setView(view==='analysis'?'':'analysis')}>{view==='analysis'?'收起完整分析':'打开封存分析'}</button>}>
   <div className="case-toolbar">
    <button onClick={()=>setView(view==='baseline'?'':'baseline')}>回放分析起点</button>
    <button className="link" onClick={()=>inspect(baseline.incident_id||'clock',frozen('baseline'))}>基线与事件依据 ↗</button>
    <button className="link" onClick={()=>inspect(record.id,'catalog')}>封存活动和责任人 ↗</button>
    <a href={'/api/v2'+base+'/artifact/task-history.json'} target="_blank" rel="noreferrer">任务记录 ↗</a>
   </div>
   <Table rows={record.runs} columns={[
    {key:'name',title:'方案'},{key:'status',title:'状态',render:(r:any)=>({completed:'已完成',failed:'失败',cancelled:'取消',interrupted:'中断',reconciliation_required:'待核对'}[r.status]||r.status)},
    {key:'sample',title:'样本 / 种子',render:(r:any)=>(r.inputs.sample_index+1)+' / '+r.inputs.seed},
    {key:'policy',title:'方案参数',render:(r:any)=><details><summary>查看参数</summary><pre>{JSON.stringify(r.inputs.patch,null,2)}</pre></details>},
    {key:'output',title:'累计完工',render:(r:any)=>r.kpi?.completed??'—'},
    {key:'evidence',title:'证据与回放',render:(r:any)=><div className="actions">
     {record.artifacts.includes(r.id+'/runtime.trig')&&<button onClick={()=>setView(view===r.id?'':r.id)}>回放此运行</button>}
     {r.status==='completed'&&<button className="link" onClick={()=>inspect('result/'+r.id,frozen(r.id))}>结果依据 ↗</button>}
     {record.artifacts.includes(r.id+'/native.spp')&&<a className="button" href={'/api/v2'+base+'/artifact/'+r.id+'/native.spp'}>下载原生模型</a>}
    </div>},
   ]}/>
  </Panel>
  <div ref={viewPanel}>
   {view&&view!=='analysis'&&<Playback key={view} context={frozen(view)} inspect={inspect} startAt={baseline.time_s}/>}
   {view==='analysis'&&(reports.length?<ScenarioResults key={selectedAnalysis} initialRun={selectedAnalysis}
    runs={reports} expected={record.runs.length} inspect={inspect} archiveCase={record.id}/>:<Empty>{reportError||'正在读取封存分析…'}</Empty>)}
  </div>
  <Panel title="采纳方案">
   {decision?<div className="case-summary"><b>{adopted?.name}</b><p>{decision.rationale}</p><small>{decision.actor} · {stamp(decision.occurred_at)}</small></div>:
    !readOnly&&<form className="case-form" onSubmit={e=>save(e,'adoption',f=>({run_id:input(f,'run'),rationale:input(f,'reason')}))}>
     <label>采纳运行<select name="run" aria-label="采纳运行">{finished.map((r:any)=><option value={r.id} key={r.id}>{r.name} · 样本 {r.inputs.sample_index+1}</option>)}</select></label>
     <label>采纳理由<textarea name="reason" required maxLength={4000}/></label><button className="primary" disabled={busy}>登记采纳决定</button>
    </form>}
  </Panel>
  {decision&&<Panel title="现场执行">
   {execution?<div className="case-summary"><b>{execution.execution_ref} · {execution.executor}</b><p>{stamp(execution.start_at)} 至 {stamp(execution.end_at)}</p><p>{execution.description}</p><small>登记人 {execution.actor}</small></div>:
    !readOnly&&<form className="case-form" onSubmit={e=>save(e,'execution',f=>({execution_ref:input(f,'ref'),executor:input(f,'executor'),start_at:iso(f,'start'),end_at:iso(f,'end'),description:input(f,'description')}))}>
     <div className="case-fields"><label>现场执行单号<input name="ref" required/></label><label>执行人或班组<input name="executor" required/></label>
     <label>实际开始<input type="datetime-local" name="start" required step="1"/></label><label>实际观察截止<input type="datetime-local" name="end" required step="1"/></label></div>
     <label>执行过程与偏差<textarea name="description" required placeholder="现场实际采取了什么措施，哪些地方与方案不同？"/></label>
     <button className="primary" disabled={busy}>登记现场执行</button>
    </form>}
  </Panel>}
  {execution&&<Panel title="实测与预测对照">
   {evaluations.map((row:any)=>{
    const actual=record.entries.find((r:any)=>r.id===row.outcome);
    return <div key={row.id} className="case-observation">
     <p>{stamp(row.occurred_at)} · {actual?.source} · {actual?.actor} <button className="link" onClick={()=>inspect(row.id,'catalog')}>对照证据 ↗</button></p>
     <div className="flow-note">{row.comparable?'同工单、同起点口径、等长窗口':'窗口或范围不同，保留两组数值'} · 预测 {Math.round(row.forecast_seconds)} 秒 · 实际 {Math.round(row.actual_seconds)} 秒</div>
     <Table rows={JSON.parse(row.rows)} columns={[{key:'order',title:'工单'},{key:'quantity',title:'需求'},
      {key:'forecast',title:'预测累计完工'},{key:'actual',title:'实测累计完工'},{key:'delta',title:'实测减预测 / 件'},
      {key:'completion_delta_s',title:'完工时差 / 分',render:(r:any)=>r.completion_delta_s==null?'—':(r.completion_delta_s/60).toFixed(1)}]}/>
    </div>;
   })}
   {reportError&&<div className="notice error">{reportError}</div>}
   {!readOnly&&adoptedReport&&<details className="case-summary" open={!evaluations.length||undefined}><summary>追加现场实测</summary>
    <form className="case-form" onSubmit={e=>save(e,'outcomes',f=>({
     source:input(f,'source'),source_record:input(f,'sourceRecord')||null,same_scope:f.has('scope'),orders:adoptedReport.summary.orders.map(o=>({
      order_id:o.id,completed:Number(input(f,'qty-'+o.id)),
      completed_at:input(f,'at-'+o.id)?iso(f,'at-'+o.id):null,
     })),
    }))}>
     <label>来源单号或说明<input name="source" required placeholder="例如：MES 工单日报 2026-11-12 / 班组交接单"/></label>
     <label>原始记录摘录（可选）<textarea name="sourceRecord" maxLength={4000}/></label>
     <label className="case-check"><input type="checkbox" name="scope"/>确认工单范围、起点状态和累计完工口径一致</label>
     <Table rows={adoptedReport.summary.orders} columns={[
      {key:'id',title:'工单'},{key:'quantity',title:'需求'},{key:'completed',title:'预测累计完工'},
      {key:'actual',title:'实测累计完工',render:(o:any)=><input aria-label={o.id+' 实测完工'} name={'qty-'+o.id} type="number" min={0} max={o.quantity} required/>},
      {key:'at',title:'整单完工时间（可选）',render:(o:any)=><input className="case-date" aria-label={o.id+' 实际完工时间'} name={'at-'+o.id} type="datetime-local" step="1"/>},
     ]}/>
     <button className="primary" disabled={busy}>保存实测并生成对照</button>
    </form>
   </details>}
  </Panel>}
  {evaluations.length>0&&<Panel title="模型改进与下一轮">
   {improvements.map((row:any)=><div className="case-summary" key={row.id}>
    <b>{row.finding}</b><p>模型调整：{row.change}</p><p>验证方法：{row.validation}</p>
    <div className="actions"><button className="link" onClick={()=>inspect(row.id,'catalog')}>改进证据 ↗</button>
     {!readOnly&&<button onClick={()=>{sessionStorage.setItem('case-next-improvement',row.id);setPage('scenarios')}}>关联下一轮比较 ↗</button>}</div>
   </div>)}
   {!readOnly&&<form className="case-form" onSubmit={e=>save(e,'improvements',f=>({evaluation_id:input(f,'evaluation'),finding:input(f,'finding'),change:input(f,'change'),validation:input(f,'validation')}))}>
    <label>对照记录<select name="evaluation">{evaluations.map((row:any)=><option key={row.id} value={row.id}>{stamp(row.occurred_at)} · {row.comparable?'可配对':'范围有差异'}</option>)}</select></label>
    <label>发现的问题<textarea name="finding" required placeholder="例如：仿真未计入现场临时换刀"/></label>
    <label>拟调整的模型或参数<textarea name="change" required/></label>
    <label>下一轮如何验证<textarea name="validation" required/></label>
    <button className="primary" disabled={busy}>记录改进建议</button>
   </form>}
  </Panel>}
  {record.entries.length>0&&<Panel title="档案操作时间线"><Table rows={record.entries} columns={[
   {key:'type',title:'证据',render:(row:any)=><button className="link" onClick={()=>inspect(row.id,'catalog')}>{labels[row.type]||row.type} ↗</button>},
   {key:'actor',title:'登记责任人'},{key:'sequence',title:'签名序号'},{key:'occurred_at',title:'登记时间',render:(row:any)=>stamp(row.occurred_at)},
  ]}/></Panel>}
 </>;
}
