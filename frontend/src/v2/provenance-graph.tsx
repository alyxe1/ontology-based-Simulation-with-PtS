import React,{useEffect,useRef,useState} from 'react';
import type {Network} from 'vis-network';
import {api,Empty,Panel,Table} from './ui';
import './provenance-graph.css';

type Action={inspect?:string;context?:string;play?:string;analysis?:string};
type Triple={id:string;from:string;to:string;label:string;predicate:string};
type Node={
 id:string;label:string;kind:string;layer:string;distance:number;focused:boolean;record_iri?:string;types:string[];
 properties:{predicate:string;label:string;value:string}[];action?:Action;
};
type Graph={nodes:Node[];edges:Triple[];focus:string;omitted:number;version?:string;shortcuts:{id:string;label:string}[]};
const colors:Record<string,string>={
 '证据实体':'#e7f1f8','活动':'#efe8f6','责任主体':'#faefd9','制造对象':'#e4f2eb',
 '概念':'#e7f1f8','属性':'#e4f2eb','本体':'#efe8f6',
};
const local=(uri:string)=>uri.replace(/^urn:ontologysim:v2:/,'os:').replace(/^http:\/\/www.w3.org\/ns\/prov#/,'prov:')
 .replace(/^http:\/\/www.w3.org\/2000\/01\/rdf-schema#/,'rdfs:').replace(/^http:\/\/www.w3.org\/2002\/07\/owl#/,'owl:');

function Canvas({data,onNode,onEdge}:{
 data:Graph;onNode:(id:string)=>void;onEdge:(id:string)=>void;
}){
 const element=useRef<HTMLDivElement>(null);
 const network=useRef<Network|null>(null);
 useEffect(()=>{
  const container=element.current;
  if(!container||!data.nodes.length)return;
  let disposed=false;
  const columns=new Map<number,Node[]>();
  for(const node of data.nodes)columns.set(node.distance,[...(columns.get(node.distance)||[]),node]);
  import('vis-network').then(({Network:VisNetwork})=>{
  if(disposed)return;
  const graph=new VisNetwork(container,{
   nodes:[...columns].flatMap(([distance,nodes])=>nodes.map((node,i)=>({
    id:node.id,label:node.label.length>24?node.label.slice(0,23)+'…':node.label,
    x:distance*250,y:(i-(nodes.length-1)/2)*57,
    color:{background:colors[node.layer]||'#f0f5f7',border:node.focused?'#256f94':'#a7bac6',
     highlight:{background:'#d9edf7',border:'#256f94'}},
    borderWidth:node.focused?2.5:1,font:{color:'#234b60',size:14},
   }))),
   edges:data.edges.map(edge=>({id:edge.id,from:edge.from,to:edge.to,arrows:'to'})),
  },{
   physics:false,layout:{improvedLayout:false},
   nodes:{shape:'box',margin:{top:9,right:9,bottom:9,left:9},widthConstraint:{minimum:85,maximum:190}},
   edges:{color:{color:'#91a8b6',highlight:'#256f94'},smooth:{enabled:true,type:'cubicBezier',forceDirection:'horizontal',roundness:.34}},
   interaction:{hover:true,dragNodes:false,navigationButtons:false},
  });
  network.current=graph;
  graph.on('click',event=>{
   if(event.nodes.length)onNode(String(event.nodes[0]));
   else if(event.edges.length)onEdge(String(event.edges[0]));
  });
  graph.fit({animation:false});
  }).catch(()=>{if(!disposed)container.textContent='画布加载失败，可继续使用下方的节点和关系表。'});
  return()=>{disposed=true;network.current?.destroy();network.current=null};
 },[data,onNode,onEdge]);
 return <div className="prov-canvas">
  <div ref={element} className="prov-network" role="img" aria-label="PROV-O 有向证据图"/>
  <button className="prov-fit" onClick={()=>network.current?.fit({animation:false})}>适应画布</button>
 </div>;
}

export function ProvenanceGraph({caseId,revision,initialLayer='abox',onAction}:{
 caseId?:string;revision?:number;initialLayer?:'abox'|'tbox';onAction?:(action:Action,verb:'inspect'|'play'|'analysis')=>void;
}){
 const [layer,setLayer]=useState(caseId?initialLayer:'tbox');
 const [focus,setFocus]=useState(''),[depth,setDepth]=useState('2'),[direction,setDirection]=useState('both');
 const [data,setData]=useState<Graph|null>(null),[nodeId,setNode]=useState(''),[edgeId,setEdge]=useState('');
 const [error,setError]=useState('');
 const isSchema=layer==='tbox';
 useEffect(()=>{
  let current=true;
  setError('');setData(null);setNode('');setEdge('');
  const path=isSchema?'/ontology/evidence-graph':'/evidence-cases/'+encodeURIComponent(caseId!)+'/graph';
  api(path+'?'+new URLSearchParams({focus,depth,direction})).then(graph=>{if(current)setData(graph)})
   .catch(e=>{if(current)setError(e.message)});
  return()=>{current=false};
 },[caseId,revision,isSchema,focus,depth,direction]);
 function changeLayer(next:'abox'|'tbox'){
  setFocus('');setDirection('both');setDepth('2');setLayer(next);
 }
 const selected=data?.nodes.find(node=>node.id===(nodeId||data.focus));
 const edge=data?.edges.find(row=>row.id===edgeId);
 const lookup=new Map(data?.nodes.map(node=>[node.id,node.label]));
 const relevant=data?.edges.filter(row=>row.from===selected?.id||row.to===selected?.id)||[];
 const selectNode=React.useCallback((id:string)=>{setNode(id);setEdge('')},[]);
 const selectEdge=React.useCallback((id:string)=>{setEdge(id)},[]);
 return <Panel title="PROV-O 证据图谱" className="provenance-panel" action={caseId&&<div className="segmented" aria-label="证据图谱层">
  <button className={!isSchema?'active':''} onClick={()=>changeLayer('abox')}>ABox · 档案实例</button>
  <button className={isSchema?'active':''} onClick={()=>changeLayer('tbox')}>TBox · 本体定义</button>
 </div>}>
  <div className="flow-note">{isSchema?'查看主本体的导入关系，以及制造与证据概念如何继承 W3C PROV-O。':'选中结果追查输入与责任人，或从基线展开受影响的制造对象。箭头按 RDF 的主语指向宾语。'}</div>
  {error?<div className="notice error" role="alert">{error}</div>:!data?<Empty>正在读取 RDF 关系…</Empty>:<>
   <div className="case-toolbar">
    <label>关键节点 <select aria-label="证据图谱关键节点" value={data.shortcuts.some(row=>row.id===focus)?focus:''} onChange={e=>setFocus(e.target.value)}>
     <option value="">默认焦点</option>{data.shortcuts.map(row=><option key={row.id} value={row.id}>{row.label}</option>)}
    </select></label>
    <label>关系范围 <select aria-label="证据图谱关系范围" value={depth} onChange={e=>setDepth(e.target.value)}>
     <option value="1">一层</option><option value="2">两层</option><option value="3">三层</option>
    </select></label>
    {!isSchema&&<label>方向 <select aria-label="证据图谱方向" value={direction} onChange={e=>setDirection(e.target.value)}>
     <option value="both">双向</option><option value="out">从主语出发</option><option value="in">沿入边查看</option>
    </select></label>}
    {isSchema&&data.version&&<span className="subtle">W3C 版本 {data.version.split('-').at(-1)}</span>}
   </div>
   <Canvas data={data} onNode={selectNode} onEdge={selectEdge}/>
   <div className="prov-legend">{[...new Set(data.nodes.map(node=>node.layer))].map(group=><span key={group}><i style={{background:colors[group]}}/>{group}</span>)}
    <span>{data.nodes.length} 个节点 · {data.edges.length} 条边{data.omitted>0?' · 另有 '+data.omitted+' 个节点，可缩小范围或重新聚焦':''}</span>
   </div>
   <div className="case-summary">
    <label>检查节点 <select aria-label="检查证据节点" value={selected?.id||''} onChange={e=>selectNode(e.target.value)}>
     {data.nodes.map(node=><option key={node.id} value={node.id}>{node.label} · {node.kind}</option>)}
    </select></label>
    {selected&&<div className="actions prov-node-actions">
     <button disabled={selected.id===data.focus} onClick={()=>setFocus(selected.id)}>以此为中心</button>
     {!isSchema&&selected.types.length>0&&<button onClick={()=>{
      setDepth('2');setDirection('both');setLayer('tbox');
      setFocus(selected.types.find(type=>type.startsWith('urn:ontologysim:v2:'))||selected.types[0]);
     }}>查看此类型的 TBox 定义</button>}
     {selected.action?.inspect&&<button onClick={()=>onAction?.(selected.action!,'inspect')}>查看该节点的依据</button>}
     {selected.action?.play&&<button onClick={()=>onAction?.(selected.action!,'play')}>回放对应状态</button>}
     {selected.action?.analysis&&<button onClick={()=>onAction?.(selected.action!,'analysis')}>打开该次运行分析</button>}
    </div>}
    {edge&&<p className="flow-note">{lookup.get(edge.from)} → {edge.label} → {lookup.get(edge.to)} · <code>{local(edge.predicate)}</code></p>}
    {selected&&<details className="prov-identifiers"><summary>节点定义与 RDF 属性</summary>
     <p><code>{selected.id}</code></p>
     {selected.record_iri&&<p>原制造实例 <code>{selected.record_iri}</code></p>}
     {selected.types.length>0&&<p>类型 {selected.types.map(local).join('、')}</p>}
     <Table rows={selected.properties.map(({predicate,label,value},i)=>({id:i+1,predicate:local(predicate),label,value}))}
      columns={[{key:'label',title:'属性'},{key:'value',title:'值',render:(row:any)=><span className="wrap">{row.value}</span>},{key:'predicate',title:'RDF 谓词'}]}/>
    </details>}
   </div>
   <Table rows={relevant.map(row=>({...row,source:lookup.get(row.from),target:lookup.get(row.to),rdf:local(row.predicate)}))}
    columns={[{key:'source',title:'主语',render:(row:any)=><button className="link" onClick={()=>selectNode(row.from)}>{row.source}</button>},
     {key:'label',title:'关系'},{key:'target',title:'宾语',render:(row:any)=><button className="link" onClick={()=>selectNode(row.to)}>{row.target}</button>},
     {key:'rdf',title:'RDF 谓词'}]}/>
  </>}
 </Panel>;
}
