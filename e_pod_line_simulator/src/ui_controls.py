"""A方向的ttk控件外观；保留原生键盘、焦点、禁用态及Combobox行为。"""

def install_control_elements(root, style, colors: dict) -> None:
    """为现有ttk布局安装可拉伸圆角边界，图像跟随根窗口释放。"""
    from PIL import Image, ImageDraw, ImageTk

    images = []

    def surface(fill, border):
        image = Image.new('RGBA', (80, 80), colors['surface'])
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((1, 1, 78, 78), radius=17, fill=fill, outline=border, width=2)
        image = image.resize((28, 28), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(image, master=root)
        images.append(photo)
        return photo

    definitions = {
        'TButton': (
            colors['surface'], colors['hover'], colors['text'], colors['border']),
        'Secondary.TButton': (
            colors['surface'], colors['hover'], colors['text'], colors['border']),
        'Primary.TButton': (
            colors['primary'], colors['primary_hover'], colors['surface'], colors['primary']),
        'Accent.TButton': (
            colors['primary'], colors['primary_hover'], colors['surface'], colors['primary']),
        'Danger.TButton': (
            colors['danger_soft'], colors['danger_soft_hover'],
            colors['danger_dark'], colors['danger_soft']),
    }
    for index, (name, (fill, hover, foreground, border)) in enumerate(definitions.items()):
        element = f'Puff{index}.Button.border'
        normal, active = surface(fill, border), surface(hover, border)
        disabled = surface(colors['bg'], colors['border'])
        focused = surface(fill, colors['primary'] if fill == colors['surface'] else colors['text'])
        style.element_create(element, 'image', normal,
                             ('disabled', disabled), ('pressed', active), ('active', active),
                             ('focus', focused),
                             border=7, sticky='nsew')
        style.layout(name, [(element, {'sticky': 'nsew', 'children': [
            ('Button.padding', {'sticky': 'nsew', 'children': [
                ('Button.label', {'sticky': 'nsew'})]})]})])
        style.configure(name, foreground=foreground, padding=(12, 7), borderwidth=0)
        style.map(name, foreground=[
            ('disabled', colors['text_secondary']), ('!disabled', foreground)])

    normal = surface(colors['surface'], colors['border'])
    focus = surface(colors['surface'], colors['primary'])
    disabled = surface(colors['bg'], colors['border'])
    style.element_create('Puff.Entry.border', 'image', normal,
                         ('disabled', disabled), ('focus', focus), border=7, sticky='nsew')
    style.layout('TEntry', [('Puff.Entry.border', {'sticky': 'nsew', 'children': [
        ('Entry.padding', {'sticky': 'nsew', 'children': [
            ('Entry.textarea', {'sticky': 'nsew'})]})]})])
    arrow = Image.new('RGBA', (40, 40), (0, 0, 0, 0))
    ImageDraw.Draw(arrow).line([(12, 16), (20, 24), (28, 16)],
                              fill=colors['text_secondary'], width=3)
    down = ImageTk.PhotoImage(arrow.resize((20, 20), Image.Resampling.LANCZOS), master=root)
    images.append(down)
    style.element_create('Puff.Combobox.downarrow', 'image', down, sticky='')
    style.layout('TCombobox', [('Puff.Entry.border', {'sticky': 'nsew', 'children': [
        ('Puff.Combobox.downarrow', {'side': 'right', 'sticky': 'ns'}),
        ('Combobox.padding', {'sticky': 'nsew', 'children': [
            ('Combobox.textarea', {'sticky': 'nsew'})]})]})])
    # ttkbootstrap支持原有style名称；所有图像必须保持Python引用。
    root._puffline_control_images = images
