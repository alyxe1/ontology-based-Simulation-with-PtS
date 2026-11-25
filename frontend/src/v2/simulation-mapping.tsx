import React,{useState} from 'react';
import {ArrowRight} from 'lucide-react';
import {Badge,Table,time} from './ui';
import {PagedTable} from './result-drilldown';
import {InstanceMapping} from './instance-mapping';
import './simulation-mapping.css';

export type Mapping={
 checkpoint_id:string;graph_sequence:number;graph_head:string;source_cursor?:number;origin_s:number;horizon_s:number;
 engine:string;seeds:number[];shacl:{conforms:boolean;shape_files:string[];shape_hashes:Record<string,string>};
 native_clock:{path:string;initial_s:number;end_field:string;end_s:number;speed:number;real_time:boolean};
 resources:{id:string;name:string;state_id:string;status:string;queue_count:number;occupied_by:string;
  frame:string;label_field:string;buffer:string;station:string;speed:number;setup_s:number;shift_hours:number;tool_count:number;maintenance_until:number}[];
 operations:{id:string;name:string;product:string;index:number;next_operation?:string;operation:string;
  material:string;tolerance_um:number;fixture:string;preferred:string;duration_s:number;
  candidates:{resource:string;eligible:boolean;reason:string}[]}[];
 workpieces:{id:string;status:string;order:string;batch:string;product:string;operation?:string;operation_name?:string;queue_seq:number;
  assigned:string;location:string;duration_s?:number;native_path?:string;native_field?:string;execution?:string;end_s?:number;remaining_s?:number;time_basis?:string}[];
 events:{id:string;kind:string;job:string;resource:string;due_s:number;delay_s:number;priority:number;target?:string}[];
 fields:{stage:string;source:string;target:string;rule:string}[];
};
const sec=(value:number)=>Number(value.toFixed(6))+' 秒';
const eventNames:Record<string,string>={release:'投产',transfer_finished:'转运完成',maintenance_finished:'维护结束',process_finished:'加工完成',setup_finished:'换型完成'};

export function SimulationMapping({mapping:m,inspect}:{mapping:Mapping;inspect:(id:string,context:string)=>void}){
 const [tab,setTab]=useState('instances'),[query,setQuery]=useState(''),[placed,setPlaced]=useState(true);
 const ref=(id:string,label?:string)=><button className="link" onClick={()=>inspect(id,m.checkpoint_id)}>{label||id} ↗</button>;
 const jobs=m.workpieces.filter(j=>(!placed||j.native_path)&&
  [j.id,j.order,j.batch,j.assigned,j.operation_name].some(v=>v?.toLowerCase().includes(query.trim().toLowerCase())));
 const clock=m.native_clock;
 return <section className="simulation-mapping" aria-label="本体到仿真模型映射">
  <div className="simulation-flow">
   <div><small>已固定的 ABox</small><strong>基线版本 {m.graph_sequence}</strong><span>{time(m.origin_s)}{m.source_cursor!=null?' · 来源帧 #'+m.source_cursor:''}</span></div><ArrowRight size={18}/>
   <div><small>语义校验</small><strong>SHACL 通过</strong><span>工艺、设备资格、工件位置与执行</span></div><ArrowRight size={18}/>
   <div><small>Python 控制器</small><strong>装载状态和应用方案</strong><span>资格、派工、换型、维护与班次</span></div><ArrowRight size={18}/>
   <div><small>Plant Simulation</small><strong>原生对象和事件时钟</strong><span>运行时通过 COM 写入与回读</span></div>
  </div>
  <p className="note">这是本次已校验方案的装载预览。各方案先装载同一现场，再应用上方的干预。实际开工工时在工序派到设备后计算，原生回读随运行结果记录。</p>
  <div className="simulation-mapping-summary"><span>统计窗口 <strong>{time(m.origin_s)} — {time(m.horizon_s)}</strong></span><span>配对样本种子 <strong>{m.seeds.join('、')}</strong></span><span>原生速度 <strong>{clock.speed}</strong> · 非实时</span></div>
  <div className="toolbar segmented" aria-label="映射明细">
   {[['instances','实例映射'],['fields','字段规则'],['equipment','设备与缓冲区'],['operations','工艺与设备资格'],['jobs','工件与剩余工时'],['events','计划事件']].map(([key,label])=><button aria-pressed={tab===key} className={tab===key?'active':''} key={key} onClick={()=>setTab(key)}>{label}</button>)}
  </div>
  {tab==='instances'&&<InstanceMapping mapping={m} inspect={inspect}/>}
  {tab==='fields'&&<Table rows={m.fields} columns={[
   {key:'stage',title:'阶段'},{key:'source',title:'本体输入',render:(r:any)=><code>{r.source}</code>},
   {key:'target',title:'原生字段或控制逻辑',render:(r:any)=><code>{r.target}</code>},{key:'rule',title:'转换方式'},
  ]}/>}
  {tab==='equipment'&&<Table rows={m.resources} columns={[
   {key:'id',title:'本体设备',render:(r:any)=>ref(r.id,r.id+' · '+r.name)},
   {key:'status',title:'状态与队列',render:(r:any)=><>{ref(r.state_id)} <Badge value={r.status}/><small>等待 {r.queue_count} 件 · 占用 {r.occupied_by||'无'}</small></>},
   {key:'params',title:'控制器参数',render:(r:any)=><><small>工时系数 {r.speed} · 换型 {sec(r.setup_s)} · 班次 {r.shift_hours} 小时</small><small>刀具计数 {r.tool_count}{r.maintenance_until>m.origin_s?' · 维护至 '+time(r.maintenance_until):''}</small></>},
   {key:'native',title:'原生写入目标',render:(r:any)=><><code>{r.label_field}</code><code>{r.buffer}</code><code>{r.station}</code></>},
  ]}/>}
  {tab==='operations'&&<PagedTable rows={m.operations} columns={[
   {key:'id',title:'产品路线与工序',render:(op:any)=><>{ref(op.id,op.product+' · '+(op.index+1)+'. '+op.name)}<small>{op.next_operation?'后继 '+op.next_operation:'路线终点'}</small></>},
   {key:'material',title:'工序要求',render:(op:any)=><><small>{op.operation} · {op.material} · {op.tolerance_um} μm</small><small>夹具 {op.fixture||'—'}</small></>},
   {key:'candidates',title:'首选与合格设备',render:(op:any)=><><small>首选 {op.preferred}</small>{op.candidates.filter((r:any)=>r.eligible).map((r:any)=><small key={r.resource}>{ref(r.resource)} {r.reason}</small>)}<details><summary>查看其他设备</summary>{op.candidates.filter((r:any)=>!r.eligible).map((r:any)=><small key={r.resource}>{r.resource} · {r.reason}</small>)}</details></>},
   {key:'duration_s',title:'开工参数',render:(op:any)=><><strong>标准工时 {sec(op.duration_s)}</strong><small>乘以选中设备的工时系数及样本因子后写入 Machine.ProcTime</small></>},
  ]}/>}
  {tab==='jobs'&&<>
   <div className="toolbar"><label>筛选工件<input value={query} onChange={e=>setQuery(e.target.value)} placeholder="工件、工单、批次、设备或工序"/></label><label><input type="checkbox" checked={placed} onChange={e=>setPlaced(e.target.checked)}/>仅显示直接装载的工件</label><span className="subtle">{jobs.length} / {m.workpieces.length} 件</span></div>
   <PagedTable rows={jobs} columns={[
    {key:'id',title:'本体工件',render:(j:any)=><>{ref(j.id)}<small>{j.order} · {j.batch}</small></>},
    {key:'status',title:'基线状态',render:(j:any)=><><Badge value={j.status}/><small>{j.assigned||'未指派'} · 队列序号 {j.queue_seq}</small></>},
    {key:'operation',title:'工序 / 标准工时',render:(j:any)=>j.operation?<>{ref(j.operation,j.operation_name)}<small>{sec(j.duration_s)}</small></>:'—'},
    {key:'native_path',title:'原生初始位置',render:(j:any)=>j.native_path?<><code>{j.native_path}</code><small>Part.Name = {j.id}</small></>:<small>此状态不创建初始原生工件</small>},
    {key:'remaining_s',title:'剩余时长写入',render:(j:any)=>j.remaining_s!=null?<><strong>{sec(j.remaining_s)}</strong><code>{j.native_field}</code><small>{ref(j.execution,'执行记录')} · {time(j.end_s)} − {time(m.origin_s)}{j.time_basis?' · 基线剩余时间估计':''}</small></>:'—'},
   ]}/>
  </>}
  {tab==='events'&&<PagedTable rows={m.events} columns={[
   {key:'kind',title:'待执行事件',render:(e:any)=><>{ref(e.id,eventNames[e.kind]||e.kind)}<small>{e.job||e.resource}</small></>},
   {key:'due_s',title:'业务时间',render:(e:any)=>time(e.due_s)},{key:'priority',title:'处理优先级'},
   {key:'target',title:'原生触发 / 回读',render:(e:any)=>e.target?<code>{e.target}</code>:<span>当前边界由控制器处理</span>},
  ]}/>}
  <details><summary>查看版本、校验文件和原生时钟</summary><div className="simulation-mapping-meta">
   <div>基线 <code>{m.checkpoint_id}</code> · <code>{m.graph_head}</code></div>
   <div>校验规则 {m.shacl.shape_files.map(name=><a key={name} href={'/api/v2/ontology/tbox/'+name} title={'SHA-256 '+m.shacl.shape_hashes[name]}>{name} ↗</a>)}</div>
   <div>执行服务 <code>{m.engine}</code></div>
   <div><code>{clock.path}</code> 初始化为 {sec(clock.initial_s)}，业务时钟偏移 {sec(m.origin_s)}</div>
   <div><code>{clock.end_field}</code> 安全上限 {sec(clock.end_s)}</div>
  </div></details>
 </section>;
}
