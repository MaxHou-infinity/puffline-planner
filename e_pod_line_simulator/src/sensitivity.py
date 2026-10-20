"""以相同仿真种子和测量窗口比较末端良品与实际单位人工成本。"""

import copy
from concurrent.futures import CancelledError
from typing import Dict, List, Optional

from src.models import CollaborationType, ProductionLine, ProductionType
from src.simulation import SimulationEngine


def _check_supported(line, cancel_event) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise CancelledError()
    if line.production_type == ProductionType.LIQUID_FILLING:
        raise ValueError('液体批次采用配方批次模型，暂不支持工序人数/节拍敏感性和批量试算')


def _simulate(line, duration_hours, warmup_minutes, simulation_seed, cancel_event) -> Dict:
    _check_supported(line, cancel_event)
    engine = SimulationEngine(line)
    engine.random_seed = simulation_seed
    result = engine.run_sync(duration_hours, warmup_minutes, cancel_event=cancel_event)
    labor_cost = result.kpis['actual_labor_cost']
    unit_cost = labor_cost / result.total_output if result.total_output > 0 else None
    return {
        'total_output': result.total_output,
        'daily_output': round(result.kpis.get('daily_output', 0.0), 1),
        'unit_cost': unit_cost,
        'actual_unit_cost': unit_cost,
        'actual_labor_cost': labor_cost,
        'total_cost': labor_cost,
        'theoretical_unit_cost': result.kpis.get('unit_cost'),
        'theoretical_total_cost': result.kpis.get('total_cost'),
        'material_cost': result.kpis.get('material_cost', 0.0),
        'upph': result.kpis.get('upph', 0.0),
        'feasible': unit_cost is not None,
        'constraints': [] if unit_cost is not None else [
            '测量窗口内无末端合格产出，不能计算单位人工成本'
        ],
        'simulation_seed': simulation_seed,
    }


def run_sensitivity(
    line: ProductionLine,
    duration_hours: float = 8.0,
    warmup_minutes: float = 0.0,
    simulation_seed: int = 42,
    cancel_event=None,
) -> List[Dict]:
    """单变量试算；所有方案使用同一仿真种子，单位成本仅含人工。"""
    _check_supported(line, cancel_event)
    base = _simulate(
        copy.deepcopy(line), duration_hours, warmup_minutes, simulation_seed, cancel_event,
    )
    scenarios = [{
        **base, 'label': '基线', 'delta_output': 0, 'delta_unit_cost': 0.0,
        'delta_material_cost': 0.0, 'apply': None,
    }]
    bottleneck = line.find_bottleneck()
    if bottleneck is None:
        return scenarios
    variants = []

    def station_variant(label, attr, value):
        variants.append((label, {'station_id': bottleneck.id, 'attr': attr, 'value': value}))

    if bottleneck.machine_takt:
        if bottleneck.machine_takt > 1:
            station_variant('机台节拍-1秒', 'machine_takt', bottleneck.machine_takt - 1.0)
        station_variant('增加1台机台', 'machine_count', bottleneck.machine_count + 1)
    elif bottleneck.collaboration_type == CollaborationType.PARALLEL:
        station_variant('瓶颈+1人', 'worker_count', bottleneck.worker_count + 1)
        station_variant('瓶颈+2人', 'worker_count', bottleneck.worker_count + 2)
        station_variant('自动化替代10%', 'process_time', round(bottleneck.process_time * .9, 3))
        if bottleneck.worker_count > 1:
            station_variant('瓶颈-1人', 'worker_count', bottleneck.worker_count - 1)
    station_variant('瓶颈OEE+5%', 'oee', min(1.0, round(bottleneck.oee + .05, 4)))
    if line.materials:
        for label, factor in [('原料价格+10%', 1.1), ('原料价格-10%', .9)]:
            variants.append((label, {'material_prices': {
                material.name: round(material.unit_cost * factor, 4)
                for material in line.materials
            }}))

    for label, apply_info in variants:
        _check_supported(line, cancel_event)
        clone = copy.deepcopy(line)
        if 'station_id' in apply_info:
            setattr(clone.get_station(apply_info['station_id']),
                    apply_info['attr'], apply_info['value'])
        else:
            for material in clone.materials:
                material.unit_cost = apply_info['material_prices'][material.name]
        row = _simulate(clone, duration_hours, warmup_minutes, simulation_seed, cancel_event)
        delta_cost = (
            round(row['unit_cost'] - base['unit_cost'], 4)
            if row['unit_cost'] is not None and base['unit_cost'] is not None else None
        )
        scenarios.append({
            **row, 'label': label,
            'delta_output': row['total_output'] - base['total_output'],
            'delta_unit_cost': delta_cost,
            'delta_material_cost': round(row['material_cost'] - base['material_cost'], 4),
            'apply': apply_info,
        })
    return scenarios


def run_sweep(
    line: ProductionLine,
    param: str,
    values: List[float],
    station_id: Optional[str] = None,
    duration_hours: float = 8.0,
    warmup_minutes: float = 0.0,
    simulation_seed: int = 42,
    cancel_event=None,
) -> List[Dict]:
    """批量单变量试算，同窗口人工费用除以末端合格产出。"""
    _check_supported(line, cancel_event)
    if param not in ('shift_hours', 'worker_count', 'machine_count', 'machine_takt', 'oee'):
        raise ValueError(f'不支持的批量试算参数：{param}')
    target = line.get_station(station_id) if station_id else line.find_bottleneck()
    if param != 'shift_hours' and target is None:
        raise ValueError('未找到要试算的工序')
    rows = []
    for value in values:
        _check_supported(line, cancel_event)
        if value <= 0 or (param == 'oee' and value > 1):
            raise ValueError(f'{param} 的试算值不合理：{value}')
        if param in ('worker_count', 'machine_count') and int(value) != value:
            raise ValueError(f'{param} 必须为整数')
        clone = copy.deepcopy(line)
        if param == 'shift_hours':
            clone.shift_hours = value
        else:
            setattr(clone.get_station(target.id), param,
                    int(value) if param in ('worker_count', 'machine_count') else float(value))
        row = _simulate(clone, duration_hours, warmup_minutes, simulation_seed, cancel_event)
        rows.append({**row, 'label': f'{param}={value}'})
    return rows
