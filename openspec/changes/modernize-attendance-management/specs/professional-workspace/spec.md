## ADDED Requirements

### Requirement: Consistent product identity
系统 SHALL 使用产品名称“笃行”、小标题“学生操行管理系统”和统一主题文案“日常有序，成长有迹”，在登录页、全局导航、页脚及报表中保持统一。

左侧导航的两行主题字样 SHALL 省略逗号，并在导航底部展示“© 2018 Gauss Tech”；其中“Gauss Tech” SHALL 直接链接到`mailto:ams@unzip.work`。

#### Scenario: Product name and subtitle are displayed
- **WHEN** 用户进入带产品标识的页面
- **THEN** 主名称为“笃行”，小标题为“学生操行管理系统”，完整组合为“笃行 · 学生操行管理系统”；主题文案使用“日常有序，成长有迹”

#### Scenario: Pictorial bamboo identity
- **WHEN** 页面显示产品Logo
- **THEN** 使用已选定的 B“竹节”图案，与独立产品文字组合，不使用原“笃”字印章；图案适配深浅背景，16–24像素使用简化版本，装饰图案不重复朗读产品名称

### Requirement: Unified professional workspace
系统 SHALL 提供一致的工作台、班级、录入、学生详情、评分规则和班级管理导航，并依据角色显示有效操作。

#### Scenario: Owner-wide settings placement
- **WHEN** 负责人进入评分规则
- **THEN** 明确显示其所有班级、当前及未来学期生效范围以及历史规则不变

#### Scenario: Preview and abandon a rules draft
- **WHEN** 班主任修改评分设置并预览后选择取消修改
- **THEN** 输入恢复已保存版本，预览和保存按钮复位，可以直接离开页面

#### Scenario: Leave a rules draft through navigation
- **WHEN** 班主任预览后点击导航或切换学期、班级
- **THEN** 页面明确提供继续修改与放弃修改并离开两种选择；继续保留输入，放弃后完成跳转，不重复触发浏览器离页阻塞

#### Scenario: Leave an unsaved attendance draft
- **WHEN** 点名中点击站内导航或切换学期
- **THEN** 明确提示输入将丢失，继续编辑则阻断跳转，确认放弃后才离开；提交回执未确认时先阻断并允许原提交恢复

#### Scenario: Roster changes before attendance submission
- **WHEN** 点名页打开后名单版本发生更新
- **THEN** 提交返回名单冲突，明确要求刷新最新名单并重新点名，不自动将旧状态套用到新名单

#### Scenario: Named committee user identity
- **WHEN** 通过独立班委账号登录的用户进入工作台
- **THEN** 身份统一显示“班委”，导航仅包含其获授权的班级和操作

### Requirement: Fast and recoverable record entry
系统 SHALL 支持默认正常、记录类型和学生状态直接点选、误触撤销、提交前人数与异常名单核对及保存状态反馈，不使用下拉菜单作为高频点名的必要步骤。录入名单学生姓名旁 SHALL 显示已有性别数据对应的男女图标，缺失时不猜测。

#### Scenario: One-click student marking
- **WHEN** 用户在考勤页标记某学生迟到
- **THEN** 单击该学生行的“迟到”即完成选择，以文字、勾选及颜色反馈，其他学生和名单顺序不变；再次点同一状态不会取消

#### Scenario: Correct a mistaken selection
- **WHEN** 用户误选缺勤后直接点到课，或撤销最近一步
- **THEN** 本页状态和人数汇总立即一致更新，未提交前不写入正式成绩

#### Scenario: Default status does not imply verified attendance
- **WHEN** 名单默认全部到课且尚未提交
- **THEN** 显示状态人数和未提交提示，不声称全部学生已核验，最终保存前可核对异常姓名与状态

#### Scenario: Preserve drafts across local context switches
- **WHEN** 用户切换班级或记录类型后再返回
- **THEN** 恢复该班该类型的本页草稿，不把状态应用到另一份名单；离开或刷新将丢失输入时提示

#### Scenario: Submission fails or its response is uncertain
- **WHEN** 保存失败或未获得确定的服务器回执
- **THEN** 保留输入并提供反馈，未确认成功不显示已保存；不确定响应沿用原提交编号重试或查询结果

#### Scenario: Authorization expires while entering records
- **WHEN** 用户已填写名单状态但授权失效
- **THEN** 保留当前输入并允许重新授权，不要求重新点名

### Requirement: Coherent icon and text controls
系统 SHALL 在导航、页面及主要区块标题、操作控件中结合语义图标和可见文字；图标须与整体国风视觉协调，同一功能使用一致图标和笔画风格。

#### Scenario: Icon and text form one action
- **WHEN** 用户点击保存、撤销、记录类型或学生状态按钮的图标或文字区域
- **THEN** 触发同一个明确动作，不增加操作步骤，选中状态同时有文字和图形反馈

#### Scenario: Icons are unavailable or a screen reader is used
- **WHEN** 图标未能加载或用户通过读屏访问页面
- **THEN** 控件仍有完整文字名称并可操作，装饰图标不重复朗读，不依赖图标或颜色单独传达状态

### Requirement: Accessible responsive interaction
系统 SHALL 在桌面和手机提供可读表格或条目布局，支持键盘操作、可见焦点和非颜色唯一的状态表达。

#### Scenario: Mobile record entry
- **WHEN** 在窄屏使用快速录入
- **THEN** 姓名、状态与主要操作不重叠，不要求缩小页面才能点击

#### Scenario: Narrow-screen direct controls
- **WHEN** 手机宽度为320像素
- **THEN** 状态按钮仍可直接点击，触控区至少44像素高，姓名与学号可读，不退回下拉选择

### Requirement: Explicit exceptional states
系统 SHALL 清楚展示空数据、只读、失败、冲突、重复请求已处理和正在保存状态。

#### Scenario: August workspace
- **WHEN** 用户在8月查看系统
- **THEN** 清晰显示只读和最近完成学期，不呈现可正常提交的新建按钮

#### Scenario: Closed semester workspace
- **WHEN** 任意角色查看已结束学期
- **THEN** 展示“已结束 · 只读”和当期名单/记录/成绩，不提供新增、编辑、删除或审核状态修改入口

#### Scenario: Historical visual identity
- **WHEN** 查看已结束学期
- **THEN** 整页采用中性灰色的只读视觉体系，九项次数与分数的文字仍保留，避免把历史页面误认作当前学期

### Requirement: Personal semester overview
系统 SHALL 在工作台默认展示名下所有班级所有学生的学期概况，包括班级、学号、姓名、九类次数与个人学期分数，支持多班筛选、姓名学号搜索，以及标注为“筛选”的考勤、活动、违纪三类学期次数非0人员筛选。当前可见且按所选顺序排列的结果 SHALL 可导出为UTF-8 CSV，含班级、身份、性别、九项次数与个人学期分数，不导出未匹配学生。

#### Scenario: All owned classes on entry
- **WHEN** 班主任进入工作台
- **THEN** 默认全选名下班级，全体当期学生可查，不以班均分替代个人概况

#### Scenario: Filter and export personal scores
- **WHEN** 班主任按班级、姓名或学号、三类非0人员及排序筛选全员学期概况后导出
- **THEN** CSV仅包含当时可见学生，顺序和九项次数、分数与列表一致；空结果不能导出

### Requirement: Grouped personal score tables
系统 SHALL 在全员学期概况、班级学期汇总和班级月度明细中使用相同的两层表头和完整九项次数：考勤下为缺勤、迟到、请假，活动下为班级、院级、校级，违纪下为轻度、中度、严重；个人分数独立显示。三个类别之间 SHALL 有无需依赖颜色也能辨识的视觉边界。

#### Scenario: Inspect a class semester
- **WHEN** 班主任打开某班学期汇总
- **THEN** 每位学生的九项次数和个人学期分数与工作台按该班筛选的口径一致，不以活动合计遮蔽院级和校级，也不省略请假及违纪

#### Scenario: Inspect monthly and owner-wide tables
- **WHEN** 用户查看本月/上月或名下全员概况
- **THEN** 考勤、活动、违纪各统领三项明细，列的可访问名称保留完整业务类型；桌面常用宽度优先同时显示学生身份与个人分数，窄屏只在表格区域横向滚动

### Requirement: Consistent student identities and ordering
系统 SHALL 在工作台、班级学期表、班级月度表、快速录入、班级名单和批量新增预览中统一用“学生”列展示姓名主行、学号副行；跨班页面需要班级时在身份副信息中弱化显示。每个列表 SHALL 支持按学号及中文拼音首字母升序、降序排序，默认按学号升序。

#### Scenario: A student appears in different lists
- **WHEN** 同一学生出现在任意学生列表中
- **THEN** 姓名是主要视觉及可用链接锚点，学号在下一行可读；跨班工作台的班级标识低于姓名和学号的视觉强度

#### Scenario: Choose a list order
- **WHEN** 用户选择学号或拼音首字母的升序/降序
- **THEN** 仅当前列表可见行按该规则排序；同音姓名以学号确定次序，默认初次进入按学号升序

#### Scenario: Reorder a draft or import preview
- **WHEN** 用户在点名或批量新增预览中切换排序
- **THEN** 已选的学生状态、待新增名单和提交事实不改变；切换月份时月度表继续使用已选排序

### Requirement: Class operational metrics
系统 SHALL 展示班级人数、所选学期累计活动次数及该学期最近活动创建时间，不把班级均分作为操行评价对象。

班级总览 SHALL 以“班级Top 3”为标题展示缺勤、迟到、请假、活动、违纪五类个人次数Top 3；缺勤、迟到、请假分别沿用新增记录里各自操作按钮的图标，活动和违纪沿用各自的新增记录类型图标。活动合并班级/院级/校级次数，违纪合并轻度/中度/严重次数；只列非0学生，同次数按学号升序，不以评分权重代替次数。

#### Scenario: Class without events
- **WHEN** 查看没有活动的班级
- **THEN** 活动次数为0、最近活动为暂无；学生的已进入计分月仍按既定规则有基础分

#### Scenario: Inspect category leaders
- **WHEN** 查看某班某学期的五类Top 3
- **THEN** 每类最多显示三位非0学生的姓名、学号和次数，活动与违纪是其各自三级次数之和，零记录类别显示空状态

### Requirement: Two login channels
系统 SHALL 在首页提供班主任和班委两个账号密码登录渠道，默认班主任，不要求班级码。

#### Scenario: Anonymous visitor
- **WHEN** 未登录用户进入首页或班级地址
- **THEN** 提供统一登录页，不泄露班级元数据或学生信息

### Requirement: Validated student creation
系统 SHALL 使用“新增学生”文案，提供旧Excel模板下载、受限xlsx解析和文本粘贴预览，最终整批事务提交。

#### Scenario: Upload preview
- **WHEN** 上传格式合法的新增学生模板
- **THEN** 展示完整名单和数量供核对，尚不写入学生；确认时复核版本、重复、角色和学期

#### Scenario: Unsafe or ambiguous spreadsheet values
- **WHEN** 输入区含公式、数值学号或文件超限
- **THEN** 明确拒绝并说明位置/原因，不默默截断、修正或导入部分名单


### Requirement: Separate global and class navigation
系统 SHALL 将系统顶层导航与明确班级上下文的局部导航分开呈现，手机和桌面保持一致层次。

#### Scenario: Entering a class
- **WHEN** 进入班级或其记录/管理页面
- **THEN** 系统区域只显示工作台/评分规则，班级区域显示班名及总览/录入/管理/日志，按权限和只读状态隐藏不可用动作

### Requirement: Linked complete monthly overview
系统 SHALL 在班级详情将所选学期已进入的每个自然月放入与排序同级的下拉控件；月度表展示完整九类次数、月分及个人当月明细链接，不跨越学期边界。

#### Scenario: Select an elapsed month
- **WHEN** 用户在班级总览选择本学期某个已进入的月份
- **THEN** 表格按该月展示，即使没有活动也有当期基础分；尚未进入的月份和8月不混入下拉选项

### Requirement: Count affected students per business record
系统 SHALL 在班级业务记录列表显示本次记录人数：考勤统计非正常人数，活动与违纪统计依据该学期权重产生非零分值的人数。

#### Scenario: Zero-weight event
- **WHEN** 一次活动有多人标记参与但其中某等级权重为0
- **THEN** 该等级学生不计入该记录的非零分人数，原始参与事实和九项次数仍保留

### Requirement: Scannable individual score anchors
系统 SHALL 通过清晰数字、朱砂扣分/青绿奖励标记、弱化零值和非颜色唯一的分数高低提示，帮助班主任扫描个人表现。

#### Scenario: Nonzero counts and score comparison
- **WHEN** 查看学生汇总或月报
- **THEN** 非零次数有明确视觉锚点，个人分数按当时基础分辅助标记，不使用未定义合格阈值或班级均分
