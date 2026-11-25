import React,{useEffect,useMemo,useState} from 'react';
import {Download,RotateCcw,Search} from 'lucide-react';
import {api,Chart,Empty} from './ui';
import './ontology-overview.css';

type Scope='state'|'evidence'|'schema';
type Group={id:string;label:string;nodes:number;triples:number;layer:string};
type Link={source:string;target:string;count:number};
type Overview={sequence:number;head:string;graph:string;triples:number;literals:number;resources:number;subjects:number;groups:Group[];links:Link[]};
type Term={kind:string;text:string;id?:string;datatype?:string;language?:string;truncated?:boolean};
type Triple={subject:Term;predicate:Term;object:Term;label:string};
type Page={items:Triple[];total:number;offset:number};
type ResourceNode=Term&{id:string;label:string;group:string;layer:string};
type ResourceEdge={source:string;target:string;predicate:Term;label:string};
type ResourceGraph={nodes?:ResourceNode[];edges?:ResourceEdge[];too_large?:boolean};
const PAGE_SIZE=30;
const format=(n:number)=>n.toLocaleString('zh-CN');
const colors:Record<string,string>={'事实':'#447999','事理':'#348775','行动':'#8365a2','标准':'#5c79a4'};

export function OntologyOverview({context,schema}:{context:string;schema:boolean}){
 const [evidence,setEvidence]=useState(false),[summary,setSummary]=useState<Overview|null>(null);
 const [generation,reload]=useState(0),[group,setGroup]=useState(''),[offset,setOffset]=useState(0);
 const [draft,setDraft]=useState(''),[query,setQuery]=useState(''),[term,setTerm]=useState<Term|null>(null);
 const [page,setPage]=useState<Page|null>(null),[error,setError]=useState(''),[pageError,setPageError]=useState('');
 const [showNodes,setShowNodes]=useState(false),[network,setNetwork]=useState<ResourceGraph|null>(null);
 const [networkError,setNetworkError]=useState('');
 const scope:Scope=schema?'schema':evidence?'evidence':'state';
 const base=useMemo(()=>new URLSearchParams({scope,context}).toString(),[scope,context]);
 useEffect(()=>{
  let active=true;
  setError('');setSummary(null);setPage(null);
  api('/graph/overview?'+base).then(value=>{if(active)setSummary(value)}).catch(e=>{if(active)setError(e.message)});
  return()=>{active=false};
 },[base,generation]);
 const version=summary?.head;
 useEffect(()=>{
  if(!version)return;
  let active=true;
  setPage(null);setPageError('');
  const params=new URLSearchParams({head:version,group:term?'':group,query,term:term?.id||'',offset:String(offset),limit:String(PAGE_SIZE)});
  api('/graph/triples?'+base+'&'+params).then(value=>{if(active)setPage(value)}).catch(e=>{if(active)setPageError(e.message)});
  return()=>{active=false};
 },[base,version,group,query,term,offset,generation]);
 useEffect(()=>{
  if(!version||!showNodes)return;
  let active=true;
  setNetwork(null);setNetworkError('');
  api('/graph/network?'+base+'&'+new URLSearchParams({head:version,group}))
   .then(value=>{if(active)setNetwork(value)}).catch(e=>{if(active)setNetworkError(e.message)});
  return()=>{active=false};
 },[base,version,group,showNodes,generation]);
 const graphOption=useMemo(()=>{
  if(!summary)return {};
  const groups=summary.groups;
  const selected=group||null;
  return {animation:false,tooltip:{show:false},series:[{
   type:'graph',layout:'circular',roam:true,left:'12%',right:'12%',top:42,bottom:42,
   circular:{rotateLabel:false},edgeSymbol:['none','arrow'],edgeSymbolSize:[0,5],
   label:{show:true,position:'right',fontSize:11,color:'#2b4c60',overflow:'truncate',width:104},
   data:groups.map(g=>({id:g.id,name:g.label,value:g.nodes,symbolSize:Math.min(35,11+Math.log2(g.nodes+1)*2.6),
    itemStyle:{color:colors[g.layer],borderColor:'#fff',borderWidth:2,opacity:!selected||selected===g.id?1:.38}})),
   links:summary.links.filter(link=>link.source!==link.target).map(link=>({...link,value:link.count,
    lineStyle:{curveness:.09,width:Math.min(3.5,.7+Math.log10(link.count+1)),
     opacity:!selected?.length ? .28 : selected===link.source||selected===link.target ? .8 : .08}})),
   lineStyle:{color:'#7395a8'},emphasis:{focus:'adjacency',lineStyle:{opacity:.88}},
  }]};
 },[summary,group]);
 const nodeOption=useMemo(()=>{
  if(!network?.nodes)return {};
  const grouped=new Map<string,ResourceNode[]>();
  for(const node of network.nodes){
   if(!grouped.has(node.group))grouped.set(node.group,[]);
   grouped.get(node.group)!.push(node);
  }
  const columns=Math.max(1,Math.ceil(Math.sqrt(grouped.size*1.5)));
  const maxRows=Math.ceil(Math.max(1,...[...grouped.values()].map(nodes=>nodes.length))/12);
  const data=[...grouped.values()].flatMap((nodes,i)=>nodes.map((node,j)=>({
   id:node.id,name:node.label,term:node,
   x:(i%columns)*206+(j%12)*15,
   y:Math.floor(i/columns)*(maxRows*15+35)+Math.floor(j/12)*15,
   itemStyle:{color:colors[node.layer],borderColor:'#fff',borderWidth:1},
  })));
  return {animation:false,tooltip:{renderMode:'richText',formatter:(p:any)=>p.dataType==='node'?p.data.term.text:p.data.label},
   series:[{type:'graph',layout:'none',data,links:network.edges,roam:true,symbolSize:8,
    edgeSymbol:['none','arrow'],edgeSymbolSize:4,label:{show:false},
    lineStyle:{color:'#87a5b6',opacity:.22,width:1,curveness:.03},
    emphasis:{focus:'adjacency',label:{show:true,position:'right',color:'#27536d',backgroundColor:'#fff'},lineStyle:{opacity:.9}}}]};
 },[network]);
 const changeGroup=(id:string)=>{setGroup(id);setTerm(null);setOffset(0)};
 const clear=()=>{changeGroup('');setDraft('');setQuery('')};
 const follow=(value:Term)=>{setDraft('');setQuery('');setOffset(0);setTerm(value)};
 const renderTerm=(value:Term)=>value.kind==='literal'
  ? <span className="rdf-value"><span>{value.text}{value.truncated?'…':''}</span><small>{value.language?'@'+value.language:value.datatype}{value.truncated?' · 完整值见 RDF':''}</small></span>
  : <button className="link rdf-term" title={value.id} onClick={()=>follow(value)}>{value.text}</button>;
 return <div className="global-ontology">
  <div className="global-ontology-controls">
   {!schema&&<div className="segmented" aria-label="实例图范围">
    <button aria-pressed={!evidence} className={!evidence?'active':''} onClick={()=>{clear();setEvidence(false)}}>运行实例</button>
    <button aria-pressed={evidence} className={evidence?'active':''} onClick={()=>{clear();setEvidence(true)}}>溯源实例</button>
   </div>}
   <button onClick={()=>reload(i=>i+1)}><RotateCcw size={14}/>刷新全局版本</button>
   {summary&&<a className="button" href={'/api/v2/graph/rdf?'+base+'&head='+version}><Download size={14}/>下载完整 RDF</a>}
  </div>
  {error?<div className="notice error" role="alert">{error}</div>:!summary?<Empty>正在读取全局图谱…</Empty>:<>
   <div className="global-graph-metrics">
    <div><small>本体版本</small><strong>{summary.sequence}</strong></div>
    <div><small>资源节点</small><strong>{format(summary.resources)}</strong></div>
    <div><small>RDF 三元组</small><strong>{format(summary.triples)}</strong></div>
    <div><small>字面值三元组</small><strong>{format(summary.literals)}</strong></div>
   </div>
   <p className="note">{schema?'制造核心、仿真和证据扩展、随应用提供的 PROV-O 定义。':'当前上下文的固定版本。'} 圆点汇总类型，箭头表示跨类型引用。逐节点展开后可点选资源或关系，属性值与公理可在下方查看。</p>
   <div className="global-ontology-controls">
    <div className="segmented" aria-label="全局图谱显示方式">
     <button className={!showNodes?'active':''} aria-pressed={!showNodes} onClick={()=>setShowNodes(false)}>按类型总览</button>
     <button className={showNodes?'active':''} aria-pressed={showNodes} onClick={()=>setShowNodes(true)}>逐节点展开</button>
    </div>
    {showNodes&&group&&<span>当前展开 {summary.groups.find(g=>g.id===group)?.label||group} 及其出向关系</span>}
    {showNodes&&network?.nodes&&<span>{format(network.nodes.length)} 个节点 · {format(network.edges?.length||0)} 条引用</span>}
   </div>
   {showNodes&&network?.too_large&&<p className="note">当前范围的节点或引用过多。可在右侧选一个类型展开，或在下方查询完整三元组。</p>}
   {showNodes&&networkError&&<div className="notice error" role="alert">{networkError}</div>}
   <div className="global-ontology-split">
    <div className="global-ontology-chart" aria-label="全局类型关系图">
     {showNodes&&network?.nodes?.length?<Chart key="nodes" option={nodeOption} height={455} onPoint={(p:any)=>follow(p.dataType==='node'?p.data.term:p.data.predicate)}/>
      :summary.groups.length?<Chart key="groups" option={graphOption} height={390} onPoint={(p:any)=>{if(p.dataType==='node')changeGroup(p.data.id)}}/>
      :<Empty>此图暂无节点</Empty>}
    </div>
    <aside className="global-ontology-index" aria-label="节点类型索引">
     <h4>节点类型</h4>
     <button className={!group?'selected':''} onClick={()=>changeGroup('')}><span>全部类型</span><b>{format(summary.resources)}</b></button>
     {summary.groups.map(g=><button key={g.id} className={group===g.id?'selected':''} onClick={()=>changeGroup(g.id)}>
      <span><i style={{background:colors[g.layer]}}/>{g.label}</span><b>{format(g.nodes)}</b>
     </button>)}
    </aside>
   </div>
   <div className="global-ontology-triples">
    <div className="global-triple-heading">
     <h4>完整三元组{group&&!term?' · '+(summary.groups.find(g=>g.id===group)?.label||group):''}</h4>
     <form onSubmit={event=>{event.preventDefault();setTerm(null);setQuery(draft.trim());setOffset(0)}}>
      <input aria-label="查找 RDF 术语" placeholder="工件编号、类、属性或 URI" value={draft} maxLength={180} onChange={e=>setDraft(e.target.value)}/>
      <button><Search size={14}/>查找</button>
     </form>
     {(group||query||term)&&<button onClick={clear}>清除筛选</button>}
    </div>
    {term&&<div className="note">相关三元组 <code>{term.text}</code></div>}
    {pageError?<div className="notice error" role="alert">{pageError}</div>:!page?<Empty>正在读取三元组…</Empty>:<>
     {page.items.length?<div className="table-scroll"><table>
      <thead><tr><th>主语</th><th>谓词</th><th>宾语</th></tr></thead>
      <tbody>{page.items.map((row,i)=><tr key={page.offset+i}>
       <td>{renderTerm(row.subject)}</td>
       <td><button className="link rdf-term" title={row.predicate.id} onClick={()=>follow(row.predicate)}>{row.label}</button><small>{row.predicate.text}</small></td>
       <td>{renderTerm(row.object)}</td>
      </tr>)}</tbody>
     </table></div>:<Empty>当前范围没有匹配三元组。仅被引用的节点可通过搜索术语查看。</Empty>}
     <div className="global-triple-pages">
      <span>{page.total?format(page.offset+1)+'–'+format(page.offset+page.items.length):'0'} / {format(page.total)} 条</span>
      <button disabled={!page.offset} onClick={()=>setOffset(n=>Math.max(0,n-PAGE_SIZE))}>上一页</button>
      <button disabled={page.offset+PAGE_SIZE>=page.total} onClick={()=>setOffset(n=>n+PAGE_SIZE)}>下一页</button>
     </div>
    </>}
   </div>
  </>}
 </div>;
}
