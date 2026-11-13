import React,{useMemo,useState} from 'react';
import {Panel,Table} from './ui';
import {amount,average,colors,compareMetric,metrics,payload,Run} from './result-data';
import {ResultChart} from './result-charts';

export function ResultComparison({runs,selected,onSelect,onOrder,expected}:{
 runs:Run[];selected:Run;onSelect:(id:string)=>void;onOrder:(id:string,order:string)=>void;expected:number;
}){
 const [metricId,setMetric]=useState('output'),[sample,setSample]=useState('all'),[reference,setReference]=useState('');
 const metric=metrics.find(row=>row.id===metricId)!;
 const samples=[...new Set(runs.map(r=>payload(r).sample_index??0))].sort((a,b)=>a-b);
 const currentSample=samples.includes(Number(sample))?sample:'all';
 const groups=useMemo(()=>{
  const result=new Map<string,{id:string;name:string;runs:Run[]}>();
  for(const run of runs){
   const p=payload(run),name=p.patch.name;
   const id=p.comparison_id?String(p.scenario_index??name):run.task.id;
   if(!result.has(id))result.set(id,{id,name,runs:[]});
   result.get(id)!.runs.push(run);
  }
  return [...result.values()];
 },[runs]);
 const control=groups.find(g=>g.id===reference)||groups.find(g=>g.name==='保持现状')||groups[0];
 const filtered=(list:Run[])=>list.filter(r=>currentSample==='all'||(payload(r).sample_index??0)===Number(currentSample));
 const rows=groups.flatMap(group=>{
  const candidates=filtered(group.runs);
  if(!candidates.length)return [];
  const values=candidates.flatMap(r=>{const value=metric.value(r);return value==null?[]:[value]});
  const compare=compareMetric(filtered(control.runs),candidates,metric.value);
  return [{
   ...group,run:candidates.find(r=>r.task.id===selected.task.id)||candidates[0],
   value:average(values),low:values.length?Math.min(...values):null,high:values.length?Math.max(...values):null,
   count:values.length,delta:group.id===control.id?null:compare.delta,pairs:compare.pairs,
  }];
 });
 const orderIds=[...new Set(rows.flatMap(row=>row.run.summary.orders.map(order=>order.id)))];
 const delivery=rows.flatMap((row,y)=>orderIds.flatMap((order,x)=>{
  const ratios=filtered(row.runs).flatMap(run=>{
   const match=run.summary.orders.find(o=>o.id===order);
   return match?[100*match.completed/match.quantity]:[];
  });
  const value=average(ratios);
  return value==null?[]:[{value:[x,y,+value.toFixed(1)],run:row.run.task.id,order}];
 }));
 const notable=rows.filter(row=>row.delta!=null);
 return <>
  <Panel title="方案指标对比" action={<span className="subtle">已完成 {runs.length} / {expected} 个任务</span>}>
   <div className="result-toolbar">
    <label>指标<select aria-label="比较指标" value={metricId} onChange={e=>setMetric(e.target.value)}>{metrics.map(m=><option key={m.id} value={m.id}>{m.label}</option>)}</select></label>
    {samples.length>1&&<label>配对样本<select aria-label="比较样本" value={currentSample} onChange={e=>setSample(e.target.value)}><option value="all">全部已完成样本</option>{samples.map(n=><option value={n} key={n}>样本 {n+1}</option>)}</select></label>}
    <label>对照方案<select aria-label="对照方案" value={control.id} onChange={e=>setReference(e.target.value)}>{groups.map(g=><option key={g.id} value={g.id}>{g.name}</option>)}</select></label>
   </div>
   <ResultChart height={Math.max(195,rows.length*35+84)} onPoint={(p:any)=>onSelect(rows[p.dataIndex].run.task.id)} option={{
    grid:{left:12,right:58,top:20,bottom:18,containLabel:true},tooltip:{trigger:'axis'},
    xAxis:{type:'value',name:metric.unit},yAxis:{type:'category',inverse:true,data:rows.map(r=>r.name),axisLabel:{width:150,overflow:'truncate'}},
    series:[{type:'bar',barMaxWidth:21,cursor:'pointer',label:{show:true,position:'right'},data:rows.map(r=>({
     value:r.value==null?null:+r.value.toFixed(2),itemStyle:{color:r.id===control.id?'#7c93a3':colors[0]},
    }))}],
   }}/>
   <Table rows={rows} columns={[
    {key:'name',title:'方案',render:(r:typeof rows[number])=><button className="link" onClick={()=>onSelect(r.run.task.id)}>{r.name}{r.id===control.id?' · 对照':''}</button>},
    {key:'value',title:metric.label+' / '+metric.unit,render:(r:typeof rows[number])=>amount(r.value)},
    {key:'range',title:'已完成样本范围',render:(r:typeof rows[number])=>r.count>1?`${amount(r.low)} – ${amount(r.high)}`:'—'},
    {key:'count',title:'有效样本'},
    {key:'delta',title:'较对照的配对差值',render:(r:typeof rows[number])=>r.delta==null?'—':`${r.delta>0?'+':''}${amount(r.delta)} ${metric.unit}（${r.pairs} 对）`},
   ]}/>
   <div className="flow-note">配对差值按同批次、同统计窗口及同种子逐对相减。范围取已完成样本的最小值和最大值。{!notable.length&&' 当前指标暂无可用配对。'}</div>
  </Panel>
  {delivery.length>0&&<Panel title="工单完工率对照" action={<span className="subtle">点击单元格查看工单</span>}>
   <ResultChart height={Math.max(200,rows.length*36+110)} onPoint={(p:any)=>onOrder(p.data.run,p.data.order)} option={{
    tooltip:{formatter:(p:any)=>p.data.order+'\n完工 '+p.value[2]+'%'},
    grid:{left:13,right:15,top:10,bottom:55,containLabel:true},
    xAxis:{type:'category',data:orderIds,splitArea:{show:true}},
    yAxis:{type:'category',inverse:true,data:rows.map(r=>r.name),axisLabel:{width:145,overflow:'truncate'}},
    visualMap:{min:0,max:100,orient:'horizontal',left:'center',bottom:6,text:['100%','0%'],
     calculable:false,inRange:{color:['#f3e2d1','#e5edf0','#aed2da','#7babb7']}},
    series:[{type:'heatmap',cursor:'pointer',data:delivery,label:{show:true,color:'#243d4d',formatter:(p:any)=>p.value[2]+'%'}}],
   }}/>
   <div className="flow-note">选择全部样本时，每格显示已完成样本的平均完工率，点击后打开其中一次运行的工单。</div>
  </Panel>}
  <Panel title="逐次运行结果">
   <Table rows={runs.map(run=>({id:run.task.id,run,name:payload(run).patch.name,sample:(payload(run).sample_index??0)+1}))} columns={[
    {key:'name',title:'方案',render:({id,name}:any)=><button className="link" onClick={()=>onSelect(id)}>{name}{id===selected.task.id?' · 当前明细':''}</button>},
    {key:'sample',title:'样本'},
    ...metrics.filter(m=>['output','ontime','overdue','wip','queue','scrap'].includes(m.id)).map(m=>({key:m.id,title:m.label+' / '+m.unit,render:({run}:{run:Run})=>amount(m.value(run))})),
    {key:'native',title:'原生核对',render:({run}:{run:Run})=>run.native.verified?<span className="positive">通过</span>:'—'},
   ]}/>
   <div className="flow-note">新增完工和新增报废按统计起点之后至终点的记录计算。平均延期只统计已完成订单。每件排队按运行指标，以截至终点已投产件数为分母。</div>
  </Panel>
 </>;
}
