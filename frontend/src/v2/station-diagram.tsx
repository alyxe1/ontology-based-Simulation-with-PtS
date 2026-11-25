import React from 'react';
import {ArrowRight,Box,Boxes,Cpu} from 'lucide-react';
import './station-diagram.css';

type StationDiagramProps={
 waiting:number;
 occupant?:string;
 marker?:{position:'buffer'|'station';id:string};
 buffer?:string;
 station?:string;
};

export function StationDiagram({waiting,occupant,marker,buffer,station}:StationDiagramProps){
 const waitingSelection=marker?.position==='buffer'?marker.id:null;
 const stationSelection=marker?.position==='station'?marker.id:null;
 return <div className="station-diagram">
  <div className={'station-zone station-buffer'+(waitingSelection?' traced':'')}>
   <div><Boxes size={18}/><b>等待缓冲区</b></div>
   <small>{waiting} 件等待</small>
   {waitingSelection&&<span className="station-part"><Box size={12}/>{waitingSelection}</span>}
   {buffer&&<code>{buffer}</code>}
  </div>
  <ArrowRight className="station-arrow" size={16} aria-hidden/>
  <div className={'station-zone station-machine'+(stationSelection?' traced':'')}>
   <div><Cpu size={18}/><b>加工工位</b></div>
   <small>{occupant||'当前无占用'}</small>
   {stationSelection&&stationSelection!==occupant&&<span className="station-part"><Box size={12}/>{stationSelection}</span>}
   {station&&<code>{station}</code>}
  </div>
 </div>;
}
