import React,{useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {ArrowRight,Focus,Maximize2,Minus,Plus} from 'lucide-react';
import type {Network} from 'vis-network';
import {Badge,Empty} from './ui';
import './knowledge.css';

const palette:Record<string,string[]>={'事实':['#e8f1f8','#83a8c1'],'事理':['#e9f4ef','#77aa93'],
 '行动':['#f0eafa','#a791c2'],'标准':['#fbf1df','#bca16d']};
type Node={id:string;label:string;kind:string;layer:string;status?:string;definition?:string;constraint_count?:number};
type Relation={id:string;from:string;to:string;label:string;predicate:string;derived:boolean};
type Graph={nodes:Node[];edges:Relation[];focus:string;view:string;glossary?:Node[];omitted?:number};
type Selection={node?:string;edge?:string};
const touches=(e:Relation,id:string)=>e.from===id||e.to===id;
const backReference=(edge:Relation,edges:Relation[])=>edge.predicate==='product'&&
 edges.some(other=>other.from===edge.to&&other.to===edge.from&&['operations','bom'].includes(other.predicate));

function layout(nodes:Node[],edges:Relation[],root:string){
 const rank=new Map([[root,0]]), queue=[root];
 for(let i=0;i<queue.length;i++){
  const id=queue[i],column=rank.get(id)!;
  for(const e of edges.filter(e=>touches(e,id)).sort((a,b)=>Number(b.from===id)-Number(a.from===id))){
   const next=e.from===id?e.to:e.from;
   if(rank.has(next))continue;
   rank.set(next,column===0?(e.from===id?1:-1):column+Math.sign(column));
   queue.push(next);
  }
 }
 const columns=new Map<number,Node[]>();
 for(const node of nodes){
  const column=rank.get(node.id)??0;
  columns.set(column,[...(columns.get(column)||[]),node]);
 }
 return [...columns].flatMap(([column,list])=>
  list.sort((a,b)=>a.layer.localeCompare(b.layer,'zh-CN')).map((node,i)=>({
   ...node,x:column*328,y:(i-(list.length-1)/2)*75,
  })));
}

function Canvas({nodes,edges,root,selection,pick,focus}:{
 nodes:Node[];edges:Relation[];root:string;selection:Selection;
 pick:(v:Selection)=>void;focus:(id:string)=>void;
}){
 const host=useRef<HTMLDivElement>(null),network=useRef<Network|null>(null);
 const [error,setError]=useState(false),[generation,setGeneration]=useState(0);
 useEffect(()=>{
  const element=host.current;
  if(!element)return;
  let disposed=false;
  const positions=layout(nodes,edges,root), columns=new Map(positions.map(n=>[n.id,n.x]));
  const pairs=new Map<string,Relation[]>();
  const pairKey=(e:Relation)=>[e.from,e.to].sort().join('|');
  for(const edge of edges){
   const key=pairKey(edge);
   pairs.set(key,[...(pairs.get(key)||[]),edge]);
  }
  import('vis-network').then(({Network:VisNetwork})=>{
   if(disposed)return;
   const graph=new VisNetwork(element,{
    nodes:positions.map(n=>{
     const [background,border]=palette[n.layer]||palette['事实'],active=n.id===root;
     return {id:n.id,x:n.x,y:n.y,label:n.label.length>24?n.label.slice(0,23)+'…':n.label,
      color:{background:active?'#235b75':background,border:active?'#174c68':border,
       highlight:{background:active?'#235b75':background,border:'#11779c'},
       hover:{background:active?'#235b75':background,border:'#11779c'}},
      font:{color:active?'#fff':'#23495e',size:15,face:'Segoe UI, Microsoft YaHei'},borderWidth:active?2:1.2};
    }),
    edges:edges.map(e=>{
     const pair=pairs.get(pairKey(e))!,index=pair.indexOf(e);
     const curve=((index%2===0)===(e.from<e.to))?'curvedCW':'curvedCCW';
     const bend=pair.length>1||columns.get(e.from)===columns.get(e.to);
     return {id:e.id,from:e.from,to:e.to,label:touches(e,root)||edges.length<=12?e.label:'',
      title:e.label,arrows:{to:{enabled:true,scaleFactor:.65}},dashes:e.derived,
      smooth:bend?{enabled:true,type:curve,roundness:.19+Math.floor(index/2)*.08}:
       {enabled:true,type:'cubicBezier',forceDirection:'horizontal',roundness:.25}};
    }),
   },{
    physics:false,layout:{improvedLayout:false},
    nodes:{shape:'box',margin:{top:12,right:16,bottom:12,left:16},widthConstraint:{minimum:80,maximum:166},shapeProperties:{borderRadius:5}},
    edges:{color:{color:'#7895a6',hover:'#287fa5',highlight:'#156b8e',inherit:false},width:1.3,
     font:{size:13,color:'#314f62',background:'#fff',strokeWidth:2,strokeColor:'#fff',align:'horizontal'}},
    interaction:{hover:true,dragNodes:false,zoomView:true},
   });
   network.current=graph;
   graph.on('click',e=>pick(e.nodes.length?{node:String(e.nodes[0])}:e.edges.length?{edge:String(e.edges[0])}:{}));
   graph.on('doubleClick',e=>{if(e.nodes.length)focus(String(e.nodes[0]))});
   graph.fit({animation:false,maxZoomLevel:1.1});
   setGeneration(g=>g+1);
  }).catch(()=>{if(!disposed)setError(true)});
  return()=>{disposed=true;network.current?.destroy();network.current=null};
 },[nodes,edges,root,pick,focus]);
 useEffect(()=>{
  if(selection.edge)network.current?.selectEdges([selection.edge]);
  else if(selection.node)network.current?.selectNodes([selection.node]);
  else network.current?.unselectAll();
 },[generation,selection]);
 function zoom(ratio:number){
  const graph=network.current;
  if(graph)graph.moveTo({scale:Math.min(2.4,Math.max(.15,graph.getScale()*ratio)),animation:false});
 }
 return <div className="ontology-network">
  <div ref={host} className="ontology-network-host" role="img" aria-label="本体有向关系图"/>
  {error&&<div className="notice error">画布加载失败。下方可查看关系明细。</div>}
  <div className="network-tools" aria-label="画布工具">
   <button aria-label="适应画布" title="适应画布" onClick={()=>network.current?.fit({animation:false,maxZoomLevel:1.1})}><Maximize2 size={15}/></button>
   <button aria-label="放大" title="放大" onClick={()=>zoom(1.25)}><Plus size={15}/></button>
   <button aria-label="缩小" title="缩小" onClick={()=>zoom(.8)}><Minus size={15}/></button>
  </div>
 </div>;
}

export function BusinessCanvas(props:{data:Graph|null;select:(id:string)=>void;inspect:(id:string)=>void}){
 if(!props.data)return <Empty>正在读取所选版本…</Empty>;
 if(!props.data.nodes.length)return <Empty>当前版本暂无关系。</Empty>;
 return <GraphWorkspace {...props} data={props.data}/>;
}

function GraphWorkspace({data,select,inspect}:{data:Graph;select:(id:string)=>void;inspect:(id:string)=>void}){
 const [neighborhood,setNeighborhood]=useState(false),[predicate,setPredicate]=useState(''),[layer,setLayer]=useState('');
 const [reverse,setReverse]=useState(false);
 const [selection,setSelection]=useState<Selection>({});
 const lookup=useMemo(()=>new Map(data.nodes.map(n=>[n.id,n])),[data.nodes]);
 const root=lookup.get(data.focus)||data.nodes[0], schema=data.view==='schema';
 const selectRef=useRef(select);selectRef.current=select;
 const focus=useCallback((id:string)=>selectRef.current(id),[]);
 const visible=useMemo(()=>{
  const allowed=new Set([root.id,...data.nodes.filter(n=>!layer||n.layer===layer).map(n=>n.id)]);
  const candidates=data.edges.filter(e=>(neighborhood||touches(e,root.id))&&(!predicate||e.predicate===predicate)&&allowed.has(e.from)&&allowed.has(e.to));
  const ids=new Set([root.id]);
  for(let i=0;i<data.nodes.length;i++){
   const size=ids.size;
   for(const e of candidates)if(ids.has(e.from)||ids.has(e.to)){ids.add(e.from);ids.add(e.to)}
   if(size===ids.size)break;
  }
  return {nodes:data.nodes.filter(n=>ids.has(n.id)),edges:candidates.filter(e=>ids.has(e.from)&&ids.has(e.to))};
 },[data,root.id,neighborhood,predicate,layer]);
 const predicates=new Map(data.edges.filter(e=>neighborhood||touches(e,root.id)).map(e=>[e.predicate,e.label]));
 const backReferences=useMemo(()=>new Set(visible.edges.filter(e=>backReference(e,visible.edges)).map(e=>e.id)),[visible]);
 const revealed=selection.edge&&backReferences.has(selection.edge)?selection.edge:'';
 const canvasEdges=useMemo(()=>visible.edges.filter(e=>reverse||!backReferences.has(e.id)||e.id===revealed),
  [visible,backReferences,reverse,revealed]);
 const selectedEdge=visible.edges.find(e=>e.id===selection.edge);
 const selectedNode=visible.nodes.find(n=>n.id===selection.node)||root;
 const chosen=selectedEdge?[selectedEdge]:visible.edges.filter(e=>touches(e,selectedNode.id));
 const colors=Object.entries(palette).filter(([name])=>schema||name!=='标准');
 const relationships=(edges:Relation[])=><ul className="network-triples">
  {edges.map(e=><li key={e.id} className={e.id===selectedEdge?.id?'selected':''}>
   <button className="link" onClick={()=>setSelection({node:e.from})}>{lookup.get(e.from)?.label}</button>
   <button className="network-predicate" onClick={()=>setSelection({edge:e.id})} title={e.predicate}>{e.label}<ArrowRight size={12}/></button>
   <button className="link" onClick={()=>setSelection({node:e.to})}>{lookup.get(e.to)?.label}</button>
  </li>)}
 </ul>;
 return <div className="ontology-graph-workspace">
  <div className="ontology-graph-toolbar">
   <div className="ontology-legend">{colors.map(([name,[fill,stroke]])=><span key={name}><i style={{background:fill,borderColor:stroke}}/>{name}</span>)}</div>
   <label>业务层<select aria-label="业务层" value={layer} onChange={e=>{setLayer(e.target.value);setSelection({})}}><option value="">全部</option>{colors.map(([name])=><option key={name}>{name}</option>)}</select></label>
   <label>关系<select aria-label="关系" value={predicate} onChange={e=>{setPredicate(e.target.value);setSelection({})}}><option value="">全部</option>{[...predicates].map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label>
   {backReferences.size>0&&<label><input type="checkbox" checked={reverse} onChange={e=>{setReverse(e.target.checked);setSelection({})}}/>显示反向引用</label>}
   {data.edges.some(e=>!touches(e,root.id))&&<label><input type="checkbox" checked={neighborhood} onChange={e=>{setNeighborhood(e.target.checked);setPredicate('');setSelection({})}}/>显示邻域关系</label>}
  </div>
  <div className="ontology-graph-split">
   <Canvas nodes={visible.nodes} edges={canvasEdges} root={root.id} selection={selection} pick={setSelection} focus={focus}/>
   <aside className="ontology-graph-inspector">
    <small>{selectedEdge?'选中关系':schema?'选中概念':'选中对象'}</small>
    <h3>{selectedEdge?.label||selectedNode.label}</h3>
    {selectedEdge?<>{selectedEdge.derived?<p>由当前数据和业务规则推导</p>:<code>{selectedEdge.predicate.includes(':')?'':'os:'}{selectedEdge.predicate}</code>}</>:<>
     <p>{selectedNode.kind} · {selectedNode.id}{selectedNode.status&&<> · <Badge value={selectedNode.status}/></>}</p>
     {selectedNode.definition&&<p>{selectedNode.definition}</p>}
     {!!selectedNode.constraint_count&&<p>{selectedNode.constraint_count} 项适用的 SHACL 约束</p>}
     <div className="actions"><button onClick={()=>inspect(selectedNode.id)}>{schema?'查看定义与约束':'查看对象与依据'}</button>
      {selectedNode.id!==root.id&&<button onClick={()=>select(selectedNode.id)}><Focus size={14}/>设为焦点</button>}</div>
    </>}
    <h4>{selectedEdge?'关系方向':'可见关系'}</h4>
    {chosen.length?relationships(chosen):<p className="subtle">当前筛选范围无关联关系。</p>}
   </aside>
  </div>
  <div className="ontology-graph-footer"><span>{visible.nodes.length} 个节点 · {canvasEdges.length} 条连线{visible.edges.length>canvasEdges.length?' · '+(visible.edges.length-canvasEdges.length)+' 条反向引用见明细':''}</span>
   <span>单击查看 · 双击切换焦点 · 虚线表示推导{data.omitted?' · 可从邻近节点继续展开 '+data.omitted+' 个节点':''}</span>
  </div>
  <details className="ontology-graph-details"><summary>查看当前关系明细</summary>{relationships(visible.edges)}</details>
  {!!data.glossary?.length&&<details className="ontology-graph-details"><summary>概念索引 · {data.glossary.length} 项</summary><div className="chips">{data.glossary.map(n=><button key={n.id} onClick={()=>select(n.id)}>{n.label} · {n.layer}</button>)}</div></details>}
 </div>;
}
