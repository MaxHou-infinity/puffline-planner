"""合成模板数据；仅用于演示，参数须由现场测时校准。"""

ASSEMBLY_TEMPLATES = {'simple': [('零件装配', 25.0, 2, 'parallel'),
            ('焊接', 30.0, 3, 'parallel'),
            ('包装', 15.0, 2, 'parallel')],
 'standard': [('零件装配', 25.0, 2, 'parallel'),
              ('焊接', 30.0, 3, 'parallel'),
              ('功能测试', 20.0, 2, 'collaborative'),
              ('组装', 22.0, 2, 'parallel'),
              ('包装', 15.0, 2, 'parallel')],
 'complex': [('镭雕', 25.0, 5, 'parallel'),
             ('零件装配', 47.0, 1, 'parallel'),
             ('预压合', 47.0, 1, 'parallel'),
             ('底座压合', 48.0, 1, 'parallel'),
             ('固件烧录与测试', 45.0, 1, 'parallel'),
             ('外观检查', 232.0, 1, 'parallel'),
             ('组装', 22.0, 2, 'parallel'),
             ('包装', 15.0, 2, 'parallel')]}


def build_process_template(production_type):
    """构造通用流程演示；历史行业工厂函数继续可用。"""
    from src.models import (
        Batch, JobRole, ProductionLine, ProductionType, Recipe, Station, Tank,
    )
    if production_type == "liquid_filling":
        line = ProductionLine("通用液体批次演示", production_type=ProductionType.LIQUID_FILLING)
        line.recipes = [Recipe("配方A", batch_volume_l=100, ingredients={"原料A": 100})]
        line.batches = [Batch("B001", "配方A", 100)]
        line.tanks = [Tank("T01", "成品罐", 500)]
        line.stations = [Station("s01", "质量放行", 30, 1, job_role=JobRole.QC_TECHNICIAN)]
        return line
    line = ProductionLine("通用机台包装演示", production_type=ProductionType.POUCH_PACKAGING,
                          output_unit="件")
    for index, name in enumerate(("装盒", "封装", "在线检测"), 1):
        line.add_station(Station(f"s{index:02d}", name, 3, 1, machine_takt=3,
                                 job_role=JobRole.PACKAGING_OPERATOR))
    return line
