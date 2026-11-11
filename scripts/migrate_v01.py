"""Convert a V0.1 specification to a reviewable V0.2 draft, never a runtime snapshot."""
import argparse,json
from pathlib import Path
from app_v2.specification import ProductionSpec
from app_v2.common import canonical,atomic_write

def convert(old):
    if old.get('version')!='1.0':raise ValueError('输入须为 V0.1 ProductionSpec')
    spec={k:v for k,v in old.items() if k not in ('products','orders','version')};spec.update(version='2.0',products=[],orders=[],fixtures=[])
    for product in old['products']:
        materials={o['material'] for o in product['operations']}
        if len(materials)!=1:raise ValueError('同一零件的工序材料不一致，需人工核对')
        spec['products'].append({**product,'material':next(iter(materials)),'version':1,'family':product['id'],'blank_kg':1.,'finished_kg':.8,'rework_index':0,'reject_probability':0.,'operations':[{k:v for k,v in o.items() if k!='material'} for o in product['operations']]})
    products={p['id']:p for p in spec['products']};lots={}
    for o in old['orders']:
        mat=products[o['product']]['material'];lid='LEGACY_LOT_'+mat.upper();lots.setdefault(lid,dict(id=lid,material=mat,quantity=0,supplier='UNSPECIFIED'));lots[lid]['quantity']+=o['quantity']
        spec['orders'].append({**o,'batch_size':min(10,o['quantity']),'material_lot':lid})
    spec['material_lots']=list(lots.values());draft=ProductionSpec.model_validate(spec)
    return draft.model_dump(),['新建原料批次编号与供应商占位，需与实际原料核对','毛坯 1 kg、成品 0.8 kg 为待确认占位参数','无夹具约束，无换型时长，无质量不合格概率；需补齐实际参数','旧快照、运行状态、责任人和签名未迁移']

def main():
    p=argparse.ArgumentParser();p.add_argument('input',type=Path);p.add_argument('output',type=Path);args=p.parse_args()
    draft,gaps=convert(json.loads(args.input.read_text(encoding='utf-8')))
    atomic_write(args.output,canonical(draft));atomic_write(args.output.with_suffix('.migration.json'),canonical({'source':str(args.input),'status':'draft_requires_review','gaps':gaps}))
    print('已生成待审查草稿及迁移缺口清单，未发布或修改历史数据。')
if __name__=='__main__':main()
