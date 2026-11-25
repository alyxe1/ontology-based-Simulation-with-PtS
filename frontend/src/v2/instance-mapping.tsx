import React,{useState} from 'react';
import {ArrowRight,Box,Boxes,Clock3,Cpu,Database,Network,Truck} from 'lucide-react';
import type {Mapping} from './simulation-mapping';
import {StationDiagram} from './station-diagram';
import {Badge,time} from './ui';

const seconds=(value:number)=>Number(value.toFixed(6))+' 秒';
type Inspect=(id:string,context:string)=>void;
type Piece=Mapping['workpieces'][number];
type Device=Mapping['resources'][number];

function Landing({device,piece}:{device?:Device;piece?:Piece}){
 if(device){
  return <div className="native-frame-preview">
   <strong><Cpu size={16}/>{device.id} · 原生资源 Frame</strong>
   <div><code>{device.frame}</code><small>Label = {device.name}</small></div>
   <StationDiagram waiting={device.queue_count} occupant={device.occupied_by} buffer={device.buffer} station={device.station}
    marker={piece?.native_path?{id:piece.id,position:piece.native_path===device.buffer?'buffer':'station'}:undefined}/>
   {piece?.native_field&&piece.remaining_s!=null&&<div className="native-field-write">
    <code>{piece.native_field}</code><b>= {seconds(piece.remaining_s)}</b>
   </div>}
  </div>;
 }
 if(piece?.native_path){
  return <div className="native-frame-preview traced">
   <strong><Truck size={17}/>原生转运缓冲区</strong><code>{piece.native_path}</code>
   <span className="station-part"><Box size={14}/>{piece.id}</span>
  </div>;
 }
 return <div className="native-frame-preview pending"><Clock3 size={20}/><strong>基线时没有此工件的原生对象</strong>
  <small>{piece?.status==='pending'?'后续投产时，控制器按工艺和设备资格决定进入哪个缓冲区。':
   piece?.status==='material_wait'?'等待物料，基线初始化不会创建原生工件。':'结果已计入基线，初始化时无需重新创建实体。'}</small>
 </div>;
}

export function InstanceMapping({mapping:m,inspect}:{mapping:Mapping;inspect:Inspect}){
 const preferred=m.workpieces.find(j=>j.native_field)||m.workpieces.find(j=>j.native_path)||m.workpieces[0];
 const [kind,setKind]=useState<'piece'|'device'>(preferred?'piece':'device');
 const [pieceId,setPiece]=useState(preferred?.id||'');
 const [deviceId,setDevice]=useState(m.resources[0]?.id||'');
 const piece=kind==='piece'?m.workpieces.find(j=>j.id===pieceId):undefined;
 const device=kind==='device'?m.resources.find(r=>r.id===deviceId):
  m.resources.find(r=>piece?.native_path&&(piece.native_path===r.buffer||piece.native_path===r.station));
 const operation=m.operations.find(op=>op.id===piece?.operation);
 const event=piece&&m.events.find(e=>e.job===piece.id);
 const ref=(id:string,label:string)=><button className="link" title={id} onClick={()=>inspect(id,m.checkpoint_id)}>{label} ↗</button>;
 const placed=piece?.native_path!=null;
 return <div className="instance-mapping" aria-label="本体实例与原生对象对照">
  <div className="instance-mapping-controls">
   <div className="segmented" aria-label="选择实例类型">
    <button className={kind==='piece'?'active':''} aria-pressed={kind==='piece'} disabled={!m.workpieces.length} onClick={()=>setKind('piece')}>工件实例</button>
    <button className={kind==='device'?'active':''} aria-pressed={kind==='device'} onClick={()=>setKind('device')}>设备实例</button>
   </div>
   {kind==='piece'?<label>工件<select aria-label="映射工件实例" value={pieceId} onChange={e=>setPiece(e.target.value)}>
    {m.workpieces.map(j=><option key={j.id} value={j.id}>{j.id} · {j.batch} · {j.operation_name||'已结束'}</option>)}
   </select></label>:<label>设备<select aria-label="映射设备实例" value={deviceId} onChange={e=>setDevice(e.target.value)}>
    {m.resources.map(r=><option key={r.id} value={r.id}>{r.id} · {r.name}</option>)}
   </select></label>}
  </div>
  <div className="instance-mapping-chain">
   <article>
    <h4><Network size={16}/>本体实例</h4>
    {piece?<div className="mapping-entity-tree">
     <div className="mapping-entity-root">
      {ref(piece.id,piece.id)}<Badge value={piece.status}/>
      {ref('schema/Workpiece','工件定义')}
     </div>
     <ul>
      <li><span>属于批次</span>{ref(piece.batch,piece.batch)}</li>
      <li><span>对应产品</span>{ref(piece.product,piece.product)}</li>
      {piece.operation&&<li><span>当前工序</span>{ref(piece.operation,piece.operation_name||piece.operation)}</li>}
      {piece.assigned&&<li><span>当前指派</span>{ref(piece.assigned,piece.assigned)}</li>}
      {piece.status==='transfer'&&<li><span>当前位置</span><b>共享转运区</b></li>}
      {piece.execution&&<li><span>当前执行</span>{ref(piece.execution,'执行记录')}</li>}
     </ul>
    </div>:device&&<div className="mapping-entity-tree">
     <div className="mapping-entity-root">
      {ref(device.id,device.id)}<Badge value={device.status}/>
      {ref('schema/Equipment','设备定义')}
     </div>
     <ul>
      <li><span>设备状态</span>{ref(device.state_id,device.state_id)}</li>
      <li><span>设备名称</span><b>{device.name}</b></li>
      {device.occupied_by&&<li><span>占用工件</span>{ref(device.occupied_by,device.occupied_by)}</li>}
      <li><span>等待工件</span><b>{device.queue_count} 件</b></li>
     </ul>
    </div>}
   </article>
   <ArrowRight className="mapping-chain-arrow" size={17} aria-hidden/>
   <article>
    <h4><Database size={16}/>装载与控制</h4>
    {piece?<ul className="instance-mapping-actions">
     {placed&&<li><strong>保留工件标识</strong><code>Part.Name = {piece.id}</code></li>}
     {piece.status==='queued'&&<li><strong>按队列顺序装载</strong><span>设备 {piece.assigned} · 基线队列序号 {piece.queue_seq}</span></li>}
     {piece.remaining_s!=null&&<li><strong>写入剩余时间后移入工位</strong>
      {piece.end_s!=null&&<span>执行结束时间 {seconds(piece.end_s)} − 基线时刻 {seconds(m.origin_s)}</span>}
      {piece.time_basis&&<small>来自基线中的剩余时间假设</small>}
     </li>}
     {piece.status==='transfer'&&<li><strong>装载到共享转运区</strong><span>等待原定转运完成事件</span></li>}
     {!placed&&<li><strong>保留业务状态</strong><span>状态 <Badge value={piece.status}/></span></li>}
     {event&&<li><strong>对应计划事件 {time(event.due_s)}</strong>{ref(event.id,'查看事件')}</li>}
     {operation&&<li><strong>工序资格</strong><span>{operation.material} · {operation.tolerance_um} μm · 标准工时 {seconds(operation.duration_s)}</span>
      <small>控制器按资格、夹具和维护状态决定后续开工</small></li>}
    </ul>:device&&<ul className="instance-mapping-actions">
     <li><strong>生成设备的 Frame</strong><span>写入设备名称。Frame 下创建一个等待缓冲区和一个加工工位。</span></li>
     <li><strong>恢复等待与占用</strong><span>按基线队列及在制状态放置工件。</span></li>
     <li><strong>开工条件由控制器管理</strong><span>换型 {seconds(device.setup_s)} · 刀具计数 {device.tool_count} · 班次 {device.shift_hours} 小时</span>
      {device.maintenance_until>m.origin_s&&<small>维护至 {time(device.maintenance_until)}</small>}</li>
    </ul>}
   </article>
   <ArrowRight className="mapping-chain-arrow" size={17} aria-hidden/>
   <article className="instance-mapping-native">
    <h4><Boxes size={17}/>Plant Simulation</h4>
    <Landing device={device} piece={piece}/>
   </article>
  </div>
 </div>;
}
