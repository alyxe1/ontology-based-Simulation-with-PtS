"""Known plan and assumptions, never unobserved actual production events."""
from .contracts import ProductionSpec, ScenarioPatch

def demo_spec() -> ProductionSpec:
    def cap(operation, materials=("steel", "aluminium"), tolerance=100):
        return {"operation": operation, "materials": list(materials), "tolerance_um": tolerance}
    def resource(i, name, caps, limit=0):
        return {"id": i, "name": name, "capabilities": caps, "tool_limit": limit,
                "mes_id": "MES-"+i.replace("_", "-"), "opc_id": "PLC/Cell01/"+i}
    resources = [resource("SAW", "下料锯床", [cap("cut")]),
        resource("CNC01", "CNC 01 · 精密铣削", [cap("mill", tolerance=10)], 8),
        resource("CNC02", "CNC 02 · 通用铣削", [cap("mill", tolerance=25)], 18),
        resource("CNC03", "CNC 03 · 铝件精加工", [cap("mill", ("aluminium",), 10)], 20),
        resource("DEBURR", "去毛刺工作站", [cap("deburr")]),
        resource("CMM", "三坐标检测", [cap("inspect", tolerance=5)])]
    products = []
    for i, name, mat, tol, duration, preferred in [
        ("P1", "液压阀体", "steel", 10, 480, "CNC01"),
        ("P2", "传动支座", "steel", 25, 360, "CNC01"),
        ("P3", "铝合金壳体", "aluminium", 10, 420, "CNC01")]:
        ops = []
        for j, label, typ, seconds, machine, precision in [
            (1,"下料","cut",50,"SAW",100), (2,"铣削","mill",duration,preferred,tol),
            (3,"去毛刺","deburr",75,"DEBURR",100), (4,"检测","inspect",90,"CMM",tol)]:
            ops.append({"id": f"OP{j}", "name":label, "operation":typ,"material":mat,
                        "tolerance_um":precision,"duration_s":seconds,"preferred":machine})
        products.append({"id":i,"name":name,"operations":ops})
    orders = [{"id":f"WO{i+1:03}","product":f"P{i%3+1}","quantity":20,
               "due_s":[25000,18000,22000,98000,94000,100000][i],"release_s":0 if i<3 else 1200}
              for i in range(6)]
    return ProductionSpec(resources=resources, products=products, orders=orders)

def demo_patches():
    return [ScenarioPatch(name="维持 FIFO"),
            ScenarioPatch(name="兼容设备改派 + EDD", rule="EDD", reroute=True),
            ScenarioPatch(name="兼容设备改派 + 延长班次", rule="EDD", reroute=True, shift_hours=12)]
