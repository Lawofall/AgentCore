"""CEO visualization hook fragment (FRAGMENT_CEO_VISUALIZATION)."""

# CEO-only short hook: when to prefer mermaid/markmap/vega-lite. Full syntax HOW
# is not resident (models know the dialects; verbose bans were cut in the prompt polish).
# Shared base keeps the one-line affordance for workers. SectionOrder.CEO_VISUALIZATION.
_CEO_VISUALIZATION_HINT = """
<visualization>
解释多步流程、架构/关系、状态流转、方案或数据对比、层级/时序等结构化内容时，优先配图——\
直接写 ```mermaid / ```markmap / ```vega-lite / ```compare 代码块，前端会渲染；数值先取再画，一段最多一张（compare 一块算一张），\
纯线性一两句能说清的别硬塞。语法与克制细则随手遵守即可（无需工具）。
HTML / 页面类可视交付：交付链会自动产出预览图（主壳 ``site/preview-*.jpg``，其它 HTML 在同目录 ``{stem}.preview-*.jpg``），勿教用户或队员去调截图工具。\
用户要对比多个方案时，各候选写成**独立完整 HTML**（各自可预览），不要合成一张对比拼图；\
气泡内并排用 ```compare（格：`A|标签` 下一行工作区图片路径，格间 `---`；或 `![标签](path.png)`；只许工作区图）。
</visualization>"""
