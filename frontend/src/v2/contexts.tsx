import React,{useState} from 'react';
import {api,Panel,Table,stamp,time} from './ui';

export function ContextManager({data,act,busy,readOnly,user,context,setContext,setFocus,setPage}:any){
 const [reset,setReset]=useState(false),[confirm,setConfirm]=useState(''),[removing,setRemoving]=useState<any>(null);
 const active=data.workspace?.id;
 const rows=[...data.workspaces.map((w:any)=>({...w,title:w.name,kind:w.id===active?'当前现场':'已归档现场'})),
             ...data.checkpoints.map((c:any)=>({...c,title:c.name,kind:'分析基线'})),
             ...data.tasks.filter((t:any)=>t.kind!=='baseline'&&!['queued','running','pausing','paused','cancelling'].includes(t.status)).map((t:any)=>({...t,title:JSON.parse(t.payload).patch?.name||'原生任务',kind:'方案或运行'}))];
 return <Panel title="上下文与数据管理" action={user.role==='admin'&&<button className="danger" disabled={busy} onClick={()=>{setConfirm('');setReset(true)}}>重置系统</button>}>
 <p className="note">移除后，该项不再出现在选择器和任务列表。移除现场会同时移除它的基线和结果。签名证据仍留存在本机。</p>
 <Table rows={rows} columns={[{key:'kind',title:'类别'},{key:'title',title:'名称',render:(r:any)=><>{r.title}<small className="context-id">{r.id}</small></>},{key:'time_s',title:'业务时刻',render:(r:any)=>time(r.time_s)},{key:'created_at',title:'创建时间',render:(r:any)=>stamp(r.created_at)},{key:'actions',title:'操作',render:(r:any)=><div className="actions">{(r.type!=='Task'||r.status==='completed')&&<button onClick={()=>{setContext(r.id===active?'live':r.id);setFocus('');setPage('production')}}>查看</button>}{!readOnly&&<button disabled={busy} className="danger" onClick={()=>setRemoving(r)}>移除</button>}</div>}]}/>
 {(reset||removing)&&<div className="drawer-shade" onClick={()=>{if(!busy){setReset(false);setRemoving(null)}}}><section className="reset-dialog" role="dialog" aria-modal="true" aria-label={reset?'重置系统':'移除上下文'} onClick={e=>e.stopPropagation()}>
 <h2>{reset?'从空工作台开始':'移除 '+removing.title}</h2>
 <p>{reset?'当前现场、历史现场、分析基线与方案列表将被清空。重置后需手动载入车间；本地账号和签名证据保留。':removing.id===active?'此操作会停止当前现场数据接入，并移除它的基线与结果。签名证据保留。':'此项将从工作台列表移除，现有签名证据保留。'}</p>
 {reset&&<label>输入「清空工作台」<input autoFocus value={confirm} onChange={e=>setConfirm(e.target.value)}/></label>}
 <div className="actions"><button disabled={busy} onClick={()=>{setReset(false);setRemoving(null)}}>取消</button><button className="danger" disabled={busy||(reset&&confirm!=='清空工作台')} onClick={()=>act(async()=>{if(reset){await api('/reset',{confirm});setContext('live');setFocus('');setPage('production');setReset(false)}else{await api('/contexts/'+encodeURIComponent(removing.id),undefined,'DELETE');setRemoving(null);if(context===removing.id||removing.type==='Workspace'){setContext('live');setFocus('')}}},reset?'工作台已清空，可重新载入车间':'上下文已移除')}>{reset?'确认重置':'确认移除'}</button></div>
 </section></div>}</Panel>;
}
