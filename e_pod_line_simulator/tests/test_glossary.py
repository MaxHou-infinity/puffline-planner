"""术语库单元测试"""

from src.glossary import GLOSSARY, glossary_text


def test_glossary_has_key_terms():
    terms = {term for term, _ in GLOSSARY}
    for required in [
        "生产类型", "工序", "并联", "协同", "OEE", "WIP", "瓶颈",
        "UPPH", "批次", "配方", "储罐", "机台节拍", "清洗时间",
        "抽检比例", "缺陷率", "返工时长", "收率", "质量门",
        "日成本", "日产量", "单位成本", "罐容约束", "批次排产序列",
        "周期性CIP", "换型矩阵", "BOM", "返工回路", "批次隔离",
        "可发运产出", "OEE分解", "停机切换", "人力需求", "当前在岗",
        "招聘缺口", "本周新增需求", "一次性招聘建议", "富余人数",
        "爬坡达产", "工种", "技能矩阵", "洁净区", "敏感性试算", "批量试算", "迭代代数",
        "种群大小", "方案对比",
    ]:
        assert required in terms


def test_glossary_text_output():
    text = glossary_text()
    assert "WIP：在制品" in text
    assert "日成本：预计日人工成本" in text
    assert "单位成本：预计口径" in text
    assert "确定性节拍折减" in text
    assert "不是随机停机" in text
    assert "不是经典可用率 × 性能率 × 合格率" in text
    assert "数据字段，本版不参与派工/界面编辑" in text
    assert "它不是工序编辑器里的 OEE 输入" in text
    assert "修改请用 Excel 模板再导入" in text
    assert "机台节拍" in text and "质量门" in text


def test_glossary_dialog_class_available():
    from src.gui_panels import GlossaryDialog
    assert callable(GlossaryDialog)
