# -*- coding: utf-8 -*-
"""生成 Dro Agent 生产化升级 PRD 正文 Word 文档。"""
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

OUT = r'D:\agent-group\Droirun-agent\docs\Dro生产化升级PRD.docx'

doc = Document()

# ---- 页面设置 ----
for section in doc.sections:
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.5)
    section.bottom_margin = Cm(2.5)
    section.left_margin = Cm(2.5)
    section.right_margin = Cm(2.5)

# ---- 样式设置 ----
style_normal = doc.styles['Normal']
style_normal.font.name = 'Times New Roman'
style_normal.font.size = Pt(12)
style_normal.element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
pf = style_normal.paragraph_format
pf.line_spacing = 1.5
pf.space_before = Pt(0)
pf.space_after = Pt(0)
pf.first_line_indent = Pt(24)

def _clear_theme_fonts(style):
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn('w:rFonts'))
    if rfonts is None:
        rfonts = OxmlElement('w:rFonts')
        rpr.insert(0, rfonts)
    for attr in ('w:asciiTheme', 'w:eastAsiaTheme', 'w:hAnsiTheme', 'w:cstheme'):
        if rfonts.get(qn(attr)) is not None:
            rfonts.set(qn(attr), 'nil')


def _fix_cn_quotes(text):
    if '"' not in text:
        return text
    result = []
    open_quote = True
    for ch in text:
        if ch == '"':
            result.append('\u201c' if open_quote else '\u201d')
            open_quote = not open_quote
        else:
            result.append(ch)
    return ''.join(result)


for lvl, size in [(1, 16), (2, 14), (3, 12)]:
    s = doc.styles[f'Heading {lvl}']
    s.font.name = 'Times New Roman'
    s.font.size = Pt(size)
    s.font.bold = True
    s.font.color.rgb = RGBColor(0, 0, 0)
    s.element.rPr.rFonts.set(qn('w:eastAsia'), '黑体')
    _clear_theme_fonts(s)
    s.paragraph_format.first_line_indent = Pt(0)
    s.paragraph_format.space_before = Pt(14 if lvl == 1 else 11 if lvl == 2 else 9)
    s.paragraph_format.space_after = Pt(6 if lvl == 1 else 5 if lvl == 2 else 4)

# Title 样式
ts = doc.styles['Title']
ts.font.name = 'Times New Roman'
ts.font.size = Pt(18)
ts.font.bold = True
ts.font.color.rgb = RGBColor(0, 0, 0)
ts.element.rPr.rFonts.set(qn('w:eastAsia'), '黑体')
_clear_theme_fonts(ts)
ts.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
ts.paragraph_format.space_after = Pt(18)
ts.paragraph_format.first_line_indent = Pt(0)


def add_para(text, bold=False, align=None, indent=True):
    text = _fix_cn_quotes(text)
    p = doc.add_paragraph()
    if align:
        p.alignment = align
    if not indent:
        p.paragraph_format.first_line_indent = Pt(0)
    run = p.add_run(text)
    run.bold = bold
    run.font.name = 'Times New Roman'
    run.element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
    run.font.size = Pt(12)
    return p


def add_table(headers, rows, col_widths=None):
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = 'Table Grid'
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    # 表头
    hdr = table.rows[0]
    for i, h in enumerate(headers):
        cell = hdr.cells[i]
        cell.text = ''
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.first_line_indent = Pt(0)
        run = p.add_run(_fix_cn_quotes(h))
        run.bold = True
        run.font.name = 'Times New Roman'
        run.element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
        run.font.size = Pt(10.5)
        # 浅灰背景
        tcPr = cell._tc.get_or_add_tcPr()
        shd = OxmlElement('w:shd')
        shd.set(qn('w:fill'), 'D9D9D9')
        tcPr.append(shd)
    # 数据行
    for r_idx, row in enumerate(rows):
        tr = table.rows[r_idx + 1]
        for c_idx, val in enumerate(row):
            cell = tr.cells[c_idx]
            cell.text = ''
            p = cell.paragraphs[0]
            p.paragraph_format.first_line_indent = Pt(0)
            p.paragraph_format.line_spacing = 1.0
            run = p.add_run(_fix_cn_quotes(str(val)))
            run.font.name = 'Times New Roman'
            run.element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
            run.font.size = Pt(10.5)
            # 行不跨页
            trPr = tr._tr.get_or_add_trPr()
            cantSplit = OxmlElement('w:cantSplit')
            trPr.append(cantSplit)
    if col_widths:
        for i, w in enumerate(col_widths):
            for row in table.rows:
                row.cells[i].width = Cm(w)
    return table


# ==================== 封面标题 ====================
doc.add_paragraph()
doc.add_paragraph()
title = doc.add_paragraph()
title.alignment = WD_ALIGN_PARAGRAPH.CENTER
title.paragraph_format.first_line_indent = Pt(0)
run = title.add_run('Dro Agent 生产化升级')
run.bold = True
run.font.name = 'Times New Roman'
run.element.rPr.rFonts.set(qn('w:eastAsia'), '黑体')
run.font.size = Pt(22)

subtitle = doc.add_paragraph()
subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
subtitle.paragraph_format.first_line_indent = Pt(0)
run = subtitle.add_run('产品需求文档（PRD）')
run.bold = True
run.font.name = 'Times New Roman'
run.element.rPr.rFonts.set(qn('w:eastAsia'), '黑体')
run.font.size = Pt(16)

doc.add_paragraph()
doc.add_paragraph()

# 文档信息表
add_table(
    ['项目', '内容'],
    [
        ['产品名称', 'Dro（Droidrun E-commerce AI Agent）'],
        ['文档版本', 'V1.0'],
        ['文档状态', '评审稿'],
        ['编写日期', '2026年9月'],
        ['关联文档', 'docs/优化开发文档.md、docs/architecture.md、docs/requirements.md'],
        ['目标读者', '产品经理、研发工程师、测试工程师、项目经理'],
    ],
    col_widths=[4, 11],
)

doc.add_page_break()

# ==================== 修订记录 ====================
doc.add_heading('修订记录', level=1)
add_table(
    ['版本', '日期', '修订人', '修订内容'],
    [
        ['V1.0', '2026-09', '张乔文', '初始版本，基于优化开发文档生成完整PRD正文'],
    ],
    col_widths=[2, 3, 3, 7],
)

doc.add_page_break()

# ==================== 1. 产品背景与定位 ====================
doc.add_heading('1 产品背景与定位', level=1)

doc.add_heading('1.1 项目背景', level=2)
add_para('Dro 是一个面向 Android 设备（真机或模拟器）的电商 AI 助手，能够自动在淘宝、京东等应用内完成比价、找相似、按预算推荐三类任务。当前项目已按照工程化规范重构为分层架构，采用 LangGraph 进行任务编排，原子 ADB 工具执行手机操作，SQLite 作为默认记忆与 checkpoint 存储，整体已具备 demo 级可演示能力。')
add_para('然而，在向生产级别演进的过程中，项目暴露出四类系统性瓶颈：其一，连接、线程与实例不复用，每次节点调用都新建 LLM 客户端与记忆实例，每次工具执行都创建并销毁线程池；其二，大量固定 time.sleep 堆积，单次比价任务的固定等待可达十五至二十秒；其三，每步成功后都额外调用一次 LLM 进行 finish 决策，单任务 LLM 调用数轻松超过十二次；其四，前端采用九百毫秒至一千五百毫秒轮询获取进度，任务与会话存储为纯内存态，重启即丢失。')
add_para('与此同时，产品方向需要从单一的电商比价助手泛化为可扩展的通用手机操作 Agent，支撑生活助手类工具的插拔扩展，参考 WorkBuddy 等桌面办公 Agent 的成熟架构思路，建立具备编排、记忆、行动与管控四层能力的 Agent 底座。')

doc.add_heading('1.2 产品定位', level=2)
add_para('Dro 定位为面向 Android 设备的通用手机操作 Agent。用户通过自然语言下达任务，Agent 自动在目标应用内完成多步操作并返回结构化结果。电商比价为首个垂直验证场景，后续可扩展至社交、工具、生活服务等领域。产品的核心差异化在于：固定工具集替代代码生成，操作可审计可重试；SqliteSaver checkpoint 支持断点续跑；工具注册表与技能包机制支持新增场景不改核心；全链路 tracing 使每步耗时、成功率与 token 消耗可量化。')

doc.add_heading('1.3 核心价值主张', level=2)
add_para('可控性方面，采用固定原子工具集替代 LLM 直接生成代码或 ADB 命令的模式，所有操作经过注册表校验、线程池超时保护与异常兜底，杜绝不可控的代码执行风险。可恢复性方面，基于 LangGraph SqliteSaver 的 checkpoint 机制，每个任务的每一步状态都持久化到 SQLite，同一 thread_id 可从断点继续执行，适用于长流程比价与异常恢复场景。可扩展性方面，工具注册表与标准化原子动作空间使新增场景只需注册新工具或技能包，不改动核心编排逻辑；MCP 协议适配后，任何 MCP 兼容客户端均可调用 Dro 的手机操作能力。可观测性方面，接入 Langfuse 自托管平台，每个任务一个 trace，每个节点、工具与 LLM 调用一个 span，支撑生产环境的问题定位与持续优化。')

# ==================== 2. 目标与成功指标 ====================
doc.add_heading('2 目标与成功指标', level=1)

doc.add_heading('2.1 业务目标', level=2)
add_para('第一，从单场景电商助手升级为多场景通用手机 Agent 底座，支撑电商之外的生活助手类工具扩展。第二，支撑多用户并发使用，通过设备池调度实现至少三台设备同时执行任务而不串数据。第三，建立评估闭环，每次代码变更可自动量化成功率变化，失败轨迹可回放复盘。第四，前端体验从轮询升级为实时流式推送，操作反馈延迟控制在百毫秒以内。')

doc.add_heading('2.2 核心指标', level=2)
add_table(
    ['指标', '当前基线', '目标值', '衡量方式'],
    [
        ['单任务端到端时长', '固定sleep堆积15-20s', '下降≥30%', '评估集平均耗时'],
        ['单任务LLM调用数', '12次以上', '4至6次', 'Langfuse trace统计'],
        ['工具调用成功率', '待测', '≥90%', 'Langfuse工具指标'],
        ['进度推送延迟', '900-1500ms轮询', '<100ms', '前端打点'],
        ['电商任务集成功率', '待测', '≥70%', '自建评估集'],
        ['单元测试通过率', '37项全绿', '保持全绿并新增覆盖', 'CI流水线'],
        ['并发设备数', '1台', '≥3台', '设备池压测'],
    ],
    col_widths=[3.5, 3.5, 3, 4],
)

# ==================== 3. 用户画像与使用场景 ====================
doc.add_heading('3 用户画像与使用场景', level=1)

doc.add_heading('3.1 用户画像', level=2)
add_para('效率型用户：频繁进行跨平台比价与购物决策，希望通过一句话指令快速获取多平台价格对比与最优推荐，不愿手动在多个应用间切换搜索。泛化用户：希望用自然语言控制手机完成重复性操作，如发消息、查天气、定闹钟、批量处理文件等，对技术细节不关注，只关心任务是否完成。开发者与评测者：通过 API 接入 Dro 的 Agent 能力，搭建自定义工作流或评估 Agent 表现，关注接口稳定性、可观测性与扩展性。')

doc.add_heading('3.2 核心使用场景', level=2)
add_para('场景一，跨平台比价。用户输入"帮我比价蓝牙耳机，预算200以内"，Agent 在京东和淘宝各执行一次搜索与信息提取，汇总价格后返回对比结论与推荐。场景二，以图找同款。用户上传商品图片，Agent 进入应用的识图入口上传图片，查找同款并返回候选商品列表。场景三，按预算最优推荐。用户输入"200以内推荐降噪耳机"，Agent 综合价格、销量、评分进行确定性打分，返回推荐列表并提供代下单选项。场景四，多轮对话。用户先找耳机，再追问"前两个哪个好评率高"，Agent 基于会话记忆继续操作并回答。场景五，任务中断恢复。任务执行中应用崩溃或设备断连，系统重启后用户可从断点继续执行，无需从头开始。场景六，远程触发，远期规划，用户通过即时通讯工具下发指令，远程操控绑定的 Android 设备完成任务。')

# ==================== 4. 功能需求 ====================
doc.add_heading('4 功能需求', level=1)

doc.add_heading('4.1 Agent 编排升级', level=2)

doc.add_heading('4.1.1 Manager-Executor 动态反馈环', level=3)
add_para('需求描述：将当前 planner 一次性生成完整计划、agent 按序盲执行的模式，重构为 Manager 与 Executor 交替执行的动态反馈环。Manager 节点使用强模型进行高层决策，每步执行后重新评估当前屏幕状态并调整下一步指令；Executor 节点使用快模型执行单步具体动作。')
add_para('业务规则：Manager 输入包含当前截图、Accessibility Tree、上一步差分状态、任务目标与记忆上下文；输出包含当前子目标、下一步动作类型与推理依据。Executor 输入为 Manager 指定的动作类型与当前屏幕 Set-of-Mark 标注图；输出为精确动作。Manager 判定任务完成时进入结果节点；识别到文本密集型子任务时路由到专用文本 Agent。该模式参考 Droidrun 在 AndroidWorld 基准上从百分之六十三提升至百分之九十一点四的核心架构经验。')
add_para('异常处理：Manager 调用失败时降级为确定性规则规划；Executor 连续两次失败时 Manager 重新评估并更换动作策略；达到最大步数时强制结束并返回已收集的部分结果。')

doc.add_heading('4.1.2 标准化原子动作空间', level=3)
add_para('需求描述：将当前十二个电商特化工具收敛为六类标准化原子动作，使 Agent 能力从电商场景泛化到通用手机操作。六类动作为 click（点击元素）、long_press（长按元素）、type（输入文本）、swipe（滑动）、system_button（系统按键）、open_app（启动应用），以及 status（任务状态上报）。')
add_para('业务规则：click 与 long_press 接收元素索引参数，由 Set-of-Mark 标注映射到实际坐标；type 接收文本与目标输入框索引，中文自动走剪贴板粘贴；swipe 接收起点坐标、终点坐标与持续时长，支持任意方向；system_button 支持 back、home、enter 三种按键；open_app 按应用名映射包名启动。所有动作统一返回 ToolResult 结构，失败不抛异常。')

doc.add_heading('4.1.3 Set-of-Mark 元素索引', level=3)
add_para('需求描述：每步对当前截图进行可交互元素检测，在每个可交互元素上叠加编号圆点，形成标注图。VLM 看到标注图后输出元素编号而非猜测坐标，编号到坐标的映射由本地维护。')
add_para('业务规则：元素检测优先使用 Accessibility Tree 的 bounds 属性提取可点击节点，轻量高效；Accessibility Tree 不可用时回退到轻量 UI 检测模型。标注编号从一开始连续分配，只标注屏幕中下部的可交互区域，排除顶部状态栏与底部导航栏。该方案是 AndroidWorld 基准上所有高分方案的共同选择，可将元素定位准确率从百分之四十至六十提升至百分之八十五以上。')

doc.add_heading('4.1.4 双通道感知与差分状态跟踪', level=3)
add_para('需求描述：每步同时采集截图与 Accessibility Tree 作为感知输入，并维护上一步的 Accessibility Tree 用于差分对比，使 Manager 精确感知界面变化。')
add_para('业务规则：当前消息包含当前截图与当前 Accessibility Tree，上一条消息包含上一步的 Accessibility Tree，Manager 通过对比两者判断动作是否生效、页面是否跳转、是否出现弹窗。设备日期注入系统提示词以支持时间相关推理。关闭开发者选项中的触摸坐标显示，减少视觉噪声。')

doc.add_heading('4.1.5 屏幕稳定化', level=3)
add_para('需求描述：将当前散落的固定 time.sleep 替换为轮询等待界面稳定的机制。动作执行后等待零点五秒，然后对比屏幕像素或 UI 树哈希，稳定后再进入下一步，带上限超时保护。')
add_para('业务规则：抽取统一的 wait_for 工具函数，支持传入谓词条件与超时时间。搜索后轮询直到出现搜索结果列表节点；启动应用后轮询当前焦点包名到位。把等待从固定时长改为状态就绪，平均缩短且更稳定。截图采用 exec-out screencap 免落地直接拉流，省一次写盘与一次删除子进程。')

doc.add_heading('4.1.6 专用文本 Agent 路由', level=3)
add_para('需求描述：当 Manager 识别到文本密集型子任务（如填写表单、搜索输入、编辑文本）时，路由到专用文本操作 Agent。该 Agent 持有 Python shell，具备原子性清空并替换全部文本的能力，接收 Accessibility Tree（不含截图）与当前聚焦元素文本作为输入。')
add_para('业务规则：文本 Agent 可程序化构造与操作文本后再插入，避免逐字输入的低效与错误。路由条件由 Manager 根据子目标类型判断，文本任务完成后回到主循环。')

doc.add_heading('4.1.7 Verifier 高风险动作确认', level=3)
add_para('需求描述：支付、删除、下单等高风险动作强制经过 Verifier 二次评估。Verifier 对 Executor 生成的候选动作进行合理性评估，选最优执行，并在必要时触发人工审批。')
add_para('业务规则：高风险动作清单包括确认支付、确认下单、删除数据、发送消息等。Verifier 评估通过后仍需前端二次确认弹窗，用户确认后方可执行。测试阶段只执行加购操作，不执行支付。')

doc.add_heading('4.1.8 ReAct 提前结束规则优先', level=3)
add_para('需求描述：将当前每步成功后都调用 LLM 进行 finish 决策的模式，改为确定性规则优先判定，LLM 仅作为兜底。')
add_para('业务规则：确定性规则包括，有预算上限时已采集到至少一个预算内商品即可结束；比价任务两个平台都采集到价格后即可结束；已采集商品数量达到阈值（如三个）即可结束。仅当规则不命中且已采集数据足以判断时才调用 LLM 决策，并设置步级别节流（如前三步不调用）。预期将单任务 LLM 调用数从十二次以上降至四至六次。')

doc.add_heading('4.2 性能优化', level=2)

doc.add_heading('4.2.1 LLM 客户端单例与连接池', level=3)
add_para('需求描述：将当前每次节点调用都新建 LLMClient 的模式改为进程级单例，内部持有共享 httpx.Client 连接池，支持 keep-alive 与连接上限。')
add_para('业务规则：LLMClient 惰性初始化，线程安全；连续同义调用（如相同用户输入、任务类型与记忆哈希的意图分类）做结果缓存，温度零点一的场景缓存命中率高。')

doc.add_heading('4.2.2 工具线程池复用', level=3)
add_para('需求描述：将当前每次 execute_tool 都创建并销毁 ThreadPoolExecutor 的模式改为模块级共享线程池，最大工作线程数为四。')
add_para('业务规则：execute_tool 直接提交到共享线程池，超时逻辑保持不变。并发任务可复用同一池子，消除每步线程池创建销毁开销。')

doc.add_heading('4.2.3 ADB 设备态缓存', level=3)
add_para('需求描述：对设备分辨率进行首得即缓存；ensure_online 增加三秒 TTL 在线态缓存，工具内由执行失败再触发重连兜底；screen_size 不再每次调用 wm size 子进程。')

doc.add_heading('4.2.4 记忆实例单例化', level=3)
add_para('需求描述：SqliteStore 改为进程级单例，读写连接分离，开启 WAL 模式支持并发读。LongTermMemory 与 ProfileStore 的外部连接改为惰性连接加复用，失败降级结果缓存。偏好提取从每轮同步调用改为 fire-and-forget 后台任务，仅当文本含明显偏好关键词时触发。')

doc.add_heading('4.3 设备池与任务调度', level=2)

doc.add_heading('4.3.1 DevicePool 设备池管理', level=3)
add_para('需求描述：建立设备池管理器，每台设备对应一个独立 Worker 线程与 FIFO 任务队列，配备互斥锁保证串行执行。支持 ADB over TCP/IP 连接远程真机与模拟器。')
add_para('业务规则：Worker 从队列取任务后执行，执行完自动取下一个；设备执行完任务后自动清理目标应用数据（adb shell pm clear），避免账号串扰。设备健康检查每三十秒执行一次 adb get-state，离线时自动 adb connect 重试，三次失败标记不可用。')

doc.add_heading('4.3.2 标签路由调度器', level=3)
add_para('需求描述：调度器根据设备标签（如平台、应用登录态、设备类型）将任务路由到匹配设备。支持按设备负载均衡与任务优先级排序。')
add_para('业务规则：任务可指定所需标签，如 {"platform": "taobao_logged_in"}；无匹配设备时任务排队等待并通知用户。队列长度超限时自动驱逐最老任务，前端展示排队中状态。')

doc.add_heading('4.3.3 任务状态持久化', level=3)
add_para('需求描述：将当前纯内存的 TASK_STORE 与 SESSION_STORE 迁移到 SQLite，建立 tasks 与 sessions 表，支撑服务重启后任务列表仍可见与 resume 恢复。')

doc.add_heading('4.4 功能扩展', level=2)

doc.add_heading('4.4.1 最优方案评分算法', level=3)
add_para('需求描述：在结果节点前插入确定性打分模块，对采集到的商品按价格、销量、评分、运费、退货险五维度加权打分，先算分再交给 LLM 生成解释文字。')
add_para('业务规则：默认权重为价格零点五、销量零点二、评分零点一五、运费负向零点一、退货险正向零点零五，权重可配置。价格归一化采用倒数归一化，评分以四分为基准线。打分结果可复现、可解释、可测试，避免纯 LLM 文本推荐的不一致性。')

doc.add_heading('4.4.2 商品深链提取与跳转', level=3)
add_para('需求描述：扩展 extract_info 使其输出每个商品卡片的相对坐标，点击进入详情页后通过 dumpsys 抓取 Activity 与 deeplink 信息，拼接淘宝或京东商品深链。前端商品卡片展示"打开"按钮，点击跳转深链。')
add_para('业务规则：无法获取深链时退化为商品关键词搜索链接。深链格式为淘宝 taobao://item?id= 与京东 item.m.jd.com/product/{sku}。')

doc.add_heading('4.4.3 代下单与加购流程', level=3)
add_para('需求描述：新增 add_to_cart、view_cart、confirm_order 三个工具，纳入规划系统。结果节点返回结构化 checkout_options，前端弹出"是否需要代下单"二次确认卡片。')
add_para('业务规则：下单操作必须经前端二次确认与会话级白名单授权，未确认绝不点击确认支付。测试阶段只执行加购，不执行支付。confirm_order 接收地址与支付授权参数，均为确认式操作。')

doc.add_heading('4.4.4 以图搜图', level=3)
add_para('需求描述：新增 image_search 工具，进入淘宝或京东的相机或扫一扫入口（UI 元素定位加固定坐标兜底），调起系统相册上传已保存图片，等待识图结果后复用 extract_info 采集。')

doc.add_heading('4.4.5 多模型选择与网关', level=3)
add_para('需求描述：引入 LiteLLM 作为统一模型网关，支持 OpenAI 兼容协议的所有供应商。前端提供模型选择下拉（千问、DeepSeek、自动），任务请求增加 model 参数。')
add_para('业务规则：Manager、Executor、Verifier 可分别配置不同模型，强模型做决策、快模型做执行。主模型超时或报错时自动切换备用模型，最后兜底规则。模型配置存本地 models.json，不上传云端。')

doc.add_heading('4.4.6 可观测性平台', level=3)
add_para('需求描述：接入 Langfuse 自托管平台，实现全链路 tracing、工具成功率统计、token 成本追踪、延迟分位指标与告警。暴露 /v2/metrics 端点（Prometheus 文本格式）。')
add_para('业务规则：每个任务一个 trace，每个节点、工具与 LLM 调用一个 span，记录 latency、token usage、input 与 output。生产 trace 可一键转为评估数据集，形成数据飞轮。告警规则包括任务成功率低于阈值、LLM 错误率飙升。')

doc.add_heading('4.4.7 安全与鉴权', level=3)
add_para('需求描述：可选 DRO_API_TOKEN Bearer 校验中间件（缺省关闭以保持零配置可用）。上传文件增加八兆大小上限与魔数校验。所有设备操作写入不可变审计日志。')
add_para('业务规则：审计日志包含时间、用户、设备、动作、截图五个字段。用户输入做 prompt injection 检测。不同用户任务路由到不同设备，执行后自动清理应用数据。')

doc.add_heading('4.4.8 记忆增强', level=3)
add_para('需求描述：会话摘要在结果节点末尾触发而非 get_history 内隐式触发；长期偏好支持显式更正（用户说"其实我喜欢黑色"时覆盖而非追加）；向量检索用 sqlite-vec 替代 Qdrant 依赖，保持零外部服务可启动。')

doc.add_heading('4.5 前端工程化', level=2)

doc.add_heading('4.5.1 页面代码抽离', level=3)
add_para('需求描述：将当前内嵌在 api.py 中约一千一百行的 HTML、CSS 与 JavaScript 抽离到 web/templates 与 web/static 目录，FastAPI 通过 StaticFiles 与 Jinja2Templates 托管。静态资源按 content hash 命名并设置长缓存。')

doc.add_heading('4.5.2 SSE 实时推送', level=3)
add_para('需求描述：将当前九百毫秒与一千五百毫秒轮询改为 Server-Sent Events 流式推送。新增 /v2/chat/{session_id}/stream 与 /v2/tasks/{thread_id}/stream 两个 SSE 端点，工具每步完成即推送 progress 事件，最终回复分片推送 token 事件。')
add_para('业务规则：前端使用 EventSource 消费，连接断开自动重连；不支持 SSE 的环境后退化为轮询。SSE 为单向通信，用户停止操作通过单独 POST 端点实现，无需全双工 WebSocket。')

doc.add_heading('4.5.3 设计系统统一', level=3)
add_para('需求描述：两页共用一套 CSS 变量设计令牌（色板、圆角、阴影、间距），去掉重复的根样式定义。增加暗色模式（prefers-color-scheme 加手动切换）。补全移动端响应式，小屏折叠会话侧栏、输入区吸底。')

doc.add_heading('4.5.4 商品卡片与步骤时间线', level=3)
add_para('需求描述：商品卡片增加缩略图、"打开深链"按钮与"需要代下单"二次确认卡片，支持导出比较结果为 JSON 或 CSV。控制台执行日志从纯文本升级为可视化步骤时间线，每步展示工具名、耗时、成功失败状态与可点击展开的截图缩略图。')

doc.add_heading('4.5.5 交互体验优化', level=3)
add_para('需求描述：最终回复支持打字机流式渲染；发送中按钮禁用并提供停止态；会话列表支持删除与重命名；长会话历史采用虚拟列表避免一次性渲染大量 DOM。')

# ==================== 5. 非功能需求 ====================
doc.add_heading('5 非功能需求', level=1)

doc.add_heading('5.1 性能', level=2)
add_para('单任务端到端时长较基线下降不少于百分之三十；工具调用 P95 延迟小于十秒（含超时兜底）；SSE 进度推送延迟小于一百毫秒；前端首屏加载小于两秒（静态资源缓存后）。')

doc.add_heading('5.2 可靠性', level=2)
add_para('设备断连自动重连，任务不丢失；checkpoint 断点恢复成功率为百分之百（同一 thread_id）；LLM、设备与外部存储任一不可用时，主流程有降级路径不崩溃。降级链为，LLM 不可用时用启发式意图加确定性兜底计划加模板拼接；设备断连时自动重连；Qdrant 或 PostgreSQL 不可用时回退 SQLite。')

doc.add_heading('5.3 安全', level=2)
add_para('敏感操作（支付、删除、下单）必须经前端二次确认与会话级授权；所有设备操作写入不可变审计日志；用户输入做 prompt injection 检测；上传文件做魔数校验与大小限制；不同用户任务路由到不同设备，执行后自动清理应用数据。')

doc.add_heading('5.4 可观测性', level=2)
add_para('每个任务一个 trace，每个节点、工具与 LLM 调用一个 span；暴露 Prometheus 格式指标端点，覆盖请求数、成功率、平均步时、工具成功率、LLM 延迟分位、token 消耗；生产 trace 可一键转为评估数据集。')

doc.add_heading('5.5 兼容性', level=2)
add_para('Python 三点十及以上；Android 设备或模拟器且 ADB 可用；零外部依赖可启动（SQLite 兜底），Qdrant 与 PostgreSQL 为可选增强；前端支持 Chrome、Edge、Safari 最新两个大版本。')

# ==================== 6. 技术架构与依赖 ====================
doc.add_heading('6 技术架构与依赖', level=1)

doc.add_heading('6.1 目标架构分层', level=2)
add_para('目标架构自下而上分为六层。基础设施层包含 LiteLLM 模型网关、Langfuse 可观测性、SQLite WAL 存储、评估体系与 Docker 部署。设备池层包含 DevicePool、每设备 FIFO 队列与 ADB over TCP/IP 连接。工具与动作层包含标准化原子动作、Set-of-Mark 索引与技能包注册表。Agent 编排层包含 LangGraph Manager-Executor 反馈环、Verifier 与专用文本 Agent。API 网关层包含 FastAPI、鉴权中间件、审计与 SSE。前端层包含 React、TypeScript、Vite 与 SSE 消费。')

doc.add_heading('6.2 外部依赖', level=2)
add_table(
    ['依赖', '用途', '是否必需'],
    [
        ['DashScope API', 'Qwen-VL 视觉模型与 DeepSeek 文本模型', '必需（可替换为其他OpenAI兼容供应商）'],
        ['ADB', 'Android 设备控制', '必需'],
        ['SQLite', '记忆、checkpoint与任务存储', '必需（内置）'],
        ['Langfuse', '全链路可观测性', '推荐（可自托管）'],
        ['LiteLLM', '多模型统一网关', '推荐'],
        ['Qdrant', '长期偏好向量检索', '可选（SQLite兜底）'],
        ['PostgreSQL', '用户画像结构化存储', '可选（SQLite兜底）'],
        ['Docker', '部署与评估环境', '推荐'],
    ],
    col_widths=[3, 6, 5],
)

# ==================== 7. 里程碑与排期 ====================
doc.add_heading('7 里程碑与排期', level=1)
add_table(
    ['迭代', '主题', '周期', '关键交付', '验收标准'],
    [
        ['I 性能底座', '连接与实例复用加状态等待', '第1-2周', 'LLM单例、线程池复用、ADB缓存、记忆单例、规则优先finish', '单任务时长降≥30%；37单测全绿；无回归'],
        ['II 编排重构', 'Manager-Executor加SoM加双通道', '第3-4周', '动态反馈环、标准化动作、SoM标注、屏幕稳定化', 'AndroidWorld成功率≥40%；LLM调用数降40%'],
        ['III 后端底座', '设备池加Langfuse加模型网关加安全', '第5-6周', 'DevicePool、FIFO队列、Langfuse接入、LiteLLM、鉴权审计', '支持3设备并发；全链路可追踪；敏感操作需确认'],
        ['IV 前端工程化', 'SSE加组件化加设计系统', '第7-8周', '页面抽离、SSE推送、设计系统、步骤时间线', '进度实时<100ms；暗色模式；构建产物缓存'],
        ['V 功能扩展', '评分加深链加代下单加以图搜图', '第9-10周', '打分算法、深链提取、代下单流程、以图搜图、记忆增强', '电商任务集成功率≥70%；代下单二次确认生效'],
        ['VI 评估闭环', 'AndroidWorld接入加CI回归加轨迹回放', '第11-12周', '评估Docker、CI流水线、失败轨迹回放', '每次变更自动评估；失败轨迹可回放'],
    ],
    col_widths=[2.2, 3, 1.8, 4, 4],
)

# ==================== 8. 风险与应对 ====================
doc.add_heading('8 风险与应对', level=1)
add_table(
    ['风险', '影响', '概率', '应对措施'],
    [
        ['单设备串行化后多用户排队饥饿', '体验下降', '中', '队列长度与超时上限；前端展示排队状态；优先扩设备池'],
        ['SQLite并发写锁冲突', '稳定性', '中', '每库独立连接加WAL模式；checkpoint考虑AsyncSqliteSaver'],
        ['代下单与深链涉及真实资金', '安全合规', '中', '二次确认加白名单；测试阶段只加购不支付；明确用户授权'],
        ['视觉与坐标不稳定导致任务失败', '成功率', '高', 'UI元素优先加SoM索引加坐标兜底；保留视觉提取可重试'],
        ['SoM标注增加每步延迟', '性能', '中', '标注用轻量UI检测（Accessibility Tree bounds），不额外调VLM'],
        ['LangGraph版本API变动快', '维护成本', '中', '锁定版本；封装适配层；核心逻辑不深度绑定框架特性'],
        ['多供应商模型行为差异大', '一致性', '中', '模型网关统一输出格式；每个模型有独立prompt变体；评估集覆盖多模型'],
    ],
    col_widths=[4, 2.5, 1.5, 6],
)

# ==================== 9. 验收标准 ====================
doc.add_heading('9 验收标准', level=1)

doc.add_heading('9.1 功能验收', level=2)
add_para('所有 P0 功能需求可演示通过；电商比价、找相似、最优推荐三类核心任务端到端可跑；代下单流程触发二次确认弹窗，未确认不执行支付；设备断连后自动重连且任务不丢失；同一 thread_id 中断后可 resume 从断点继续。')

doc.add_heading('9.2 性能验收', level=2)
add_para('单任务端到端时长较基线下降不少于百分之三十（评估集平均值）；单任务 LLM 调用数不超过六次；SSE 进度推送延迟小于一百毫秒；三台设备并发任务无串数据、无崩溃。')

doc.add_heading('9.3 质量验收', level=2)
add_para('单元测试全绿，新增功能覆盖率不低于百分之八十；电商任务集成功率不低于百分之七十；Langfuse trace 完整覆盖每个任务的节点、工具与 LLM 调用；无 print 残留，统一结构化日志。')

doc.add_heading('9.4 安全验收', level=2)
add_para('敏感操作必须二次确认，绕过尝试被拦截；上传文件超八兆或非图片格式被拒绝；操作审计日志不可篡改，包含时间、用户、设备、动作、截图五个字段。')

# ==================== 附录 ====================
doc.add_page_break()
doc.add_heading('附录A 术语表', level=1)
add_table(
    ['术语', '解释'],
    [
        ['SoM（Set-of-Mark）', '在截图上为可交互元素叠加编号圆点，模型通过选编号间接定位元素的技术'],
        ['Manager-Executor', '每步执行后Manager重新评估规划的动态反馈环，区别于一次性plan-then-execute模式'],
        ['Checkpoint', 'LangGraph每步状态持久化机制，支持同一thread_id断点恢复'],
        ['MCP', 'Model Context Protocol，Anthropic推出的工具调用开放协议，使Skills可跨客户端复用'],
        ['Accessibility Tree', 'Android无障碍服务提供的界面元素层级结构，包含元素bounds、text、clickable等属性'],
        ['WAL', 'SQLite的Write-Ahead Logging模式，支持并发读与更好的写入性能'],
        ['SSE', 'Server-Sent Events，基于HTTP的单向服务器推送协议，适用于LLM流式输出'],
        ['Verifier', '对候选动作进行二次评估的验证器，用于高风险操作的安全把关'],
    ],
    col_widths=[4, 11],
)

doc.add_heading('附录B 参考标杆', level=1)
add_table(
    ['项目', '参考点'],
    [
        ['Droidrun（91.4% AndroidWorld）', 'Manager-Executor动态反馈环架构、专用文本Agent、差分状态跟踪、屏幕稳定化'],
        ['Mobile-Agent-v3 / GUI-Owl', 'Set-of-Mark元素索引、基础VLM模型、自进化轨迹生产'],
        ['V-Droid（微软）', '验证器驱动范式，LLM作为verifier而非generator'],
        ['WorkBuddy / OpenClaw（腾讯）', 'Harness四层架构（编排/记忆/行动/管控）、MCP工具协议、Skills生态、Hooks安全机制、Claw远程控制'],
        ['AndroidWorld', '116任务跨20个应用的GUI Agent评估基准，当前SOTA 91.4%'],
        ['Langfuse / LangSmith', 'Agent全链路可观测性、tracing、评估与数据飞轮'],
        ['OpenSTF', '开源设备农场架构，设备注册与心跳机制'],
    ],
    col_widths=[4.5, 10.5],
)

doc.save(OUT)
print(f'PRD generated: {OUT}')
