import React,{useId,useState} from 'react';
import {Badge,Empty,Table,time} from './ui';

const layers=[
 {name:'事实',question:'有什么、发生了什么',color:'#eef5fb',ink:'#285a79'},
 {name:'事理',question:'按什么工艺和规则',color:'#eaf6ef',ink:'#226648'},
 {name:'行动',question:'计划怎么做、推演结果如何',color:'#f2edf8',ink:'#654686'},
];

export function BusinessCanvas({data,select,inspect}:any){
 const arrow=useId();const [selected,setSelected]=useState('');
 if(!data)return <Empty>正在读取所选版本…</Empty>;
 const root=data.nodes.find((n:any)=>n.id===selected)||data.nodes.find((n:any)=>n.focused)||data.nodes[0];
 const lookup=new Map<string,any>(data.nodes.map((n:any)=>[n.id,n]));
 const positions=new Map<string,{x:number,y:number}>();
 let bottom=10;
 const bands=layers.map(l=>{
   const nodes=data.nodes.filter((n:any)=>n.layer===l.name);const y=bottom;
   nodes.forEach((n:any,i:number)=>positions.set(n.id,{x:i%2===0?184:521,y:y+14+Math.floor(i/2)*58}));
   const height=Math.max(67,Math.ceil(nodes.length/2)*58+14);bottom+=height+10;
   return {...l,nodes,y,height};
 });
 const related=data.edges.filter((e:any)=>e.from===root?.id||e.to===root?.id);
 const words=(s:string,n:number)=>s.length>n?s.slice(0,n)+'…':s;
 return <>
 <div className="ontology-canvas"><svg role="group" aria-label="事实、事理、行动两列关系图" viewBox={'0 0 860 '+bottom}>
 <defs><marker id={arrow} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#7091a2"/></marker></defs>
 {bands.map(l=><g key={l.name}><rect x="12" y={l.y} width="836" height={l.height} rx="7" fill={l.color}/><text x="30" y={l.y+27} fill={l.ink} fontSize="15" fontWeight="600">{l.name}</text><text x="30" y={l.y+47} fill={l.ink} fontSize="10">{l.question}</text>{!l.nodes.length&&<text x="184" y={l.y+39} fill="#536d79" fontSize="12">当前焦点没有此类对象</text>}</g>)}
 {related.map((e:any)=>{const a=positions.get(e.from)!,b=positions.get(e.to)!;const x=a.x===b.x?a.x+305:a.x<b.x?a.x+295:a.x;const end=a.x===b.x?b.x+295:a.x<b.x?b.x:b.x+295;const y=a.y+24,z=b.y+24;return <path key={e.id} d={e.from===e.to?`M ${x-10} ${y} c 24 -26 24 26 0 10`:`M ${x} ${y} C ${(x+end)/2+18} ${y} ${(x+end)/2+18} ${z} ${end} ${z}`} fill="none" stroke="#7091a2" strokeWidth="1.6" markerEnd={'url(#'+arrow+')'}><title>{e.label}</title></path>})}
 {data.nodes.map((n:any)=>{const {x,y}=positions.get(n.id)!;return <g key={n.id} className="ontology-node" role="button" tabIndex={0} aria-label={n.label+' · '+n.kind} onClick={()=>setSelected(n.id)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();setSelected(n.id)}}}><title>{n.label} · {n.id}</title><rect x={x} y={y} width="295" height="46" rx="5" fill={root?.id===n.id?'#2e6e88':'white'} stroke="#a9bbc5"/><text x={x+11} y={y+18} fontWeight="600" fontSize="13" fill={root?.id===n.id?'white':'#244b5e'}>{words(n.label,21)}</text><text x={x+11} y={y+35} fontSize="10" fill={root?.id===n.id?'#daedf5':'#597484'}>{words(n.kind+' · '+n.id,36)}</text></g>})}
 </svg></div>
 {root&&<div className="ontology-inspector"><div className="toolbar"><strong>{root.label} · {root.layer}</strong>{root.status&&<Badge value={root.status}/>}<button onClick={()=>inspect(root.id)}>查看定义与依据</button><button onClick={()=>select(root.id)}>以此为中心</button></div>
 <Table rows={related.map((e:any)=>({...e,subject:lookup.get(e.from)?.label,object:lookup.get(e.to)?.label}))} columns={[{key:'subject',title:'起点'},{key:'label',title:'关系'},{key:'object',title:'终点'},{key:'derived',title:'依据',render:(r:any)=>r.derived?'由规则或顺序推得':'本体关系'}]}/></div>}
 {data.glossary?.length>0&&<details className="ontology-glossary"><summary>其他概念 · {data.glossary.length} 项</summary><div className="chips">{data.glossary.map((n:any)=><button key={n.id} onClick={()=>inspect(n.id)}>{n.label} · {n.layer}</button>)}</div></details>}
 </>;
}

export function OrderedRoute({route,inspect}:any){
 if(!route)return <Empty>没有可显示的产品工艺</Empty>;
 return <div className="panel-pad"><h3>{route.name} · 正常工艺顺序</h3>
 <div className="ordered-route" aria-label="产品工艺顺序">{route.steps.map((o:any,i:number)=><React.Fragment key={o.id}>{i>0&&<span aria-hidden>→</span>}<button onClick={()=>inspect(o.id)}><small>工序 {String(i+1).padStart(2,'0')}</small><strong>{o.name}</strong><span>{o.preferred} · {time(o.duration_s)}</span></button></React.Fragment>)}</div>
 <p className="note">{route.linked?'实线表示本体中明确的下一道工序，正常完工按此链流转。':'此存档保存的是顺序号，箭头由顺序号推得。'} 检验失败允许一次返工，回到「{route.steps.find((o:any)=>o.id===route.rework_to)?.name}」；再次失败报废。</p></div>;
}
