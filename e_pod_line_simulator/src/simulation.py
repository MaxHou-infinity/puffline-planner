"""
仿真引擎层 - 基于SimPy的离散事件仿真

这个文件包含所有仿真相关的逻辑，包括：
- SimulationEngine: 仿真引擎核心类
- 瓶颈识别算法
- 资源浪费检测算法
- WIP监控逻辑

SimPy简介：
SimPy是一个离散事件仿真库，用于模拟系统中的事件流。
在产线仿真中，每个"物料加工完成"就是一个事件。

核心概念：
- Environment: 仿真环境，管理仿真时钟和事件队列
- Process: 仿真进程，用生成器函数实现（yield）
- Resource: 资源（如工人），可以被多个进程竞争使用
- Store: 存储（如WIP缓冲区），可以存储物品

仿真流程：
1. 创建SimPy环境
2. 为每个工序创建工作进程
3. 创建监控进程（瓶颈、WIP）
4. 运行仿真直到指定时间
5. 收集结果并返回
"""

import random
import copy
import math
import uuid
from concurrent.futures import CancelledError
import simpy
from typing import List, Dict, Callable, Optional, Any
from queue import Queue, Empty
import threading
import time

from src.models import (
    BatchStatus,
    JobRole,
    ProductionLine,
    ProductionType,
    Station,
    Alert,
    SimulationState,
    SimulationResult,
    CollaborationType,
)


class SimulationEngine:
    """
    仿真引擎类 - 运行产线仿真并收集数据
    
    这是整个仿真系统的核心，负责：
    1. 初始化SimPy环境
    2. 启动各工序的仿真进程
    3. 监控瓶颈和WIP
    4. 收集仿真结果
    5. 通过回调函数通知GUI更新
    
    使用方式：
        engine = SimulationEngine(production_line)
        engine.set_callback(on_state_update)  # 设置更新回调
        engine.run(duration_hours=8, speed=16)  # 运行仿真
        results = engine.get_results()  # 获取结果
    """
    
    def __init__(self, production_line: ProductionLine):
        """
        初始化仿真引擎
        
        Args:
            production_line: 要仿真的产线对象
        """
        self.line = copy.deepcopy(production_line)  # 工作副本；运行不修改调用者配置
        self.env = None  # SimPy环境，在run()方法中创建
        
        # 仿真状态
        self.is_running = False  # 是否正在运行
        self.is_paused = False  # 是否暂停
        self.speed = 1  # 仿真速度倍数
        
        # 统计数据
        self.station_outputs: Dict[str, int] = {}  # 各工序产出统计
        self.station_wips: Dict[str, int] = {}  # 各工序WIP统计
        self.total_output = 0  # 总产出
        
        # SimPy资源（在run()中初始化）
        self.station_resources: Dict[str, simpy.Resource] = {}  # 工序资源（工人）
        self.buffers: Dict[str, simpy.Store] = {}  # WIP缓冲区
        
        # 回调函数
        self.state_callback: Optional[Callable[[SimulationState], None]] = None
        
        # 报警队列
        self.alert_queue: Queue = Queue()
        # 外部事件注入队列（如切换事件），跨线程安全
        self.event_queue: Queue = Queue()
        # 报警完整记录（供报告导出与测试断言）
        self.alert_log: List[Alert] = []
        # WIP 时间序列采样（供报告导出）
        self.wip_samples: List[Dict[str, Any]] = []
        # 切换事件记录（供报告导出与测试断言）
        self.changeover_events: List[Dict[str, Any]] = []
        # V1.3：批次/质量/清洗结果
        self.batch_results: List[Dict[str, Any]] = []
        self.quality_results: List[Dict[str, Any]] = []
        self.cleaning_events: List[Dict[str, Any]] = []
        self._last_batch_recipe: Optional[str] = None
        self.random_seed: Optional[int] = 0
        # P0 报警去重状态
        self._blockage_state: Dict[str, str] = {}   # station_id -> warning/critical
        self._blockage_since: Dict[str, float] = {}
        self._blockage_full_since: Dict[str, float] = {}
        self._predict_alerted: set = set()
        # P1 工序级时间统计
        self.station_running_seconds: Dict[str, float] = {}
        self.station_starved_seconds: Dict[str, float] = {}
        self.station_blocked_seconds: Dict[str, float] = {}
        self._starvation_alerted: set = set()
        self._blocked_alerted: set = set()
        # V3.2：罐容约束（批次满罐等待去重）
        self._tank_full_alerted: set = set()
        # V3.2：周期性 CIP 计时
        self._last_clean_hour: float = 0.0
        # V3.2：原料与库存
        self.material_events: List[Dict[str, Any]] = []
        self._material_alerted: set = set()
        self._inventory: Dict[str, float] = {}
        self.duration_seconds = 0.0
        self.warmup_seconds = 0.0
        self.run_id = ""
        self.config_revision = ""
        self.status = "idle"
        self.error_message = ""
        self._cancel_event = None
        self._sim_thread = None
        self._stop_event = threading.Event()
        self._time_intervals = []
        self._active_intervals = {}
        self._station_attempts = {}
        self._station_good = {}
        self._station_scrap = {}
        self._changeover_until = {}
        self._batch_runtime = {}
        self._tank_drained_quantity = 0.0
        self._initial_tank_levels = {tank.id: tank.current_level_l for tank in self.line.tanks}
    
    def set_callback(self, callback: Callable[[SimulationState], None]) -> None:
        """
        设置状态更新回调函数
        
        当仿真状态发生变化时，会调用这个函数通知GUI更新
        
        Args:
            callback: 回调函数，接收SimulationState参数
        """
        self.state_callback = callback

    def _emit_alert(self, alert: Alert) -> None:
        """记录报警：同时写入完整记录列表与 GUI 消费队列"""
        self.alert_log.append(alert)
        self.alert_queue.put(alert)

    def trigger_changeover(self, station_id: str, minutes: int = 45) -> bool:
        """
        触发指定工序的切换（口味更换）停机事件

        Args:
            station_id: 工序 ID
            minutes: 切换停机时长（分钟），默认 45

        Returns:
            bool: 是否成功触发
        """
        if minutes <= 0:
            raise ValueError("切换时长必须大于0分钟")

        station = self.line.get_station(station_id)
        if station is None:
            raise ValueError(f"工序不存在：{station_id}")

        requested_time = self.env.now if self.env else 0.0
        record = {
            'requested_time': requested_time,
            'start_time': None,
            'station_id': station_id,
            'station_name': station.name,
            'minutes': int(minutes),
            'status': 'queued',
        }
        event = {
            'type': 'changeover',
            'station_id': station_id,
            'minutes': int(minutes),
            'record': record,
        }
        self.event_queue.put(event)

        self.changeover_events.append(record)
        self._emit_alert(Alert(
            alert_type='changeover',
            severity='info',
            station_id=station_id,
            message=f"{station.name}已申请切换，当前件完成后停机{int(minutes)}分钟",
            suggestion="等待当前件完成，切换完成后自动恢复生产",
            timestamp_minutes=requested_time / 60.0,
        ))
        return True

    def _record_changeover(self, event, start_time):
        """Record actual start/end separately from the user's request time."""
        if start_time > self.env.now:
            yield self.env.timeout(start_time - self.env.now)
        record = event['record']
        record.update(time=self.env.now, start_time=self.env.now, status='started')
        self._emit_alert(Alert(
            alert_type='changeover', severity='info', station_id=event['station_id'],
            message=f"{record['station_name']}开始切换，停机{event['minutes']}分钟",
            suggestion="切换完成后自动恢复生产",
            timestamp_minutes=self.env.now / 60.0,
        ))
        yield self.env.timeout(event['minutes'] * 60)
        record.update(end_time=self.env.now, status='completed')

    def _drain_events(self, station_id: str) -> List[Dict[str, Any]]:
        """
        从事件队列取出属于指定工序的切换事件，其余事件放回队列

        SimPy 进程为单线程执行，此方法在工序进程内调用是安全的；
        事件队列本身由 queue.Queue 保证跨线程安全。
        """
        events = []
        others = []
        while True:
            try:
                event = self.event_queue.get_nowait()
            except Empty:
                break
            if event.get('type') == 'changeover' and event.get('station_id') == station_id:
                events.append(event)
            else:
                others.append(event)
        for event in others:
            self.event_queue.put(event)
        return events

    def _bottleneck_suggestion(self, station) -> str:
        """按工序类型生成瓶颈建议（P0）"""
        if station.machine_takt:
            return (
                f"建议增加{station.name}的机台数或降低机台节拍"
                f"（当前节拍 {station.machine_takt:.1f} 秒）"
            )
        if station.collaboration_type == CollaborationType.COLLABORATIVE:
            return (
                f"{station.name}为协同工序：加人不会提升产能，"
                f"建议提升 OEE/工艺节拍或评估改为并联"
            )
        return f"建议增加{station.name}的并联工人数量或提升OEE"

    def _wip_suggestion(self, station, station_index: int) -> str:
        """按失衡方向生成 WIP 建议（P0）"""
        downstream = (
            self.line.stations[station_index + 1]
            if station_index < len(self.line.stations) - 1
            else None
        )
        if downstream and downstream.get_capacity() < station.get_capacity():
            return (
                f"本工序产能高于下游「{downstream.name}」"
                f"（{downstream.get_capacity():.0f}），输入堆积往往由下游堵塞停线引起；"
                f"建议提升「{downstream.name}」产能或消除下游瓶颈"
            )
        if station_index > 0:
            upstream = self.line.stations[station_index - 1]
            up_cap = upstream.get_capacity()
            st_cap = station.get_capacity()
            if up_cap > st_cap:
                return (
                    f"上游「{upstream.name}」产能（{up_cap:.0f}）高于本工序"
                    f"（{st_cap:.0f}），建议提升{station.name}产能"
                    f"（节拍/OEE/设备）或限制上游投料；扩大缓冲区仅缓解不治本"
                )
            return f"建议平衡「{upstream.name}」与{station.name}的节拍或增加缓存"
        return f"建议提升{station.name}产能或扩大缓冲区"

    def _tank_total_available(self) -> float:
        """全部储罐的剩余可用容量（千克）"""
        return sum(t.available_capacity() for t in self.line.tanks)

    def _distribute_to_tanks(self, volume: float) -> float:
        """把产出的液体分配到储罐（按序填充），返回实际注入量"""
        remaining = volume
        for tank in self.line.tanks:
            if remaining <= 0:
                break
            take = min(remaining, tank.available_capacity())
            if take > 0:
                tank.add_liquid(take)
                remaining -= take
        return volume - remaining

    def _withdraw_from_tanks(self, volume: float) -> float:
        """从储罐取液（优先取液位最高的罐），返回实际取走量"""
        remaining = volume
        for tank in sorted(
            self.line.tanks, key=lambda t: t.current_level_l, reverse=True
        ):
            if remaining <= 0:
                break
            remaining -= tank.withdraw(remaining)
        return volume - remaining

    def _tank_drain_process(self) -> simpy.events.Process:
        """按配方灌装速率消耗放行罐，工序人数不能改变液体批次能力。

        这是批次/储罐近似模型，不是逐工序液体输送或设备排程模型。
        """
        if self.line.production_type != ProductionType.LIQUID_FILLING or not self.line.recipes:
            return
        while True:
            yield self.env.process(self._working_timeout(60))
            recipe = next((recipe for recipe in self.line.recipes
                           if recipe.name == self._last_batch_recipe), self.line.recipes[0])
            self._tank_drained_quantity += self._withdraw_from_tanks(
                recipe.filling_rate_l_per_h / 60.0
            )

    def _run_clean(
        self, reason: str, recipe_name: str, minutes: Optional[float] = None,
    ) -> simpy.events.Process:
        """
        执行一次清洗（配方切换 / 周期性 CIP）

        reason: recipe_change | periodic
        """
        recipe = next(
            (r for r in self.line.recipes if r.name == recipe_name), None
        )
        duration = minutes if minutes is not None else (
            recipe.clean_time_min if recipe and recipe.clean_time_min > 0 else 30.0
        )
        clean_start = self.env.now
        yield self.env.process(self._working_timeout(duration * 60, kind="cleaning"))
        self.cleaning_events.append({
            'time': clean_start,
            'recipe_from': self._last_batch_recipe,
            'recipe_to': recipe_name,
            'clean_min': duration,
            'reason': reason,
        })
        self._last_clean_hour = self.env.now / 3600.0

    def _batch_sequence_process(self) -> simpy.events.Process:
        """
        批次排产序列（V3.2）

        批次按列表顺序执行（前一批放行后下一批开始），并按配置在
        批间执行周期性 CIP。
        """
        for index, batch in enumerate(self.line.batches):
            need_periodic = False
            if (
                index > 0
                and self.line.cip_interval_batches > 0
                and index % self.line.cip_interval_batches == 0
            ):
                need_periodic = True
            if (
                self.line.cip_interval_hours > 0
                and self.env.now / 3600.0 - self._last_clean_hour
                >= self.line.cip_interval_hours - 1e-9
            ):
                need_periodic = True
            yield self.env.process(self._batch_process(batch, periodic_clean=need_periodic))

    def _material_arrival_process(self) -> simpy.events.Process:
        """按到货计划补充原料库存（V3.2）"""
        for arrival in sorted(
            self.line.material_arrivals, key=lambda a: a.time_minutes
        ):
            wait_minutes = arrival.time_minutes - self.env.now / 60.0
            if wait_minutes > 0:
                yield self.env.timeout(wait_minutes * 60)
            self._inventory[arrival.material] = (
                self._inventory.get(arrival.material, 0.0) + arrival.quantity
            )
            self.material_events.append({
                'time': self.env.now,
                'type': 'arrival',
                'material': arrival.material,
                'quantity': arrival.quantity,
            })

    def _consume_batch_materials(
        self, recipe, batch
    ) -> simpy.events.Process:
        """
        批次投料（V3.2）：按配方原料 × 批次量/配方基准量 消耗库存；
        库存不足时等待到货并报警，缺料期间批次阻塞。
        """
        scale = (
            batch.quantity_l / recipe.batch_volume_l
            if recipe.batch_volume_l > 0
            else 1.0
        )
        required = {
            name: round(qty * scale, 6)
            for name, qty in recipe.ingredients.items()
        }

        def _missing() -> Dict[str, float]:
            return {
                m: q for m, q in required.items()
                if self._inventory.get(m, 0.0) + 1e-9 < q
            }

        missing = _missing()
        while missing:
            for material in missing:
                key = (batch.id, material)
                if key not in self._material_alerted:
                    self._material_alerted.add(key)
                    self._emit_alert(Alert(
                        alert_type="material_shortage",
                        severity="WARNING",
                        station_id="",
                        message=(
                            f"批次 {batch.id} 缺料：{material} 缺 "
                            f"{missing[material]:.1f}（库存 "
                            f"{self._inventory.get(material, 0.0):.1f}）"
                        ),
                        suggestion="安排原料到货或调整批次投料计划",
                        timestamp_minutes=round(self.env.now / 60.0, 1),
                    ))
            yield self.env.timeout(60)
            missing = _missing()

        for material, quantity in required.items():
            self._inventory[material] -= quantity
            self.material_events.append({
                'time': self.env.now,
                'type': 'consume',
                'batch_id': batch.id,
                'material': material,
                'quantity': quantity,
            })

    def _consume_station_materials(self, station) -> simpy.events.Process:
        """
        工序级 BOM 投料（V3.2）：每件消耗组件；缺料时等待并报警。
        """
        required = dict(station.bom)

        def _missing() -> Dict[str, float]:
            return {
                m: q for m, q in required.items()
                if self._inventory.get(m, 0.0) + 1e-9 < q
            }

        missing = _missing()
        while missing:
            for material in missing:
                key = (station.id, material)
                if key not in self._material_alerted:
                    self._material_alerted.add(key)
                    self._emit_alert(Alert(
                        alert_type="material_shortage",
                        severity="WARNING",
                        station_id=station.id,
                        message=(
                            f"{station.name} 缺料：{material} 缺 "
                            f"{missing[material]:.1f}（库存 "
                            f"{self._inventory.get(material, 0.0):.1f}）"
                        ),
                        suggestion="安排组件到货或补充库存",
                        timestamp_minutes=round(self.env.now / 60.0, 1),
                    ))
            yield self.env.timeout(60)
            missing = _missing()

        for material, quantity in required.items():
            self._inventory[material] -= quantity
            self.material_events.append({
                'time': self.env.now,
                'type': 'consume',
                'station_id': station.id,
                'material': material,
                'quantity': quantity,
            })

    def _batch_process(self, batch, periodic_clean=False) -> simpy.events.Process:
        """
        批次过程（V1.3 烟油灌装）

        流程：调配 → 陈化 → 灌装 → QC → 放行；罐液位同步更新。
        """
        recipe = next((r for r in self.line.recipes if r.name == batch.recipe_name), None)
        if recipe is None:
            raise ValueError("批次引用的配方不存在：" + batch.recipe_name)

        # 同一批间清洗同时满足换配方与周期CIP，取时长最大值，不能覆盖矩阵。
        previous = self._last_batch_recipe
        recipe_change = previous is not None and previous != recipe.name
        clean_min = 0.0
        if recipe_change:
            clean_min = self.line.changeover_matrix.get(previous, {}).get(
                recipe.name, recipe.clean_time_min
            )
        if periodic_clean:
            clean_min = max(clean_min, recipe.clean_time_min or 30.0)
        if clean_min > 0:
            reason = 'recipe_change' if recipe_change else 'periodic'
            before = len(self.cleaning_events)
            yield self.env.process(self._run_clean(reason, recipe.name, clean_min))
            if periodic_clean and len(self.cleaning_events) > before:
                self.cleaning_events[-1]['periodic'] = True
        self._last_batch_recipe = recipe.name

        self._batch_runtime[batch.id] = {
            'input_quantity': 0.0, 'produced_good_quantity': 0.0,
            'pending_tank_quantity': 0.0, 'stored_quantity': 0.0,
            'stage': 'waiting_materials',
        }
        runtime = self._batch_runtime[batch.id]
        # 投料：库存不足时等待到货（V3.2；未配置原料时保持原行为）
        if self.line.materials or self.line.inventory:
            yield self.env.process(self._consume_batch_materials(recipe, batch))

        runtime['input_quantity'] = batch.quantity_l
        runtime['stage'] = 'mixing'
        batch.status = BatchStatus.MIXING
        start_time = self.env.now
        batch.start_time = start_time
        yield self.env.process(self._working_timeout(recipe.mixing_time_min * 60))

        if recipe.aging_time_min > 0:
            batch.status = BatchStatus.AGING
            runtime['stage'] = 'aging'
            yield self.env.timeout(recipe.aging_time_min * 60)

        batch.status = BatchStatus.FILLING
        runtime['stage'] = 'filling'
        fill_seconds = batch.quantity_l / recipe.filling_rate_l_per_h * 3600
        yield self.env.process(self._working_timeout(fill_seconds))

        batch.status = BatchStatus.QC
        runtime['stage'] = 'qc'
        yield self.env.process(self._working_timeout(recipe.qc_time_min * 60))

        yielded_l = batch.quantity_l * recipe.yield_rate

        # 质量门：QC 化验按抽检比例与缺陷率判定
        pass_rate = 1.0
        qc_station = next(
            (s for s in self.line.stations if s.job_role == JobRole.QC_TECHNICIAN),
            None,
        )
        if qc_station and qc_station.sampling_rate > 0 and qc_station.defect_rate > 0:
            if self._rng.random() < qc_station.sampling_rate:
                if self._rng.random() < qc_station.defect_rate:
                    batch.status = BatchStatus.REWORK
                    batch.rework_count += 1
                    yield self.env.process(self._working_timeout(qc_station.rework_minutes * 60))
                    pass_rate = 0.0  # 批次整体隔离；不是按参数折算部分放行。
        self.quality_results.append({
            'time': self.env.now,
            'batch_id': batch.id,
            'pass_rate': round(pass_rate, 4),
            'rework_count': batch.rework_count,
        })

        end_time = self.env.now
        batch.pass_rate = pass_rate

        # 放行/隔离（V3.2）：未通过质量门的批次隔离，不计入可发运产出
        if pass_rate < 1.0:
            batch.status = BatchStatus.REJECTED
            batch.end_time = end_time
            runtime['stage'] = 'rejected'
            self._emit_alert(Alert(
                alert_type="batch_rejected",
                severity="WARNING",
                station_id="",
                message=f"批次 {batch.id} 未通过质量门，已隔离",
                suggestion="检查配方/工艺参数或执行返工/报废流程",
                timestamp_minutes=round(self.env.now / 60.0, 1),
            ))
            self.batch_results.append({
                'batch_id': batch.id,
                'recipe_name': batch.recipe_name,
                'status': 'rejected',
                'start_time': start_time,
                'end_time': end_time,
                'yield_l': 0.0,
                'cycle_min': round((end_time - start_time) / 60.0, 3),
                'pass_rate': pass_rate,
            })
            return

        # 罐容约束（V3.2）：按可用罐容分批注入，容量不足时等待并报警，
        # 禁止静默丢弃
        remaining = yielded_l
        runtime['stage'] = 'waiting_tank'
        runtime['produced_good_quantity'] = yielded_l
        runtime['pending_tank_quantity'] = remaining
        while remaining > 1e-6:
            available = self._tank_total_available()
            if available <= 1e-6:
                if batch.id not in self._tank_full_alerted:
                    self._tank_full_alerted.add(batch.id)
                    self._emit_alert(Alert(
                        alert_type="tank_full",
                        severity="WARNING",
                        station_id="",
                        message=(
                            f"批次 {batch.id} 等待储罐容量：需求 {remaining:.1f} 千克，"
                            f"可用 0 千克"
                        ),
                        suggestion="检查成品罐液位与灌装消耗，或增加储罐容量",
                        timestamp_minutes=round(self.env.now / 60.0, 1),
                    ))
                yield self.env.timeout(60)
                continue
            filled = self._distribute_to_tanks(min(remaining, available))
            remaining -= filled
            runtime['pending_tank_quantity'] = remaining
            runtime['stored_quantity'] += filled
            if remaining > 1e-6:
                if batch.id not in self._tank_full_alerted:
                    self._tank_full_alerted.add(batch.id)
                    self._emit_alert(Alert(
                        alert_type="tank_full",
                        severity="WARNING",
                        station_id="",
                        message=(
                            f"批次 {batch.id} 分批注入储罐：剩余 {remaining:.1f} 千克"
                        ),
                        suggestion="检查成品罐液位与灌装消耗，或增加储罐容量",
                        timestamp_minutes=round(self.env.now / 60.0, 1),
                    ))
                yield self.env.timeout(60)

        end_time = self.env.now
        batch.end_time = end_time
        batch.status = BatchStatus.RELEASED
        runtime['stage'] = 'released'
        if end_time >= self.warmup_seconds:
            self.total_output += yielded_l
        self.batch_results.append({
            'batch_id': batch.id,
            'recipe_name': batch.recipe_name,
            'status': 'released',
            'start_time': start_time,
            'end_time': end_time,
            'yield_l': round(yielded_l, 3),
            'cycle_min': round((end_time - start_time) / 60.0, 3),
            'pass_rate': pass_rate,
        })
    
    def _validate_run(self, duration_hours: float, warmup_minutes: float = 0.0) -> None:
        """Protect the event loop even when callers bypass GUI/import validation."""
        if not math.isfinite(duration_hours) or duration_hours <= 0:
            raise ValueError("仿真时长必须为有限正数")
        if (not math.isfinite(warmup_minutes)
                or not 0 <= warmup_minutes * 60 < duration_hours * 3600):
            raise ValueError("预热时间必须非负，且小于仿真总时长")
        if not math.isfinite(self.line.shift_hours) or self.line.shift_hours <= 0:
            raise ValueError("班次时长必须为有限正数")
        if (not math.isfinite(self.line.break_minutes)
                or not 0 <= self.line.break_minutes <= self.line.shift_hours * 60):
            raise ValueError("休息时间必须在零与班次时长之间")
        if not math.isfinite(self.line.worker_hourly_wage) or self.line.worker_hourly_wage < 0:
            raise ValueError("时薪必须为有限非负数")
        station_ids = set()
        for station in self.line.stations:
            if station.id in station_ids:
                raise ValueError("工序ID不能重复")
            station_ids.add(station.id)
            for value in (station.worker_count, station.machine_count, station.buffer_capacity):
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or not math.isfinite(value) or value <= 0 or int(value) != value):
                    raise ValueError("人数、机台数和缓冲容量必须为正整数")
            if not math.isfinite(station.process_time) or station.process_time <= 0:
                raise ValueError("工序时间必须为有限正数")
            for value in (station.oee, station.efficiency):
                if not math.isfinite(value) or not 0 <= value <= 1:
                    raise ValueError("OEE和人员效率必须在0到1之间")
            if station.machine_takt is not None and (
                not math.isfinite(station.machine_takt) or station.machine_takt <= 0
            ):
                raise ValueError("机台节拍必须为有限正数")
            for value in (station.changeover_time, station.clean_time_minutes,
                          station.rework_minutes):
                if not math.isfinite(value) or value < 0:
                    raise ValueError("停机、清洗和返工时间必须为有限非负数")
            for value in (station.sampling_rate, station.defect_rate):
                if not math.isfinite(value) or not 0 <= value <= 1:
                    raise ValueError("质量比例必须为0到1之间的有限数值")
            if any(not math.isfinite(qty) or qty < 0 for qty in station.bom.values()):
                raise ValueError("BOM用量必须为有限非负数")
        if self.line.production_type == ProductionType.LIQUID_FILLING:
            recipes = {recipe.name: recipe for recipe in self.line.recipes}
            for recipe in self.line.recipes:
                if (not math.isfinite(recipe.batch_volume_l) or recipe.batch_volume_l <= 0
                        or not math.isfinite(recipe.filling_rate_l_per_h)
                        or recipe.filling_rate_l_per_h <= 0):
                    raise ValueError("配方基准量和灌装速度必须为有限正数")
                for value in (recipe.mixing_time_min, recipe.aging_time_min,
                              recipe.qc_time_min, recipe.clean_time_min):
                    if not math.isfinite(value) or value < 0:
                        raise ValueError("批次阶段时长必须为有限非负数")
                if not math.isfinite(recipe.yield_rate) or not 0 <= recipe.yield_rate <= 1:
                    raise ValueError("配方收率必须在0到1之间")
            for batch in self.line.batches:
                if batch.recipe_name not in recipes:
                    raise ValueError("批次引用的配方不存在：" + batch.recipe_name)
                if not math.isfinite(batch.quantity_l) or batch.quantity_l <= 0:
                    raise ValueError("批次数量必须为有限正数")
            if self.line.batches and not self.line.tanks:
                raise ValueError("烟油批次需要至少一个成品储罐")
        from src.utils import validate_production_line
        valid, error = validate_production_line(self.line, allow_empty=True)
        if not valid:
            raise ValueError(error)

    def _prepare_run(self, duration_hours, warmup_minutes=0.0, cancel_event=None):
        self._validate_run(duration_hours, warmup_minutes)
        if self._sim_thread is not None and self._sim_thread.is_alive():
            raise RuntimeError("已有仿真正在运行，请先停止")
        self.duration_seconds = float(duration_hours) * 3600
        self.warmup_seconds = float(warmup_minutes) * 60
        for tank in self.line.tanks:
            tank.current_level_l = self._initial_tank_levels.get(tank.id, tank.current_level_l)
        self.config_revision = self.line.configuration_revision()
        self._input_config = copy.deepcopy(self.line.to_dict())
        self.run_id = uuid.uuid4().hex
        self.status = "running"
        self.error_message = ""
        self.is_running = True
        self.is_paused = False
        self._cancel_event = cancel_event
        self._stop_event.clear()
        self.station_outputs = {station.id: 0 for station in self.line.stations}
        self.station_wips = {station.id: 0 for station in self.line.stations}
        self.total_output = 0.0
        self.batch_results = []
        self.quality_results = []
        self.cleaning_events = []
        self.material_events = []
        self.wip_samples = []
        # 保留首次启动前用户排入的换型事件；重跑不继承历史报警。
        if self.env is not None:
            self.alert_log = []
            self.changeover_events = []
            while True:
                try:
                    self.event_queue.get_nowait()
                except Empty:
                    break
        self._material_alerted = set()
        self._inventory = {m.name: m.initial_stock for m in self.line.materials}
        self._inventory.update(self.line.inventory)
        self._rng = random.Random(self.random_seed)
        self._last_batch_recipe = None
        self._last_clean_hour = 0.0
        self._blockage_state = {}
        self._blockage_since = {}
        self._blockage_full_since = {}
        self._predict_alerted = set()
        self._starvation_alerted = set()
        self._blocked_alerted = set()
        self._tank_full_alerted = set()
        self.station_running_seconds = {}
        self.station_starved_seconds = {}
        self.station_blocked_seconds = {}
        self._time_intervals = []
        self._active_intervals = {}
        self._station_attempts = {}
        self._station_good = {}
        self._station_scrap = {}
        self._changeover_until = {}
        self._batch_runtime = {}
        self._tank_drained_quantity = 0.0
        self.station_resources = {}
        self.buffers = {}
        for tank in self.line.tanks:
            tank.current_level_l = self._initial_tank_levels.get(tank.id, tank.current_level_l)
        for batch in self.line.batches:
            batch.status = BatchStatus.QUEUED
            batch.start_time = batch.end_time = 0.0
            batch.pass_rate = 1.0
            batch.rework_count = 0
        for station in self.line.stations:
            station.current_status = "idle"
            station.wip_count = station.total_output = 0
        self.env = simpy.Environment()
        self._init_resources()
        if self.line.production_type == ProductionType.LIQUID_FILLING:
            self.env.process(self._batch_sequence_process())
            self.env.process(self._tank_drain_process())
        else:
            self._spawn_station_processes()
        self.env.process(self._material_arrival_process())
        self.env.process(self._monitor_bottleneck())
        self.env.process(self._monitor_wip())
        self.env.process(self._monitor_starvation())
        self.env.process(self._update_state_periodically())

    def run(self, duration_hours: float = 8.0, speed: int = 16) -> None:
        """以指定演示速度启动后台仿真；速度不影响业务日历。"""
        if not math.isfinite(speed) or speed <= 0:
            raise ValueError("仿真速度必须为有限正数")
        self._prepare_run(duration_hours)
        self.speed = speed
        self._sim_thread = threading.Thread(
            target=self._run_simulation_thread,
            args=(self.duration_seconds, speed), daemon=True,
        )
        self._sim_thread.start()

    def _init_resources(self) -> None:
        for station in self.line.stations:
            self.station_resources[station.id] = simpy.Resource(
                self.env, capacity=station.processing_units()
            )
            self.buffers[station.id] = simpy.Store(self.env, capacity=int(station.buffer_capacity))

    def _spawn_station_processes(self) -> None:
        for station in self.line.stations:
            if station.oee == 0 or station.efficiency == 0:
                station.current_status = "planned_stop"
                continue
            for _ in range(station.processing_units()):
                self.env.process(self._station_process(station))

    def _interval_start(self, station_id, kind):
        token = object()
        self._active_intervals[token] = (station_id, kind, self.env.now)
        return token

    def _interval_end(self, token):
        station_id, kind, start = self._active_intervals.pop(token)
        self._time_intervals.append((station_id, kind, start, self.env.now))
        counters = {
            "running": self.station_running_seconds,
            "waiting": self.station_starved_seconds,
            "blocked": self.station_blocked_seconds,
        }
        if kind in counters:
            elapsed = max(0.0, self.env.now - max(start, self.warmup_seconds))
            counters[kind][station_id] = counters[kind].get(station_id, 0.0) + elapsed

    def _station_wait_seconds(self, station_id, kind):
        """Measured resource seconds, including waits that have not ended yet."""
        counters = {
            'waiting': self.station_starved_seconds,
            'blocked': self.station_blocked_seconds,
        }
        closed = counters[kind].get(station_id, 0.0)
        active = sum(
            max(0.0, self.env.now - max(begun, self.warmup_seconds))
            for active_station, active_kind, begun in self._active_intervals.values()
            if active_station == station_id and active_kind == kind
        )
        return closed + active

    def _wait_for_production(self, station=None):
        """生产在班前，休息/计划换型与清洗在班尾；跨班恢复同一物料。"""
        while True:
            now = self.env.now
            shift_seconds = self.line.shift_hours * 3600
            productive = self.line.effective_hours(station) * 3600
            phase = now % shift_seconds
            station_id = station.id if station is not None else ""
            if station is not None:
                for event in self._drain_events(station.id):
                    start_time = max(
                        now, self._changeover_until.get(station.id, 0.0)
                    )
                    self._changeover_until[station.id] = start_time + event['minutes'] * 60
                    self.env.process(self._record_changeover(event, start_time))
            manual_until = self._changeover_until.get(station_id, 0.0)
            if manual_until > now:
                if station is not None:
                    station.current_status = "changeover"
                yield self.env.timeout(manual_until - now)
                continue
            if phase < productive - 1e-9:
                return productive - phase
            if station is not None:
                station.current_status = "planned_stop"
            yield self.env.timeout(shift_seconds - phase)

    def _working_timeout(self, seconds, station=None, kind="running"):
        """消耗指定生产时长；保留跨计划停机的剩余加工时间。"""
        remaining = seconds
        while remaining > 1e-9:
            available = yield self.env.process(self._wait_for_production(station))
            interval = min(remaining, available)
            token = self._interval_start(station.id if station is not None else "", kind)
            if station is not None:
                station.current_status = "running" if kind == "running" else "waiting"
            yield self.env.timeout(interval)
            self._interval_end(token)
            remaining -= interval

    def _station_process(self, station: Station) -> simpy.events.Process:
        index = self.line.stations.index(station)
        incoming = self.buffers[station.id]
        downstream = (self.buffers[self.line.stations[index + 1].id]
                      if index + 1 < len(self.line.stations) else None)
        while True:
            yield self.env.process(self._wait_for_production(station))
            if index > 0:
                station.current_status = "waiting"
                token = self._interval_start(station.id, "waiting")
                item = yield incoming.get()
                self._interval_end(token)
                self.station_wips[station.id] = len(incoming.items)
            else:
                item = object()
            # 同一物料在本站至多返工一次；返工不重复领BOM，也不创建第二个物料。
            if station.bom and (self.line.materials or self.line.inventory):
                yield self.env.process(self._consume_station_materials(station))
            takt = (station.machine_takt if station.machine_takt is not None
                    else station.process_time)
            yield self.env.process(self._working_timeout(
                takt / (station.oee * station.efficiency), station
            ))
            measured = self.env.now >= self.warmup_seconds
            if measured:
                self._station_attempts[station.id] = self._station_attempts.get(station.id, 0) + 1
            good = True
            for attempt in range(2):
                defective = (
                    station.sampling_rate > 0 and station.defect_rate > 0
                    and self._rng.random() < station.sampling_rate
                    and self._rng.random() < station.defect_rate
                )
                if not defective:
                    break
                rework = attempt == 0 and station.rework_minutes > 0
                self.quality_results.append({
                    'time': self.env.now, 'station_id': station.id,
                    'station_name': station.name, 'defect': True,
                    'rework_min': station.rework_minutes if rework else 0.0,
                    'outcome': 'rework' if rework else 'scrapped', 'attempt': attempt,
                })
                if not rework:
                    good = False
                    if self.env.now >= self.warmup_seconds:
                        self._station_scrap[station.id] = (
                            self._station_scrap.get(station.id, 0) + 1
                        )
                    break
                yield self.env.process(self._working_timeout(
                    station.rework_minutes * 60, station, kind="rework"
                ))
            if not good:
                station.current_status = "idle"
                continue
            if self.env.now >= self.warmup_seconds:
                self._station_good[station.id] = self._station_good.get(station.id, 0) + 1
            # 质量门通过后才向下游交付；Store.put保留背压与唤醒语义。
            if downstream is not None:
                station.current_status = "blocked"
                token = self._interval_start(station.id, "blocked")
                yield downstream.put(item)
                self._interval_end(token)
                self.station_wips[self.line.stations[index + 1].id] = len(downstream.items)
            if self.env.now >= self.warmup_seconds:
                self.station_outputs[station.id] += 1
                if downstream is None:
                    self.total_output += 1
            station.current_status = "idle"

    def _monitor_bottleneck(self) -> simpy.events.Process:
        """
        监控瓶颈进程
        
        定期检查瓶颈工序是否发生变化，如果变化则发出警报
        
        Yields:
            simpy.events: SimPy事件
        """
        last_bottleneck_id = None
        
        while True:
            # 每60秒检查一次（仿真时间）
            yield self.env.timeout(60)
            
            # 找出当前瓶颈
            bottleneck = self.line.find_bottleneck()
            if bottleneck is None:
                continue
            
            # 如果瓶颈发生变化，发出警报
            if bottleneck.id != last_bottleneck_id:
                # 计算从仿真开始经过的分钟数
                timestamp_minutes = self.env.now / 60.0
                alert = Alert(
                    alert_type="bottleneck",
                    severity="critical",
                    station_id=bottleneck.id,
                    message=(
                        f"瓶颈工序：{bottleneck.name}，"
                        f"产能：{bottleneck.get_capacity():.0f}{self.line.get_unit()}/h"
                    ),
                    suggestion=self._bottleneck_suggestion(bottleneck),
                    timestamp_minutes=timestamp_minutes
                )
                self._emit_alert(alert)
                last_bottleneck_id = bottleneck.id
    
    def _monitor_wip(self) -> simpy.events.Process:
        """
        监控WIP堆积进程
        
        检查各工序的WIP是否接近缓冲区容量，如果接近则发出警报
        
        Yields:
            simpy.events: SimPy事件
        """
        while True:
            # 每30秒检查一次（仿真时间）
            yield self.env.timeout(30)
            
            # 计算从仿真开始经过的分钟数
            timestamp_minutes = self.env.now / 60.0
            
            # 检查每个工序的WIP
            for station in self.line.stations:
                buffer = self.buffers[station.id]
                wip_count = len(buffer.items)
                utilization = wip_count / station.buffer_capacity if station.buffer_capacity > 0 else 0

                # 记录 WIP 时间序列采样（供报告导出）
                self.wip_samples.append({
                    'time': self.env.now,
                    'station_id': station.id,
                    'station_name': station.name,
                    'wip': wip_count,
                })
                
                station_index = self.line.stations.index(station)

                # P2 预测性预警：估算到达 80% 预警线的时间
                if (
                    utilization < 0.8
                    and station_index > 0
                    and station.id not in self._predict_alerted
                ):
                    upstream = self.line.stations[station_index - 1]
                    # P2.2：优先使用实测上游产出速率，避免理论产能高估
                    if self.env.now >= 60.0:
                        measured_per_h = (
                            self.station_outputs.get(upstream.id, 0)
                            / self.env.now * 3600.0
                        )
                        up_cap = max(measured_per_h, 0.0)
                    else:
                        up_cap = upstream.get_capacity()
                    st_cap = station.get_capacity()
                    net_per_min = (up_cap - st_cap) / 60.0
                    remaining = station.buffer_capacity * 0.8 - wip_count
                    if net_per_min > 0 and remaining > 0:
                        minutes_to_alert = remaining / net_per_min
                        if minutes_to_alert <= 30.0:
                            self._predict_alerted.add(station.id)
                            rate_label = (
                                "按实测上游速率"
                                if self.env.now >= 60.0
                                else "按上游产能估算"
                            )
                            self._emit_alert(Alert(
                                alert_type="blockage",
                                severity="info",
                                station_id=station.id,
                                message=(
                                    f"预计 {minutes_to_alert:.0f} 分钟后"
                                    f"{station.name}的WIP将达到80%预警线"
                                    f"（{rate_label}）"
                                ),
                                suggestion=self._wip_suggestion(station, station_index),
                                timestamp_minutes=timestamp_minutes,
                            ))

                # 低于阈值：重置状态
                if utilization < 0.8:
                    self._blockage_state.pop(station.id, None)
                    self._blockage_since.pop(station.id, None)
                    self._blockage_full_since.pop(station.id, None)
                    continue

                # 状态去重与升级（P0）：warning 每工序仅报一次，满缓冲持续 3 分钟升级 critical
                current = self._blockage_state.get(station.id)
                sustained_min = (
                    (self.env.now - self._blockage_since[station.id]) / 60.0
                    if station.id in self._blockage_since
                    else 0.0
                )

                if utilization < 1.0:
                    # 缓冲区未满，重置满缓冲计时
                    self._blockage_full_since.pop(station.id, None)
                    if current != "warning":
                        self._blockage_state[station.id] = "warning"
                        self._blockage_since[station.id] = self.env.now
                        self._emit_alert(Alert(
                            alert_type="blockage",
                            severity="warning",
                            station_id=station.id,
                            message=f"{station.name}的WIP堆积：{wip_count}/{station.buffer_capacity}",
                            suggestion=self._wip_suggestion(station, station_index),
                            timestamp_minutes=timestamp_minutes,
                        ))
                else:
                    if station.id not in self._blockage_full_since:
                        self._blockage_full_since[station.id] = self.env.now
                    full_min = (
                        (self.env.now - self._blockage_full_since[station.id]) / 60.0
                    )
                    if current != "critical":
                        if current == "warning" and full_min >= 3.0:
                            self._blockage_state[station.id] = "critical"
                            self._emit_alert(Alert(
                                alert_type="blockage",
                                severity="critical",
                                station_id=station.id,
                                message=(
                                    f"{station.name}缓冲区已满并持续 {full_min:.0f} 分钟"
                                    f"（自80%预警以来 {sustained_min:.0f} 分钟）"
                                    f"：{wip_count}/{station.buffer_capacity}"
                                ),
                                suggestion=self._wip_suggestion(station, station_index),
                                timestamp_minutes=timestamp_minutes,
                            ))
                        elif current is None:
                            # 直接从 0 跳到满缓冲：先报 warning
                            self._blockage_state[station.id] = "warning"
                            self._blockage_since[station.id] = self.env.now
                            self._blockage_full_since[station.id] = self.env.now
                            self._emit_alert(Alert(
                                alert_type="blockage",
                                severity="warning",
                                station_id=station.id,
                                message=f"{station.name}的WIP堆积：{wip_count}/{station.buffer_capacity}",
                                suggestion=self._wip_suggestion(station, station_index),
                                timestamp_minutes=timestamp_minutes,
                            ))

    def _monitor_starvation(self) -> simpy.events.Process:
        """
        饥饿/堵塞监控（P1）

        - 下游工序累计等待物料 ≥5 分钟 → 饥饿报警
        - 上游工序被下游堵塞累计 ≥5 分钟 → 堵塞报警
        """
        while True:
            yield self.env.timeout(60)
            timestamp_minutes = self.env.now / 60.0

            for idx, station in enumerate(self.line.stations):
                if idx == 0:
                    continue
                starved = self._station_wait_seconds(station.id, 'waiting')
                per_worker = starved / station.processing_units()
                if per_worker >= 300 and station.id not in self._starvation_alerted:
                    self._starvation_alerted.add(station.id)
                    bottleneck = self.line.find_bottleneck()
                    if bottleneck and bottleneck.id != station.id and idx > self.line.stations.index(bottleneck):
                        suggestion = (
                            f"上游受瓶颈「{bottleneck.name}」限制"
                            f"（{bottleneck.get_capacity():.0f}/h），"
                            f"建议提升瓶颈产能或增加缓存"
                        )
                    else:
                        suggestion = "上游供料不足或被堵塞，建议平衡上游节拍或增加缓存"
                    self._emit_alert(Alert(
                        alert_type="starvation",
                        severity="warning",
                        station_id=station.id,
                        message=(
                            f"{station.name}累计等待物料 {per_worker / 60.0:.1f} 分钟/工位"
                            f"（汇总 {starved / 60.0:.1f} 分钟）"
                        ),
                        suggestion=suggestion,
                        timestamp_minutes=timestamp_minutes,
                    ))

            for idx, station in enumerate(self.line.stations):
                if idx >= len(self.line.stations) - 1:
                    continue
                blocked = self._station_wait_seconds(station.id, 'blocked')
                per_worker = blocked / station.processing_units()
                if per_worker >= 300 and station.id not in self._blocked_alerted:
                    self._blocked_alerted.add(station.id)
                    bottleneck = self.line.find_bottleneck()
                    if bottleneck and bottleneck.id != station.id and idx < self.line.stations.index(bottleneck):
                        suggestion = (
                            f"下游受瓶颈「{bottleneck.name}」限制"
                            f"（{bottleneck.get_capacity():.0f}/h），"
                            f"建议提升瓶颈产能或增加缓存"
                        )
                    else:
                        suggestion = "下游消化不足，建议提升下游产能或增加缓存"
                    self._emit_alert(Alert(
                        alert_type="blockage",
                        severity="warning",
                        station_id=station.id,
                        message=(
                            f"{station.name}被下游堵塞累计 {per_worker / 60.0:.1f} 分钟/工位"
                            f"（汇总 {blocked / 60.0:.1f} 分钟）"
                        ),
                        suggestion=suggestion,
                        timestamp_minutes=timestamp_minutes,
                    ))
    
    def _update_state_periodically(self) -> simpy.events.Process:
        """
        定期更新状态并通知GUI
        
        这是GUI更新的关键，定期收集仿真状态并通过回调函数通知GUI
        根据仿真速度调整更新频率，1倍速时按真实时间更新
        
        Yields:
            simpy.events: SimPy事件
        """
        while True:
            # 根据仿真速度调整更新间隔
            # 1倍速：每1秒（真实时间）更新一次，对应1秒仿真时间
            # 16倍速：每1秒（真实时间）更新一次，对应16秒仿真时间
            # 这样在1倍速时，用户可以看到真实的时间流逝
            if self.speed == 1:
                update_interval = 1.0  # 1秒（仿真时间）
            else:
                update_interval = 10.0  # 其他速度下，每10秒（仿真时间）更新一次
            
            yield self.env.timeout(update_interval)
            
            # 如果GUI没有设置回调，跳过
            if self.state_callback is None:
                continue
            
            # 收集当前状态
            state = self._collect_state()
            
            # 调用回调函数，通知GUI更新
            # 注意：这里在SimPy线程中，需要切换到GUI线程
            # 实际实现中，应该使用队列传递状态
            if self.state_callback:
                try:
                    self.state_callback(state)
                except Exception as e:
                    # 捕获异常，避免影响仿真运行
                    print(f"回调函数执行错误：{e}")
    
    def _collect_state(self) -> SimulationState:
        """
        收集当前仿真状态
        
        将SimPy的仿真状态转换为SimulationState对象，用于GUI显示
        
        Returns:
            SimulationState: 当前仿真状态快照
        """
        # 构建各工序状态字典
        station_states = {}
        for station in self.line.stations:
            station_states[station.id] = {
                'status': station.current_status,
                'output': self.station_outputs.get(station.id, 0),
                'wip': self.station_wips.get(station.id, 0),
                'capacity': station.get_capacity()
            }
        
        # 找出瓶颈
        bottleneck = self.line.find_bottleneck()
        bottleneck_id = bottleneck.id if bottleneck else None
        
        # 创建状态对象
        state = SimulationState(
            current_time=self.env.now,
            station_states=station_states,
            total_output=self.total_output,
            bottleneck_id=bottleneck_id
        )
        
        return state
    
    def _run_simulation_thread(self, duration_seconds: float, speed: int = 1) -> None:
        """小步推进与可中断等待，停止后不再有后台状态写入。"""
        try:
            while self.env.now < duration_seconds and self.is_running:
                if self.is_paused:
                    self._stop_event.wait(0.05)
                    continue
                previous = self.env.now
                target = min(previous + 1.0, duration_seconds)
                self.env.run(until=target)
                self._stop_event.wait((target - previous) / speed)
            self.status = "completed" if self.env.now >= duration_seconds else "stopped"
        except Exception as error:
            self.status = "failed"
            self.error_message = str(error)
            self._emit_alert(Alert("simulation_error", "critical", "", str(error)))
        finally:
            self.is_running = False

    def pause(self) -> None:
        """
        暂停仿真
        
        暂停时，is_paused设为True，但is_running保持True
        这样可以通过resume()恢复仿真，而不需要重新创建引擎
        """
        self.is_paused = True
        if self.is_running:
            self.status = "paused"
    
    def resume(self) -> None:
        """
        恢复仿真
        
        从暂停状态恢复，is_paused设为False
        仿真线程会继续运行
        """
        self.is_paused = False
        if self.is_running:
            self.status = "running"
    
    def stop(self) -> None:
        """请求停止并等后台线程退出，随后可安全构建部分运行结果。"""
        self.is_running = False
        self._stop_event.set()
        thread = self._sim_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
            if thread.is_alive():
                raise RuntimeError("仿真线程尚未停止，请稍后重试")
        if self.status == "running":
            self.status = "stopped"

    def get_results(self) -> Dict[str, Any]:
        """
        获取仿真结果
        
        Returns:
            Dict: 包含仿真结果的字典
        """
        return {
            'total_output': self.total_output,
            'station_outputs': self.station_outputs.copy(),
            'station_wips': self.station_wips.copy(),
            'bottleneck': self.line.find_bottleneck()
        }

    def run_sync(
        self, duration_hours: float = 8.0, warmup_minutes: float = 0.0,
        cancel_event=None,
    ) -> SimulationResult:
        """同步可取消仿真，统计半开窗口[预热结束,实际结束)。

        预热保留物理库存与WIP；计数、费用、时间指标均用同一测量窗口。
        """
        self._prepare_run(duration_hours, warmup_minutes, cancel_event)
        self.speed = 1
        try:
            stop_at = self.env.timeout(self.duration_seconds)
            while not stop_at.processed:
                if cancel_event is not None and cancel_event.is_set():
                    raise CancelledError("仿真已取消")
                self.env.step()
            self.status = "completed"
            return self.build_result()
        except CancelledError:
            self.status = "cancelled"
            raise
        except Exception:
            self.status = "failed"
            raise
        finally:
            self.is_running = False

    def build_result(self) -> SimulationResult:
        """同一实际测量窗口内的良品、费用和资源时间；理论值另行保留。"""
        elapsed = float(self.env.now) if self.env is not None else 0.0
        start = min(self.warmup_seconds, elapsed)
        duration = max(0.0, elapsed - start)
        material_events = [event for event in self.material_events
                           if start <= event.get('time', 0) < elapsed]
        quality_results = [event for event in self.quality_results
                           if start <= event.get('time', 0) < elapsed]
        batch_results = [batch for batch in self.batch_results
                         if start <= batch.get('end_time', 0) < elapsed]
        unit_costs = {material.name: material.unit_cost for material in self.line.materials}
        material_cost = sum(event.get('quantity', 0.0) * unit_costs.get(
            event.get('material', ''), 0.0
        ) for event in material_events if event.get('type') == 'consume')
        labor_cost = (
            self.line.total_worker_count() * self.line.worker_hourly_wage * duration / 3600
        )
        kpis = {
            'bottleneck_capacity': self.line.get_bottleneck_capacity(),
            'daily_output': self.line.calculate_daily_output(),
            'total_cost': self.line.calculate_total_cost(),
            'unit_cost': self.line.calculate_unit_cost(),
            'balance_rate': self.line.calculate_line_balance_rate(),
            'upph': self.line.calculate_upph(),
            'actual_output': self.total_output,
            'actual_hourly_output': self.total_output / (duration / 3600) if duration else 0.0,
            'actual_labor_cost': round(labor_cost, 6),
            'labor_cost': round(labor_cost, 6),
            'material_cost': round(material_cost, 6),
            'total_actual_cost': round(labor_cost + material_cost, 6),
            'actual_unit_cost': labor_cost / self.total_output if self.total_output > 0 else None,
            'batch_cycle_min': (sum(batch['cycle_min'] for batch in batch_results)
                                / len(batch_results) if batch_results else 0.0),
            'batch_pass_rate': (sum(batch.get('status') == 'released' for batch in batch_results)
                                / len(batch_results) if batch_results else 0.0),
            'yield_rate': self.line.calculate_avg_yield_rate(),
            'machine_oee': self.line.calculate_avg_machine_oee(),
            'shippable_quantity': self.total_output,
            'rejected_batches': sum(batch.get('status') == 'rejected' for batch in batch_results),
        }
        if self.line.production_type == ProductionType.LIQUID_FILLING:
            kpis['cost_per_liter'] = kpis['actual_unit_cost']
        elif self.line.production_type == ProductionType.POUCH_PACKAGING:
            kpis['cost_per_pouch'] = kpis['actual_unit_cost']
        intervals = list(self._time_intervals) + [
            (station_id, kind, begun, elapsed)
            for station_id, kind, begun in self._active_intervals.values()
        ]
        times = {}
        cleaning_seconds = 0.0
        for station_id, kind, begun, ended in intervals:
            seconds = max(0.0, min(ended, elapsed) - max(begun, start))
            times[(station_id, kind)] = times.get((station_id, kind), 0.0) + seconds
            if kind == 'cleaning':
                cleaning_seconds += seconds
        kpis['cleaning_time_ratio'] = cleaning_seconds / duration if duration else 0.0
        station_metrics = {}
        for station in self.line.stations:
            running = times.get((station.id, 'running'), 0.0)
            rework = times.get((station.id, 'rework'), 0.0)
            waiting = times.get((station.id, 'waiting'), 0.0)
            blocked = times.get((station.id, 'blocked'), 0.0)
            resource_seconds = station.processing_units() * duration
            utilization = (
                min(1.0, (running + rework) / resource_seconds) if resource_seconds else 0.0
            )
            takt = (station.machine_takt if station.machine_takt is not None
                    else station.process_time)
            attempts = self._station_attempts.get(station.id, 0)
            good = self._station_good.get(station.id, 0)
            scrap = self._station_scrap.get(station.id, 0)
            completed = good + scrap
            # 最终质量处理完成才进入同一窗口的分子与分母，含预热期开始的返工件。
            performance = (
                min(1.0, completed * takt / (running + rework)) if running + rework else 0.0
            )
            quality = good / completed if completed else 0.0
            station_metrics[station.id] = {
                'name': station.name, 'capacity': round(station.get_capacity(), 3),
                'resource_count': station.processing_units(),
                'running_sec': round(running, 6), 'rework_sec': round(rework, 6),
                'waiting_sec': round(waiting, 6), 'blocked_sec': round(blocked, 6),
                'utilization': utilization, 'oee_availability': utilization,
                'oee_performance': performance, 'oee_quality': quality,
                'oee_total': utilization * performance * quality,
                'good_output': good, 'scrap_count': scrap,
                'completed_items': completed, 'processing_attempts': attempts,
            }
        if self.line.production_type == ProductionType.LIQUID_FILLING:
            station_metrics = {}
        line_config = copy.deepcopy(getattr(self, '_input_config', self.line.to_dict()))
        for station in line_config.get('stations', []):
            for key in ('current_status', 'wip_count', 'total_output'):
                station.pop(key, None)
        for batch in line_config.get('batches', []):
            for key in ('status', 'start_time', 'end_time', 'pass_rate', 'rework_count'):
                batch.pop(key, None)
        self.station_wips = {sid: len(buffer.items) for sid, buffer in self.buffers.items()}
        scope = ('recipe_batch_flow_no_station_staffing_capacity'
                 if self.line.production_type == ProductionType.LIQUID_FILLING
                 else 'discrete_station_flow')
        return SimulationResult(
            line_name=self.line.name, duration_seconds=self.duration_seconds,
            total_output=self.total_output, station_outputs=self.station_outputs.copy(),
            station_wips=self.station_wips.copy(), kpis=kpis,
            alerts=[alert for alert in self.alert_log if alert.timestamp_minutes * 60 >= start],
            wip_samples=[sample for sample in self.wip_samples if sample.get('time', 0) >= start],
            changeover_events=copy.deepcopy(self.changeover_events), batch_results=batch_results,
            quality_results=quality_results, cleaning_events=list(self.cleaning_events),
            material_events=material_events, inventory=dict(self._inventory),
            labor_summary=dict(self.line.labor_config), unit=self.line.get_unit(),
            station_metrics=station_metrics, line_config=line_config,
            run_id=self.run_id, config_revision=self.config_revision, random_seed=self.random_seed,
            elapsed_seconds=elapsed, measurement_seconds=duration,
            warmup_seconds=self.warmup_seconds, status=self.status, model_scope=scope,
            closing_tanks=[tank.to_dict() for tank in self.line.tanks],
            incomplete_batches=[dict(batch.to_dict(), **self._batch_runtime.get(batch.id, {}))
                                for batch in self.line.batches
                                if batch.status not in (
                                    BatchStatus.RELEASED, BatchStatus.REJECTED
                                )],
            liquid_balance={
                'initial_tank_quantity': sum(self._initial_tank_levels.values()),
                'produced_good_quantity': sum(item.get('produced_good_quantity', 0.0)
                                              for item in self._batch_runtime.values()),
                'drained_quantity': self._tank_drained_quantity,
                'closing_tank_quantity': sum(tank.current_level_l for tank in self.line.tanks),
                'pending_tank_quantity': sum(item.get('pending_tank_quantity', 0.0)
                                            for item in self._batch_runtime.values()),
            } if self.line.production_type == ProductionType.LIQUID_FILLING else {},
        )



def detect_waste(
    stations: List[Station], bottleneck_capacity: float, threshold: float = 1.2,
    worker_hourly_wage: float = 20.0, shift_hours: float = 8.0,
) -> List[Alert]:
    """人工并联工序的能力冗余估算；不从设备能力推导可裁减操作员。"""
    if not math.isfinite(bottleneck_capacity) or bottleneck_capacity <= 0:
        return []
    alerts = []
    for station in stations:
        if (station.machine_takt is not None
                or station.collaboration_type != CollaborationType.PARALLEL):
            continue
        capacity = station.get_capacity()
        if capacity <= bottleneck_capacity * threshold:
            continue
        per_worker = capacity / station.worker_count
        optimal_workers = math.ceil(bottleneck_capacity / per_worker)
        reducible = station.worker_count - optimal_workers
        if reducible <= 0:
            continue
        savings = reducible * worker_hourly_wage * shift_hours
        excess = (capacity / bottleneck_capacity - 1) * 100
        alerts.append(Alert(
            alert_type="waste", severity="warning", station_id=station.id,
            message=f"{station.name}产能过剩{excess:.0f}%",
            suggestion=(f"估算可减少{reducible}人，按时薪{worker_hourly_wage:g}元、"
                        f"{shift_hours:g}小时/班可节约{savings:.0f}元/班；需现场验证"),
        ))
    return alerts
