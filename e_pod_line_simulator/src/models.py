"""
数据模型层 - 定义所有数据结构

这个文件包含所有数据模型类，用于表示产线、工序、报警等数据。
数据结构的设计原则：
1. 数据与逻辑分离：模型类只存储数据，不包含复杂计算逻辑
2. 易于序列化：支持JSON格式的保存和加载
3. 类型明确：使用类型提示方便IDE智能提示和错误检查

主要类：
- Station: 工序模型（产线上的一个加工环节）
- ProductionLine: 产线模型（包含多个工序）
- Alert: 报警模型（瓶颈、浪费等警报）
- SimulationState: 仿真状态（记录仿真过程中的快照）
"""

from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass, field, asdict
from enum import Enum
from datetime import datetime
import copy
import hashlib
import json
import math


class CollaborationType(Enum):
    """
    协作模式枚举类
    
    定义工序的两种工作模式：
    - PARALLEL: 并联模式，多人同时加工不同物料，产能可以叠加
      例如：2个人并联工作，产能 = 单人产能 × 2
    - COLLABORATIVE: 协同模式，多人共同完成一个物料，产能不叠加
      例如：2个人协同工作，产能 = 单人产能（但可能更快）
    """
    PARALLEL = "parallel"  # 并联模式
    COLLABORATIVE = "collaborative"  # 协同模式


class ProductionType(Enum):
    """
    生产类型模板（V1.3）

    - ASSEMBLY: 烟弹离散组装（现状默认）
    - LIQUID_FILLING: 烟油液体/批量灌装
    - POUCH_PACKAGING: 尼古丁袋高速包装
    """
    ASSEMBLY = "assembly"
    LIQUID_FILLING = "liquid_filling"
    POUCH_PACKAGING = "pouch_packaging"


class JobRole(Enum):
    """
    工种（V1.3 人力模型）

    - MIXER: 调香师/调配工
    - FILLING_OPERATOR: 灌装线操作员
    - QC_TECHNICIAN: QC 化验员
    - PACKAGING_OPERATOR: 包装机手
    - CLEANER: 清洗工
    - GENERAL: 通用装配工（assembly 默认）
    """
    MIXER = "mixer"
    FILLING_OPERATOR = "filling_operator"
    QC_TECHNICIAN = "qc_technician"
    PACKAGING_OPERATOR = "packaging_operator"
    CLEANER = "cleaner"
    GENERAL = "general"


class BatchStatus(Enum):
    """
    批次状态（V1.3 烟油/尼古丁袋）
    """
    QUEUED = "queued"
    MIXING = "mixing"
    AGING = "aging"
    FILLING = "filling"
    QC = "qc"
    REWORK = "rework"
    RELEASED = "released"
    REJECTED = "rejected"


UNIT_LABELS = {
    ProductionType.ASSEMBLY: "颗",
    # 烟油灌装按质量计量（千克）；内部 *_l 字段名保留兼容，实际按千克解释
    ProductionType.LIQUID_FILLING: "千克",
    ProductionType.POUCH_PACKAGING: "袋",
}


@dataclass
class Station:
    """
    工序模型类 - 代表产线上的一个加工工序
    
    这个类存储一个工序的所有信息，包括：
    - 基本信息：名称、ID、耗时等
    - 资源配置：工人数量、协作模式
    - 效率参数：OEE（设备效率）、人员效率
    - 状态信息：当前状态、WIP数量等
    
    使用 @dataclass 装饰器的好处：
    1. 自动生成 __init__、__repr__ 等方法
    2. 代码更简洁，减少样板代码
    3. 支持类型提示，IDE可以提供更好的代码补全
    
    示例用法：
        station = Station(
            id="s01",
            name="注油",
            process_time=25,
            worker_count=2
        )
        capacity = station.get_capacity()  # 计算产能
    """
    
    # 基本信息
    id: str  # 工序唯一标识符，用于在产线中识别这个工序
    name: str  # 工序名称，显示给用户看的名称，如"注油"、"焊接"
    process_time: float  # 单颗耗时（秒），处理一个物料所需的理论时间
    
    # 资源配置
    worker_count: int  # 工人数量，分配到该工序的工人数
    collaboration_type: CollaborationType = CollaborationType.PARALLEL  # 协作模式，默认并联
    
    # 效率参数（0-1之间的浮点数）
    oee: float = 0.85  # OEE（Overall Equipment Effectiveness）设备综合效率
                       # 默认0.85表示实际效率是理论效率的85%
    efficiency: float = 0.95  # 人员效率，考虑人员技能、疲劳等因素
                              # 默认0.95表示人员实际效率是理论值的95%
    
    # 切换参数
    changeover_time: int = 45  # 切换时间（分钟），更换产品型号时需要的停机时间
                               # 默认45分钟，这是电子烟行业常见的切换时间
    
    # 缓冲区参数
    buffer_capacity: int = 100  # WIP缓冲区容量，工序间可以暂存的最大物料数
                                # 默认100，防止物料堆积过多

    # V1.3 扩展：机台节拍（尼古丁袋高速包装，秒/单位）
    machine_takt: Optional[float] = None

    # V1.3 扩展：人力与洁净区
    job_role: JobRole = JobRole.GENERAL
    cleanroom_zone: str = ""  # A/B/C/D，空表示不限制

    # V1.3 扩展：质量门（在线检测）
    sampling_rate: float = 0.0   # 抽检比例 0-1
    defect_rate: float = 0.0     # 检出缺陷率 0-1
    rework_minutes: float = 0.0  # 单次返工/隔离时长（分钟）

    # V1.3 扩展：清洗切换（CIP/SIP，分钟）
    clean_time_minutes: float = 0.0

    # V3.2：BOM 组件消耗（组件名 → 单件用量）
    bom: Dict[str, float] = field(default_factory=dict)
    
    # 运行时状态（这些字段在仿真过程中会动态更新）
    current_status: str = "idle"  # 当前状态：idle(空闲)、running(运行)、blocked(堵塞)、waiting(等待)
    wip_count: int = 0  # 当前WIP数量，等待在该工序前加工的物料数
    total_output: int = 0  # 累计产出，该工序已完成的物料总数

    # V3.4：设备台数与操作员人数分离；放在末尾以保留旧位置参数兼容性。
    machine_count: int = 1
    
    def get_capacity(self) -> float:
        """生产时间内的能力（单位/小时），不按固定班长摊销停机。

        机台模式由 ``machine_count`` 决定并发能力；``worker_count`` 只表示人。
        计划休息、换型和清洗由产线日历另行扣除，避免重复折减。
        """
        takt = self.machine_takt if self.machine_takt is not None else self.process_time
        if not math.isfinite(takt) or takt <= 0:
            raise ValueError("工序节拍必须为有限正数")
        return 3600.0 / takt * self.processing_units() * self.oee * self.efficiency

    def processing_units(self) -> int:
        """可独立处理物料的资源数；协同人工工序始终为一个团队。"""
        units = self.machine_count if self.machine_takt is not None else (
            self.worker_count if self.collaboration_type == CollaborationType.PARALLEL else 1
        )
        if (isinstance(units, bool) or not isinstance(units, (int, float))
                or not math.isfinite(units) or units <= 0 or int(units) != units):
            raise ValueError("工人/机台数量必须为正整数")
        return int(units)

    def planned_downtime_minutes(self) -> float:
        """每个配置班次的计划换型与清洗时长；不含产线休息。"""
        return float(self.changeover_time) + float(self.clean_time_minutes)
    
    def get_utilization(self, bottleneck_capacity: float) -> float:
        """
        计算工序的负荷率（利用率）
        
        负荷率 = 实际产能 / 瓶颈产能
        - 负荷率 = 1.0：该工序是瓶颈，满负荷运行
        - 负荷率 > 1.0：该工序产能过剩，有浪费
        - 负荷率 < 1.0：该工序产能不足（理论上不应该出现）
        
        Args:
            bottleneck_capacity: 瓶颈产能（颗/小时），产线上产能最低的工序的产能
            
        Returns:
            float: 负荷率，0-1之间的值（通常不会超过1.2）
            
        为什么需要计算负荷率？
        - 帮助识别资源浪费：负荷率远小于1.0的工序，说明配置过多人员
        - 帮助优化产线平衡：让所有工序的负荷率接近1.0
        """
        if bottleneck_capacity == 0:
            # 防止除零错误，如果瓶颈产能为0，返回0
            return 0.0
        
        # 负荷率 = 本工序产能 / 瓶颈产能
        # 如果本工序产能是144颗/h，瓶颈是120颗/h，负荷率 = 144/120 = 1.2
        # 说明本工序产能过剩20%
        utilization = self.get_capacity() / bottleneck_capacity
        
        return utilization
    
    def to_dict(self) -> Dict[str, Any]:
        """
        将工序对象转换为字典格式
        
        用于JSON序列化，方便保存到文件
        
        Returns:
            Dict: 包含工序所有字段的字典
        """
        # asdict()是dataclass提供的方法，自动将对象转为字典
        # 但需要处理枚举类型，将其转为字符串
        data = asdict(self)
        # 将枚举类型转为字符串值
        data['collaboration_type'] = self.collaboration_type.value
        data['job_role'] = self.job_role.value
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Station':
        """
        从字典创建工序对象（反序列化）
        
        用于从JSON文件加载配置
        
        Args:
            data: 包含工序数据的字典
            
        Returns:
            Station: 新创建的工序对象
        """
        data = copy.deepcopy(data)
        # 旧版本把工人数同时解释为台数。迁移时保留两者历史取值；新建默认一台。
        if data.get('machine_takt') is not None and 'machine_count' not in data:
            data['machine_count'] = data.get('worker_count', 1)
        # 将字符串转换回枚举类型，不修改调用方的原始字典。
        if isinstance(data.get('collaboration_type'), str):
            data['collaboration_type'] = CollaborationType(data['collaboration_type'])
        if isinstance(data.get('job_role'), str):
            data['job_role'] = JobRole(data['job_role'])
        # 使用字典解包创建对象
        return cls(**data)


@dataclass
class ProductionLine:
    """
    产线模型类 - 包含多个工序，代表一条完整的产线
    
    这个类管理一条产线的所有工序，提供：
    - 工序管理：添加、删除、查找工序
    - 产线分析：找出瓶颈、计算KPI
    - 配置管理：保存和加载产线配置
    
    产线的核心概念：
    - 瓶颈工序：产能最低的工序，限制整条产线的产出
    - 产线平衡率：瓶颈产能 / 平均产能，反映产线是否平衡
    - 物料流向：工序按顺序排列，物料从第一个工序流向最后一个
    
    示例用法：
        line = ProductionLine("测试产线")
        line.add_station(Station("s01", "注油", 25, 2))
        line.add_station(Station("s02", "焊接", 30, 3))
        bottleneck = line.find_bottleneck()
    """
    
    # 基本信息
    name: str  # 产线名称，如"电子烟产线_标准版"
    stations: List[Station] = field(default_factory=list)  # 工序列表，按顺序存储
    
    # 班次配置
    shift_hours: int = 8  # 班次时长（小时），默认8小时
    break_minutes: int = 60  # 休息时间（分钟），默认60分钟（包含午餐和休息）
    worker_hourly_wage: float = 20.0  # 工人时薪（元/小时），用于计算成本

    # V1.3：生产类型模板
    production_type: ProductionType = ProductionType.ASSEMBLY

    # V1.3：配方/批次/罐
    recipes: List["Recipe"] = field(default_factory=list)
    batches: List["Batch"] = field(default_factory=list)
    tanks: List["Tank"] = field(default_factory=list)

    # V1.3：人力模型
    labor_config: Dict[str, int] = field(default_factory=dict)  # {job_role: 人数}
    cleanroom_limits: Dict[str, int] = field(default_factory=dict)  # {zone: 上限}
    skill_matrix: Dict[str, List[str]] = field(default_factory=dict)  # {role: [可互换]}

    # V3.2：周期性 CIP（0 = 禁用）
    cip_interval_batches: int = 0   # 每 N 个批次清洗一次
    cip_interval_hours: float = 0.0  # 每运行 N 小时清洗一次

    # V3.2：原料与库存
    materials: List["Material"] = field(default_factory=list)
    inventory: Dict[str, float] = field(default_factory=dict)  # {原料: 当前库存}
    material_arrivals: List["MaterialArrival"] = field(default_factory=list)

    # V3.2：换型矩阵（配方/规格 A → B 的切换时长，分钟）
    changeover_matrix: Dict[str, Dict[str, float]] = field(default_factory=dict)
    
    def add_station(self, station: Station) -> None:
        """
        添加一个工序到产线
        
        工序按添加顺序排列，代表物料流向
        第一个添加的工序是上游，最后一个是下游
        
        Args:
            station: 要添加的工序对象
            
        为什么需要这个方法？
        - 提供清晰的API，而不是直接操作stations列表
        - 可以在这里添加校验逻辑（如检查工序ID是否重复）
        """
        # 检查ID是否重复（可选，防止错误）
        for existing in self.stations:
            if existing.id == station.id:
                raise ValueError(f"工序ID '{station.id}' 已存在，请使用不同的ID")
        
        # 添加到列表末尾
        self.stations.append(station)
    
    def remove_station(self, station_id: str) -> bool:
        """
        从产线中删除一个工序
        
        Args:
            station_id: 要删除的工序ID
            
        Returns:
            bool: 是否成功删除（如果ID不存在返回False）
        """
        # 遍历列表，找到匹配的工序
        for i, station in enumerate(self.stations):
            if station.id == station_id:
                # 找到后删除并返回
                self.stations.pop(i)
                return True
        # 没找到，返回False
        return False
    
    def get_station(self, station_id: str) -> Optional[Station]:
        """
        根据ID查找工序
        
        Args:
            station_id: 工序ID
            
        Returns:
            Optional[Station]: 找到的工序对象，如果不存在返回None
        """
        for station in self.stations:
            if station.id == station_id:
                return station
        return None
    
    def find_bottleneck(self) -> Optional[Station]:
        """
        找出瓶颈工序（综合考虑多种因素）
        
        综合瓶颈识别算法考虑以下因素：
        1. **产能**：产能最低的工序是潜在瓶颈
        2. **WIP堆积**：WIP数量过多的工序可能是瓶颈
        3. **利用率**：高利用率的工序更可能是瓶颈
        4. **状态**：经常处于blocked或waiting状态的工序可能是瓶颈
        
        算法思路：
        1. 计算每个工序的综合瓶颈分数
        2. 分数越高，越可能是瓶颈
        3. 找出分数最高的工序作为瓶颈
        
        综合分数计算公式：
        score = (产能权重 × 产能因子) + 
               (WIP权重 × WIP因子) + 
               (利用率权重 × 利用率因子) + 
               (状态权重 × 状态因子)
        
        Returns:
            Optional[Station]: 瓶颈工序对象，如果产线为空返回None
            
        示例：
            line = ProductionLine("测试产线")
            line.add_station(Station("s01", "注油", 25, 2))  # 产能约233颗/h
            line.add_station(Station("s02", "焊接", 30, 3))  # 产能约306颗/h
            bottleneck = line.find_bottleneck()  # 返回"注油"工序
        """
        if self.production_type == ProductionType.LIQUID_FILLING:
            # 烟油由配方批次驱动，不以支持岗位的单件节拍判断瓶颈。
            return None
        if not self.stations:
            return None
        
        # 计算各工序的产能
        capacities = [station.get_capacity() for station in self.stations]
        if not capacities:
            return None
        
        # 计算瓶颈产能（用于计算利用率）
        min_capacity = min(capacities)
        max_capacity = max(capacities)
        capacity_range = max_capacity - min_capacity if max_capacity > min_capacity else 1
        
        # 权重配置（可根据实际情况调整）
        weights = {
            'capacity': 0.6,      # 产能权重，最高优先级
            'wip': 0.2,           # WIP堆积权重
            'utilization': 0.15,   # 利用率权重
            'status': 0.05        # 状态权重
        }
        
        # 计算每个工序的综合瓶颈分数
        bottleneck_scores = []
        for station in self.stations:
            # 1. 产能因子：产能越低，分数越高（0-1）
            capacity = station.get_capacity()
            if capacity_range > 0:
                capacity_factor = (max_capacity - capacity) / capacity_range
            else:
                capacity_factor = 1.0
            
            # 2. WIP因子：WIP越多，分数越高（0-1）
            # 归一化处理，假设WIP超过缓冲区容量的50%就算高
            wip_ratio = station.wip_count / station.buffer_capacity if station.buffer_capacity > 0 else 0
            wip_factor = min(wip_ratio / 0.5, 1.0)  # 超过50%缓冲区容量则为1.0
            
            # 3. 利用率因子：利用率越高，分数越高（0-1）
            utilization = station.get_utilization(min_capacity)
            utilization_factor = min(utilization, 1.0)  # 超过1.0则为1.0
            
            # 4. 状态因子：非idle状态时间越长，分数越高（0-1）
            # 简化处理：如果当前不是idle状态，给予较高分数
            status_factor = 1.0 if station.current_status != "idle" else 0.0
            
            # 计算综合分数
            total_score = (
                weights['capacity'] * capacity_factor +
                weights['wip'] * wip_factor +
                weights['utilization'] * utilization_factor +
                weights['status'] * status_factor
            )
            
            bottleneck_scores.append((total_score, station))
        
        # 找出分数最高的工序作为瓶颈
        # 如果分数相同，优先选择产能最低的工序
        bottleneck_scores.sort(key=lambda x: (-x[0], x[1].get_capacity()))
        
        return bottleneck_scores[0][1]
    
    def get_bottleneck_capacity(self) -> float:
        """生产时间内的理论上限；烟油按配方批次周期，不从人工工序推导。"""
        if self.production_type == ProductionType.LIQUID_FILLING:
            if not self.recipes:
                return 0.0
            recipe = self.recipes[0]
            quantity = recipe.batch_volume_l
            cycle = (recipe.mixing_time_min + recipe.aging_time_min + recipe.qc_time_min) / 60
            cycle += quantity / recipe.filling_rate_l_per_h
            return quantity * recipe.yield_rate / cycle if cycle > 0 else 0.0
        return min((station.get_capacity() for station in self.stations), default=0.0)

    def effective_hours(self, station: Optional[Station] = None) -> float:
        """每班生产窗口：先生产，休息及该站计划换型/清洗统一安排在班尾。"""
        downtime = station.planned_downtime_minutes() if station is not None else 0.0
        return max(0.0, self.shift_hours - (self.break_minutes + downtime) / 60.0)

    def calculate_daily_output(self) -> float:
        """单个配置班次的理论良品上限，不含缺料、排队及随机质量损失。"""
        if self.production_type == ProductionType.LIQUID_FILLING:
            # 烟油只执行用户列出的批次；不把配方速度乘工人数。
            remaining = self.effective_hours() * 3600
            output = 0.0
            previous = None
            elapsed = 0.0
            last_clean = 0.0
            recipes = {recipe.name: recipe for recipe in self.recipes}
            for index, batch in enumerate(self.batches):
                recipe = recipes.get(batch.recipe_name)
                if recipe is None or recipe.filling_rate_l_per_h <= 0:
                    return 0.0
                periodic = (
                    index > 0 and self.cip_interval_batches > 0
                    and index % self.cip_interval_batches == 0
                ) or (
                    self.cip_interval_hours > 0
                    and (elapsed - last_clean) / 3600 >= self.cip_interval_hours
                )
                clean = 0.0
                if previous is not None and previous != recipe.name:
                    clean = self.changeover_matrix.get(previous, {}).get(
                        recipe.name, recipe.clean_time_min
                    )
                if periodic:
                    clean = max(clean, recipe.clean_time_min or 30.0)
                cycle = (
                    (recipe.mixing_time_min + recipe.aging_time_min + recipe.qc_time_min
                     + clean) * 60 + batch.quantity_l / recipe.filling_rate_l_per_h * 3600
                )
                if cycle > remaining:
                    break
                remaining -= cycle
                elapsed += cycle
                if clean > 0:
                    last_clean = elapsed - cycle + clean * 60
                output += batch.quantity_l * recipe.yield_rate
                previous = recipe.name
            return output
        return min((station.get_capacity() * self.effective_hours(station)
                    for station in self.stations), default=0.0)

    def total_worker_count(self) -> int:
        """工序操作员及未在工序出现的支持工种，避免同一角色重复计数。"""
        represented = {station.job_role.value for station in self.stations}
        return sum(station.worker_count for station in self.stations) + sum(
            count for role, count in self.labor_config.items() if role not in represented
        )

    def configuration_revision(self) -> str:
        """配置身份；不包含运行时状态，供结果失效判定和报告追溯。"""
        data = self.to_dict()
        for station in data['stations']:
            for key in ('current_status', 'wip_count', 'total_output'):
                station.pop(key, None)
        for batch in data['batches']:
            for key in ('status', 'start_time', 'end_time', 'pass_rate', 'rework_count'):
                batch.pop(key, None)
        encoded = json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False)
        return hashlib.sha256(encoded.encode('utf-8')).hexdigest()

    def calculate_total_cost(self) -> float:
        """
        计算总人力成本（元/天）
        
        计算公式：
        总成本 = 总人数 × 时薪 × 班次时长
        
        Returns:
            float: 总人力成本（元/天）
        """
        # 计算总人数
        total_workers = self.total_worker_count()
        
        # 总成本 = 总人数 × 时薪 × 班次时长
        total_cost = total_workers * self.worker_hourly_wage * self.shift_hours
        
        return total_cost
    
    def calculate_unit_cost(self) -> float:
        """
        计算单颗成本（元/颗）
        
        计算公式：
        单颗成本 = 总成本 / 日产量
        
        Returns:
            float: 单颗成本（元/颗），如果日产量为0返回0
        """
        daily_output = self.calculate_daily_output()
        if daily_output == 0:
            return 0.0
        
        total_cost = self.calculate_total_cost()
        unit_cost = total_cost / daily_output
        
        return unit_cost
    
    def calculate_line_balance_rate(self) -> float:
        """
        计算产线平衡率
        
        产线平衡率 = 瓶颈产能 / 平均产能
        
        平衡率的意义：
        - 平衡率 = 1.0：产线完全平衡（理想状态，实际很难达到）
        - 平衡率 > 0.8：产线较为平衡
        - 平衡率 < 0.6：产线严重不平衡，需要优化
        
        Returns:
            float: 产线平衡率（0-1之间），如果产线为空返回0
        """
        if not self.stations:
            return 0.0
        
        # 计算平均产能
        total_capacity = sum(station.get_capacity() for station in self.stations)
        avg_capacity = total_capacity / len(self.stations)
        
        # 获取瓶颈产能
        bottleneck_capacity = self.get_bottleneck_capacity()
        
        if avg_capacity == 0:
            return 0.0
        
        # 平衡率 = 瓶颈产能 / 平均产能
        balance_rate = bottleneck_capacity / avg_capacity
        
        return balance_rate
    
    def calculate_upph(self) -> float:
        """
        计算UPPH（Units Per Person per Hour，单位时间内每人能生产的成品数量）
        
        UPPH是制造业中常用的人均效率指标，用于衡量：
        - 人均产出效率：数值越高，说明人力利用效率越高
        - 产线设计合理性：UPPH过低可能表示人力配置不合理
        - 培训效果：UPPH提升说明员工技能提升或流程优化
        
        计算公式：
        UPPH = 日产量 / (总工人数 × 有效工作时间)
        
        或者等价于：
        UPPH = 瓶颈产能 / 总工人数
        
        为什么使用瓶颈产能？
        - 产线的实际产出受瓶颈限制，所以用瓶颈产能更准确
        - 这样计算出的UPPH反映的是"在瓶颈约束下的人均效率"
        
        UPPH的典型范围：
        - 电子烟行业：通常UPPH在 30-80 颗/人·小时
        - UPPH < 30：可能人力配置过多或流程效率低
        - UPPH > 80：可能人力配置不足或设备自动化程度高
        
        Returns:
            float: UPPH值（颗/人·小时），如果产线为空或没有工人返回0
            
        示例：
            假设产线有10个工人，瓶颈产能为600颗/小时
            UPPH = 600 / 10 = 60 颗/人·小时
            表示平均每个工人每小时能产出60颗产品
        """
        # 如果产线为空，返回0
        if not self.stations:
            return 0.0
        
        # 计算总工人数
        total_workers = self.total_worker_count()
        
        # 如果没有工人，返回0（避免除零错误）
        if total_workers == 0:
            return 0.0
        
        # 获取瓶颈产能（颗/小时）
        bottleneck_capacity = self.get_bottleneck_capacity()
        
        # UPPH = 瓶颈产能 / 总工人数
        # 这个公式表示：在瓶颈约束下，平均每个工人每小时能产出多少颗
        upph = bottleneck_capacity / total_workers
        
        return upph

    def validate_labor(self) -> Tuple[bool, str]:
        """
        校验人力模型（V1.3）

        检查：
        - 工种合法性
        - 技能矩阵可互换工种合法性
        - 洁净区人数上限

        Returns:
            Tuple[bool, str]: (是否合法, 错误消息)
        """
        if not self.labor_config and not self.cleanroom_limits and not self.skill_matrix:
            return True, ""

        valid_roles = {role.value for role in JobRole}

        for role in self.labor_config:
            if role not in valid_roles:
                return False, f"未知工种：{role}"

        for role, interchangeable in self.skill_matrix.items():
            if role not in valid_roles:
                return False, f"技能矩阵包含未知工种：{role}"
            for other in interchangeable:
                if other not in valid_roles:
                    return False, f"技能矩阵包含未知互换工种：{other}"

        # 洁净区人数上限（按工序人数汇总）
        zone_count: Dict[str, int] = {}
        for station in self.stations:
            if station.cleanroom_zone:
                zone_count[station.cleanroom_zone] = (
                    zone_count.get(station.cleanroom_zone, 0) + station.worker_count
                )
        for zone, limit in self.cleanroom_limits.items():
            actual = zone_count.get(zone, 0)
            if actual > limit:
                return False, f"洁净区 {zone} 人数 {actual} 超过上限 {limit}"

        return True, ""

    def calculate_batch_cycle_min(self) -> float:
        """平均批次周期（分钟），未完成批次不计入"""
        finished = [b for b in self.batches if b.end_time > 0]
        if not finished:
            return 0.0
        total = sum((b.end_time - b.start_time) for b in finished)
        return total / len(finished) / 60.0

    def calculate_batch_pass_rate(self) -> float:
        """平均批次合格率"""
        if not self.batches:
            return 0.0
        return sum(b.pass_rate for b in self.batches) / len(self.batches)

    def calculate_avg_yield_rate(self) -> float:
        """平均配方收率"""
        if not self.recipes:
            return 0.0
        return sum(r.yield_rate for r in self.recipes) / len(self.recipes)

    def calculate_avg_machine_oee(self) -> float:
        """平均机台 OEE（尼古丁袋）"""
        machines = [s for s in self.stations if s.machine_takt]
        if not machines:
            return 0.0
        return sum(s.oee for s in machines) / len(machines)

    def get_unit(self) -> str:
        """
        获取当前生产类型的计量单位

        - 烟弹组装：颗
        - 烟油灌装：千克
        - 尼古丁袋：袋
        """
        return UNIT_LABELS.get(self.production_type, "颗")
    
    def to_dict(self) -> Dict[str, Any]:
        """
        将产线对象转换为字典格式（用于JSON保存）
        
        Returns:
            Dict: 包含产线所有字段的字典
        """
        return {
            'name': self.name,
            'shift_hours': self.shift_hours,
            'break_minutes': self.break_minutes,
            'worker_hourly_wage': self.worker_hourly_wage,
            'production_type': self.production_type.value,
            'recipes': [r.to_dict() for r in self.recipes],
            'batches': [b.to_dict() for b in self.batches],
            'tanks': [t.to_dict() for t in self.tanks],
            'labor_config': dict(self.labor_config),
            'cleanroom_limits': dict(self.cleanroom_limits),
            'skill_matrix': {k: list(v) for k, v in self.skill_matrix.items()},
            'cip_interval_batches': self.cip_interval_batches,
            'cip_interval_hours': self.cip_interval_hours,
            'materials': [m.to_dict() for m in self.materials],
            'inventory': dict(self.inventory),
            'material_arrivals': [a.to_dict() for a in self.material_arrivals],
            'changeover_matrix': {
                k: dict(v) for k, v in self.changeover_matrix.items()
            },
            'stations': [station.to_dict() for station in self.stations]
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ProductionLine':
        """
        从字典创建产线对象（用于JSON加载）
        
        Args:
            data: 包含产线数据的字典
            
        Returns:
            ProductionLine: 新创建的产线对象
        """
        # 创建产线对象
        line = cls(
            name=data['name'],
            shift_hours=data.get('shift_hours', 8),
            break_minutes=data.get('break_minutes', 60),
            worker_hourly_wage=data.get('worker_hourly_wage', 20.0),
            production_type=ProductionType(data.get('production_type', 'assembly')),
            recipes=[Recipe.from_dict(r) for r in data.get('recipes', [])],
            batches=[Batch.from_dict(b) for b in data.get('batches', [])],
            tanks=[Tank.from_dict(t) for t in data.get('tanks', [])],
            labor_config=dict(data.get('labor_config', {})),
            cleanroom_limits=dict(data.get('cleanroom_limits', {})),
            skill_matrix={k: list(v) for k, v in data.get('skill_matrix', {}).items()},
            cip_interval_batches=data.get('cip_interval_batches', 0),
            cip_interval_hours=data.get('cip_interval_hours', 0.0),
            materials=[Material.from_dict(m) for m in data.get('materials', [])],
            inventory=dict(data.get('inventory', {})),
            material_arrivals=[
                MaterialArrival.from_dict(a)
                for a in data.get('material_arrivals', [])
            ],
            changeover_matrix={
                k: dict(v)
                for k, v in data.get('changeover_matrix', {}).items()
            },
        )
        
        # 添加所有工序
        for station_data in data['stations']:
            station = Station.from_dict(station_data)
            line.add_station(station)
        
        return line


@dataclass
class Recipe:
    """
    配方模型（V1.3 烟油 / 尼古丁袋）

    描述一个口味/浓度变体的配方与工艺参数：
    - 批次量、收率、尼古丁浓度
    - 调配/陈化/灌装/QC 时长
    - CIP/SIP 清洗时长
    """
    name: str
    batch_volume_l: float = 100.0
    yield_rate: float = 0.95
    nicotine_concentration: float = 0.0  # mg/ml（烟油）或 mg/袋（袋装）
    flavor: str = ""
    ingredients: Dict[str, float] = field(default_factory=dict)
    mixing_time_min: float = 60.0
    aging_time_min: float = 120.0
    filling_rate_l_per_h: float = 500.0
    qc_time_min: float = 30.0
    clean_time_min: float = 45.0  # CIP/SIP 清洗时长

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Recipe":
        return cls(**data)


@dataclass
class Tank:
    """
    储罐模型（V1.3 烟油）
    """
    id: str
    name: str
    capacity_l: float = 1000.0
    current_level_l: float = 0.0
    cleaning_status: str = "clean"  # clean / in_use / cip / sip

    def available_capacity(self) -> float:
        """剩余可用容量（千克）"""
        return max(0.0, self.capacity_l - self.current_level_l)

    def add_liquid(self, volume: float) -> bool:
        """注入液体；容量不足时返回 False 且不写入"""
        if self.available_capacity() + 1e-6 < volume:
            return False
        self.current_level_l += volume
        return True

    def withdraw(self, volume: float) -> float:
        """取走液体，返回实际取走量（不足时取空）"""
        take = min(volume, self.current_level_l)
        self.current_level_l -= take
        return take

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Tank":
        return cls(**data)


@dataclass
class Batch:
    """
    批次模型（V1.3 烟油 / 尼古丁袋）
    """
    id: str
    recipe_name: str
    quantity_l: float = 100.0
    status: BatchStatus = BatchStatus.QUEUED
    start_time: float = 0.0
    end_time: float = 0.0
    trace_id: str = ""
    pass_rate: float = 1.0
    rework_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data['status'] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Batch":
        if isinstance(data.get('status'), str):
            data['status'] = BatchStatus(data['status'])
        return cls(**data)


@dataclass
class Material:
    """原料（V3.2 库存模型）"""
    name: str
    unit: str = "kg"
    initial_stock: float = 0.0
    unit_cost: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Material":
        return cls(**data)


@dataclass
class MaterialArrival:
    """原料到货计划（V3.2）"""
    time_minutes: float
    material: str
    quantity: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MaterialArrival":
        return cls(**data)


@dataclass
class Alert:
    """
    报警模型类 - 用于表示各种报警信息
    
    报警类型：
    - bottleneck: 瓶颈警报（某工序产能最低）
    - waste: 资源浪费警报（某工序产能过剩）
    - blockage: 堵塞风险警报（WIP堆积过多）
    
    报警严重程度：
    - critical: 严重（瓶颈，必须处理）
    - warning: 警告（浪费，建议优化）
    - info: 信息（一般提示）
    """
    
    alert_type: str  # 报警类型：bottleneck/waste/blockage
    severity: str  # 严重程度：critical/warning/info
    station_id: str  # 相关工序ID
    message: str  # 报警消息，显示给用户的文本
    suggestion: str = ""  # 优化建议，告诉用户该怎么做
    timestamp_minutes: float = 0.0  # 报警时间戳（从仿真开始经过的分钟数）

    def __post_init__(self) -> None:
        """Keep severity consistent for filtering, colors, and exported records."""
        self.severity = self.severity.strip().lower()
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于JSON序列化）"""
        return asdict(self)


@dataclass
class SimulationState:
    """
    仿真状态类 - 记录仿真过程中的状态快照
    
    用于在仿真过程中记录关键数据，用于：
    - GUI实时显示
    - 历史数据分析
    - 性能评估
    
    这个类在仿真过程中会频繁创建，所以字段要尽量精简
    """
    
    current_time: float  # 当前仿真时间（秒）
    station_states: Dict[str, Dict[str, Any]]  # 各工序状态字典
    # 格式：{station_id: {'status': 'running', 'output': 100, 'wip': 5}}
    
    total_output: float = 0.0  # 末端合格量；烟油单位为千克
    bottleneck_id: Optional[str] = None  # 当前瓶颈工序ID
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于JSON序列化）"""
        return asdict(self)


@dataclass
class Scenario:
    """
    方案类 - 存储产线配置的快照，用于方案对比
    
    这个类用于保存产线配置的某个状态，包含：
    - 产线配置（ProductionLine对象的深拷贝）
    - 方案名称和描述
    - 创建时间
    
    使用深拷贝的原因：
    - 防止后续修改影响已保存的方案
    - 确保方案数据的独立性
    
    示例用法：
        scenario = Scenario("方案A", production_line, "优化后的配置")
        kpis = scenario.get_kpis()  # 获取所有KPI指标
    """
    
    name: str  # 方案名称，如"方案A"、"优化方案"
    production_line: ProductionLine  # 产线配置快照（深拷贝）
    created_at: str  # 创建时间（格式化字符串，如"2024-01-15 10:30:00"）
    description: str = ""  # 方案描述（可选），用于记录方案的优化思路或特点
    
    def get_kpis(self) -> Dict[str, float]:
        """
        计算并返回所有KPI指标
        
        这个方法会实时计算产线的所有关键绩效指标，用于方案对比
        
        Returns:
            Dict[str, float]: KPI字典，包含：
                - total_workers: 总工人数
                - bottleneck_capacity: 瓶颈产能（颗/小时）
                - daily_output: 预计日产量（颗）
                - total_cost: 日成本（元）
                - unit_cost: 单颗成本（元/颗）
                - balance_rate: 产线平衡率（0-1）
                - upph: UPPH（颗/人·小时）
        """
        # 计算总工人数
        total_workers = self.production_line.total_worker_count()
        
        # 计算所有KPI指标
        kpis = {
            'total_workers': float(total_workers),
            'bottleneck_capacity': self.production_line.get_bottleneck_capacity(),
            'daily_output': self.production_line.calculate_daily_output(),
            'total_cost': self.production_line.calculate_total_cost(),
            'unit_cost': self.production_line.calculate_unit_cost(),
            'balance_rate': self.production_line.calculate_line_balance_rate(),
            'upph': self.production_line.calculate_upph(),
            'unit': self.production_line.get_unit(),
        }
        
        return kpis
    
    @classmethod
    def create(cls, name: str, production_line: ProductionLine, description: str = "") -> 'Scenario':
        """
        创建方案对象（工厂方法）
        
        这个方法会自动创建时间戳，并使用深拷贝保存产线配置
        
        Args:
            name: 方案名称
            production_line: 要保存的产线对象
            description: 方案描述（可选）
            
        Returns:
            Scenario: 新创建的方案对象
            
        为什么使用深拷贝？
        - 如果直接引用production_line，后续修改会影响已保存的方案
        - 深拷贝确保方案数据的独立性
        """
        # 获取当前时间并格式化
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 使用深拷贝保存产线配置快照
        # 这样即使后续修改production_line，也不会影响已保存的方案
        production_line_copy = copy.deepcopy(production_line)
        
        return cls(
            name=name,
            production_line=production_line_copy,
            created_at=created_at,
            description=description
        )


@dataclass
class SimulationResult:
    """
    仿真结果聚合对象 - 用于报告导出与测试断言

    由 SimulationEngine.build_result() 生成，包含：
    - 产线基本信息与 KPI
    - 各工序产出与 WIP
    - 报警列表、WIP 采样与切换事件记录
    """

    line_name: str
    duration_seconds: float
    total_output: float
    station_outputs: Dict[str, int]
    station_wips: Dict[str, int]
    kpis: Dict[str, float]
    alerts: List[Alert] = field(default_factory=list)
    wip_samples: List[Dict[str, Any]] = field(default_factory=list)
    changeover_events: List[Dict[str, Any]] = field(default_factory=list)
    batch_results: List[Dict[str, Any]] = field(default_factory=list)
    quality_results: List[Dict[str, Any]] = field(default_factory=list)
    cleaning_events: List[Dict[str, Any]] = field(default_factory=list)
    material_events: List[Dict[str, Any]] = field(default_factory=list)
    inventory: Dict[str, float] = field(default_factory=dict)
    labor_summary: Dict[str, int] = field(default_factory=dict)
    unit: str = "颗"
    station_metrics: Dict[str, Dict[str, float]] = field(default_factory=dict)
    line_config: Dict[str, Any] = field(default_factory=dict)
    # V3.4：默认值确保已有测试和第三方构造器仍可使用旧参数。
    run_id: str = ""
    config_revision: str = ""
    random_seed: Optional[int] = None
    elapsed_seconds: float = 0.0
    measurement_seconds: float = 0.0
    warmup_seconds: float = 0.0
    status: str = "completed"
    model_scope: str = "discrete_station_flow"
    closing_tanks: List[Dict[str, Any]] = field(default_factory=list)
    incomplete_batches: List[Dict[str, Any]] = field(default_factory=list)
    liquid_balance: Dict[str, float] = field(default_factory=dict)


# ==================== V1.3 模板产线工厂函数 ====================


def create_liquid_line(name: str = "烟油灌装线") -> ProductionLine:
    """创建烟油灌装型模板产线（V1.3）"""
    line = ProductionLine(name, production_type=ProductionType.LIQUID_FILLING, worker_hourly_wage=25.0)
    line.recipes.append(Recipe(
        name="经典烟草",
        batch_volume_l=500.0,
        yield_rate=0.95,
        nicotine_concentration=20.0,
        flavor="经典",
        ingredients={"尼古丁": 20.0, "丙二醇": 400.0, "香料": 80.0},
        mixing_time_min=60.0,
        aging_time_min=240.0,
        filling_rate_l_per_h=800.0,
        qc_time_min=30.0,
        clean_time_min=60.0,
    ))
    line.tanks.append(Tank("T01", "调配罐", 2000.0, 0.0))
    line.tanks.append(Tank("T02", "成品罐", 3000.0, 0.0))
    line.batches.append(Batch("B001", "经典烟草", 500.0))
    line.add_station(Station(
        "s01", "灌装", 1.5, 2,
        job_role=JobRole.FILLING_OPERATOR,
        cleanroom_zone="C",
    ))
    line.add_station(Station(
        "s02", "封口", 1.0, 2,
        job_role=JobRole.FILLING_OPERATOR,
        cleanroom_zone="C",
    ))
    line.add_station(Station(
        "s03", "QC化验", 30.0, 1,
        job_role=JobRole.QC_TECHNICIAN,
        cleanroom_zone="C",
        sampling_rate=0.2,
        defect_rate=0.01,
        rework_minutes=15.0,
    ))
    line.labor_config = {
        JobRole.MIXER.value: 1,
        JobRole.FILLING_OPERATOR.value: 4,
        JobRole.QC_TECHNICIAN.value: 1,
        JobRole.CLEANER.value: 1,
    }
    line.cleanroom_limits = {"C": 6}
    line.skill_matrix = {
        JobRole.FILLING_OPERATOR.value: [JobRole.CLEANER.value],
    }
    return line


def create_pouch_line(name: str = "尼古丁袋包装线") -> ProductionLine:
    """创建尼古丁袋高速包装模板产线（V1.3）"""
    line = ProductionLine(name, production_type=ProductionType.POUCH_PACKAGING)
    line.recipes.append(Recipe(
        name="薄荷袋",
        batch_volume_l=50.0,
        yield_rate=0.98,
        nicotine_concentration=8.0,
        flavor="薄荷",
        mixing_time_min=30.0,
        aging_time_min=0.0,
        filling_rate_l_per_h=600.0,
        qc_time_min=20.0,
        clean_time_min=30.0,
    ))
    line.batches.append(Batch("P001", "薄荷袋", 50.0))
    line.tanks.append(Tank("T01", "混合罐", 200.0, 0.0))
    line.add_station(Station(
        "p01", "填充机", 1.0, 2,
        machine_takt=1.5,
        machine_count=2,
        oee=0.90,
        clean_time_minutes=30.0,
        job_role=JobRole.PACKAGING_OPERATOR,
        cleanroom_zone="C",
        sampling_rate=0.05,
        defect_rate=0.005,
        rework_minutes=2.0,
    ))
    line.add_station(Station(
        "p02", "密封机", 1.0, 2,
        machine_takt=1.2,
        machine_count=2,
        oee=0.92,
        job_role=JobRole.PACKAGING_OPERATOR,
        cleanroom_zone="C",
    ))
    line.add_station(Station(
        "p03", "在线检测", 1.0, 1,
        machine_takt=1.0,
        oee=0.95,
        job_role=JobRole.QC_TECHNICIAN,
        cleanroom_zone="C",
        sampling_rate=0.1,
        defect_rate=0.01,
        rework_minutes=3.0,
    ))
    line.labor_config = {
        JobRole.MIXER.value: 1,
        JobRole.PACKAGING_OPERATOR.value: 5,
        JobRole.QC_TECHNICIAN.value: 1,
        JobRole.CLEANER.value: 1,
    }
    line.cleanroom_limits = {"C": 8}
    return line
