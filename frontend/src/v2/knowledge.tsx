import React from 'react';
import {Empty,time} from './ui';
export {BusinessCanvas} from './business-network';

export function OrderedRoute({route,inspect}:any){
 if(!route)return <Empty>没有可显示的产品工艺</Empty>;
 return <div className="panel-pad"><h3>{route.name} · 正常工艺顺序</h3>
 <div className="ordered-route" aria-label="产品工艺顺序">{route.steps.map((o:any,i:number)=><React.Fragment key={o.id}>{i>0&&<span aria-hidden>→</span>}<button onClick={()=>inspect(o.id)}><small>工序 {String(i+1).padStart(2,'0')}</small><strong>{o.name}</strong><span>{o.preferred} · {time(o.duration_s)}</span></button></React.Fragment>)}</div>
 <p className="note">{route.linked?'实线表示本体中明确的下一道工序，正常完工按此链流转。':'此存档保存的是顺序号，箭头由顺序号推得。'} 检验失败允许一次返工，回到「{route.steps.find((o:any)=>o.id===route.rework_to)?.name}」；再次失败报废。</p></div>;
}
