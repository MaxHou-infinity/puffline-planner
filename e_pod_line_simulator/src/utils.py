"""
工具函数层 - 提供各种辅助功能

这个文件包含所有工具函数，包括：
- 文件I/O：JSON和Excel的读写
- 参数校验：验证用户输入
- 日志管理：记录程序运行日志
- 辅助函数：格式化、计算等

设计原则：
1. 函数独立：每个函数只做一件事，不依赖其他业务逻辑
2. 错误处理：完善的异常处理和错误提示
3. 易于测试：纯函数，输入输出明确
"""

import json
import os
import sys
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
import logging

from src.models import (
    Batch,
    JobRole,
    Material,
    MaterialArrival,
    ProductionLine,
    ProductionType,
    Recipe,
    Station,
    Tank,
)
from src.theme import alert_color as _theme_alert_color
from src.theme import status_color as _theme_status_color


# ==================== 首次运行引导 ====================

def get_app_data_dir(app_name: str = "PuffLinePlanner") -> str:
    """返回应用用户数据目录（跨平台，不依赖第三方库）"""
    home = Path.home()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", str(home)))
    elif sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(home / ".config")))
    path = base / app_name
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return str(path)


def get_first_run_marker_path() -> str:
    """首次运行标记文件路径（存在即视为已完成引导）"""
    return os.path.join(get_app_data_dir(), "first_run.json")


def should_show_first_run(marker_path: Optional[str] = None) -> bool:
    """首次运行检测：标记文件不存在时返回 True（应展示引导）"""
    return not os.path.exists(marker_path or get_first_run_marker_path())


def mark_first_run_done(marker_path: Optional[str] = None) -> bool:
    """写入首次运行标记；写入失败不阻塞启动，返回是否成功"""
    target = marker_path or get_first_run_marker_path()
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            json.dump({"first_run_done": True}, f)
        return True
    except OSError:
        return False


def parse_shift_params(
    shift_hours: str, break_minutes: str, wage: str
) -> Tuple[float, float, float]:
    """解析快速配置向导的班次参数，返回 (班次时长, 休息分钟, 时薪)。

    任一参数不合法时抛出 ValueError，消息指明具体字段，便于界面直接展示。
    """
    try:
        hours = float(shift_hours.strip())
    except (TypeError, ValueError):
        raise ValueError(f"班次时长必须是大于 0 的数字，当前输入：{shift_hours!r}")
    if hours <= 0:
        raise ValueError(f"班次时长必须大于 0，当前输入：{shift_hours!r}")

    try:
        minutes = float(break_minutes.strip())
    except (TypeError, ValueError):
        raise ValueError(f"休息时间必须是非负数字（分钟），当前输入：{break_minutes!r}")
    if minutes < 0:
        raise ValueError(f"休息时间不能为负数，当前输入：{break_minutes!r}")

    try:
        hourly_wage = float(wage.strip())
    except (TypeError, ValueError):
        raise ValueError(f"工人时薪必须是大于 0 的数字，当前输入：{wage!r}")
    if hourly_wage <= 0:
        raise ValueError(f"工人时薪必须大于 0，当前输入：{wage!r}")

    return hours, minutes, hourly_wage


# ==================== 文件I/O ====================

def load_config(file_path: str) -> Optional[ProductionLine]:
    """
    从JSON文件加载产线配置
    
    这是配置文件加载的核心函数，用于：
    - 加载用户保存的产线配置
    - 加载示例配置
    - 导入其他来源的配置
    
    Args:
        file_path: JSON文件路径
        
    Returns:
        Optional[ProductionLine]: 加载的产线对象，如果加载失败返回None
        
    示例：
        line = load_config("configs/default.json")
        if line:
            print(f"加载产线：{line.name}")
    """
    try:
        # 检查文件是否存在
        if not os.path.exists(file_path):
            print(f"文件不存在：{file_path}")
            return None
        
        # 读取JSON文件
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 从字典创建产线对象
        line = ProductionLine.from_dict(data)
        
        return line
        
    except json.JSONDecodeError as e:
        # JSON格式错误
        print(f"JSON格式错误：{e}")
        return None
    except KeyError as e:
        # 缺少必需字段
        print(f"配置文件缺少字段：{e}")
        return None
    except Exception as e:
        # 其他错误
        print(f"加载配置失败：{e}")
        return None


def save_config(production_line: ProductionLine, file_path: str) -> bool:
    """
    将产线配置保存为JSON文件
    
    用于保存用户配置，方便下次使用
    
    Args:
        production_line: 要保存的产线对象
        file_path: 保存路径
        
    Returns:
        bool: 是否保存成功
        
    示例：
        success = save_config(line, "configs/my_line.json")
        if success:
            print("保存成功")
    """
    try:
        # 确保目录存在
        directory = os.path.dirname(file_path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
        
        # 转换为字典
        data = production_line.to_dict()
        
        # 写入JSON文件（格式化输出，便于阅读）
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        return True
        
    except Exception as e:
        print(f"保存配置失败：{e}")
        return False


def load_ui_config() -> Dict[str, Any]:
    """
    读取 UI 配置（如主题），文件不存在时返回空字典
    """
    try:
        with open(UI_CONFIG_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_ui_config(config: Dict[str, Any]) -> bool:
    """
    保存 UI 配置（如主题）
    """
    try:
        directory = os.path.dirname(UI_CONFIG_FILE)
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
        with open(UI_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def import_from_excel(file_path: str) -> Tuple[Optional[ProductionLine], Optional[str]]:
    """
    从Excel文件导入产线配置
    
    Excel格式要求（标准模板）：
    | 工序名 | 耗时(秒) | 人数 | 模式 | OEE | 人员效率 | 切换时间(分钟) | 缓冲区容量 |
    |--------|----------|------|------|-----|----------|----------------|------------|
    | 注油   | 25       | 2    | 并联 | 0.85| 0.95     | 45             | 100        |
    
    必填字段：工序名、耗时(秒)、人数
    可选字段：模式（默认"并联"）、OEE（默认0.85）、人员效率（默认0.95）、切换时间（默认45）、缓冲区容量（默认100）
    
    Args:
        file_path: Excel文件路径
        
    Returns:
        Tuple[Optional[ProductionLine], Optional[str]]: 
            - 第一个元素：导入的产线对象，如果导入失败返回None
            - 第二个元素：错误消息，如果成功返回None
            
    示例：
        line, error = import_from_excel("configs/产线配置.xlsx")
        if line:
            print(f"导入成功：{line.name}")
        else:
            print(f"导入失败：{error}")
    """
    try:
        # 尝试导入pandas（如果安装了）
        try:
            import pandas as pd
        except ImportError:
            error_msg = "需要安装pandas和openpyxl库才能导入Excel\n请运行：pip install pandas openpyxl"
            return None, error_msg
        
        # 检查文件是否存在
        if not os.path.exists(file_path):
            return None, f"文件不存在：{file_path}"
        
        # 读取Excel文件（支持.xlsx和.xls格式）
        try:
            df = pd.read_excel(file_path, engine='openpyxl')
        except Exception as e:
            return None, f"无法读取Excel文件：{str(e)}\n请确保文件格式正确（.xlsx或.xls）"
        
        # 检查是否为空文件
        if df.empty:
            return None, "Excel文件为空，没有数据行"
        
        # 列名映射（支持多种可能的列名）
        column_mapping = {
            'name': ['工序名', '工序名称', '名称', 'name', 'Name', 'NAME'],
            'process_time': ['耗时(秒)', '耗时', '单颗耗时', 'process_time', 'Process Time', 'PROCESS_TIME', '时间', '秒'],
            'worker_count': ['人数', '工人数', '人员数', 'worker_count', 'Worker Count', 'WORKER_COUNT', '工人数量'],
            'collaboration_type': ['模式', '协作模式', 'collaboration_type', 'Collaboration Type', 'COLLABORATION_TYPE', '类型'],
            'oee': ['OEE', 'oee', '设备效率', '设备综合效率'],
            'efficiency': ['人员效率', '效率', 'efficiency', 'Efficiency', 'EFFICIENCY', '人员效率系数'],
            'changeover_time': ['切换时间(分钟)', '切换时间', 'changeover_time', 'Changeover Time', 'CHANGEOVER_TIME', '切换'],
            'buffer_capacity': ['缓冲区容量', '缓冲区', 'buffer_capacity', 'Buffer Capacity', 'BUFFER_CAPACITY', '缓冲'],
            'machine_takt': ['机台节拍(秒)', '机台节拍', 'machine_takt', 'Machine Takt', 'MACHINE_TAKT'],
            'clean_time_minutes': ['清洗时间(分钟)', '清洗时间', 'clean_time_minutes', 'Clean Time', 'CLEAN_TIME_MINUTES'],
            'sampling_rate': ['抽检比例', 'sampling_rate', 'Sampling Rate', 'SAMPLING_RATE'],
            'defect_rate': ['缺陷率', 'defect_rate', 'Defect Rate', 'DEFECT_RATE'],
            'rework_minutes': ['返工时长(分钟)', '返工时长', 'rework_minutes', 'Rework Minutes', 'REWORK_MINUTES'],
            'job_role': ['工种', 'job_role', 'Job Role', 'JOB_ROLE'],
            'cleanroom_zone': ['洁净区', 'cleanroom_zone', 'Cleanroom Zone', 'CLEANROOM_ZONE'],
            'bom': ['BOM(组件:用量;...)', 'BOM', 'bom', 'BOM组件'],
        }
        
        # 查找实际列名
        actual_columns = {}
        for key, possible_names in column_mapping.items():
            for col in df.columns:
                col_str = str(col).strip()
                if col_str in possible_names:
                    actual_columns[key] = col_str
                    break
        
        # 检查必填字段
        required_fields = ['name', 'process_time', 'worker_count']
        missing_fields = [field for field in required_fields if field not in actual_columns]
        if missing_fields:
            field_names = {
                'name': '工序名',
                'process_time': '耗时(秒)',
                'worker_count': '人数'
            }
            missing_names = [field_names.get(f, f) for f in missing_fields]
            return None, f"Excel文件缺少必填列：{', '.join(missing_names)}\n请参考模板格式"
        
        # 创建产线对象
        line = ProductionLine(name="从Excel导入")

        # V1.3：读取生产类型
        try:
            pt_value = ""
            try:
                cfg_df = pd.read_excel(
                    file_path, sheet_name='产线配置', engine='openpyxl'
                )
                for _, row in cfg_df.iterrows():
                    if str(row.iloc[0]).strip() == 'production_type':
                        pt_value = str(row.iloc[1]).strip().lower()
                        break
            except Exception:
                pass
            if not pt_value:
                pt_df = pd.read_excel(
                    file_path, sheet_name='生产类型', header=None,
                    engine='openpyxl',
                )
                pt_value = str(pt_df.iloc[0, 1]).strip().lower()
            if pt_value in ('assembly', 'liquid_filling', 'pouch_packaging'):
                line.production_type = ProductionType(pt_value)
        except Exception:
            pass

        # V3.3.1：读取产线级配置（班次/CIP/换型矩阵）
        try:
            config_df = pd.read_excel(file_path, sheet_name='产线配置', engine='openpyxl')
            config_map = {}
            for _, row in config_df.iterrows():
                key = str(row.iloc[0]).strip()
                config_map[key] = row.iloc[1]
            if config_map.get('shift_hours') not in (None, ''):
                line.shift_hours = int(float(config_map['shift_hours']))
            if config_map.get('break_minutes') not in (None, ''):
                line.break_minutes = int(float(config_map['break_minutes']))
            if config_map.get('worker_hourly_wage') not in (None, ''):
                line.worker_hourly_wage = float(config_map['worker_hourly_wage'])
            if config_map.get('cip_interval_batches') not in (None, ''):
                line.cip_interval_batches = int(float(config_map['cip_interval_batches']))
            if config_map.get('cip_interval_hours') not in (None, ''):
                line.cip_interval_hours = float(config_map['cip_interval_hours'])
            matrix_text = str(config_map.get('changeover_matrix_json') or '').strip()
            if matrix_text:
                matrix = json.loads(matrix_text)
                if isinstance(matrix, dict):
                    line.changeover_matrix = matrix
        except Exception:
            pass

        # V3.3.1：读取原料与到货计划
        try:
            material_df = pd.read_excel(file_path, sheet_name='原料', engine='openpyxl')
            for _, row in material_df.iterrows():
                material = Material(
                    name=str(row['原料名']).strip(),
                    unit=str(row.get('单位', 'kg')).strip(),
                    initial_stock=float(row.get('初始库存', 0)),
                    unit_cost=float(row.get('单价', 0) or 0),
                )
                line.materials.append(material)
                line.inventory[material.name] = material.initial_stock
        except Exception:
            pass

        try:
            arrival_df = pd.read_excel(
                file_path, sheet_name='到货计划', engine='openpyxl'
            )
            for _, row in arrival_df.iterrows():
                line.material_arrivals.append(MaterialArrival(
                    time_minutes=float(row['到货时间(分钟)']),
                    material=str(row['原料']).strip(),
                    quantity=float(row['数量']),
                ))
        except Exception:
            pass

        # V1.3：读取配方/罐/批次
        try:
            recipe_df = pd.read_excel(file_path, sheet_name='配方', engine='openpyxl')
            for _, row in recipe_df.iterrows():
                line.recipes.append(Recipe(
                    name=str(row['配方名']).strip(),
                    batch_volume_l=float(row.get('批次量L', 100)),
                    yield_rate=float(row.get('收率', 0.95)),
                    nicotine_concentration=float(row.get('尼古丁浓度', 0)),
                    flavor=str(row.get('口味', '')),
                    mixing_time_min=float(row.get('调配min', 60)),
                    aging_time_min=float(row.get('陈化min', 120)),
                    filling_rate_l_per_h=float(row.get('灌装速率L/h', 500)),
                    qc_time_min=float(row.get('QCmin', 30)),
                    clean_time_min=float(row.get('清洗min', 45)),
                ))
        except Exception:
            pass

        try:
            tank_df = pd.read_excel(file_path, sheet_name='罐', engine='openpyxl')
            for _, row in tank_df.iterrows():
                line.tanks.append(Tank(
                    id=str(row['罐ID']).strip(),
                    name=str(row['罐名']).strip(),
                    capacity_l=float(row.get('容量L', 1000)),
                    current_level_l=float(row.get('当前液位L', 0)),
                ))
        except Exception:
            pass

        try:
            batch_df = pd.read_excel(file_path, sheet_name='批次', engine='openpyxl')
            for _, row in batch_df.iterrows():
                line.batches.append(Batch(
                    id=str(row['批次ID']).strip(),
                    recipe_name=str(row['配方名']).strip(),
                    quantity_l=float(row.get('批次量L', 100)),
                ))
        except Exception:
            pass
        
        # 错误收集列表
        errors = []
        
        # 遍历每一行，创建工序
        for idx, row in df.iterrows():
            try:
                # 获取必填字段
                name = str(row[actual_columns['name']]).strip()
                if not name or name == 'nan' or name.lower() == 'nan':
                    errors.append(f"第{idx+2}行：工序名不能为空")
                    continue
                
                # 获取耗时（秒）
                try:
                    process_time = float(row[actual_columns['process_time']])
                    if process_time <= 0:
                        errors.append(f"第{idx+2}行（{name}）：耗时必须大于0")
                        continue
                except (ValueError, TypeError):
                    errors.append(f"第{idx+2}行（{name}）：耗时格式错误，必须是数字")
                    continue
                
                # 获取人数
                try:
                    worker_count = int(float(row[actual_columns['worker_count']]))
                    if worker_count < 1:
                        errors.append(f"第{idx+2}行（{name}）：人数至少为1")
                        continue
                except (ValueError, TypeError):
                    errors.append(f"第{idx+2}行（{name}）：人数格式错误，必须是整数")
                    continue
                
                # 获取可选字段（使用默认值）
                collaboration_type_str = 'parallel'  # 默认并联
                if 'collaboration_type' in actual_columns:
                    col_val = str(row[actual_columns['collaboration_type']]).strip().lower()
                    if col_val in ['并联', 'parallel', 'parallel']:
                        collaboration_type_str = 'parallel'
                    elif col_val in ['协同', 'collaborative', 'collaborative']:
                        collaboration_type_str = 'collaborative'
                
                oee = 0.85  # 默认OEE
                if 'oee' in actual_columns:
                    try:
                        oee_val = float(row[actual_columns['oee']])
                        if 0 < oee_val <= 1:
                            oee = oee_val
                    except (ValueError, TypeError):
                        pass  # 使用默认值
                
                efficiency = 0.95  # 默认人员效率
                if 'efficiency' in actual_columns:
                    try:
                        eff_val = float(row[actual_columns['efficiency']])
                        if 0 < eff_val <= 1:
                            efficiency = eff_val
                    except (ValueError, TypeError):
                        pass  # 使用默认值
                
                changeover_time = 45  # 默认切换时间（分钟）
                if 'changeover_time' in actual_columns:
                    try:
                        changeover_val = int(float(row[actual_columns['changeover_time']]))
                        if changeover_val >= 0:
                            changeover_time = changeover_val
                    except (ValueError, TypeError):
                        pass  # 使用默认值
                
                buffer_capacity = 100  # 默认缓冲区容量
                if 'buffer_capacity' in actual_columns:
                    try:
                        buffer_val = int(float(row[actual_columns['buffer_capacity']]))
                        if buffer_val > 0:
                            buffer_capacity = buffer_val
                    except (ValueError, TypeError):
                        pass  # 使用默认值

                # V3.3.1：读取 V1.3+/V3.2 可选字段
                machine_takt = None
                if 'machine_takt' in actual_columns:
                    text = str(row[actual_columns['machine_takt']]).strip()
                    if text and text.lower() != 'nan':
                        machine_takt = float(text)

                clean_time_minutes = 0.0
                if 'clean_time_minutes' in actual_columns:
                    clean_time_minutes = float(
                        row[actual_columns['clean_time_minutes']] or 0
                    )

                sampling_rate = 0.0
                if 'sampling_rate' in actual_columns:
                    sampling_rate = float(
                        row[actual_columns['sampling_rate']] or 0
                    )

                defect_rate = 0.0
                if 'defect_rate' in actual_columns:
                    defect_rate = float(row[actual_columns['defect_rate']] or 0)

                rework_minutes = 0.0
                if 'rework_minutes' in actual_columns:
                    rework_minutes = float(
                        row[actual_columns['rework_minutes']] or 0
                    )

                job_role = JobRole.GENERAL
                if 'job_role' in actual_columns:
                    role_text = str(
                        row[actual_columns['job_role']]
                    ).strip()
                    role_map = {
                        '调香师': JobRole.MIXER,
                        '调配工': JobRole.MIXER,
                        '灌装操作员': JobRole.FILLING_OPERATOR,
                        'qc化验员': JobRole.QC_TECHNICIAN,
                        'qc 化验员': JobRole.QC_TECHNICIAN,
                        '化验员': JobRole.QC_TECHNICIAN,
                        '包装机手': JobRole.PACKAGING_OPERATOR,
                        '清洗工': JobRole.CLEANER,
                        '通用装配工': JobRole.GENERAL,
                        '通用': JobRole.GENERAL,
                    }
                    job_role = role_map.get(role_text.lower(), JobRole.GENERAL)

                cleanroom_zone = ""
                if 'cleanroom_zone' in actual_columns:
                    cleanroom_zone = str(
                        row[actual_columns['cleanroom_zone']]
                    ).strip()

                bom = {}
                if 'bom' in actual_columns:
                    bom_text = str(row[actual_columns['bom']]).strip()
                    if bom_text and bom_text.lower() != 'nan':
                        for part in bom_text.split(';'):
                            if ':' not in part:
                                continue
                            component, amount = part.split(':', 1)
                            try:
                                bom[component.strip()] = float(amount)
                            except ValueError:
                                pass
                
                # 创建工序对象
                # 导入CollaborationType（延迟导入避免循环依赖）
                try:
                    from src.models import CollaborationType
                except ImportError:
                    from models import CollaborationType
                collaboration_type = CollaborationType.PARALLEL if collaboration_type_str == 'parallel' else CollaborationType.COLLABORATIVE
                
                station = Station(
                    id=f"s{idx+1:02d}",
                    name=name,
                    process_time=process_time,
                    worker_count=worker_count,
                    collaboration_type=collaboration_type,
                    oee=oee,
                    efficiency=efficiency,
                    changeover_time=changeover_time,
                    buffer_capacity=buffer_capacity,
                    machine_takt=machine_takt,
                    clean_time_minutes=clean_time_minutes,
                    sampling_rate=sampling_rate,
                    defect_rate=defect_rate,
                    rework_minutes=rework_minutes,
                    job_role=job_role,
                    cleanroom_zone=cleanroom_zone,
                    bom=bom,
                )
                
                # 校验工序参数
                valid, error_msg = validate_station(name, process_time, worker_count)
                if not valid:
                    errors.append(f"第{idx+2}行（{name}）：{error_msg}")
                    continue
                
                line.add_station(station)
                
            except Exception as e:
                errors.append(f"第{idx+2}行：处理失败 - {str(e)}")
                continue
        
        # 检查是否成功创建了至少一个工序
        if not line.stations:
            error_msg = "未能成功导入任何工序。\n"
            if errors:
                error_msg += "\n错误详情：\n" + "\n".join(errors[:10])  # 最多显示10个错误
                if len(errors) > 10:
                    error_msg += f"\n... 还有{len(errors)-10}个错误"
            return None, error_msg
        
        # 如果有部分错误，但仍然成功导入了部分数据，给出警告
        warning_msg = None
        if errors:
            warning_msg = f"成功导入{len(line.stations)}个工序，但有{len(errors)}行数据有错误：\n" + "\n".join(errors[:5])
            if len(errors) > 5:
                warning_msg += f"\n... 还有{len(errors)-5}个错误"
        
        # 校验整个产线配置
        valid, error_msg = validate_production_line(line)
        if not valid:
            return None, f"导入的产线配置不合法：{error_msg}"
        
        # 返回结果（如果有警告，会在GUI中显示）
        return line, warning_msg
        
    except Exception as e:
        return None, f"导入Excel失败：{str(e)}"


def create_excel_template(file_path: str, production_type: str = "assembly") -> bool:
    """
    创建Excel导入模板文件
    
    创建一个标准的Excel模板文件，用户可以参考这个模板来填写自己的产线配置

    V1.3：支持 production_type 参数（assembly / liquid_filling / pouch_packaging），
    液体/袋装类型额外生成 配方、罐、批次 三个 Sheet。
    
    Args:
        file_path: 模板文件保存路径
        production_type: 生产类型（assembly / liquid_filling / pouch_packaging）
        
    Returns:
        bool: 是否创建成功
        
    示例：
        success = create_excel_template("configs/产线配置模板.xlsx")
    """
    try:
        # 尝试导入pandas（如果安装了）
        try:
            import pandas as pd
        except ImportError:
            print("需要安装pandas和openpyxl库才能创建Excel模板\n请运行：pip install pandas openpyxl")
            return False
        
        # 确保目录存在
        directory = os.path.dirname(file_path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
        
        # 创建示例数据（V3.3.1：补齐 V1.3+/V3.2 字段）
        template_data = [
            ['注油', 25, 2, '并联', 0.85, 0.95, 45, 100, '', 0, 0, 0, 0,
             '通用装配工', '', ''],
            ['焊接', 30, 3, '并联', 0.90, 0.95, 45, 100, '', 0, 0, 0, 0,
             '通用装配工', '', ''],
            ['组装', 20, 2, '协同', 0.88, 0.95, 0, 100, '', 0, 0, 0, 0,
             '通用装配工', '', ''],
            ['包装', 15, 2, '并联', 0.92, 0.95, 0, 100, '', 0, 0, 0, 0,
             '通用装配工', '', ''],
        ]

        df = pd.DataFrame(
            template_data,
            columns=[
                '工序名', '耗时(秒)', '人数', '模式', 'OEE', '人员效率',
                '切换时间(分钟)', '缓冲区容量', '机台节拍(秒)', '清洗时间(分钟)',
                '抽检比例', '缺陷率', '返工时长(分钟)', '工种', '洁净区',
                'BOM(组件:用量;...)',
            ],
        )

        # 产线级配置（V3.3.1）
        line_config_rows = [
            ['production_type', production_type],
            ['shift_hours', 8],
            ['break_minutes', 60],
            ['worker_hourly_wage', 20.0],
            ['cip_interval_batches', 0],
            ['cip_interval_hours', 0.0],
            ['changeover_matrix_json', ''],
        ]

        with pd.ExcelWriter(file_path, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='工序', index=False)
            pd.DataFrame(
                line_config_rows, columns=['配置项', '值']
            ).to_excel(writer, sheet_name='产线配置', index=False)

            if production_type in ('liquid_filling', 'pouch_packaging'):
                recipe_df = pd.DataFrame([
                    ['经典烟草', 500, 0.95, 20.0, '经典', 60, 240, 800, 30, 60],
                ], columns=[
                    '配方名', '批次量L', '收率', '尼古丁浓度', '口味',
                    '调配min', '陈化min', '灌装速率L/h', 'QCmin', '清洗min',
                ])
                recipe_df.to_excel(writer, sheet_name='配方', index=False)

                tank_df = pd.DataFrame([
                    ['T01', '调配罐', 2000, 0],
                    ['T02', '成品罐', 3000, 0],
                ], columns=['罐ID', '罐名', '容量L', '当前液位L'])
                tank_df.to_excel(writer, sheet_name='罐', index=False)

                batch_df = pd.DataFrame([
                    ['B001', '经典烟草', 500, 'queued'],
                ], columns=['批次ID', '配方名', '批次量L', '状态'])
                batch_df.to_excel(writer, sheet_name='批次', index=False)

            # 原料与到货计划（V3.3.1，组装 BOM/烟油投料通用）
            material_df = pd.DataFrame([
                ['棉芯', '个', 1000, 0.5],
            ], columns=['原料名', '单位', '初始库存', '单价'])
            material_df.to_excel(writer, sheet_name='原料', index=False)

            arrival_df = pd.DataFrame([
                [60, '棉芯', 500],
            ], columns=['到货时间(分钟)', '原料', '数量'])
            arrival_df.to_excel(writer, sheet_name='到货计划', index=False)

        return True
        
    except Exception as e:
        print(f"创建Excel模板失败：{e}")
        return False


# ==================== 参数校验 ====================

def validate_station(name: str, process_time: float, worker_count: int) -> Tuple[bool, str]:
    """
    校验工序参数
    
    检查用户输入的工序参数是否合法
    
    Args:
        name: 工序名称
        process_time: 单颗耗时（秒）
        worker_count: 工人数量
        
    Returns:
        Tuple[bool, str]: (是否合法, 错误消息)
        
    示例：
        valid, error = validate_station("注油", 25, 2)
        if not valid:
            print(f"验证失败：{error}")
    """
    # 校验名称
    if not name or not name.strip():
        return False, "工序名称不能为空"
    
    if len(name) > 20:
        return False, "工序名称不能超过20个字符"
    
    # 校验耗时
    if process_time <= 0:
        return False, "单件耗时必须大于0"
    
    if process_time > 3600:
        return False, "单件耗时不能超过3600秒（1小时）"
    
    # 校验人数
    if worker_count < 1:
        return False, "工人数量至少为1"
    
    if worker_count > 20:
        return False, "工人数量不能超过20（单工序）"
    
    # 所有检查通过
    return True, ""


def validate_production_line(line: ProductionLine) -> Tuple[bool, str]:
    """
    校验产线配置
    
    检查产线配置是否完整和合法
    
    Args:
        line: 产线对象
        
    Returns:
        Tuple[bool, str]: (是否合法, 错误消息)
    """
    # 检查是否有工序
    if not line.stations:
        return False, "产线至少需要一个工序"
    
    # 检查工序数量
    if len(line.stations) > 20:
        return False, "产线工序数量不能超过20个"
    
    # 检查每个工序
    station_ids = set()
    for station in line.stations:
        # 检查ID是否重复
        if station.id in station_ids:
            return False, f"工序ID重复：{station.id}"
        station_ids.add(station.id)
        
        # 校验工序参数
        valid, error = validate_station(
            station.name,
            station.process_time,
            station.worker_count
        )
        if not valid:
            return False, f"工序'{station.name}'参数错误：{error}"

    # V1.3：非组装类型校验人力模型（工种/技能矩阵/洁净区上限）
    if line.production_type != ProductionType.ASSEMBLY:
        labor_ok, labor_msg = line.validate_labor()
        if not labor_ok:
            return False, labor_msg
    
    # 所有检查通过
    return True, ""


# ==================== 日志管理 ====================

def setup_logger(log_file: Optional[str] = None, level: int = logging.INFO) -> logging.Logger:
    """
    设置日志系统
    
    配置日志格式和输出位置，用于记录程序运行日志
    
    Args:
        log_file: 日志文件路径，如果为None则使用默认路径 logs/app.log
        level: 日志级别，默认INFO
        
    Returns:
        logging.Logger: 配置好的日志对象
        
    示例：
        logger = setup_logger("logs/app.log")
        logger.info("程序启动")
    """
    # 创建日志格式
    # 格式：时间 - 级别 - 消息
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # 创建日志对象
    logger = logging.getLogger('epod_simulator')
    logger.setLevel(level)
    
    # 清除已有的处理器（避免重复添加）
    logger.handlers.clear()
    
    # 添加控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # 默认写入日志文件，便于追溯运行记录
    if log_file is None:
        log_file = LOG_FILE

    # 如果指定了日志文件，添加文件处理器
    if log_file:
        # 确保日志目录存在
        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
        
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    return logger


# ==================== 辅助函数 ====================

def format_time(seconds: float) -> str:
    """
    格式化时间显示
    
    将秒数转换为易读的时间格式（小时:分钟:秒）
    
    Args:
        seconds: 时间（秒）
        
    Returns:
        str: 格式化后的时间字符串
        
    示例：
        print(format_time(3661))  # 输出："01:01:01"
    """
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_number(num: float, decimals: int = 2) -> str:
    """
    格式化数字显示
    
    将浮点数格式化为指定小数位数的字符串
    
    Args:
        num: 要格式化的数字
        decimals: 小数位数，默认2位
        
    Returns:
        str: 格式化后的字符串
        
    示例：
        print(format_number(123.456))  # 输出："123.46"
    """
    return f"{num:.{decimals}f}"


def calculate_roi(investment: float, daily_savings: float) -> float:
    """
    计算投资回收期（天数）
    
    ROI = Return on Investment，投资回报率
    这里计算的是投资回收期，即需要多少天才能收回投资
    
    Args:
        investment: 投资金额（元）
        daily_savings: 每日节省金额（元/天）
        
    Returns:
        float: 回收期（天），如果daily_savings<=0则返回0
        
    示例：
        roi = calculate_roi(10000, 200)  # 投资1万，每天省200，回收期50天
    """
    if daily_savings <= 0:
        return 0.0
    
    return investment / daily_savings


def get_status_color(status: str) -> str:
    """
    根据状态获取颜色代码
    
    用于GUI显示，不同状态用不同颜色
    
    Args:
        status: 状态字符串（idle/running/blocked/waiting/changeover）
        
    Returns:
        str: 颜色代码（十六进制格式，如"#00FF00"）
        
    颜色方案来自 src/theme.py 设计令牌（V1.2.0）：
        - idle: 灰色（空闲）
        - running: 绿色（运行中）
        - blocked: 红色（堵塞）
        - waiting: 橙色（等待）
        - changeover: 蓝色（切换中）
    """
    return _theme_status_color(status)


def get_alert_color(severity: str) -> str:
    """
    根据报警严重程度获取颜色
    
    Args:
        severity: 严重程度（critical/warning/info）
        
    Returns:
        str: 颜色代码
    """
    return _theme_alert_color(severity)


# ==================== 常量定义 ====================

# 默认配置路径
DEFAULT_CONFIG_DIR = "configs"
DEFAULT_CONFIG_FILE = os.path.join(DEFAULT_CONFIG_DIR, "default.json")

# 日志文件路径
LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "app.log")

# UI 配置（V3.0：主题等）
UI_CONFIG_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'configs',
    'ui_config.json',
)

# GUI配置
CANVAS_WIDTH = 1200  # 画布宽度（像素）
CANVAS_HEIGHT = 600  # 画布高度（像素）
STATION_WIDTH = 120  # 工序节点宽度
STATION_HEIGHT = 80  # 工序节点高度

# 仿真配置
DEFAULT_SIMULATION_HOURS = 8.0  # 默认仿真时长（小时）
DEFAULT_SIMULATION_SPEED = 16   # 默认仿真速度（倍数）
