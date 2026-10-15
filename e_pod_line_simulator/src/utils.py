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
from typing import Callable, Dict, Any, List, Optional, Tuple
from pathlib import Path
import logging
import math
import tempfile

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
    """返回用户数据目录；PUFFLINE_DATA_DIR 可隔离测试，不在读取时写磁盘。"""
    override = os.environ.get("PUFFLINE_DATA_DIR")
    if override:
        return str(Path(override).expanduser().resolve())
    home = Path.home()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", str(home)))
    elif sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(home / ".config")))
    path = base / app_name
    return str(path)


def atomic_write_json(path: str, data: Any) -> None:
    """Write complete finite JSON before atomically replacing an existing file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=str(target.parent),
            prefix=".puffline-", suffix=".tmp", delete=False,
        ) as stream:
            temporary = stream.name
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, str(target))
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def get_user_data_path(filename: str, migrate: bool = False) -> str:
    """Copy valid legacy JSON once without deleting or overwriting its source."""
    target = Path(get_app_data_dir()) / filename
    if migrate and not target.exists():
        legacy = Path(__file__).resolve().parent.parent / "configs" / filename
        if legacy.is_file():
            with legacy.open(encoding="utf-8") as stream:
                data = json.load(stream)
            atomic_write_json(str(target), data)
    return str(target)


def get_scenarios_path() -> str:
    """Return the writable scenario store, migrating the old store on first use."""
    return get_user_data_path("scenarios.json", migrate=True)


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
    if not math.isfinite(hours) or not 0 < hours <= 24:
        raise ValueError(f"班次时长必须大于 0，当前输入：{shift_hours!r}")

    try:
        minutes = float(break_minutes.strip())
    except (TypeError, ValueError):
        raise ValueError(f"休息时间必须是非负数字（分钟），当前输入：{break_minutes!r}")
    if not math.isfinite(minutes) or minutes < 0 or minutes >= hours * 60:
        raise ValueError(f"休息时间不能为负数，当前输入：{break_minutes!r}")

    try:
        hourly_wage = float(wage.strip())
    except (TypeError, ValueError):
        raise ValueError(f"工人时薪必须是大于 0 的数字，当前输入：{wage!r}")
    if not math.isfinite(hourly_wage) or hourly_wage <= 0:
        raise ValueError(f"工人时薪必须大于 0，当前输入：{wage!r}")

    return hours, minutes, hourly_wage


def resolve_template_reset(
    excel_imported: bool, confirm: Optional[Callable[[], bool]] = None
) -> bool:
    """判断向导切换模板/生产类型时是否重置已生成的产线。

    未导入 Excel 时直接重置（模板变更应重建产线）；
    已导入 Excel 时需用户确认，确认函数返回 True 才重置。
    """
    if not excel_imported:
        return True
    if confirm is None:
        return False
    return bool(confirm())


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
        valid, error = validate_production_line(line, allow_empty=True)
        if not valid:
            raise ValueError(error)
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
        atomic_write_json(file_path, data)
        
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
        atomic_write_json(UI_CONFIG_FILE, config)
        return True
    except Exception:
        return False


def import_from_excel(file_path: str) -> Tuple[Optional[ProductionLine], Optional[str]]:
    """Import an Excel configuration transactionally; reject explicit invalid values."""
    try:
        import pandas as pd
        from src.models import CollaborationType

        if not os.path.isfile(file_path):
            return None, f"文件不存在：{file_path}"
        with pd.ExcelFile(file_path, engine='openpyxl') as book:
            sheets = {name: pd.read_excel(book, sheet_name=name, keep_default_na=False) for name in book.sheet_names}
        df = sheets.get('工序', next(iter(sheets.values())))
        if df.empty:
            return None, "Excel文件为空，没有数据行"
        column_mapping = {
            'id': ['工序ID', 'id'],
            'machine_count': ['机台数量', '机台数', 'machine_count'],
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
        
        actual_columns = {}
        for key, names in column_mapping.items():
            for col in df.columns:
                if str(col).strip() in names:
                    actual_columns[key] = col
                    break
        missing = [name for name in ('name', 'process_time', 'worker_count')
                   if name not in actual_columns]
        if missing:
            return None, "Excel文件缺少必填列：" + ', '.join(missing)

        def blank(value):
            return value is None or (isinstance(value, str) and not value.strip()) or bool(pd.isna(value))

        def number(value, default, label, integer=False):
            if blank(value):
                value = default
            if isinstance(value, bool):
                raise ValueError(f"{label}必须是数字")
            parsed = float(value)
            if not math.isfinite(parsed) or (integer and int(parsed) != parsed):
                raise ValueError(f"{label}必须是有限" + ("整数" if integer else "数值"))
            return int(parsed) if integer else parsed

        def pick(row, names, default):
            for name in names:
                if name in row and not blank(row[name]):
                    return row[name]
            return default

        line = ProductionLine(name="从Excel导入")
        if '产线配置' in sheets:
            for _, row in sheets['产线配置'].iterrows():
                key, value = str(row.iloc[0]).strip(), row.iloc[1]
                if blank(value):
                    continue
                if key == 'production_type':
                    line.production_type = ProductionType(str(value).strip())
                elif key in ('shift_hours', 'break_minutes', 'worker_hourly_wage',
                             'cip_interval_batches', 'cip_interval_hours'):
                    setattr(line, key, number(value, 0, key, key == 'cip_interval_batches'))
                elif key == 'changeover_matrix_json':
                    line.changeover_matrix = json.loads(str(value))
                    if not isinstance(line.changeover_matrix, dict):
                        raise ValueError("换型矩阵必须是JSON对象")
        elif '生产类型' in sheets:
            type_df = pd.read_excel(file_path, sheet_name='生产类型', header=None)
            line.production_type = ProductionType(str(type_df.iloc[0, 1]).strip())

        for _, row in sheets.get('原料', pd.DataFrame()).iterrows():
            material = Material(
                name=str(row['原料名']).strip(), unit=str(pick(row, ['单位'], 'kg')),
                initial_stock=number(row.get('初始库存'), 0, '初始库存'),
                unit_cost=number(row.get('单价'), 0, '原料单价'),
            )
            line.materials.append(material)
            line.inventory[material.name] = material.initial_stock
        for _, row in sheets.get('到货计划', pd.DataFrame()).iterrows():
            line.material_arrivals.append(MaterialArrival(
                time_minutes=number(row['到货时间(分钟)'], 0, '到货时间'),
                material=str(row['原料']).strip(), quantity=number(row['数量'], 0, '到货数量'),
            ))
        for _, row in sheets.get('配方', pd.DataFrame()).iterrows():
            recipe = Recipe(
                name=str(row['配方名']).strip(),
                batch_volume_l=number(pick(row, ['批次量kg', '批次量L'], 100), 100, '配方批量'),
                yield_rate=number(row.get('收率'), .95, '收率'),
                nicotine_concentration=number(row.get('尼古丁浓度'), 0, '尼古丁浓度'),
                flavor=str(pick(row, ['口味'], '')),
                mixing_time_min=number(row.get('调配min'), 60, '调配时间'),
                aging_time_min=number(row.get('陈化min'), 120, '陈化时间'),
                filling_rate_l_per_h=number(pick(row, ['灌装速率kg/h', '灌装速率L/h'], 500), 500, '灌装速率'),
                qc_time_min=number(row.get('QCmin'), 30, 'QC时间'),
                clean_time_min=number(row.get('清洗min'), 45, '清洗时间'),
            )
            if '投料JSON' in row and not blank(row['投料JSON']):
                recipe.ingredients = json.loads(str(row['投料JSON']))
            line.recipes.append(recipe)
        for _, row in sheets.get('罐', pd.DataFrame()).iterrows():
            line.tanks.append(Tank(
                id=str(row['罐ID']).strip(), name=str(row['罐名']).strip(),
                capacity_l=number(pick(row, ['容量kg', '容量L'], 1000), 1000, '罐容量'),
                current_level_l=number(pick(row, ['当前液位kg', '当前液位L'], 0), 0, '罐内数量'),
            ))
        for _, row in sheets.get('批次', pd.DataFrame()).iterrows():
            line.batches.append(Batch(
                id=str(row['批次ID']).strip(), recipe_name=str(row['配方名']).strip(),
                quantity_l=number(pick(row, ['批次量kg', '批次量L'], 100), 100, '批次量'),
            ))
        role_map = {
            '调香师': JobRole.MIXER, '调配工': JobRole.MIXER,
            '灌装操作员': JobRole.FILLING_OPERATOR, 'qc化验员': JobRole.QC_TECHNICIAN,
            'qc 化验员': JobRole.QC_TECHNICIAN, '化验员': JobRole.QC_TECHNICIAN,
            '包装机手': JobRole.PACKAGING_OPERATOR, '清洗工': JobRole.CLEANER,
            '通用装配工': JobRole.GENERAL, '通用': JobRole.GENERAL,
        }
        for idx, row in df.iterrows():
            def field(key, default=None):
                return pick(row, [actual_columns[key]], default) if key in actual_columns else default
            try:
                mode = str(field('collaboration_type', 'parallel')).strip().lower()
                mode = {'并联': 'parallel', '协同': 'collaborative'}.get(mode, mode)
                role_text = str(field('job_role', 'general')).strip().lower()
                role = role_map[role_text] if role_text in role_map else JobRole(role_text)
                workers = number(field('worker_count'), 1, '人数', True)
                takt = field('machine_takt')
                kwargs = {}
                for key, default, integer in (
                    ('oee', .85, False), ('efficiency', .95, False),
                    ('changeover_time', 45, False), ('buffer_capacity', 100, True),
                    ('clean_time_minutes', 0, False), ('sampling_rate', 0, False),
                    ('defect_rate', 0, False), ('rework_minutes', 0, False),
                ):
                    kwargs[key] = number(field(key), default, key, integer)
                if 'machine_count' in actual_columns:
                    kwargs['machine_count'] = number(field('machine_count'), 1, '机台数', True)
                elif takt is not None:
                    kwargs['machine_count'] = workers  # Legacy sheets used people as machine count.
                bom = {}
                for part in str(field('bom', '')).split(';'):
                    if not part.strip():
                        continue
                    component, amount = part.split(':', 1)
                    bom[component.strip()] = number(amount, 0, 'BOM用量')
                line.add_station(Station(
                    id=str(field('id', f"s{idx+1:02d}")).strip(),
                    name=str(field('name', '')).strip(),
                    process_time=number(field('process_time'), 0, '耗时'), worker_count=workers,
                    collaboration_type=CollaborationType(mode), job_role=role,
                    machine_takt=number(takt, 1, '机台节拍') if takt is not None else None,
                    cleanroom_zone=str(field('cleanroom_zone', '')), bom=bom, **kwargs,
                ))
            except (ValueError, TypeError, KeyError) as exc:
                return None, f"第{idx+2}行参数错误：{exc}"
        valid, error = validate_production_line(line)
        return (line, None) if valid else (None, f"导入的产线配置不合法：{error}")
    except Exception as exc:
        return None, f"导入Excel失败：{exc}"


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
            [row + [1] for row in template_data],
            columns=[
                '工序名', '耗时(秒)', '人数', '模式', 'OEE', '人员效率',
                '切换时间(分钟)', '缓冲区容量', '机台节拍(秒)', '清洗时间(分钟)',
                '抽检比例', '缺陷率', '返工时长(分钟)', '工种', '洁净区',
                'BOM(组件:用量;...)', '机台数量',
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
                    '配方名', '批次量kg', '收率', '尼古丁浓度', '口味',
                    '调配min', '陈化min', '灌装速率kg/h', 'QCmin', '清洗min',
                ])
                recipe_df.to_excel(writer, sheet_name='配方', index=False)

                tank_df = pd.DataFrame([
                    ['T01', '调配罐', 2000, 0],
                    ['T02', '成品罐', 3000, 0],
                ], columns=['罐ID', '罐名', '容量kg', '当前液位kg'])
                tank_df.to_excel(writer, sheet_name='罐', index=False)

                batch_df = pd.DataFrame([
                    ['B001', '经典烟草', 500, 'queued'],
                ], columns=['批次ID', '配方名', '批次量kg', '状态'])
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

def _finite_number(value: Any) -> bool:
    """Accept finite numeric model values, excluding bool and numeric strings."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_station(name: str, process_time: float, worker_count: int) -> Tuple[bool, str]:
    """Validate the common station input boundary without truncating numbers."""
    if not isinstance(name, str) or not name.strip():
        return False, "工序名称不能为空"
    if len(name) > 20:
        return False, "工序名称不能超过20个字符"
    if not _finite_number(process_time) or not 0 < process_time <= 3600:
        return False, "单件耗时必须为大于0且不超过3600秒的有限数"
    if (not _finite_number(worker_count) or int(worker_count) != worker_count
            or not 1 <= worker_count <= 20):
        return False, "工人数量必须为1到20之间的整数"
    return True, ""


def validate_production_line(
    line: ProductionLine, allow_empty: bool = False,
) -> Tuple[bool, str]:
    """Validate finite values, dimensions and references before importing or running."""
    def bounded(value, label, low=0, high=float('inf'), integer=False, positive=False):
        if (not _finite_number(value) or value < low or value > high
                or (positive and value == 0) or (integer and int(value) != value)):
            raise ValueError(f"{label}必须是合法范围内的有限" + ("整数" if integer else "数值"))

    try:
        if not line.stations and not allow_empty:
            return False, "产线至少需要一个工序"
        if len(line.stations) > 20:
            return False, "产线工序数量不能超过20个"
        bounded(line.shift_hours, "班次时长", high=24, positive=True)
        bounded(line.worker_hourly_wage, "工人时薪", positive=True)
        bounded(line.break_minutes, "休息时间")
        if line.break_minutes >= line.shift_hours * 60:
            return False, "休息时间必须小于班次时长"
        if not isinstance(line.production_type, ProductionType):
            return False, "未知生产类型"
        ids = set()
        for station in line.stations:
            if not isinstance(station.id, str) or not station.id.strip() or station.id in ids:
                return False, f"工序ID为空或重复：{station.id}"
            ids.add(station.id)
            valid, error = validate_station(station.name, station.process_time, station.worker_count)
            if not valid:
                return False, f"工序'{station.name}'参数错误：{error}"
            for key in ('oee', 'efficiency', 'sampling_rate', 'defect_rate'):
                bounded(getattr(station, key), f"工序{station.name}的{key}", high=1)
            for key in ('changeover_time', 'clean_time_minutes', 'rework_minutes'):
                bounded(getattr(station, key), f"工序{station.name}的{key}")
            for key in ('buffer_capacity', 'machine_count'):
                bounded(getattr(station, key, 1), f"工序{station.name}的{key}",
                        integer=True, positive=True)
            if station.machine_takt is not None:
                bounded(station.machine_takt, f"工序{station.name}的机台节拍", positive=True)
            if not isinstance(station.job_role, JobRole):
                return False, f"工序{station.name}工种无效"
            for component, amount in station.bom.items():
                if not component:
                    return False, "BOM组件名不能为空"
                bounded(amount, f"工序{station.name}的BOM用量", positive=True)
        for mapping, label, integer in (
            (line.inventory, '库存', False), (line.labor_config, '岗位人数', True),
            (line.cleanroom_limits, '洁净区人数上限', True),
        ):
            for name, value in mapping.items():
                if not isinstance(name, str) or not name.strip():
                    return False, f"{label}名称不能为空"
                bounded(value, label, integer=integer)
        bounded(line.cip_interval_batches, "周期CIP批次数", integer=True)
        bounded(line.cip_interval_hours, "周期CIP小时数")
        for transitions in line.changeover_matrix.values():
            if not isinstance(transitions, dict):
                return False, "换型矩阵必须为嵌套对象"
            for value in transitions.values():
                bounded(value, "换型矩阵时间")
        for collection, label, identifier in (
            (line.recipes, '配方', 'name'), (line.batches, '批次', 'id'),
            (line.tanks, '罐', 'id'), (line.materials, '原料', 'name'),
        ):
            identifiers = [getattr(item, identifier) for item in collection]
            if (any(not isinstance(i, str) or not i.strip() for i in identifiers)
                    or len(set(identifiers)) != len(identifiers)):
                return False, f"{label}名称或ID为空/重复"
        recipes = {r.name for r in line.recipes}
        for recipe in line.recipes:
            for key in ('batch_volume_l', 'filling_rate_l_per_h'):
                bounded(getattr(recipe, key), f"配方{recipe.name}的{key}", positive=True)
            bounded(recipe.yield_rate, f"配方{recipe.name}收率", high=1)
            for key in ('mixing_time_min', 'aging_time_min', 'qc_time_min', 'clean_time_min'):
                bounded(getattr(recipe, key), f"配方{recipe.name}的{key}")
            for value in recipe.ingredients.values():
                bounded(value, f"配方{recipe.name}的投料用量")
        for batch in line.batches:
            if batch.recipe_name not in recipes:
                return False, f"批次{batch.id}引用不存在的配方"
            bounded(batch.quantity_l, f"批次{batch.id}批量", positive=True)
        for tank in line.tanks:
            bounded(tank.capacity_l, f"罐{tank.id}容量", positive=True)
            bounded(tank.current_level_l, f"罐{tank.id}液位", high=tank.capacity_l)
        for material in line.materials:
            bounded(material.initial_stock, f"原料{material.name}库存")
            bounded(material.unit_cost, f"原料{material.name}单价")
        for arrival in line.material_arrivals:
            if not isinstance(arrival.material, str) or not arrival.material.strip():
                return False, "到货原料名不能为空"
            bounded(arrival.time_minutes, "到货时间")
            bounded(arrival.quantity, "到货数量")
        labor_ok, labor_msg = line.validate_labor()
        if not labor_ok:
            return False, labor_msg
    except (ValueError, TypeError, AttributeError) as exc:
        return False, str(exc)
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
LOG_DIR = os.path.join(get_app_data_dir(), "logs")
LOG_FILE = os.path.join(LOG_DIR, "app.log")

# UI 配置（V3.0：主题等）
UI_CONFIG_FILE = get_user_data_path('ui_config.json')

# GUI配置
CANVAS_WIDTH = 1200  # 画布宽度（像素）
CANVAS_HEIGHT = 600  # 画布高度（像素）
STATION_WIDTH = 120  # 工序节点宽度
STATION_HEIGHT = 80  # 工序节点高度

# 仿真配置
DEFAULT_SIMULATION_HOURS = 8.0  # 默认仿真时长（小时）
DEFAULT_SIMULATION_SPEED = 16   # 默认仿真速度（倍数）
