"""
人力规划模块（V3.2 P0）

面向 HRBP/HR 的纯计算层：目标产量 → 岗位/人数 → 排班 → 成本 →
招聘缺口 → 爬坡达产。不依赖仿真引擎，便于独立测试与报告。

说明：人力需求为规划估算，不构成用工承诺；协同工序加人不提升产能。
"""

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from src.models import CollaborationType, ProductionLine, ProductionType


@dataclass
class ShiftPlan:
    """班次计划（V3.2）"""
    shifts_per_day: int = 1
    shift_hours: float = 8.0
    break_minutes: int = 60
    overtime_hours_per_shift: float = 0.0

    def effective_hours_per_day(self) -> float:
        """每日有效工时（小时）= 班次数 ×（班次时长 − 休息 + 加班）"""
        per_shift = (
            self.shift_hours
            - self.break_minutes / 60.0
            + self.overtime_hours_per_shift
        )
        return max(0.0, self.shifts_per_day * per_shift)


@dataclass
class LaborCostConfig:
    """人力成本参数（V3.2）"""
    base_hourly_wage: float = 20.0
    overtime_multiplier: float = 1.5
    social_insurance_rate: float = 0.30
    recruitment_cost_per_head: float = 0.0
    training_cost_per_head: float = 0.0
    absence_rate: float = 0.0
    monthly_turnover_rate: float = 0.0


@dataclass
class LearningCurveConfig:
    """新员工爬坡曲线（V3.2）"""
    ramp_days: int = 90
    start_efficiency: float = 0.6
    target_efficiency: float = 1.0

    def efficiency_at(self, day: int) -> float:
        """第 day 天（1 起）的综合效率，线性爬坡至目标值"""
        if self.ramp_days <= 0:
            return self.target_efficiency
        progress = min(max(day, 0), self.ramp_days) / self.ramp_days
        return (
            self.start_efficiency
            + (self.target_efficiency - self.start_efficiency) * progress
        )


def _per_head_capacity(station) -> float:
    """单人的可支撑产能（单位/h）"""
    capacity = station.get_capacity()
    if station.worker_count <= 0:
        return capacity
    if station.collaboration_type == CollaborationType.COLLABORATIVE:
        # 协同：加人不提升产能，按整线实际配人估算
        return capacity
    return capacity / station.worker_count


def _validate_plan(line, daily_target, shift_plan) -> None:
    if line.production_type == ProductionType.LIQUID_FILLING:
        raise ValueError('液体批次采用配方批次模型，暂不支持按工序人数反推目标编制')
    if daily_target <= 0 or not math.isfinite(daily_target):
        raise ValueError('目标日产量必须为正的有限数值')
    if (shift_plan.shifts_per_day < 1
            or int(shift_plan.shifts_per_day) != shift_plan.shifts_per_day
            or shift_plan.shift_hours <= 0 or shift_plan.break_minutes < 0
            or shift_plan.overtime_hours_per_shift < 0
            or shift_plan.break_minutes >= shift_plan.shift_hours * 60
            or shift_plan.shifts_per_day * (
                shift_plan.shift_hours + shift_plan.overtime_hours_per_shift
            ) > 24):
        raise ValueError('班次必须为正整数，工时/休息合理且每日排班不超过24小时')


def _planning_capacity(station, shift_plan) -> float:
    """把每班实际停机折算为有效生产小时内可支撑的小时能力。"""
    hours = shift_plan.effective_hours_per_day() / shift_plan.shifts_per_day
    downtime = ((station.changeover_time or 0) + (station.clean_time_minutes or 0)) / 60
    return station.get_capacity() * max(0.0, hours - downtime) / hours


def _per_shift_roles(line, by_station) -> Dict[str, int]:
    roles: Dict[str, int] = {}
    for station in line.stations:
        role = station.job_role.value if station.job_role else 'general'
        roles[role] = roles.get(role, 0) + by_station[station.id]
    for role, count in line.labor_config.items():
        if role not in roles:
            roles[role] = count
    return roles


def required_headcount_by_station(
    line: ProductionLine,
    daily_target: float,
    shift_plan: ShiftPlan,
) -> Dict[str, int]:
    """返回每班岗位数；机台/协同团队保持配置，不把加人当作扩设备。"""
    _validate_plan(line, daily_target, shift_plan)
    daily_hours = shift_plan.effective_hours_per_day()
    required_hourly = daily_target / daily_hours if daily_hours > 0 else 0.0
    result: Dict[str, int] = {}
    for station in line.stations:
        capacity = _planning_capacity(station, shift_plan)
        per_head = capacity / station.worker_count if station.worker_count > 0 else 0
        if per_head <= 0:
            result[station.id] = station.worker_count
            continue
        needed = math.ceil(required_hourly / per_head)
        if station.machine_takt or station.collaboration_type == CollaborationType.COLLABORATIVE:
            needed = max(station.worker_count, 1)
        result[station.id] = max(1, needed)
    return result


def required_headcount_by_role(
    line: ProductionLine,
    daily_target: float,
    shift_plan: ShiftPlan,
) -> Dict[str, int]:
    """返回全日总编制：每班岗位（含支持岗）× 班次数。"""
    by_station = required_headcount_by_station(line, daily_target, shift_plan)
    return {
        role: count * shift_plan.shifts_per_day
        for role, count in _per_shift_roles(line, by_station).items()
    }


def current_headcount_by_role(line: ProductionLine) -> Dict[str, int]:
    """
    当前在岗（按工序工种 × 人数汇总，V3.3 修复）

    优先按工序实际配置统计；labor_config 中的支持工种（未出现在
    工序中的，如调香师/清洗工）一并计入。
    """
    by_role: Dict[str, int] = {}
    for station in line.stations:
        role = station.job_role.value if station.job_role else "general"
        by_role[role] = by_role.get(role, 0) + station.worker_count
    for role, count in line.labor_config.items():
        if role not in by_role:
            by_role[role] = count
    return by_role


def total_headcount(
    line: ProductionLine,
    daily_target: float,
    shift_plan: ShiftPlan,
) -> int:
    """目标产量下全日编制，包含支持岗和各班次独立人员。"""
    return sum(required_headcount_by_role(line, daily_target, shift_plan).values())


def calculate_labor_costs(
    line: ProductionLine,
    headcount: int,
    shift_plan: ShiftPlan,
    cost: LaborCostConfig,
    days_per_month: int = 26,
    daily_output: Optional[float] = None,
    headcount_by_role_per_shift: Optional[Dict[str, int]] = None,
) -> Dict[str, float]:
    """headcount 为全日编制；每人每天工作一个班，缺勤覆盖按每班工种取整。"""
    if (headcount < 0 or cost.base_hourly_wage < 0
            or cost.social_insurance_rate < 0 or not 0 <= cost.absence_rate < 1
            or not 0 <= cost.monthly_turnover_rate <= 1
            or cost.overtime_multiplier < 0 or days_per_month < 1):
        raise ValueError('人数、工资、福利、缺勤率或离职率参数不合理')
    base_pay_per_head_day = cost.base_hourly_wage * (
        shift_plan.shift_hours
        + shift_plan.overtime_hours_per_shift * cost.overtime_multiplier
    )
    gross_per_head_day = base_pay_per_head_day * (1 + cost.social_insurance_rate)

    coverage = 1.0
    if 0 <= cost.absence_rate < 1:
        coverage = 1.0 / (1.0 - cost.absence_rate)
    covered_by_role = {}
    covered_by_role_per_shift = {}
    if headcount_by_role_per_shift is not None:
        covered_by_role_per_shift = {
            role: math.ceil(count * coverage)
            for role, count in headcount_by_role_per_shift.items()
        }
        covered_by_role = {
            role: count * shift_plan.shifts_per_day
            for role, count in covered_by_role_per_shift.items()
        }
        covered_headcount = sum(covered_by_role.values())
    else:
        covered_headcount = math.ceil(headcount * coverage)

    daily_labor_cost = gross_per_head_day * covered_headcount
    monthly_labor_cost = daily_labor_cost * days_per_month

    replacements_per_month = math.ceil(covered_headcount * cost.monthly_turnover_rate)
    monthly_recruit_training = replacements_per_month * (
        cost.recruitment_cost_per_head + cost.training_cost_per_head
    )
    monthly_total = monthly_labor_cost + monthly_recruit_training

    if daily_output is None:
        daily_output = line.calculate_daily_output() * shift_plan.shifts_per_day
    return {
        'headcount': headcount,
        'headcount_per_shift': headcount / shift_plan.shifts_per_day,
        'covered_headcount': covered_headcount,
        'covered_headcount_per_shift': covered_headcount / shift_plan.shifts_per_day,
        'covered_headcount_by_role': covered_by_role,
        'covered_headcount_by_role_per_shift': covered_by_role_per_shift,
        'daily_labor_cost': round(daily_labor_cost, 2),
        'monthly_labor_cost': round(monthly_labor_cost, 2),
        'monthly_recruit_training': round(monthly_recruit_training, 2),
        'monthly_total': round(monthly_total, 2),
        'per_unit_labor_cost': (
            round(daily_labor_cost / daily_output, 4) if daily_output > 0 else None
        ),
    }


def weekly_hiring_gap(
    required: Dict[str, int],
    current: Dict[str, int],
    hiring_plan: List[Tuple[int, str, int]],
    weeks: int = 12,
) -> List[Dict]:
    """
    按周招聘缺口时间线（V3.3）

    gap = 截至该周末还缺的人数（累计口径）；
    new_gap = 本周相对上周的新增缺口（增量口径，避免“每周重复招人”误解）。
    """
    rows = []
    prev_total = 0
    for week in range(1, weeks + 1):
        gap: Dict[str, int] = {}
        for role, req in required.items():
            cur = current.get(role, 0)
            arrivals = sum(
                qty for (w, r, qty) in hiring_plan if r == role and w <= week
            )
            gap[role] = max(0, req - cur - arrivals)
        total_gap = sum(gap.values())
        new_gap = max(0, total_gap - prev_total)
        rows.append({
            'week': week,
            'gap': gap,
            'total_gap': total_gap,
            'new_gap': new_gap,
        })
        prev_total = total_gap
    return rows


def ramp_days_to_full(
    line: ProductionLine,
    daily_target: float,
    shift_plan: ShiftPlan,
    required: Dict[str, int],
    current: Dict[str, int],
    learning: LearningCurveConfig,
    hiring_plan: Optional[List[Tuple[int, str, int]]] = None,
) -> Optional[int]:
    """
    按爬坡曲线估算达产天数

    各工种独立校验，不允许富余装配工抵消 QC 缺口。None 表示计划内无法达产。
    hiring_plan=None 时仅估算缺口立即到岗后的培训期；传入列表则按实际到岗周计算。
    """
    if all(current.get(role, 0) >= count for role, count in required.items()):
        return 0
    plan = hiring_plan
    if plan is None:
        plan = [(1, role, max(0, count - current.get(role, 0)))
                for role, count in required.items()]
    arrivals = [((week - 1) * 7 + 1, role, count) for week, role, count in plan]
    horizon = max([day for day, _, _ in arrivals] or [1]) + max(0, learning.ramp_days)
    for day in range(1, horizon + 1):
        enough = all(
            current.get(role, 0) + sum(
                count * learning.efficiency_at(day - arrival + 1)
                for arrival, hired_role, count in arrivals
                if hired_role == role and arrival <= day
            ) >= needed - 1e-6
            for role, needed in required.items()
        )
        if enough:
            return day
    return None


def build_hr_summary(
    line: ProductionLine,
    daily_target: float,
    shift_plan: ShiftPlan,
    cost: LaborCostConfig,
    learning: LearningCurveConfig,
    current: Dict[str, int],
    hiring_plan: List[Tuple[int, str, int]],
    weeks: int = 12,
) -> Dict:
    """汇总 HR 人力规划结果（供报告与 GUI 使用）"""
    if any(count < 0 for count in current.values()):
        raise ValueError('当前在岗人数不得为负')
    if any(week < 1 or count < 0 for week, _, count in hiring_plan):
        raise ValueError('到岗周必须为正，计划人数不得为负')
    if (learning.ramp_days < 0 or learning.start_efficiency < 0
            or learning.target_efficiency <= 0):
        raise ValueError('爬坡天数和效率参数不合理')
    by_station = required_headcount_by_station(line, daily_target, shift_plan)
    per_shift_roles = _per_shift_roles(line, by_station)
    by_role = {role: count * shift_plan.shifts_per_day
               for role, count in per_shift_roles.items()}
    total = sum(by_role.values())
    constraints = []
    daily_hours = shift_plan.effective_hours_per_day()
    required_hourly = daily_target / daily_hours
    if not line.stations:
        constraints.append('未配置工序，无法校验目标产能')
    for station in line.stations:
        capacity = _planning_capacity(station, shift_plan)
        if capacity <= 0:
            constraints.append(f'{station.name} 无可用生产能力，请检查效率和停机时长')
        elif (station.machine_takt
              or station.collaboration_type == CollaborationType.COLLABORATIVE):
            if required_hourly > capacity + 1e-9:
                constraints.append(
                    f'{station.name} 设备/协同能力上限 {capacity:.2f}/小时，'
                    f'低于目标 {required_hourly:.2f}/小时；加人不能解除约束'
                )
    capacity_feasible = not constraints
    costs = calculate_labor_costs(
        line, total, shift_plan, cost,
        daily_output=daily_target if capacity_feasible else 0,
        headcount_by_role_per_shift=per_shift_roles,
    )
    # 缺勤覆盖先按每班/工种取整，再用于成本、招聘与达产的同一编制基准。
    # 原有 by_role / total 继续表示最低岗位编制，便于界面区分两种口径。
    covered_by_role = costs['covered_headcount_by_role']
    gap_rows = weekly_hiring_gap(covered_by_role, current, hiring_plan, weeks)
    days_to_full = ramp_days_to_full(
        line, daily_target, shift_plan, covered_by_role, current, learning, hiring_plan
    ) if capacity_feasible else None
    if capacity_feasible and days_to_full is None:
        constraints.append('现有工种与到岗/培训计划不足以覆盖目标编制（含缺勤），'
                           '请补足对应工种的招聘计划')
    station_derivation = []
    bottleneck = line.find_bottleneck()
    for station in line.stations:
        capacity = _planning_capacity(station, shift_plan)
        per_head = capacity / station.worker_count if station.worker_count > 0 else 0
        changeover_minutes = (station.changeover_time or 0) + (
            station.clean_time_minutes or 0
        )
        station_derivation.append({
            'name': station.name,
            'configured': station.worker_count,
            'process_time': station.process_time,
            'machine_takt': station.machine_takt,
            'machine_count': getattr(station, 'machine_count', 1),
            'oee': station.oee,
            'efficiency': station.efficiency,
            'changeover_minutes': changeover_minutes,
            'collaboration': station.collaboration_type.value,
            'capacity': round(capacity, 2),
            'per_head': round(per_head, 2),
            'needed': by_station.get(station.id, 1),
            'is_bottleneck': bottleneck is not None and station.id == bottleneck.id,
        })

    return {
        'daily_target': daily_target,
        'effective_hours_per_day': shift_plan.effective_hours_per_day(),
        'required_hourly': round(required_hourly, 3),
        'headcount_by_station': by_station,
        'headcount_by_role': by_role,
        'headcount_by_role_per_shift': per_shift_roles,
        'headcount_per_shift': sum(per_shift_roles.values()),
        'shifts_per_day': shift_plan.shifts_per_day,
        'total_headcount': total,
        'covered_headcount': costs['covered_headcount'],
        'covered_headcount_per_shift': costs['covered_headcount_per_shift'],
        'covered_headcount_by_role': dict(covered_by_role),
        'covered_headcount_by_role_per_shift': dict(
            costs['covered_headcount_by_role_per_shift']
        ),
        'current_headcount': dict(current),
        'current_total': sum(current.values()),
        'initial_gap': sum(max(0, count - current.get(role, 0))
                           for role, count in covered_by_role.items()),
        'one_time_hiring': sum(max(
            0, count - current.get(role, 0)
            - sum(qty for _, hired_role, qty in hiring_plan if hired_role == role)
        ) for role, count in covered_by_role.items()),
        'excess_headcount': sum(max(0, count - covered_by_role.get(role, 0))
                                for role, count in current.items()),
        'station_derivation': station_derivation,
        'costs': costs,
        'weekly_gap': gap_rows,
        'days_to_full': days_to_full,
        'feasible': not constraints,
        'capacity_feasible': capacity_feasible,
        'constraints': constraints,
        'ramp_assumption': '按每班各工种取整的缺勤覆盖编制校验招聘与达产；'
                           '计划到岗周第1天入职，新员工按日爬坡；未计划招聘不假定到岗',
    }
