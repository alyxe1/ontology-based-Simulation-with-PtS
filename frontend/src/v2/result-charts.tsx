import React,{useMemo} from 'react';
import {graphic} from 'echarts';
import {Chart,Empty,time} from './ui';
import {Analysis,Segment,colors,kindLabels,minutes} from './result-data';

const tooltip={renderMode:'richText',confine:true};
const hour=(seconds:number)=>seconds/3600;
const hourAxis={type:'value',name:'业务小时',nameLocation:'middle',nameGap:23};
const zoom=[{type:'inside',filterMode:'weakFilter'},{type:'slider',filterMode:'weakFilter',bottom:9,height:15}];

export function ResultChart({option,height=300,onPoint}:any){
 const styled=useMemo(()=>({animation:false,color:colors,...option,tooltip:{...tooltip,...option.tooltip}}),[option]);
 return <Chart option={styled} height={height} onPoint={onPoint}/>;
}

export function TimeSeries({series,start,end,unit='件',height=290}:{
 series:{name:string;points:number[][];step?:boolean}[];start:number;end:number;unit?:string;height?:number;
}){
 return <ResultChart height={height} option={{
  tooltip:{trigger:'axis'},legend:{type:'scroll',top:5},
  grid:{left:52,right:26,top:43,bottom:67},
  xAxis:{...hourAxis,min:hour(start),max:hour(end)},yAxis:{type:'value',name:unit,min:0},
  dataZoom:zoom,series:series.map(s=>({name:s.name,type:'line',showSymbol:false,step:s.step===false?false:'end',
   data:s.points.map(([at,count])=>[hour(at),count])})),
 }}/>;
}

export function Histogram({values,onSelect}:{
 values:{id:string;value:number}[];onSelect:(ids:string[])=>void;
}){
 if(!values.length)return <Empty>统计窗口内没有样本</Empty>;
 const max=Math.max(...values.map(row=>row.value));
 const width=Math.max(1,Math.ceil(max/8));
 const buckets=Array.from({length:Math.max(1,Math.ceil(max/width))},(_,index)=>({label:`${index*width}–${(index+1)*width}`,ids:[] as string[]}));
 for(const row of values)buckets[Math.min(buckets.length-1,Math.floor(row.value/width))].ids.push(row.id);
 return <ResultChart height={245} onPoint={(p:any)=>onSelect(buckets[p.dataIndex].ids)} option={{
  tooltip:{trigger:'axis'},grid:{left:46,right:20,top:30,bottom:47},
  xAxis:{type:'category',data:buckets.map(b=>b.label),name:'分钟',nameLocation:'middle',nameGap:23,axisLabel:{interval:0}},
  yAxis:{type:'value',name:'工件',minInterval:1},series:[{type:'bar',data:buckets.map(b=>b.ids.length),cursor:'pointer'}],
 }}/>;
}

export function ResourceGantt({segments,resources,start,end,onSelect}:{
 segments:Segment[];resources:string[];start:number;end:number;onSelect:(row:Segment)=>void;
}){
 const kinds=Object.keys(kindLabels) as Segment['kind'][];
 return <ResultChart height={Math.max(275,resources.length*30+100)} onPoint={(p:any)=>onSelect(segments[p.value[3]])} option={{
  tooltip:{formatter:(p:any)=>{
   const s=segments[p.value[3]];
   return [s.resource,s.job,s.operation_name,kindLabels[s.kind],time(s.start_s)+' → '+time(s.end_s),minutes(s.end_s-s.start_s)+' 分钟'].filter(Boolean).join('\n');
  }},
  legend:{top:4},grid:{left:75,right:25,top:40,bottom:62},
  xAxis:{...hourAxis,min:hour(start),max:hour(end)},yAxis:{type:'category',data:resources,inverse:true},
  dataZoom:zoom,series:kinds.map((kind,index)=>({
   name:kindLabels[kind],type:'custom',encode:{x:[1,2],y:0},itemStyle:{color:colors[index]},cursor:'pointer',
   data:segments.flatMap((row,i)=>row.kind===kind?[[resources.indexOf(row.resource),hour(row.start_s),hour(row.end_s),i]]:[]),
   renderItem:(p:any,api:any)=>{
    const from=api.coord([api.value(1),api.value(0)]),to=api.coord([api.value(2),api.value(0)]);
    const bounds={x:p.coordSys.x,y:p.coordSys.y,width:p.coordSys.width,height:p.coordSys.height};
    const shape=graphic.clipRectByRect({x:from[0],y:from[1]-8,width:Math.max(1,to[0]-from[0]),height:16},bounds);
    return shape?{type:'rect',shape,style:{fill:colors[index]}}:undefined;
   },
  })),
 }}/>;
}

export function MaterialFlow({analysis,jobIds,onSelect}:{
 analysis:Analysis;jobIds:Set<string>;onSelect:(ids:string[])=>void;
}){
 const {nodes,links}=useMemo(()=>{
  const paths=new Map<string,string[]>();
  for(const row of analysis.segments){
   if(row.kind!=='processing'||!jobIds.has(row.job))continue;
   const path=paths.get(row.job)||[];
   path.push(row.resource);paths.set(row.job,path);
  }
  const nodes=new Map<string,{name:string;depth:number;display:string;jobs:Set<string>}>();
  const links=new Map<string,{source:string;target:string;jobs:Set<string>}>();
  for(const [jid,path] of paths){
   const stages=['窗口入口',...path];
   stages.forEach((display,depth)=>{
    const name=depth+':'+display;
    if(!nodes.has(name))nodes.set(name,{name,depth,display,jobs:new Set()});
    nodes.get(name)!.jobs.add(jid);
    if(depth===0)return;
    const source=(depth-1)+':'+stages[depth-1],key=source+'→'+name;
    if(!links.has(key))links.set(key,{source,target:name,jobs:new Set()});
    links.get(key)!.jobs.add(jid);
   });
  }
  return {
   nodes:[...nodes.values()].map(n=>({...n,jobs:[...n.jobs],value:n.jobs.size})),
   links:[...links.values()].map(e=>({...e,jobs:[...e.jobs],value:e.jobs.size})),
  };
 },[analysis,jobIds]);
 if(!links.length)return <Empty>此筛选范围内没有加工流转记录</Empty>;
 return <ResultChart height={340} onPoint={(p:any)=>onSelect(p.data.jobs)} option={{
  tooltip:{formatter:(p:any)=>p.dataType==='edge'?`${p.data.source} → ${p.data.target}\n${p.data.value} 件`:`${p.data.display}\n${p.data.value} 件`},
  series:[{type:'sankey',left:12,right:64,top:14,bottom:14,nodeWidth:13,nodeGap:12,
   data:nodes,links,layoutIterations:16,label:{formatter:(p:any)=>p.data.display,fontSize:11},
   emphasis:{focus:'adjacency'},lineStyle:{color:'gradient',opacity:.33},cursor:'pointer'}],
 }}/>;
}
