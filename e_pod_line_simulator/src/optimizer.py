"""
遗传算法辅助优化（V3.2 P2 / V3.3 增强）

遗传算法寻优：对工序人数 / 缓冲区容量 / 机台节拍搜索，输出 TOP 方案。
- V3.3：初始种群注入当前配置、工序锁定、实际单位成本口径、进度回调。
"""

import copy
import math
import random
from concurrent.futures import CancelledError
from typing import Callable, Dict, List, Optional

from src.models import CollaborationType, ProductionLine, ProductionType
from src.simulation import SimulationEngine

GENE_WORKER = "worker_count"
GENE_BUFFER = "buffer_capacity"
GENE_TAKT = "machine_takt"


def _gene_bounds(station) -> Dict[str, tuple]:
    fixed_team = station.machine_takt or (
        station.collaboration_type == CollaborationType.COLLABORATIVE
    )
    bounds = {
        GENE_WORKER: (
            (station.worker_count, station.worker_count) if fixed_team else (1, 10)
        ),
        GENE_BUFFER: (10, 500),
    }
    if station.machine_takt:
        bounds[GENE_TAKT] = (
            min(0.5, station.machine_takt), max(0.5, round(station.machine_takt * 1.5, 2))
        )
    return bounds


def _current_individual(line: ProductionLine) -> List[float]:
    individual: List[float] = []
    for station in line.stations:
        individual.append(float(station.worker_count))
        individual.append(float(station.buffer_capacity))
        if station.machine_takt:
            individual.append(float(station.machine_takt))
    return individual


def _gene_specs(line: ProductionLine) -> List[tuple]:
    """返回 [(station_id, gene_key, lo, hi)]，与个体编码顺序一致"""
    specs: List[tuple] = []
    for station in line.stations:
        for key, (lo, hi) in _gene_bounds(station).items():
            specs.append((station.id, key, lo, hi))
    return specs


def _locked_mask(line: ProductionLine, locked_stations: List[str]) -> List[bool]:
    locked = set(locked_stations or [])
    return [sid in locked for sid, _, _, _ in _gene_specs(line)]


def _random_individual(
    line: ProductionLine,
    rng: random.Random,
    locked: List[str],
) -> List[float]:
    mask = _locked_mask(line, locked)
    current = _current_individual(line)
    individual: List[float] = []
    for index, (_, key, lo, hi) in enumerate(_gene_specs(line)):
        if mask[index]:
            individual.append(current[index])
            continue
        if key == GENE_TAKT:
            individual.append(round(rng.uniform(lo, hi), 2))
        else:
            individual.append(float(rng.randint(int(lo), int(hi))))
    return individual


def _apply_genes(line: ProductionLine, individual: List[float]) -> None:
    index = 0
    for station in line.stations:
        for key in _gene_bounds(station):
            value = individual[index]
            index += 1
            if key == GENE_WORKER:
                station.worker_count = int(round(value))
            elif key == GENE_BUFFER:
                station.buffer_capacity = int(round(value))
            elif key == GENE_TAKT:
                station.machine_takt = value


def _params_summary(line: ProductionLine, individual: List[float]) -> Dict:
    clone = copy.deepcopy(line)
    _apply_genes(clone, individual)
    summary: Dict[str, Dict] = {}
    for station in clone.stations:
        entry = {
            'name': station.name,
            GENE_WORKER: station.worker_count,
            GENE_BUFFER: station.buffer_capacity,
        }
        if station.machine_takt:
            entry[GENE_TAKT] = station.machine_takt
        if station.machine_takt:
            entry['machine_count'] = station.machine_count
        summary[station.id] = entry
    return summary


def _params_text(params: Dict) -> str:
    parts = []
    for station_id, entry in params.items():
        name = entry.get('name', station_id)
        parts.append(
            f"{name}:{entry['worker_count']}人/{entry['buffer_capacity']}缓冲"
        )
    return "；".join(parts)


def _crossover(a: List[float], b: List[float], rng: random.Random) -> List[float]:
    point = rng.randint(1, len(a) - 1) if len(a) > 1 else 0
    return a[:point] + b[point:]


def _mutate(
    line: ProductionLine,
    individual: List[float],
    rng: random.Random,
    locked: List[str],
    rate: float = 0.2,
) -> List[float]:
    mask = _locked_mask(line, locked)
    result = list(individual)
    for index, (_, key, lo, hi) in enumerate(_gene_specs(line)):
        if mask[index] or rng.random() >= rate:
            continue
        if key == GENE_TAKT:
            result[index] = round(rng.uniform(lo, hi), 2)
        else:
            result[index] = float(rng.randint(int(lo), int(hi)))
    return result


def _evaluate(
    line: ProductionLine,
    individual: List[float],
    objective: str,
    duration_hours: float,
    warmup_minutes: float,
    simulation_seed: int = 42,
    cancel_event=None,
) -> Optional[Dict]:
    try:
        clone = copy.deepcopy(line)
        _apply_genes(clone, individual)
        engine = SimulationEngine(clone)
        engine.random_seed = simulation_seed
        result = engine.run_sync(duration_hours, warmup_minutes, cancel_event=cancel_event)
        total_cost = result.kpis['actual_labor_cost']
        actual_unit_cost = (
            total_cost / result.total_output if result.total_output > 0 else None
        )
        feasible = actual_unit_cost is not None and math.isfinite(actual_unit_cost)
        score = (
            result.total_output if objective == 'output' else -actual_unit_cost
        ) if feasible else -math.inf
        return {
            "score": score,
            "total_output": result.total_output,
            "unit_cost": actual_unit_cost,
            "actual_unit_cost": actual_unit_cost,
            "total_cost": total_cost,
            "actual_labor_cost": total_cost,
            "theoretical_unit_cost": result.kpis.get('unit_cost'),
            "theoretical_total_cost": result.kpis.get('total_cost'),
            "material_cost": result.kpis.get('material_cost', 0.0),
            "feasible": feasible,
            "constraints": [] if feasible else ['测量窗口内无末端合格产出，不能计算单位人工成本'],
            "simulation_seed": simulation_seed,
            "params": _params_summary(clone, individual),
            "individual": individual,
        }
    except CancelledError:
        raise
    except Exception:
        return None


def optimize(
    line: ProductionLine,
    objective: str = "unit_cost",
    generations: int = 8,
    population: int = 6,
    top_n: int = 3,
    duration_hours: float = 8.0,
    warmup_minutes: float = 0.0,
    seed: int = 42,
    locked_stations: Optional[List[str]] = None,
    include_baseline: bool = True,
    progress_callback: Optional[Callable[[int, int, float], None]] = None,
    simulation_seed: int = 42,
    search_seed: Optional[int] = None,
    cancel_event=None,
) -> List[Dict]:
    """
    运行遗传算法，按末端良品或实际单位人工成本返回可行 TOP 方案。

    locked_stations: 锁定的工序 ID，其基因固定为当前值；
    include_baseline: 初始种群注入当前产线配置；
    progress_callback: (当前代, 总代数, 当前最优分数)。
    seed/search_seed: 搜索随机种子（search_seed 优先）；simulation_seed: 所有候选共用的仿真种子。
    params 以工序 ID 为键；零良品不构成低成本方案，全不可行时抛 ValueError。
    cancel_event: 可选 threading.Event，取消时抛 CancelledError。
    """
    if line.production_type == ProductionType.LIQUID_FILLING:
        raise ValueError('烟油采用配方批次模型，暂不支持工序人数/节拍遗传优化')
    if objective not in ('unit_cost', 'output'):
        raise ValueError('优化目标必须为 unit_cost 或 output')
    if population < 1 or generations < 1 or top_n < 1:
        raise ValueError('种群、代数和返回方案数必须为正整数')
    if duration_hours <= 0 or not 0 <= warmup_minutes < duration_hours * 60:
        raise ValueError('仿真时长必须为正，预热必须小于总时长')
    if not line.stations:
        raise ValueError('请先配置工序后再优化')
    resolved_search_seed = seed if search_seed is None else search_seed
    rng = random.Random(resolved_search_seed)

    def check_cancelled():
        if cancel_event is not None and cancel_event.is_set():
            raise CancelledError()

    check_cancelled()
    locked = locked_stations or []
    individuals = [_random_individual(line, rng, locked) for _ in range(population)]
    if include_baseline and individuals:
        individuals[0] = _current_individual(line)

    evaluated = [
        e for e in (
            _evaluate(
                line, ind, objective, duration_hours, warmup_minutes,
                simulation_seed, cancel_event,
            )
            for ind in individuals
        )
        if e is not None
    ]

    total_generations = max(0, generations - 1)
    for generation in range(1, total_generations + 1):
        check_cancelled()
        if not evaluated:
            break
        parents = sorted(evaluated, key=lambda e: e["score"], reverse=True)
        next_gen: List[Dict] = []
        for _ in range(population):
            check_cancelled()
            pool = parents[: max(2, len(parents) // 2)]
            a = rng.choice(pool)
            b = rng.choice(pool)
            child = _crossover(a["individual"], b["individual"], rng)
            child = _mutate(line, child, rng, locked)
            evaluated_child = _evaluate(
                line, child, objective, duration_hours, warmup_minutes,
                simulation_seed, cancel_event,
            )
            if evaluated_child is not None:
                next_gen.append(evaluated_child)
        evaluated = sorted(
            evaluated + next_gen, key=lambda e: e['score'], reverse=True
        )[:population]
        if progress_callback:
            progress_callback(generation, total_generations, evaluated[0]["score"])

    evaluated.sort(key=lambda e: e["score"], reverse=True)
    top = []
    seen = set()
    for entry in evaluated:
        signature = tuple(entry['individual'])
        if entry['feasible'] and signature not in seen:
            top.append(entry)
            seen.add(signature)
        if len(top) == top_n:
            break
    if not top:
        raise ValueError('未找到有末端合格产出的可行方案，请检查缺料、质量或仿真时长')
    for rank, entry in enumerate(top, 1):
        entry["rank"] = rank
        entry["objective"] = objective
        entry['search_seed'] = resolved_search_seed
    return top
