import React,{useEffect,useRef,useState} from 'react';
import {CheckCircle2,GitBranch,Play,Plus} from 'lucide-react';
import {api,Badge,Panel,Table,time} from './ui';
import {SimulationMapping} from './simulation-mapping';
import './scenario-composer.css';

type Draft={name:string;rule?:'FIFO'|'EDD';reroute?:boolean;shift_hours?:number;assignments:Record<string,string>};
const templates:Record<string,Draft>={
 fifo:{name:'首选设备 + FIFO',rule:'FIFO',reroute:false,assignments:{}},
 edd:{name:'合格设备改派 + EDD',rule:'EDD',reroute:true,assignments:{}},
 shift:{name:'改派 + 12 小时班次',rule:'EDD',reroute:true,shift_hours:12,assignments:{}},
 custom:{name:'自定义方案',assignments:{}},
};
const policyLabel=(rule:string)=>rule==='EDD'?'EDD · 交期优先':'FIFO · 先到先加工';
const shiftLabel=(hours:number)=>hours?hours+' 小时/天':'各设备原定班次';

export function ScenarioComposer({cp,inspect,act,busy,readOnly,onSubmitted}:any){
 const [options,setOptions]=useState<any>(null),[loadError,setLoadError]=useState('');
 const [drafts,setDrafts]=useState<Draft[]>([]),[active,setActive]=useState(0),[template,setTemplate]=useState('edd');
 const [onlyAffected,setOnlyAffected]=useState(true),[jobId,setJobId]=useState(''),[equipment,setEquipment]=useState('');
 const [samples,setSamples]=useState<1|5>(1),[preview,setPreview]=useState<{key:string;plan:any}|null>(null);
 const [mappingOpen,setMappingOpen]=useState(false);
 const mappingPanel=useRef<HTMLDivElement>(null);
 useEffect(()=>{
  if(mappingOpen)mappingPanel.current?.scrollIntoView({block:'start'});
 },[mappingOpen]);
 useEffect(()=>{
  let current=true;
  api('/checkpoints/'+encodeURIComponent(cp)+'/options').then(row=>{if(current)setOptions(row)}).catch(err=>{if(current)setLoadError(err.message)});
  return()=>{current=false};
 },[cp]);
 const draft=drafts[active],job=options?.jobs.find((j:any)=>j.id===jobId);
 const choices=options?.jobs.filter((j:any)=>!onlyAffected||!options.incident_id||j.affected)||[];
 const body={checkpoint_id:cp,repetitions:samples,scenarios:drafts};
 const key=JSON.stringify(body),plan=preview?.key===key?preview.plan:null;
 function edit(change:Partial<Draft>){setDrafts(drafts.map((d,i)=>i===active?{...d,...change}:d))}
 function select(index:number){setActive(index);setJobId('');setEquipment('')}
 function add(){
  const value=templates[template];
  let name=value.name,i=2;
  while(drafts.some(d=>d.name===name))name=value.name+' '+i++;
  setDrafts([...drafts,{...value,name,assignments:{}}]);select(drafts.length);
 }
 function remove(){setDrafts(drafts.filter((_,i)=>i!==active));select(Math.max(0,active-1))}
 if(loadError)return <div className="notice error">{loadError}</div>;
 if(!options)return <div className="note">正在读取基线中的队列与设备资格…</div>;
 return <div className="comparison-form">
  <div className="comparison-control">
   <div><strong>对照：保持现状</strong><small>{policyLabel(options.policy.rule)} · {shiftLabel(options.policy.shift_hours)} · 保留当前队列指派，后续{options.policy.reroute?'按资格和负荷选设备':'使用首选设备'}</small></div>
   <div><strong>{time(options.time_s)} — {time(options.horizon_s)}</strong><small>统一统计窗口 · {options.jobs.length} 件等待{options.source_cursor==null?'':` · 已确认帧 #${options.source_cursor}`}</small></div>
  </div>
  <h4>2. 添加要比较的干预</h4>
  <div className="toolbar">
   <label>方案模板<select aria-label="方案模板" value={template} disabled={readOnly} onChange={e=>setTemplate(e.target.value)}>{Object.entries(templates).map(([id,p])=><option key={id} value={id}>{p.name}</option>)}</select></label>
   <button disabled={readOnly||busy||drafts.length>=3} onClick={add}><Plus size={14}/>添加方案</button>
   <span className="subtle">模板可以修改；一次最多添加 3 个干预方案</span>
  </div>
  {draft&&<div className="comparison-edit">
   <div className="toolbar segmented" aria-label="已添加方案">{drafts.map((d,i)=><button className={i===active?'active':''} key={i} onClick={()=>select(i)}>{d.name||'未命名方案'}</button>)}</div>
   <div className="comparison-fields">
    <label>方案名称<input value={draft.name} maxLength={80} disabled={readOnly} onChange={e=>edit({name:e.target.value})}/></label>
    <label>等待队列顺序<select aria-label="等待队列顺序" value={draft.rule||''} disabled={readOnly} onChange={e=>edit({rule:(e.target.value||undefined) as Draft['rule']})}><option value="">沿用基线</option><option value="FIFO">FIFO · 先到先加工</option><option value="EDD">EDD · 交期优先</option></select></label>
    <label>设备分配<select value={draft.reroute===undefined?'':String(draft.reroute)} disabled={readOnly} onChange={e=>edit({reroute:e.target.value===''?undefined:e.target.value==='true'})}><option value="">沿用基线与现有指派</option><option value="true">按资格和负荷自动改派</option><option value="false">统一回到工序首选设备</option></select></label>
    <label>全车间班次<select aria-label="班次设置方式" value={draft.shift_hours===undefined?'inherit':draft.shift_hours===0?'original':'hours'} disabled={readOnly} onChange={e=>edit({shift_hours:e.target.value==='inherit'?undefined:e.target.value==='original'?0:12})}><option value="inherit">沿用基线</option><option value="original">使用各设备原定班次</option><option value="hours">统一每天可开工时长</option></select></label>
    {draft.shift_hours!==undefined&&draft.shift_hours!==0&&<label>每天可开工小时<input aria-label="每天可开工小时" type="number" min="0.25" max="24" step="0.25" value={draft.shift_hours} disabled={readOnly} onChange={e=>{if(e.target.valueAsNumber>0)edit({shift_hours:e.target.valueAsNumber})}}/></label>}
    <button className="danger" disabled={readOnly||busy} onClick={remove}>移除本方案</button>
   </div>
   <details className="manual-routing" open={Object.keys(draft.assignments).length>0||undefined}>
    <summary>指定等待工件的目标设备 <small>（可选，优先于自动改派）</small></summary>
    <div className="toolbar">
     {options.incident_id&&<label><input type="checkbox" checked={onlyAffected} onChange={e=>{setOnlyAffected(e.target.checked);setJobId('');setEquipment('')}}/>只看异常影响的等待工件</label>}
     <label>工件<select aria-label="工件" value={jobId} onChange={e=>{setJobId(e.target.value);setEquipment('')}}><option value="">选择等待工件</option>{choices.map((j:any)=><option key={j.id} value={j.id}>{j.id} · {j.batch} · {j.operation_name} · 当前 {j.assigned}</option>)}</select></label>
     <label>目标设备<select aria-label="目标设备" value={equipment} disabled={!job||readOnly} onChange={e=>setEquipment(e.target.value)}><option value="">选择合格设备</option>{job?.candidates.filter((c:any)=>c.eligible).map((c:any)=><option key={c.resource} value={c.resource}>{c.resource} · {c.name}</option>)}</select></label>
     <button disabled={!job||!equipment||readOnly||busy} onClick={()=>edit({assignments:{...draft.assignments,[jobId]:equipment}})}>确认指派</button>
    </div>
    {!choices.length&&<p className="subtle">{onlyAffected&&options.incident_id?'此异常没有可手动改派的等待工件。可取消筛选查看其他工件。':'此基线无等待工件；仍可调整后续派工或班次。'}</p>}
    {job&&<><Table rows={job.candidates.filter((c:any)=>c.eligible).map((c:any)=>({...c,id:c.resource}))} columns={[
     {key:'resource',title:'合格设备',render:(c:any)=><button className="link" onClick={()=>setEquipment(c.resource)}>{c.resource} {c.resource===job.assigned?'· 当前指派':''}</button>},
     {key:'status',title:'基线状态',render:(c:any)=><Badge value={c.status}/>},{key:'queue',title:'现有队列 / 件'},
     {key:'setup_s',title:'预计换型 / 秒'},{key:'available_at_s',title:'当前占用解除估计',render:(c:any)=>time(c.available_at_s)},
     {key:'rule',title:'资格依据',render:(c:any)=><button className="link" onClick={()=>inspect(c.evidence[1]||c.rule,cp)}>查看匹配要求 ↗</button>},
    ]}/><small className="subtle">占用解除时间只考虑当前加工与维护，不等于开工时间。队列、夹具、换型和班次由仿真处理。</small>
    <details><summary>查看不合格设备及原因</summary><ul>{job.candidates.filter((c:any)=>!c.eligible).map((c:any)=><li key={c.resource}>{c.resource}：{c.reason}</li>)}</ul></details></>}
    <div className="chips">{Object.entries(draft.assignments).map(([jid,rid])=><button key={jid} disabled={readOnly||busy} title="移除指派" onClick={()=>{const next={...draft.assignments};delete next[jid];edit({assignments:next})}}>{jid} → {rid} ×</button>)}</div>
   </details>
  </div>}
  <h4>3. 校验并提交 Plant Simulation</h4>
  <div className="toolbar">
   <label>配对样本<select aria-label="配对样本" value={samples} onChange={e=>setSamples(+e.target.value as 1|5)}><option value={1}>快速比较 · 1 组</option><option value={5}>多次验证 · 5 组</option></select></label>
   <button disabled={!draft||busy||readOnly} onClick={()=>act(async()=>{setPreview(null);setMappingOpen(false);const result=await api('/compare/validate',body);setPreview({key,plan:result});return result},'方案通过基线与设备资格校验')}><CheckCircle2 size={14}/>校验方案</button>
   {plan?.mapping&&<button aria-expanded={mappingOpen} onClick={()=>setMappingOpen(open=>!open)}><GitBranch size={14}/>{mappingOpen?'收起模型映射':'查看本体 → 仿真模型映射'}</button>}
   <button className="primary" disabled={!plan||busy||readOnly} onClick={()=>act(async()=>{const tasks=await api('/compare',body);onSubmitted(tasks);setPreview(null);return tasks},'方案已排队，Plant Simulation 将逐个打开并执行')}><Play size={14}/>运行比较{plan?` · ${plan.task_count} 个任务`:''}</button>
  </div>
  {plan&&<Table rows={plan.scenarios} columns={[{key:'name',title:'已校验方案'},{key:'rule',title:'派工顺序',render:(p:any)=>policyLabel(p.rule)},{key:'reroute',title:'设备分配',render:(p:any)=>p.recalculate_queue?(p.reroute?'自动改派':'回首选设备'):'保留现有队列；后续'+(p.reroute?'按资格改派':'用首选设备')},{key:'shift_hours',title:'班次',render:(p:any)=>shiftLabel(p.shift_hours)},{key:'assignments',title:'已核准指派',render:(p:any)=>p.assignments.map((a:any)=>a.job+' → '+a.target).join('，')||'—'}]}/>}
  {mappingOpen&&plan?.mapping&&<div ref={mappingPanel} className="simulation-mapping-anchor"><SimulationMapping mapping={plan.mapping} inspect={inspect}/></div>}
  <div className="note">已开工工件不抢占。方案只修改派工、合格设备指派和开工班次；沿用同一工艺、订单、统计终点和配对样本。现场仍可继续接收观测，后续观测不进入这批方案。</div>
 </div>;
}
