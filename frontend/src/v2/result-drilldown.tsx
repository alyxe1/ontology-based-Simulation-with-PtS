import React,{useMemo,useRef,useState} from 'react';
import {Badge,Empty,Panel,Table,time} from './ui';
import {Analysis,Job,Order,Run,Segment,amount,colors,kindLabels,minutes,statusOf,windowJobs} from './result-data';
import {Histogram,MaterialFlow,ResourceGantt,ResultChart,TimeSeries} from './result-charts';

export type Inspect=(id:string,context?:string)=>void;
type Drill=(job:string,resource?:string)=>void;
type OpenEvidence=(identity:string)=>void;
const selectOptions=(values:string[])=>values.map(id=><option key={id} value={id}>{id}</option>);

export function PagedTable({rows,columns}:any){
 const [page,setPage]=useState(0);
 const size=20,last=Math.max(0,Math.ceil(rows.length/size)-1),current=Math.min(page,last);
 return <>
  <Table rows={rows.slice(current*size,(current+1)*size)} columns={columns}/>
  {rows.length>size&&<div className="result-toolbar result-pager">
   <span>{current*size+1}–{Math.min((current+1)*size,rows.length)} / {rows.length} 条</span>
   <button disabled={!current} onClick={()=>setPage(current-1)}>上一页</button>
   <button disabled={current>=last} onClick={()=>setPage(current+1)}>下一页</button>
  </div>}
 </>;
}

function JobTable({jobs,analysis,trace,evidence}:{
 jobs:Job[];analysis?:Analysis;trace:Drill;evidence:OpenEvidence;
}){
 return <PagedTable rows={jobs} columns={[
  {key:'id',title:'工件',render:(j:Job)=><button className="link" onClick={()=>trace(j.id)}>{j.id} ↗</button>},
  {key:'order',title:'工单'},{key:'batch',title:'批次'},{key:'status',title:'终点状态',render:(j:Job)=><Badge value={j.status}/>},
  {key:'released_at',title:'投产',render:(j:Job)=>j.released_at>=0?time(j.released_at):'—'},
  {key:'completed_at',title:'完工 / 报废',render:(j:Job)=>j.completed_at>=0?time(j.completed_at):'—'},
  {key:'wait',title:'窗口排队 / 分',render:(j:Job)=>analysis?minutes(analysis.job_wait_s[j.id]||0):'—'},
  {key:'evidence',title:'依据',render:(j:Job)=><button className="link" onClick={()=>evidence(j.id)}>工件证据 ↗</button>},
 ]}/>;
}

export function OrdersView({run,reference,analysis,initialOrder,trace,evidence}:{
 run:Run;reference?:Run;analysis?:Analysis;initialOrder:string;trace:Drill;evidence:OpenEvidence;
}){
 const [orderId,setOrder]=useState(initialOrder),[search,setSearch]=useState('');
 const {orders,jobs,kpi}=run.summary;
 const current=orders.find(o=>o.id===orderId)||orders[0];
 const referenceOrders=new Map(reference?.summary.orders.map(o=>[o.id,o]));
 const chosen=jobs.filter(j=>(!current||j.order===current.id)&&[j.id,j.batch,j.status].some(v=>v.toLowerCase().includes(search.toLowerCase().trim())));
 return <>
  <Panel title="工单交付进度" action={<span className="subtle">点击柱条或工单查看工件</span>}>
   <ResultChart height={Math.max(216,orders.length*28+75)} onPoint={(p:any)=>{setOrder(orders[p.dataIndex].id);setSearch('')}} option={{
    tooltip:{trigger:'axis'},legend:{top:3},grid:{left:66,right:32,top:40,bottom:38},
    xAxis:{type:'value',name:'件',minInterval:1},yAxis:{type:'category',inverse:true,data:orders.map(o=>o.id)},
    series:[
     {name:'完工',type:'bar',stack:'orders',data:orders.map(o=>o.completed),itemStyle:{color:colors[1]},cursor:'pointer'},
     {name:'报废',type:'bar',stack:'orders',data:orders.map(o=>o.scrapped),itemStyle:{color:colors[4]},cursor:'pointer'},
     {name:'其余未完工',type:'bar',stack:'orders',data:orders.map(o=>o.quantity-o.completed-o.scrapped),itemStyle:{color:'#afbfca'},cursor:'pointer'},
    ],
   }}/>
   <Table rows={orders} columns={[
    {key:'id',title:'工单',render:(o:Order)=><button className="link" onClick={()=>{setOrder(o.id);setSearch('')}}>{o.id}{o.id===current?.id?' · 已选':''}</button>},
    {key:'product',title:'产品',render:(o:Order)=>analysis?.products[o.product]||o.product},
    {key:'completed',title:'完工 / 需求',render:(o:Order)=>o.completed+' / '+o.quantity},
    {key:'due_s',title:'交期',render:(o:Order)=>time(o.due_s)},
    {key:'completion_s',title:'完工时刻',render:(o:Order)=>time(o.completion_s)},
    {key:'status',title:'交付状态',render:(o:Order)=>{
     const status=statusOf(o,kpi.window_end_s);
     return <span className={status==='按期完工'?'positive':status==='未到期'?'':'negative'}>{status}{o.tardiness_s>0?' · '+minutes(o.tardiness_s)+' 分':''}</span>;
    }},
    ...(reference?[{key:'delta',title:'较对照完工变化 / 件',render:(o:Order)=>{
     const previous=referenceOrders.get(o.id);
     if(!previous)return '—';
     const delta=o.completed-previous.completed;
     return (delta>0?'+':'')+delta;
    }},{key:'time_delta',title:'较对照完工时差 / 分',render:(o:Order)=>{
     const previous=referenceOrders.get(o.id)?.completion_s;
     if(previous==null||o.completion_s==null)return '—';
     const delta=o.completion_s-previous;
     return (delta>0?'+':'')+minutes(delta);
    }}]:[]),
    {key:'evidence',title:'依据',render:(o:Order)=><button className="link" onClick={()=>evidence(o.id)}>工单证据 ↗</button>},
   ]}/>
   <div className="flow-note">按统计终点判断未完工订单是否已到期。{reference&&'完工变化与同种子、同窗口的对照相比。'}</div>
  </Panel>
  {current&&<Panel title={current.id+' · 工件明细'} action={<input aria-label="筛选工单工件" placeholder="查找工件、批次或状态" value={search} onChange={e=>setSearch(e.target.value)}/>}>
   <JobTable key={current.id+search} jobs={chosen} analysis={analysis} trace={trace} evidence={evidence}/>
  </Panel>}
 </>;
}

export function ResourcesView({analysis,reference,trace,evidence,openTimeline}:{
 analysis:Analysis;reference?:Analysis;trace:Drill;evidence:OpenEvidence;openTimeline:(resource:string)=>void;
}){
 const [selected,setSelected]=useState(''),[pool,setPool]=useState('');
 const sorted=[...analysis.resources].sort((a,b)=>b.queue_area_s-a.queue_area_s||b.utilization-a.utilization);
 const resource=sorted.find(r=>r.id===selected)||sorted[0];
 const comparison=reference?.resources.find(r=>r.id===resource?.id);
 const fixture=analysis.fixtures.find(r=>r.id===pool)||analysis.fixtures[0];
 const window=analysis.end_s-analysis.start_s;
 const states=[['加工','processing_s','#377e9f'],['换型','setup_s','#b08046'],['维护','maintenance_s','#b55354'],
  ['班外未占用','off_shift_s','#c9d5dc'],['班内空闲','idle_s','#79a69c']] as const;
 const queued=analysis.queue_spells.filter(s=>s.resource===resource?.id).sort((a,b)=>(b.end_s-b.start_s)-(a.end_s-a.start_s));
 return <>
  <Panel title="设备状态占比" action={<span className="subtle">按整个统计窗口计算</span>}>
   <ResultChart height={Math.max(245,sorted.length*28+75)} onPoint={(p:any)=>setSelected(sorted[p.dataIndex].id)} option={{
    tooltip:{trigger:'axis'},legend:{top:4},grid:{left:64,right:38,top:40,bottom:38},
    xAxis:{type:'value',name:'%',min:0,max:100},yAxis:{type:'category',inverse:true,data:sorted.map(r=>r.id)},
    series:states.map(([name,key,color])=>({name,type:'bar',stack:'status',cursor:'pointer',itemStyle:{color},data:sorted.map(r=>+(100*r[key]/window).toFixed(2))})),
   }}/>
   <div className="flow-note">设备跨班加工或维护时仍记为占用。剩余时间按班内空闲和班外未占用分摊。</div>
  </Panel>
  <Panel title="设备与队列压力" action={<span className="subtle">按累计排队件小时排序</span>}>
   {sorted[0]?.queue_area_s>0&&<div className="result-highlight">累计排队最多的是 <button className="link" onClick={()=>setSelected(sorted[0].id)}>{sorted[0].id}</button>，约 {amount(sorted[0].queue_area_s/3600)} 件小时，峰值 {sorted[0].peak_queue} 件。</div>}
   <Table rows={sorted} columns={[
    {key:'id',title:'设备',render:(r:typeof resource)=><button className="link" onClick={()=>setSelected(r.id)}>{r.id}{r.id===resource?.id?' · 已选':''}</button>},
    {key:'utilization',title:'加工占比',render:(r:typeof resource)=>amount(100*r.utilization)+'%'},
    {key:'operations',title:'完工工序'},{key:'maintenance_periods',title:'维护段数'},
    {key:'queue_area_s',title:'排队 / 件小时',render:(r:typeof resource)=>amount(r.queue_area_s/3600)},
    {key:'average_queue',title:'平均队列 / 件',render:(r:typeof resource)=>amount(r.average_queue)},
    {key:'peak_queue',title:'峰值 / 件'},{key:'end_queue',title:'终点 / 件'},
    {key:'trace',title:'下钻',render:(r:typeof resource)=><button className="link" onClick={()=>openTimeline(r.id)}>设备甘特 ↗</button>},
   ]}/>
   <div className="flow-note">累计排队是队列长度对时间的积分。结合维护、换型、班次及夹具占用判断排队原因。</div>
  </Panel>
  {resource&&<Panel title={resource.id+' · 队列变化与等待明细'} action={<button onClick={()=>evidence(resource.id+'/state')}>设备状态证据 ↗</button>}>
   <TimeSeries start={analysis.start_s} end={analysis.end_s} series={[
    {name:'当前运行',points:resource.queue_curve},...(comparison?[{name:'配对对照',points:comparison.queue_curve}]:[]),
   ]}/>
   {comparison&&<div className="flow-note">对照累计排队 {amount(comparison.queue_area_s/3600)} 件小时，当前运行 {amount(resource.queue_area_s/3600)} 件小时。</div>}
   <PagedTable key={resource.id} rows={queued.map((row,i)=>({...row,id:i+1}))} columns={[
    {key:'job',title:'工件',render:(s:typeof queued[number])=><button className="link" onClick={()=>trace(s.job)}>{s.job} ↗</button>},
    {key:'start_s',title:'进入队列',render:(s:typeof queued[number])=>time(s.start_s)},
    {key:'end_s',title:'离开 / 统计终点',render:(s:typeof queued[number])=>time(s.end_s)},
    {key:'duration',title:'排队 / 分',render:(s:typeof queued[number])=>minutes(s.end_s-s.start_s)},
    {key:'open_at_end',title:'等待段状态',render:(s:typeof queued[number])=>s.open_at_end?'终点仍在等待':'已结束'},
   ]}/>
  </Panel>}
  {fixture&&<Panel title="共享夹具占用" action={<select aria-label="选择夹具" value={fixture.id} onChange={e=>setPool(e.target.value)}>{analysis.fixtures.map(f=><option key={f.id} value={f.id}>{f.name}</option>)}</select>}>
   <TimeSeries start={analysis.start_s} end={analysis.end_s} unit="套" series={[
    {name:'占用',points:fixture.curve},{name:'可用总量',points:[[analysis.start_s,fixture.quantity],[analysis.end_s,fixture.quantity]]},
   ]}/>
   <div className="flow-note">峰值 {fixture.peak} / {fixture.quantity} 套，时间加权平均占用 {amount(fixture.average)} 套。</div>
  </Panel>}
 </>;
}

export function FlowView({analysis,run,reference,trace,evidence}:{
 analysis:Analysis;run:Run;reference?:Analysis;trace:Drill;evidence:OpenEvidence;
}){
 const [product,setProduct]=useState(''),[drill,setDrill]=useState<{title:string;ids:string[]}|null>(null);
 const drillPanel=useRef<HTMLDivElement>(null);
 function showJobs(title:string,ids:string[]){
  setDrill({title,ids});
  requestAnimationFrame(()=>drillPanel.current?.scrollIntoView({block:'nearest'}));
 }
 const ids=useMemo(()=>new Set(run.summary.jobs.filter(j=>!product||j.product===product).map(j=>j.id)),[run,product]);
 const finished=windowJobs(run,'done').filter(j=>j.released_at>=0);
 const lead=finished.map(j=>({id:j.id,value:(j.completed_at-j.released_at)/60}));
 const waited=run.summary.jobs.filter(j=>analysis.job_wait_s[j.id]>0).map(j=>({id:j.id,value:analysis.job_wait_s[j.id]/60}));
 const {curve,start_s:start,end_s:end}=analysis;
 const buckets=Math.min(24,Math.max(1,Math.ceil((end-start)/3600))),width=(end-start)/buckets;
 const volumes=Array<number>(buckets).fill(0);
 for(const at of analysis.completion_times)volumes[Math.min(buckets-1,Math.max(0,Math.floor((at-start)/width)))]++;
 return <>
  <div className="result-grid">
   <Panel title="新增产出与投入">
    <TimeSeries start={start} end={end} series={[{name:'累计新增投产',points:curve.map(p=>[p[0],p[1]])},
     {name:'累计新增完工',points:curve.map(p=>[p[0],p[2]])},{name:'累计新增报废',points:curve.map(p=>[p[0],p[3]])},
     ...(reference?[{name:'对照累计新增完工',points:reference.curve.map(p=>[p[0],p[2]])}]:[])]}/>
   </Panel>
   <Panel title="在制品与排队趋势" action={<span className="subtle">峰值 {analysis.peak_wip} 件</span>}>
    <TimeSeries start={start} end={end} series={[{name:'在制',points:curve.map(p=>[p[0],p[4]])},{name:'其中排队',points:curve.map(p=>[p[0],p[5]])}]}/>
   </Panel>
  </div>
  <Panel title="分时段完工量" action={<span className="subtle">每段 {minutes(width)} 分钟</span>}>
   <ResultChart height={245} onPoint={(p:any)=>{
    const items=finished.filter(j=>Math.min(buckets-1,Math.max(0,Math.floor((j.completed_at-start)/width)))===p.dataIndex);
    showJobs('该时段完工工件',items.map(j=>j.id));
   }} option={{
    tooltip:{trigger:'axis'},grid:{left:45,right:20,top:25,bottom:37},
    xAxis:{type:'category',data:volumes.map((_,i)=>time(start+width*i))},yAxis:{type:'value',minInterval:1,name:'件'},
    series:[{type:'bar',name:'完工',data:volumes,cursor:'pointer'}],
   }}/>
  </Panel>
  <Panel title="加工流向 · 桑基图" action={<label>产品 <select aria-label="桑基图产品" value={product} onChange={e=>{setProduct(e.target.value);setDrill(null)}}><option value="">全部</option>{Object.entries(analysis.products).map(([id,name])=><option key={id} value={id}>{name}</option>)}</select></label>}>
   <MaterialFlow analysis={analysis} jobIds={ids} onSelect={ids=>showJobs('所选流向的工件',ids)}/>
   <div className="flow-note">按窗口内有加工记录的工件及其实际设备顺序统计。连线宽度表示经过该段的工件数，返工再次经过设备会出现在后续层。点击节点或连线查看工件。</div>
  </Panel>
  <div className="result-grid">
   <Panel title="完工件流转时间分布" action={<span className="subtle">{lead.length} 件</span>}>
    <Histogram values={lead} onSelect={ids=>showJobs('该流转时长区间的工件',ids)}/>
    <div className="flow-note">从投产到完工，包含窗口前已发生的时间。</div>
   </Panel>
   <Panel title="工件累计排队时间分布" action={<span className="subtle">{waited.length} 件</span>}>
    <Histogram values={waited} onSelect={ids=>showJobs('该排队时长区间的工件',ids)}/>
    <div className="flow-note">统计窗口内有排队的工件。终点仍在等待的工件只累计至终点。</div>
   </Panel>
  </div>
  {drill&&<div ref={drillPanel}><Panel title={drill.title+' · '+drill.ids.length+' 件'} action={<button onClick={()=>setDrill(null)}>清除选中</button>}>
   <JobTable key={drill.title+drill.ids.join(',')} jobs={run.summary.jobs.filter(j=>drill.ids.includes(j.id))} analysis={analysis} trace={trace} evidence={evidence}/>
  </Panel></div>}
 </>;
}

export function QualityView({analysis,run,trace,evidence}:{
 analysis:Analysis;run:Run;trace:Drill;evidence:OpenEvidence;
}){
 const [product,setProduct]=useState(''),[result,setResult]=useState('all');
 const scrap=windowJobs(run,'scrapped');
 const products=Object.entries(analysis.products).map(([id,name])=>{
  const inspections=analysis.inspections.filter(r=>r.product===id);
  const first=inspections.filter(r=>r.attempt===0),passed=first.filter(r=>r.result==='pass').length;
  const rechecks=inspections.filter(r=>r.attempt>0);
  return {id,name,first:first.length,passed,failed:first.length-passed,rechecks:rechecks.length,
   recovered:rechecks.filter(r=>r.result==='pass').length,scrap:scrap.filter(j=>j.product===id).length};
 });
 const rows=analysis.inspections.filter(r=>(!product||r.product===product)&&(result==='all'||r.result===result));
 return <>
  <Panel title="首检、复检与报废" action={<span className="subtle">统计窗口内发生的记录</span>}>
   <ResultChart height={250} onPoint={(p:any)=>setProduct(products[p.dataIndex].id)} option={{
    tooltip:{trigger:'axis'},legend:{top:3},grid:{left:50,right:22,top:45,bottom:40},
    xAxis:{type:'category',data:products.map(p=>p.name),axisLabel:{width:125,overflow:'truncate'}},
    yAxis:{type:'value',name:'记录数',minInterval:1},
    series:[
     {type:'bar',name:'首检通过',data:products.map(p=>p.passed),itemStyle:{color:colors[1]}},
     {type:'bar',name:'首检未通过',data:products.map(p=>p.failed),itemStyle:{color:colors[2]}},
     {type:'bar',name:'复检通过',data:products.map(p=>p.recovered),itemStyle:{color:colors[0]}},
     {type:'bar',name:'报废工件',data:products.map(p=>p.scrap),itemStyle:{color:colors[4]}},
    ].map(s=>({...s,cursor:'pointer'})),
   }}/>
   <Table rows={products} columns={[
    {key:'name',title:'产品',render:(p:typeof products[number])=><button className="link" onClick={()=>setProduct(p.id)}>{p.name}</button>},
    {key:'first',title:'首检次数'},{key:'passed',title:'首检通过'},
    {key:'yield',title:'首检通过率',render:(p:typeof products[number])=>p.first?amount(p.passed*100/p.first)+'%':'—'},
    {key:'failed',title:'首检未通过'},{key:'rechecks',title:'复检次数'},{key:'recovered',title:'复检通过'},{key:'scrap',title:'报废件数'},
   ]}/>
   <div className="flow-note">首检通过率以窗口内首检次数为分母。复检可能对应窗口前发生的首检。</div>
  </Panel>
  <Panel title="检验记录" action={<span className="subtle">{rows.length} 条</span>}>
   <div className="result-toolbar">
    <label>产品<select aria-label="检验产品" value={product} onChange={e=>setProduct(e.target.value)}><option value="">全部产品</option>{products.map(p=><option value={p.id} key={p.id}>{p.name}</option>)}</select></label>
    <label>结果<select aria-label="检验结果" value={result} onChange={e=>setResult(e.target.value)}><option value="all">全部</option><option value="pass">通过</option><option value="fail">未通过</option></select></label>
   </div>
   <PagedTable key={product+result} rows={rows} columns={[
    {key:'job',title:'工件',render:(r:typeof rows[number])=><button className="link" onClick={()=>trace(r.job)}>{r.job} ↗</button>},
    {key:'batch',title:'批次'},{key:'material_lot',title:'原料批次'},{key:'time_s',title:'检验时刻',render:(r:typeof rows[number])=>time(r.time_s)},
    {key:'attempt',title:'检验阶段',render:(r:typeof rows[number])=>r.attempt==null?'—':r.attempt===0?'首检':'复检'},
    {key:'result',title:'检验结果',render:(r:typeof rows[number])=><span className={r.result==='pass'?'positive':'negative'}>{r.result==='pass'?'通过':'未通过'}</span>},
    {key:'evidence',title:'依据',render:(r:typeof rows[number])=><button className="link" onClick={()=>evidence(r.id)}>检验证据 ↗</button>},
   ]}/>
  </Panel>
  {scrap.length>0&&<Panel title="窗口新增报废工件"><JobTable jobs={scrap.filter(j=>!product||j.product===product)} analysis={analysis} trace={trace} evidence={evidence}/></Panel>}
 </>;
}

export function TimelineView({analysis,run,job,resource,filter,evidence}:{
 analysis:Analysis;run:Run;job:string;resource:string;filter:Drill;evidence:OpenEvidence;
}){
 const [order,setOrder]=useState(''),[kind,setKind]=useState(''),[selected,setSelected]=useState<Segment|null>(null);
 const activity=kind==='queue'&&!job?'':kind;
 const lookup=new Map(run.summary.jobs.map(j=>[j.id,j]));
 const matchingJobs=run.summary.jobs.filter(j=>!order||j.order===order);
 const matchingIds=new Set(matchingJobs.map(j=>j.id));
 const rows=useMemo(()=>{
  const waits:Segment[]=job?analysis.queue_spells.filter(row=>row.job===job).map((row,i)=>({...row,id:'queue/'+i,kind:'queue'})):[];
  return [...analysis.segments,...waits].filter(row=>
   (!resource||row.resource===resource)&&(!job||row.job===job)&&(!order||matchingIds.has(row.job))&&(!activity||activity===row.kind),
  ).sort((a,b)=>a.start_s-b.start_s||a.resource.localeCompare(b.resource));
 },[analysis,run,job,resource,order,activity]);
 const picked=selected&&rows.includes(selected)?selected:null;
 const item=lookup.get(job);
 const rowEvidence=(row:Segment)=>evidence(row.kind==='queue'?row.job:row.id);
 return <Panel title="设备甘特与工件轨迹" action={<span className="subtle">{rows.length} 段</span>}>
  <div className="result-toolbar">
   <label>设备<select aria-label="甘特设备" value={resource} onChange={e=>filter(job,e.target.value)}><option value="">全部设备</option>{selectOptions(analysis.resources.map(r=>r.id))}</select></label>
   <label>工单<select aria-label="甘特工单" value={order} onChange={e=>{setOrder(e.target.value);filter('',resource)}}><option value="">全部工单</option>{selectOptions(run.summary.orders.map(r=>r.id))}</select></label>
   <label>工件<select aria-label="甘特工件" value={job} onChange={e=>filter(e.target.value,resource)}><option value="">全部工件</option>{selectOptions(matchingJobs.map(j=>j.id))}</select></label>
   <label>活动<select aria-label="甘特活动" value={activity} onChange={e=>setKind(e.target.value)}><option value="">全部活动</option>{Object.entries(kindLabels).filter(([key])=>key!=='queue'||job).map(([id,name])=><option key={id} value={id}>{name}</option>)}</select></label>
   <button onClick={()=>{setOrder('');setKind('');filter('','');setSelected(null)}}>清除筛选</button>
  </div>
  {item&&<div className="result-highlight">{item.id} · {item.order} · {item.batch} · <Badge value={item.status}/> · 窗口累计排队 {minutes(analysis.job_wait_s[item.id]||0)} 分 <button className="link" onClick={()=>evidence(item.id)}>工件证据 ↗</button></div>}
  {rows.length>0?<ResourceGantt segments={rows} resources={analysis.resources.filter(r=>rows.some(s=>s.resource===r.id)).map(r=>r.id)}
   start={analysis.start_s} end={analysis.end_s} onSelect={setSelected}/>:<Empty>当前筛选范围内没有活动</Empty>}
  {picked&&<div className="result-highlight">{picked.resource} · {picked.job||'设备'} · {kindLabels[picked.kind]} · {picked.operation_name||''} · {time(picked.start_s)} 至 {time(picked.end_s)} <button className="link" onClick={()=>rowEvidence(picked)}>查看活动证据 ↗</button></div>}
  <div className="flow-note">拖动时间轴缩放。活动按统计窗口截取，已在窗口起点进行的加工和维护也会显示。选择单件工件时可同时查看它的排队段。</div>
  <PagedTable key={[job,resource,order,kind].join(':')} rows={rows.map((s,i)=>({...s,rowKey:i,id:s.id}))} columns={[
   {key:'resource',title:'设备'},{key:'job',title:'工件',render:(s:Segment)=>s.job?<button className="link" onClick={()=>{setOrder('');setKind('');filter(s.job,'')}}>{s.job} ↗</button>:'—'},
   {key:'kind',title:'活动',render:(s:Segment)=>kindLabels[s.kind]},{key:'operation_name',title:'工序'},
   {key:'start_s',title:'起点',render:(s:Segment)=>time(s.start_s)},{key:'end_s',title:'终点',render:(s:Segment)=>time(s.end_s)},
   {key:'duration',title:'历时 / 分',render:(s:Segment)=>minutes(s.end_s-s.start_s)},
   {key:'evidence',title:'依据',render:(s:Segment)=><button className="link" onClick={()=>rowEvidence(s)}>证据 ↗</button>},
  ]}/>
 </Panel>;
}
