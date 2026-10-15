"""
方案管理器模块 - 管理所有保存的方案

这个模块提供方案管理功能，包括：
- 保存方案
- 删除方案
- 获取方案列表
- 对比方案KPI

设计说明：
- 方案保存在内存中（程序关闭后丢失）
- 方案数量限制：至少2个，至多3个
- 使用深拷贝确保方案数据独立性
"""

import json
import os
import copy
from typing import Dict, List, Optional, Any, Tuple

from src.models import Scenario, ProductionLine
from src.utils import atomic_write_json, validate_production_line


class ScenarioManager:
    """
    方案管理器类 - 管理所有保存的方案
    
    功能：
    - 保存方案（最多5个）
    - 删除方案
    - 获取方案列表
    - 对比多个方案的KPI
    
    方案数量限制：
    - 最少2个方案才能对比
    - 最多保存5个方案（满足 PRD 验收）
    
    使用方式：
        manager = ScenarioManager()
        manager.save_scenario("方案A", production_line)
        scenarios = manager.list_scenarios()
        comparison = manager.compare_scenarios(["方案A", "方案B"])
    """
    
    def __init__(self):
        """初始化方案管理器"""
        # 方案字典，key为方案名称，value为Scenario对象
        self.scenarios: Dict[str, Scenario] = {}
        
        # 方案数量限制
        self.MAX_SCENARIOS = 5  # 最多5个方案（PRD 验收标准）
        self.MIN_SCENARIOS_FOR_COMPARE = 2  # 至少2个方案才能对比
        self.storage_path: Optional[str] = None  # 自动持久化路径

    def set_storage_path(self, path: str) -> None:
        """设置自动持久化路径（保存/删除方案后自动落盘）"""
        self.storage_path = path
    
    def save_scenario(self, name: str, production_line: ProductionLine, description: str = "") -> Tuple[bool, Optional[str]]:
        """
        保存方案
        
        如果方案数量已达到上限（3个），会返回错误提示
        
        Args:
            name: 方案名称（必填，不能重复）
            production_line: 要保存的产线对象
            description: 方案描述（可选）
            
        Returns:
            tuple[bool, Optional[str]]: 
                - 第一个元素：是否保存成功
                - 第二个元素：错误消息（如果失败）
                
        示例：
            success, error = manager.save_scenario("方案A", line, "优化后的配置")
            if success:
                print("保存成功")
            else:
                print(f"保存失败：{error}")
        """
        # 验证方案名称
        if not name or not name.strip():
            return False, "方案名称不能为空"
        
        name = name.strip()
        
        # 检查名称是否已存在
        if name in self.scenarios:
            return False, f"方案名称'{name}'已存在，请使用不同的名称"
        
        # 检查方案数量限制
        if len(self.scenarios) >= self.MAX_SCENARIOS:
            return False, f"方案数量已达到上限（{self.MAX_SCENARIOS}个），请先删除一个方案"
        
        # 创建方案对象（使用工厂方法，自动处理深拷贝和时间戳）
        try:
            valid, error = validate_production_line(production_line, allow_empty=True)
            if not valid:
                return False, error
            scenario = Scenario.create(name, production_line, description)
            self.scenarios[name] = scenario
            try:
                self._persist()
            except Exception:
                del self.scenarios[name]
                raise
            return True, None
        except Exception as e:
            return False, f"保存方案失败：{str(e)}"
    
    def delete_scenario(self, name: str) -> Tuple[bool, Optional[str]]:
        """
        删除方案
        
        Args:
            name: 方案名称
            
        Returns:
            tuple[bool, Optional[str]]: 
                - 第一个元素：是否删除成功
                - 第二个元素：错误消息（如果失败）
        """
        if name not in self.scenarios:
            return False, f"方案'{name}'不存在"
        
        # 检查删除后是否还能满足对比要求
        if len(self.scenarios) <= self.MIN_SCENARIOS_FOR_COMPARE:
            # 如果删除后方案数量少于2个，仍然允许删除（用户可能想重新开始）
            pass
        
        previous = self.scenarios.pop(name)
        try:
            self._persist()
            return True, None
        except Exception as exc:
            self.scenarios[name] = previous
            return False, f"删除方案失败，原方案已保留：{exc}"

    def _persist(self) -> None:
        """Persist a mutation or propagate failure so the caller can roll it back."""
        if self.storage_path:
            if not self.save_to_file(self.storage_path):
                raise OSError("方案文件未能保存")

    def save_to_file(self, path: str) -> bool:
        """
        将全部方案持久化为 JSON 文件

        Args:
            path: 保存路径

        Returns:
            bool: 是否成功
        """
        data = {
            name: {
                'created_at': scenario.created_at,
                'description': scenario.description,
                'production_line': scenario.production_line.to_dict(),
            }
            for name, scenario in self.scenarios.items()
        }
        directory = os.path.dirname(path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
        atomic_write_json(path, data)
        return True

    def export_scenario(self, name: str, path: str) -> bool:
        """
        将单个方案导出为 JSON 文件（自定义路径）

        导出格式与 save_to_file 一致（{方案名: 方案数据}），
        可被 load_from_file 重新加载。

        Args:
            name: 方案名称
            path: 目标文件路径（.json）

        Returns:
            bool: 是否成功
        """
        scenario = self.scenarios.get(name)
        if scenario is None:
            return False
        data = {
            name: {
                'created_at': scenario.created_at,
                'description': scenario.description,
                'production_line': scenario.production_line.to_dict(),
            }
        }
        directory = os.path.dirname(path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
        atomic_write_json(path, data)
        return True

    def load_from_file(self, path: str) -> bool:
        """
        从 JSON 文件加载方案

        Args:
            path: 文件路径

        Returns:
            bool: 是否成功
        """
        if not os.path.exists(path):
            return False
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            loaded = {}
            for name, item in data.items():
                line = ProductionLine.from_dict(item['production_line'])
                valid, error = validate_production_line(line, allow_empty=True)
                if not valid:
                    raise ValueError(error)
                loaded[name] = Scenario(
                    name=name,
                    production_line=line,
                    created_at=item.get('created_at', ''),
                    description=item.get('description', ''),
                )
            self.scenarios = loaded
            return True
        except Exception:
            return False
    
    def get_scenario(self, name: str) -> Optional[Scenario]:
        """
        获取指定方案
        
        Args:
            name: 方案名称
            
        Returns:
            Optional[Scenario]: 方案对象，如果不存在返回None
        """
        scenario = self.scenarios.get(name)
        return copy.deepcopy(scenario) if scenario is not None else None
    
    def list_scenarios(self) -> List[str]:
        """
        获取所有方案名称列表
        
        Returns:
            List[str]: 方案名称列表，按创建时间排序
        """
        # 按创建时间排序
        sorted_scenarios = sorted(
            self.scenarios.items(),
            key=lambda x: x[1].created_at
        )
        return [name for name, _ in sorted_scenarios]
    
    def get_scenario_count(self) -> int:
        """
        获取当前方案数量
        
        Returns:
            int: 方案数量
        """
        return len(self.scenarios)
    
    def can_add_scenario(self) -> bool:
        """
        检查是否可以添加新方案
        
        Returns:
            bool: 是否可以添加（方案数量未达上限）
        """
        return len(self.scenarios) < self.MAX_SCENARIOS
    
    def can_compare(self) -> bool:
        """
        检查是否可以对比方案
        
        Returns:
            bool: 是否可以对比（至少2个方案）
        """
        return len(self.scenarios) >= self.MIN_SCENARIOS_FOR_COMPARE
    
    def compare_scenarios(self, scenario_names: List[str]) -> Dict[str, Any]:
        """
        对比多个方案的KPI
        
        对比逻辑：
        1. 计算每个方案的所有KPI
        2. 以第一个方案为基准，计算其他方案的差异
        3. 推荐最佳方案（单颗成本最低）
        
        Args:
            scenario_names: 要对比的方案名称列表（至少2个）
            
        Returns:
            Dict[str, Any]: 对比结果，包含：
                - 'scenarios': 方案KPI列表
                - 'differences': 差异列表
                - 'recommendation': 推荐方案名称和理由
                
        示例：
            comparison = manager.compare_scenarios(["方案A", "方案B", "方案C"])
            # 返回对比结果，包含KPI和差异
        """
        # 验证方案数量
        if len(scenario_names) < self.MIN_SCENARIOS_FOR_COMPARE:
            raise ValueError(f"至少需要{self.MIN_SCENARIOS_FOR_COMPARE}个方案才能对比")
        
        # 验证所有方案都存在
        for name in scenario_names:
            if name not in self.scenarios:
                raise ValueError(f"方案'{name}'不存在")
        units = {self.scenarios[name].production_line.get_unit() for name in scenario_names}
        if len(units) != 1:
            raise ValueError("不同生产类型或计量单位的方案不能直接比较")
        
        # 获取所有方案的KPI
        scenario_kpis = []
        for name in scenario_names:
            scenario = self.scenarios[name]
            kpis = scenario.get_kpis()
            scenario_kpis.append({
                'name': name,
                'kpis': kpis
            })
        
        # 计算差异（以第一个方案为基准）
        differences = []
        if len(scenario_kpis) > 1:
            base_kpis = scenario_kpis[0]['kpis']
            unit = base_kpis.get('unit', '颗')
            
            # KPI指标列表（用于对比）
            kpi_metrics = [
                ('total_workers', '总人数', '人'),
                ('bottleneck_capacity', '瓶颈产能', f'{unit}/h'),
                ('daily_output', '日产量', unit),
                ('total_cost', '预计日人工成本', '元'),
                ('unit_cost', '预计单位人工成本', f'元/{unit}'),
                ('balance_rate', '产线平衡率', '%'),
                ('upph', 'UPPH', f'{unit}/人·h')
            ]
            
            for metric_key, metric_name, unit in kpi_metrics:
                base_value = base_kpis[metric_key]
                
                # 计算每个方案相对于基准方案的差异
                diff_values = []
                for i, scenario_data in enumerate(scenario_kpis):
                    value = scenario_data['kpis'][metric_key]
                    if i == 0:
                        # 基准方案，差异为0
                        diff_values.append({
                            'value': value,
                            'diff_absolute': 0.0,
                            'diff_percent': 0.0
                        })
                    else:
                        # 计算绝对差异和百分比差异
                        diff_absolute = value - base_value
                        if base_value != 0:
                            diff_percent = (diff_absolute / base_value) * 100
                        else:
                            diff_percent = 0.0 if diff_absolute == 0 else 100.0
                        
                        diff_values.append({
                            'value': value,
                            'diff_absolute': diff_absolute,
                            'diff_percent': diff_percent
                        })
                
                differences.append({
                    'metric_key': metric_key,
                    'metric_name': metric_name,
                    'unit': unit,
                    'values': diff_values
                })
        
        # 推荐最佳方案（单位成本最低）
        best_scenario = None
        best_unit_cost = float('inf')
        recommendation_reason = ""
        
        for scenario_data in scenario_kpis:
            unit_cost = scenario_data['kpis']['unit_cost']
            if unit_cost > 0 and unit_cost < best_unit_cost:
                best_unit_cost = unit_cost
                best_scenario = scenario_data['name']
        
        if best_scenario:
            # 获取最佳方案的其他优势
            best_kpis = next(s['kpis'] for s in scenario_kpis if s['name'] == best_scenario)
            advantages = []
            
            # 检查各项指标是否最优
            for key, label in [('daily_output', '预计产能最高'),
                               ('balance_rate', '预计平衡率最高'), ('upph', '预计UPPH最高')]:
                if all(best_kpis[key] >= other['kpis'][key] for other in scenario_kpis):
                    advantages.append(label)
            
            if advantages:
                recommendation_reason = f"{best_scenario}（预计单位人工成本最低，{', '.join(advantages)}）"
            else:
                recommendation_reason = f"{best_scenario}（预计单位人工成本最低）"
        
        return {
            'scenarios': scenario_kpis,
            'differences': differences,
            'recommendation': recommendation_reason if best_scenario else "无法推荐"
        }
